#!/usr/bin/env python3
"""996 match controller. Local candidate: final APP/helper pins required.

No fixed running/lease deadline. A start request never uses Damping as a test.
Stop/X/F1/APP exit may remove support: keep people clear and support the robot.
Default is plan only; production installation is a separate reviewed operation.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import signal
import stat
import subprocess
import sys
import time
import uuid

ROOT=Path('/opt/booster/bhuman_match_996')
PROJECT=Path('/home/booster/Workspace/BHuman_996_Match_20261019')
OLD_ROOT=Path('/opt/booster/bhuman_trial_20261019_v2')
SERIAL='1424725032007'
CORE_SHA='2fdd52054126cfe4795ee5a03bd90a835aac9179aa7d68eda5c9b71f7a51d7df'
APP_SHA='0d54da25ec0c8165f8aa0648cc26019eed3b540d9e75638f1f296662dd4dfba9'
MODE_QUERY_SHA='6c709cb9da7a59a9d012b9501aa3910871b0fd0fad7dbbefa5503e0d677b37f3'
OBSERVER_SHA='8029d488f59d90f8e929ef08e3af27bcabf926a8f4b1bca0c766d2123c48de82'
DAMPING_SHA='ef2b39bc8c6a7ea8991dfc01e8fed508f453f2c75db3af3e1e18bb8f8959cd54'
STATE=ROOT/'MATCH_STATE.json'
PERMIT=ROOT/'DAEMON_START.json'
START=ROOT/'APP_START.json'
START_FAILURE=ROOT/'START_FAILURE.json'
STARTUP_TIMELINE=ROOT/'STARTUP_TIMELINE.json'
MODE_QUERY_LAST=ROOT/'MODE_QUERY_LAST.json'
UNIT='bh996-match.service'
RECOVERY='bh996-match-recovery.service'
RETRY='bh996-match-recovery-retry.timer'
HOOK=Path('/etc/systemd/system/booster-daemon.service.d/bh996-match.conf')
CALL=f'/usr/bin/python3 -I {ROOT}/match_control.py'
PROFILE='/opt/booster/config/dds_configs/single-board_configs/fastdds_profile_udp_only.xml'
APP_ARGS=['-c',str(PROJECT),'--k1-996-match']
UNITS={
 UNIT:f'''[Unit]\nDescription=996 operator-started match (no fixed runtime)\nOnSuccess={RECOVERY}\nOnFailure={RECOVERY}\n[Service]\nType=simple\nUser=booster\nGroup=booster\nWorkingDirectory={PROJECT}\nEnvironment=FASTRTPS_DEFAULT_PROFILES_FILE={PROFILE}\nEnvironment=LD_LIBRARY_PATH={PROJECT}/lib:{PROJECT}/Config:/usr/local/lib\nEnvironment=XDG_RUNTIME_DIR=/run/user/1000\nEnvironment=PULSE_SERVER=unix:/run/user/1000/pulse/native\nEnvironment=PULSE_SOURCE=alsa_input.usb-NationalChip_NationalChip_Uac_Speaker_20161029-00.multichannel-input\nEnvironment=PULSE_SINK=alsa_output.usb-C-Media_Electronics_Inc._USB_Audio_Device-00.analog-stereo\nLimitRTPRIO=65\nLimitMEMLOCK=40M\nRestart=no\nTimeoutStopSec=15\nKillSignal=SIGINT\nKillMode=control-group\nExecCondition=+{CALL} unit-ready --execute\nExecStart={PROJECT}/bin/bhuman -c {PROJECT} --k1-996-match\n''',
 RECOVERY:f'''[Unit]\nDescription=996 match original-route recovery\nOnFailure={RETRY}\nStartLimitIntervalSec=300\nStartLimitBurst=3\n[Service]\nType=oneshot\nExecStart={CALL} rollback --execute\nTimeoutStartSec=360\nTimeoutStopSec=10\n''',
 RETRY:f'''[Timer]\nOnActiveSec=15s\nUnit={RECOVERY}\nAccuracySec=1s\nRemainAfterElapse=no\n'''}
HOOK_TEXT=f'[Service]\nExecStartPre={CALL} boot-guard --execute\n'


class Rejected(RuntimeError):pass
class ModeQueryUnavailable(Rejected):pass
def require(value,message):
    if not value:raise Rejected(message)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def valid_sha(value):return len(value)==64 and all(char in '0123456789abcdef' for char in value)


def journal_cognition(row,pid,now):
    """Return fresh text telemetry, never stringify journald binary/repeated fields.

    journalctl JSON encodes a non-UTF8 MESSAGE as an integer array and may encode
    repeated fields as lists. Such rows are not Cognition evidence. Missing or
    malformed rows/timestamps likewise cannot certify application readiness.
    """
    if not isinstance(row,dict) or row.get('_PID')!=str(pid):return None
    message=row.get('MESSAGE');stamp=row.get('__MONOTONIC_TIMESTAMP')
    if not isinstance(message,str) or not isinstance(stamp,str):return None
    if not re.fullmatch(r'[0-9]{1,20}',stamp):return None
    timestamp=int(stamp);age=now-timestamp/1e6
    if timestamp<=0 or not 0<=age<=5:return None
    found=re.search(r'K1_OFFICIAL_LIVE INPUT .*cognition_frames=([0-9]{1,20}) cognition_time=([0-9]{1,20})(?=\s|$)',message)
    if not found:return None
    return timestamp,int(found[1]),int(found[2])


def healthy_observer(raw):
    rows=[json.loads(line) for line in raw.decode().splitlines() if line.startswith('{')]
    require(bool(rows),'no observer JSON');end=rows[-1]
    require(end['type']=='observer_done' and end['exit_code']==0 and end['callback_errors']==0,'observer failure')
    require(end['closed_readers']==end['requested_readers']==6,'incomplete observer close')
    w=end['matched_writers_before_close']
    require(w['rt/button_event']==0 and w['rt/remote_controller_state']==1
            and w['rt/button_even2']==1 and w['rt/remote_controller_stat2']==1,'wrong controller topology')
    g=end['rt/device_gateway']['aggregate'];low=end['rt/low_state']
    require(g['callbacks_total']>=10 and g['min_connected_joint_count']==22
            and g['max_disconnected_joint_count']==0 and g['empty_joint_callbacks']==0,'unhealthy joints')
    require(low['samples_total']>=100 and low['arrival_age_ms']<100,'stale low state')
    for name in ('all_serial_q_dq','all_parallel_q_dq_tau_est','imu_rpy_gyro_acceleration'):
        require(low['finite_checks'][name]['nonfinite_values']==0,'nonfinite low state')


class Controller:
    def __init__(self,ops):self.o=ops
    def state(self):
        state=self.o.read(STATE)
        if state is not None:
            require(state.get('schema')==1 and state.get('owner')=='bh996-match'
                    and state.get('serial')==SERIAL,'untrusted match ownership')
        return state
    def save(self,state):self.o.write(STATE,state)
    def current(self,state):
        require(state and state['boot_id']==self.o.boot_id(),'no current-boot match')
        require(state['app_sha256']==APP_SHA,'match APP changed')
        return state
    def status(self):
        state=self.state()
        return {'phase':state['phase'] if state else 'NONE','running':bool(state and self.o.match_running(state)),
                'no_fixed_runtime_limit':True,'match_ready':False,
                'note':'Running does not prove fresh F1, deliberate RB+A or match fitness; native READY entry is used.'}
    @contextmanager
    def startup_stage(self,trace,name):
        started=self.o.now()
        row={'name':name,'started_elapsed_seconds':max(0,started-trace['started_at']),
             'status':'running'}
        trace['stages'].append(row)
        print('STARTUP '+name+' elapsed='+format(row['started_elapsed_seconds'],'.2f')+'s',flush=True)
        try:yield
        except BaseException as error:
            row['status']='failed';row['error']=self.error_record(error);raise
        else:row['status']='complete'
        finally:
            row['duration_seconds']=max(0,self.o.now()-started)
            print('STARTUP '+name+' '+row['status']+' duration='+format(row['duration_seconds'],'.2f')+'s',flush=True)
    def record_startup_timeline(self,trace,outcome,error=None):
        trace['outcome']=outcome;trace['elapsed_seconds']=max(0,self.o.now()-trace['started_at'])
        if error is not None:trace['error']=self.error_record(error)
        try:self.o.write(STARTUP_TIMELINE,trace) # Same root:root 0600 atomic evidence writer.
        except BaseException as write_error:
            print('WARNING: STARTUP_TIMELINE evidence write failed ('+type(write_error).__name__+')',file=sys.stderr)
    def start(self,operator):
        require(operator,'explicit operator-present attestation required')
        trace={'schema':1,'owner':'bh996-match','serial':SERIAL,'app_sha256':APP_SHA,
               'boot_id':self.o.boot_id(),'started_at':self.o.now(),'stages':[]}
        owned=False
        try:
            with self.o.lock():
                previous=self.state()
                if previous and previous['phase']=='RUNNING' and self.o.match_running(previous):
                    self.current(previous);return self.status() # No mode call or restart.
                require(not previous or previous['phase'] in ('RESTORED','BOOT_RESTORED'),'pending match: stop/recover first')
                with self.startup_stage(trace,'01_package_and_exclusive_ownership'):
                    self.o.preflight();self.o.old_trial_idle();self.o.no_body();self.o.originals()
                with self.startup_stage(trace,'02_original_inputs_and_damping_query'):
                    self.o.inputs_online();self.o.require_mode_zero() # QUERY ONLY, before any mutation.
                self.o.archive_core()
                state={'schema':1,'owner':'bh996-match','serial':SERIAL,'boot_id':self.o.boot_id(),
                       'id':uuid.uuid4().hex,'app_sha256':APP_SHA,'phase':'INITIALIZING',
                       'daemon_stop_intent':False,'body_start_intent':False,'services':[],
                       'files_restored':False,'pid':None}
                self.save(state);owned=True
                with self.startup_stage(trace,'03_initialize_input_routes'):
                    self.o.core('arm');state['daemon_stop_intent']=True;self.save(state)
                    self.o.daemon_stop();self.o.offline();self.o.core('apply');self.o.patched()
                state['phase']='PATCHED';self.save(state)
                self.o.write(PERMIT,{'boot_id':state['boot_id'],'id':state['id'],'expires':self.o.now()+30})
            with self.startup_stage(trace,'04_start_factory_daemon'):
                self.o.daemon_start() # ExecStartPre boot guard must not wait for this lock.
            with self.o.lock():
                state=self.current(self.state());self.o.no_body();self.o.patched()
                with self.startup_stage(trace,'05_patched_inputs_and_damping_query'):
                    self.o.inputs_online();self.o.require_mode_zero()
                with self.startup_stage(trace,'06_factory_camera_video_speech_ready'):
                    names=self.o.services_to_lease();state['services']=names;self.save(state)
                with self.startup_stage(trace,'07_exclusive_camera_and_speech_lease'):
                    self.o.pause_services(names)
                with self.startup_stage(trace,'08_five_second_live_health'):
                    raw=self.o.observe_health();self.o.write_raw(ROOT/'health.log',raw)
                    healthy_observer(raw)
                state['health_sha256']=sha(raw);state['health_at']=self.o.now();state['phase']='STARTING'
                self.save(state)
                self.o.write(START,{'boot_id':state['boot_id'],'id':state['id'],'expires':self.o.now()+30})
                state['body_start_intent']=True;self.save(state)
            with self.startup_stage(trace,'09_final_unit_checks_and_start_app'):
                self.o.start_app()
            with self.o.lock():
                state=self.current(self.state());state['pid']=self.o.app_pid();state['phase']='RUNNING';self.save(state)
            with self.startup_stage(trace,'10_fresh_camera_and_cognition'):
                self.o.wait_cognition() # Only fresh current-PID telemetry; not software self-certification of pose.
            self.record_startup_timeline(trace,'ready_for_operator')
            return {'phase':'RUNNING','ready_for_operator':True,'match_ready':False,
                    'startup_elapsed_seconds':trace['elapsed_seconds'],
                    'note':'Use fresh F1 then deliberate RB+A in INITIAL; native READY positioning needs no RB+Y.'}
        except BaseException as startup_error:
            self.record_startup_timeline(trace,'failed',startup_error)
            if owned:
                failure={'schema':1,'owner':'bh996-match','serial':SERIAL,
                         'boot_id':state['boot_id'],'id':state['id'],
                         'phase_at_failure':state['phase'],'at':self.o.now(),
                         'startup_error':self.error_record(startup_error),'recovery_error':None}
                self.record_start_failure(failure) # Preserve the FIRST error even if recovery is interrupted.
                try:self.rollback()
                except BaseException as recovery_error:
                    failure['recovery_error']=self.error_record(recovery_error)
                    self.record_start_failure(failure)
                    raise Rejected('startup failed: '+str(startup_error)+'; recovery failed: '+str(recovery_error)) from startup_error
                self.record_start_failure(failure)
            raise
    @staticmethod
    def error_record(error):
        # Fixed-command errors only: do not serialize environment, credentials or traceback locals.
        return {'type':type(error).__name__,'message':str(error)[:1500]}
    def record_start_failure(self,failure):
        try:self.o.write(START_FAILURE,failure) # LinuxOps atomic write is root:root, mode 0600.
        except BaseException as error:
            print('WARNING: START_FAILURE evidence write failed ('+type(error).__name__+')',file=sys.stderr)
    def unit_ready(self):
        with self.o.lock():
            state=self.current(self.state());permit=self.o.read(START)
            require(state['phase']=='STARTING' and permit and permit['id']==state['id']
                    and permit['boot_id']==state['boot_id'] and self.o.now()<permit['expires'],'no fresh one-shot APP start')
            self.o.no_body();self.o.patched();self.o.app_hash();self.o.inputs_online()
            self.o.services_offline(state['services']);self.o.require_mode_zero()
            raw=self.o.read_raw(ROOT/'health.log')
            require(0<=self.o.now()-state['health_at']<=90 and sha(raw)==state['health_sha256'],'stale/changed health proof')
            healthy_observer(raw)
            require(self.o.now()<permit['expires'],'APP start permit expired during checks')
            self.o.delete(START)
    def stop(self):
        state=self.state()
        if not state or state['phase'] in ('RESTORED','BOOT_RESTORED'):return self.status()
        self.o.request_recovery() # systemd joins the single recovery unit, not parallel CLI restores.
        return self.status()
    def rollback(self):
        state=self.state()
        if not state:
            self.o.originals();return
        if state['phase'] in ('RESTORED','BOOT_RESTORED'):
            self.o.originals();self.o.cancel_retry();return
        try:return self._rollback()
        except BaseException:
            self.o.schedule_retry() # Only after valid owned recovery was requested.
            raise
    def _rollback(self):
        state=self.state()
        if not state:
            self.o.originals();return
        if state['phase'] in ('RESTORED','BOOT_RESTORED'):
            self.o.originals();self.o.cancel_retry();return
        self.o.stop_app_no_wait();self.o.wait_body_off() # Never hold lock while a stop callback may recover.
        with self.o.lock():
            state=self.state()
            if state['phase'] in ('RESTORED','BOOT_RESTORED'):
                self.o.cancel_retry();return
            self.o.no_body()
            # An unstarted body must not cause a standing stock robot to be unloaded.
            if state['body_start_intent']:self.o.force_damping()
            elif self.o.daemon_online():self.o.require_mode_zero()
            state['phase']='RECOVERING';self.save(state);self.o.delete(START);self.o.delete(PERMIT)
            if self.o.core_phase()=='ARMED' and not state['daemon_stop_intent']:
                self.o.finish_unmodified_arm()
            elif self.o.core_phase() not in ('NONE','RESTORED'):
                self.o.daemon_stop();self.o.offline();self.o.core('restore')
            self.o.originals();state['files_restored']=True;self.save(state)
        if state['daemon_stop_intent'] and state['boot_id']==self.o.boot_id() and not self.o.daemon_online():self.o.daemon_start()
        with self.o.lock():
            self.o.inputs_online()
            if state['body_start_intent']:self.o.force_damping()
            else:self.o.require_mode_zero()
            self.o.restore_services(state['services'])
            state['services']=[];state['phase']='RESTORED';state['pid']=None;self.save(state)
            self.o.cancel_retry() # A previous failed recovery must not stop a future match.
    def boot_guard(self):
        with self.o.lock():
            state=self.state()
            if not state or state['phase'] in ('RESTORED','BOOT_RESTORED') or state.get('files_restored'):
                self.o.originals();return
            permit=self.o.read(PERMIT)
            if (state['boot_id']==self.o.boot_id() and state['phase']=='PATCHED' and permit
                and permit.get('id')==state['id'] and permit.get('boot_id')==state['boot_id'] and self.o.now()<permit['expires']):
                self.o.patched();self.o.delete(PERMIT);return
            self.o.offline() # No SDK or service start: new boot restores files BEFORE daemon.
            if self.o.core_phase() not in ('NONE','RESTORED'):self.o.core('restore')
            self.o.originals();self.o.delete(START);self.o.delete(PERMIT)
            state['phase']='BOOT_RESTORED';state['files_restored']=True;state['pid']=None;self.save(state)


class LinuxOps:
    def __init__(self):
        require(platform.system()=='Linux' and os.geteuid()==0,'Linux/root required')
        require(Path(__file__).resolve()==ROOT/'match_control.py','fixed installed match controller path required')
        require(Path('/sys/firmware/devicetree/base/serial-number').read_bytes().rstrip(b'\0\r\n')==SERIAL.encode(),'wrong hardware')
        info=ROOT.lstat();require(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and stat.S_IMODE(info.st_mode)==0o700,'untrusted match root')
        self.hash(ROOT/'route_transaction.py',CORE_SHA)
        spec=importlib.util.spec_from_file_location('pinned_match_routes',ROOT/'route_transaction.py')
        self.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.module)
        require(self.module.TRIAL_ROOT==ROOT,'route root mismatch');self.trial=self.module.Trial(Path('/'),live=True)
        self.env=dict(os.environ,FASTRTPS_DEFAULT_PROFILES_FILE=PROFILE)
        for line in Path('/opt/booster/env/containerd_ports.env').read_text().splitlines():
            line=line.removeprefix('export ').strip()
            if '=' in line and not line.startswith('#'):
                name,value=line.split('=',1);require(name.replace('_','').isalnum() and '$' not in value and '`' not in value,'nonliteral ports env')
                self.env[name]=value.strip('"\'')
    def run(self,args,timeout=25,check=True):
        result=subprocess.run(args,env=self.env,text=True,capture_output=True,timeout=timeout)
        require(not check or result.returncode==0,'fixed command failed: '+str(args[:3])+': '+result.stderr[-300:]);return result
    def now(self):return time.monotonic()
    def boot_id(self):return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    def hash(self,path,expected):
        require(valid_sha(expected),'UNFINALIZED package pin: '+str(path));raw,meta=self.module.read_file(path) if hasattr(self,'module') else (path.read_bytes(),None)
        require(sha(raw)==expected,'hash mismatch: '+str(path))
    def app_hash(self):self.hash(PROJECT/'bin/bhuman',APP_SHA)
    def read_raw(self,path):
        raw,meta=self.module.read_file(path,4*1024*1024);require(meta['uid']==0 and meta['mode']==0o600,'untrusted state');return raw
    def read(self,path):
        require(not path.is_symlink(),'symlink state');return json.loads(self.read_raw(path)) if path.exists() else None
    def write_raw(self,path,raw):self.module.atomic_write(path,raw,0o600,0,0)
    def write(self,path,value):self.write_raw(path,(json.dumps(value,sort_keys=True)+'\n').encode())
    def delete(self,path):
        if path.exists() or path.is_symlink():require(not path.is_symlink(),'symlink state');path.unlink()
    @contextmanager
    def lock(self):
        fd=os.open(ROOT/'match.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            info=os.fstat(fd);require(stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_nlink==1 and stat.S_IMODE(info.st_mode)==0o600,'untrusted lock')
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);yield
        finally:os.close(fd)
    def core_phase(self):return self.trial.execute('status')['phase'] if self.trial.checkpoint.exists() else 'NONE'
    def core(self,action):require(self.trial.execute(action)['ok'],'route transaction failed')
    def originals(self):
        for name in self.module.TARGETS:self.trial.verify_target(name,{self.module.APPROVED[name]['old']})
    def patched(self):
        for name in self.module.TARGETS:self.trial.verify_target(name,{self.module.APPROVED[name]['new']})
    def archive_core(self):
        require(self.core_phase() in ('NONE','RESTORED'),'pending route transaction')
        if self.trial.checkpoint.exists():self.trial.checkpoint.rename(ROOT/f'ROUTES.restored.{time.time_ns()}.json')
    def finish_unmodified_arm(self):
        with self.trial.lock():
            manifest=self.trial.manifest();state=self.trial.state(manifest);require(state['phase']=='ARMED','not unmodified ARMED')
            self.originals();state['phase']='RESTORED';self.trial.save(state)
    def no_body(self):
        for name in ('bhuman','brain_node','fw_salvador'):require(self.run(['/usr/bin/pgrep','-x',name],check=False).returncode==1,'other/current actor active: '+name)
    def unit_state(self,unit):
        result=self.run(['/usr/bin/systemctl','show',unit,'-p','ActiveState','-p','SubState','-p','MainPID','-p','Job','-p','InvocationID'])
        return dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
    def unit_idle(self,unit):
        value=self.unit_state(unit);pid=value.get('MainPID','0') if unit.endswith('.timer') else value.get('MainPID')
        require(value.get('ActiveState') in ('inactive','failed') and pid=='0' and value.get('Job') in ('','0'),'unit active/pending: '+unit)
    def old_trial_idle(self):
        for name in ('CANDIDATE_LEASE.json','CANDIDATE_SERVICES.json','CANDIDATE_START.json','CANDIDATE_DAEMON_START.json'):
            path=OLD_ROOT/name;require(not path.exists() and not path.is_symlink(),'old trial not finalized: '+name)
        checkpoint=self.read(OLD_ROOT/'ARMED.json');require(checkpoint is None,'old trial checkpoint not archived')
        require(not Path('/etc/systemd/system/booster-daemon.service.d/bh996-v2.conf').exists(),'old trial boot hook active')
        for unit in ('bh996-v2-live.service','bh996-v2-zero-pd.service','bh996-v2-recovery.service','bh996-v2-deadline.timer','bh996-v2-retry.timer'):
            self.unit_idle(unit)
    def preflight(self):
        for expected in (APP_SHA,MODE_QUERY_SHA,OBSERVER_SHA,DAMPING_SHA):require(valid_sha(expected),'UNFINALIZED match package')
        self.app_hash()
        for name,text in UNITS.items():require(Path('/etc/systemd/system',name).read_text()==text,'match unit drift')
        require(not HOOK.is_symlink() and HOOK.read_text()==HOOK_TEXT,'match boot guard missing/drifted')
        self.unit_idle(UNIT);self.unit_idle(RECOVERY);self.unit_idle(RETRY)
        require(self.daemon_online(),'factory daemon not online')
    def record_mode_query(self,record):
        # Keep the first timeout when the second fresh process succeeds. Raw
        # rows never contain this history, so the diagnostic cannot recurse.
        rows=(getattr(self,'_mode_query_attempts',[])+[record])[-2:]
        self._mode_query_attempts=rows
        self.write(MODE_QUERY_LAST,dict(record,attempts=rows))
    def mode_value(self):
        self.hash(PROJECT/'bin/k1_mode_query',MODE_QUERY_SHA)
        started=self.now()
        try:
            result=self.run(['/usr/sbin/runuser','-u','booster','--',str(PROJECT/'bin/k1_mode_query'),'--query-only'],timeout=10,check=False)
        except subprocess.TimeoutExpired as error:
            def text(value):return value.decode(errors='replace') if isinstance(value,bytes) else value or ''
            self.record_mode_query({'schema':1,'operation':'GetMode query-only','read_only':True,
                       'serial':SERIAL,'boot_id':self.boot_id(),'at':started,'elapsed_seconds':max(0,self.now()-started),
                       'returncode':None,'stdout':text(error.stdout)[-6000:],'stderr':text(error.stderr)[-6000:],
                       'error':'helper process exceeded 10s bound'})
            raise Rejected('mode query process exceeded 10s; no service or mode changed; see '+str(MODE_QUERY_LAST)) from error
        self.record_mode_query({'schema':1,'operation':'GetMode query-only','read_only':True,
                   'serial':SERIAL,'boot_id':self.boot_id(),'at':started,'elapsed_seconds':max(0,self.now()-started),
                   'returncode':result.returncode,'stdout':result.stdout[-6000:],'stderr':result.stderr[-6000:]})
        try:rows=[json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
        except (ValueError,TypeError) as error:raise Rejected('malformed mode query JSON; see '+str(MODE_QUERY_LAST)) from error
        require(bool(rows) and isinstance(rows[-1],dict),'no valid mode query JSON; see '+str(MODE_QUERY_LAST))
        value=rows[-1]
        require(value.get('kind')=='k1_mode_query_only','wrong mode query kind; see '+str(MODE_QUERY_LAST))
        require('serial' not in value or value['serial']==SERIAL,'wrong mode query serial; see '+str(MODE_QUERY_LAST))
        if result.returncode==0:
            require(value.get('serial')==SERIAL and value.get('result')==0,'mode query failed; see '+str(MODE_QUERY_LAST))
            return value # Non-Damping modes are deliberately reported, never retried or changed.
        rpc_timeout=(re.search(r'RPC request timed out\.',result.stderr) and
                     re.search(r'api_id\s*=\s*2017(?:\s|,|$)',result.stderr))
        helper_deadline=(result.returncode==124 and value.get('result')==-124 and
                         value.get('error')=='deadline_8_seconds')
        if rpc_timeout or helper_deadline:
            raise ModeQueryUnavailable('GetMode RPC unavailable (helper exit '+str(result.returncode)+'); see '+str(MODE_QUERY_LAST))
        raise Rejected('mode query helper rejected (exit '+str(result.returncode)+'); see '+str(MODE_QUERY_LAST))
    def require_mode_zero(self):
        self._mode_query_attempts=[] # A new check cannot inherit evidence from an earlier batch.
        for attempt in range(2):
            try:value=self.mode_value()
            except ModeQueryUnavailable as error:
                if attempt:
                    raise Rejected('GetMode unavailable after 2 bounded fresh query-only processes; no service restart or mode change; see '+str(MODE_QUERY_LAST)) from error
                print('WAIT_MODE_QUERY: GetMode暂时无响应；仅重建一次只读查询进程，不重启服务、不切换模式',flush=True)
                time.sleep(.25);continue
            require(type(value.get('mode')) is int and value['mode']==0 and value.get('damping') is True,
                    'REFUSED: must already be Damping; do not restart a standing robot')
            return
    def force_damping(self):
        self.hash(PROJECT/'bin/k1_damping_only',DAMPING_SHA)
        result=self.run(['/usr/sbin/runuser','-u','booster','--',str(PROJECT/'bin/k1_damping_only'),'--force-damping-only'],timeout=10)
        require('request_result=0' in result.stdout and 'verify_result=0 mode=0' in result.stdout,'final Damping not confirmed')
    def daemon_online(self):return self.unit_state('booster-daemon.service').get('ActiveState')=='active'
    def daemon_transition(self,action):
        end=self.now()+120;submitted=False
        while self.now()<end:
            value=self.unit_state('booster-daemon.service');pid=value.get('MainPID');job=value.get('Job')
            stopped=value.get('ActiveState') in ('inactive','failed') and pid=='0' and job in ('','0')
            online=value.get('ActiveState')=='active' and value.get('SubState')=='running' and pid and pid.isdigit() and int(pid)>1 and job in ('','0')
            if action=='stop' and stopped:self.offline();return
            if action=='start' and online:return
            if not submitted and job in ('','0') and value.get('ActiveState') not in ('activating','deactivating','reloading'):
                self.run(['/usr/bin/systemctl',action,'--no-block','booster-daemon.service']);submitted=True
            time.sleep(.25)
        raise Rejected('daemon '+action+' not complete in 120s')
    def daemon_stop(self):self.daemon_transition('stop')
    def daemon_start(self):self.daemon_transition('start')
    def offline(self):
        value=self.unit_state('booster-daemon.service');require(value.get('MainPID')=='0' and value.get('ActiveState') in ('inactive','failed','activating'),'daemon not offline/pre-exec')
        self.no_body()
        for entry in os.scandir('/proc'):
            if not entry.name.isdecimal():continue
            try:name=Path(os.readlink('/proc/'+entry.name+'/exe').removesuffix(' (deleted)')).name
            except FileNotFoundError:continue
            except OSError as error:raise Rejected('cannot inspect process ownership') from error
            require(name not in ('motion','device_gateway','remote_controller'),'factory input process still alive')
    def bdb(self,action,name,timeout=5):
        require(action in ('status','kill','boot') and name in ('device_gateway','remote_controller','ros.camera','ros.booster.video_stream','lui'),'unsupported fixed service operation')
        result=self.run(['/opt/booster/bin/bdb','service',action,name,'--json'],timeout=timeout,check=False)
        if result.returncode:
            if 'connection refused' in (result.stderr+result.stdout).lower():raise ConnectionError('config listener not yet available')
            raise Rejected('bdb failure: '+name)
        return json.loads(result.stdout)
    @staticmethod
    def online(value):return value.get('stage')=='ONLINE' and type(value.get('pid')) is int and value['pid']>1
    @staticmethod
    def off(value):return value.get('stage') in ('OFFLINE','STOPPED','EXITED','FAILED') and value.get('pid') is None
    def wait_service(self,name,online,end):
        while self.now()<end:
            try:
                value=self.bdb('status',name,timeout=min(5,end-self.now()))
                if (self.online(value) if online else self.off(value)):return
            except (ConnectionError,subprocess.TimeoutExpired):pass
            time.sleep(min(.25,max(0,end-self.now())))
        raise Rejected('service readiness timeout: '+name)
    def inputs_online(self):
        end=self.now()+90
        for name in ('device_gateway','remote_controller'):self.wait_service(name,True,end)
    def services_to_lease(self):
        names=('ros.camera','ros.booster.video_stream','lui')
        required=set(names[:2]);end=self.now()+90;stable={};last={}
        print('WAIT_FACTORY_SERVICES: 等待相机、视频及语音服务就绪（最多90秒，不会在此步骤再次重启服务）',flush=True)
        while self.now()<end:
            settled=[]
            for name in names:
                remaining=end-self.now()
                if remaining<=0:break
                try:value=self.bdb('status',name,timeout=min(5,remaining))
                except (ConnectionError,subprocess.TimeoutExpired) as error:
                    stable.pop(name,None)
                    last[name]={'stage':'UNKNOWN','error':type(error).__name__+': '+str(error)[:300]}
                    continue
                stage=value.get('stage');pid=value.get('pid')
                last[name]={'stage':stage,'pid':pid}
                for key in ('error','last_error'):
                    if isinstance(value.get(key),str):last[name][key]=value[key][:300]
                require(stage not in ('FAILED','EXITED'),
                        'service readiness failed: name='+name+' '+json.dumps(last[name],sort_keys=True))
                candidate=(self.online(value) or
                           (name not in required and stage in ('OFFLINE','STOPPED') and pid is None))
                if not candidate:
                    stable.pop(name,None);continue
                signature=(stage,pid);previous=stable.get(name);now=self.now()
                if previous is None or previous[0]!=signature:stable[name]=(signature,now)
                elif now-previous[1]>=1.:settled.append(name)
            if len(settled)==len(names) and self.now()<end:
                return [name for name in names if last[name]['stage']=='ONLINE']
            time.sleep(min(.25,max(0,end-self.now())))
        details='; '.join('name='+name+' '+json.dumps(last.get(name,{'stage':'UNOBSERVED'}),sort_keys=True)
                          for name in names)
        raise Rejected('service readiness timeout before lease (90s): '+details)
    def pause_services(self,names):
        for name in reversed(names):self.bdb('kill',name);self.wait_service(name,False,self.now()+20)
    def services_offline(self,names):
        for name in names:self.wait_service(name,False,self.now()+20)
    def restore_services(self,names):
        require(isinstance(names,list) and all(n in ('ros.camera','ros.booster.video_stream','lui') for n in names),'invalid owned service list')
        end=self.now()+20
        for name in names:
            while self.now()<end:
                value=self.bdb('status',name)
                if self.online(value):break
                if self.off(value):self.bdb('boot',name);self.wait_service(name,True,end);break
                time.sleep(.25)
            else:raise Rejected('restore service not settled')
    def observe_health(self):
        self.hash(PROJECT/'bin/k1_dds_observer',OBSERVER_SHA)
        result=self.run(['/usr/sbin/runuser','-u','booster','--',str(PROJECT/'bin/k1_dds_observer'),'--duration-seconds','5','--interface-ip','127.0.0.1','--domain-id','0'],timeout=10)
        return result.stdout.encode()
    def app_pid(self):
        value=self.unit_state(UNIT);pid=value.get('MainPID','0')
        require(value.get('ActiveState')=='active' and pid.isdigit() and int(pid)>1,'match skipped/exited')
        executable=Path('/proc')/pid/'exe'
        require(os.readlink(executable)==str(PROJECT/'bin/bhuman'),'wrong match executable')
        # /proc/PID/exe is intentionally a kernel symlink. Do not route it
        # through read_file(O_NOFOLLOW), which correctly rejects ordinary links.
        fd=os.open(executable,os.O_RDONLY|os.O_CLOEXEC)
        try:
            before=os.fstat(fd);require(stat.S_ISREG(before.st_mode) and 0<before.st_size<=128*1024*1024,'untrusted running ELF')
            digest=hashlib.sha256();size=0
            while True:
                chunk=os.read(fd,1024*1024)
                if not chunk:break
                size+=len(chunk);require(size<=128*1024*1024,'running ELF too large');digest.update(chunk)
            after=os.fstat(fd)
            signature=lambda row:(row.st_dev,row.st_ino,row.st_size,row.st_mtime_ns,row.st_ctime_ns)
            require(signature(before)==signature(after) and size==before.st_size and digest.hexdigest()==APP_SHA,'running ELF drift/hash mismatch')
        finally:os.close(fd)
        current=self.unit_state(UNIT)
        require(current.get('ActiveState')=='active' and current.get('MainPID')==pid
                and current.get('InvocationID')==value.get('InvocationID')
                and os.readlink(executable)==str(PROJECT/'bin/bhuman'),'match process identity changed during read')
        return int(pid)
    def match_running(self,state):
        value=self.unit_state(UNIT)
        if value.get('ActiveState')!='active':return False
        pid=self.app_pid();require(state.get('pid')==pid and state['boot_id']==self.boot_id(),'running match identity drift');return True
    def start_app(self):
        self.run(['/usr/bin/systemctl','start',UNIT]);end=self.now()+10
        while self.now()<end:
            value=self.unit_state(UNIT)
            if value.get('ActiveState')=='active' and int(value.get('MainPID','0'))>1:self.app_pid();return
            require(value.get('ActiveState') not in ('inactive','failed'),'match ExecCondition skipped/early exit');time.sleep(.2)
        raise Rejected('match process did not start')
    def wait_cognition(self):
        value=self.unit_state(UNIT);invocation=value.get('InvocationID');pid=self.app_pid();require(invocation and len(invocation)==32,'missing match invocation')
        end=self.now()+120;first=None;last=None # Startup-only bound; no running match deadline.
        while self.now()<end:
            require(self.app_pid()==pid,'match process changed')
            result=self.run(['/usr/bin/journalctl','--no-pager','-o','json','_SYSTEMD_INVOCATION_ID='+invocation,'-n','80'])
            for line in result.stdout.splitlines():
                try:row=json.loads(line)
                except (json.JSONDecodeError,ValueError):continue
                current=journal_cognition(row,pid,self.now())
                if current is None or (last is not None and current[0]<=last[0]):continue
                # Replayed journal rows, a counter reset, or a stale baseline must
                # never turn one isolated record into fresh advancing Cognition.
                if (first is None or self.now()-first[0]/1e6>5
                    or (last is not None and (current[1]<last[1] or current[2]<last[2]))):first=current
                last=current
                if current[1]>=first[1]+5 and current[2]>first[2]:return
            time.sleep(.5)
        raise Rejected('fresh Cognition not ready during startup budget (not match runtime)')
    def stop_app_no_wait(self):self.run(['/usr/bin/systemctl','stop','--no-block',UNIT])
    def wait_body_off(self):
        end=self.now()+25
        while self.now()<end:
            if self.run(['/usr/bin/pgrep','-x','bhuman'],check=False).returncode==1:return
            time.sleep(.2)
        raise Rejected('APP still owns body; do not restore shared inputs')
    def request_recovery(self):
        self.stop_app_no_wait();self.run(['/usr/bin/systemctl','start',RECOVERY],timeout=365)
    def schedule_retry(self):self.run(['/usr/bin/systemctl','restart',RETRY])
    def cancel_retry(self):self.run(['/usr/bin/systemctl','stop',RETRY])


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',nargs='?',default='plan',choices=('plan','status','start','stop','rollback','boot-guard','unit-ready'))
    parser.add_argument('--execute',action='store_true');parser.add_argument('--operator-present',action='store_true')
    args=parser.parse_args(argv)
    if args.action=='plan' or not args.execute:
        print(json.dumps({'plan_only':True,'no_fixed_runtime_limit':True,'root':str(ROOT),'project':str(PROJECT),'stop_may_remove_support':True}));return 0
    if args.action=='start' and not args.operator_present:
        print('REJECTED: --operator-present required before Ops',file=sys.stderr);return 2
    ops=LinuxOps();controller=Controller(ops)
    signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(RuntimeError('interrupted')))
    signal.signal(signal.SIGINT,lambda *_:(_ for _ in ()).throw(RuntimeError('interrupted')))
    actions={'start':lambda:controller.start(args.operator_present),'status':controller.status,'stop':controller.stop,
             'rollback':controller.rollback,'boot-guard':controller.boot_guard,'unit-ready':controller.unit_ready}
    try:result=actions[args.action]();print(json.dumps({'ok':True,'action':args.action,'result':result}));return 0
    except BaseException as error:print('REJECTED: '+str(error),file=sys.stderr);return 1


if __name__=='__main__':sys.exit(main())
