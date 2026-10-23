"""Pure FakeOps and direct-method mocks. No real SDK/systemctl/SSH/APP."""
from contextlib import contextmanager,redirect_stdout,redirect_stderr
import copy
import ast
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
import tempfile
from unittest.mock import patch
import match_control as m

HERE=Path(__file__).parent


def health():
    return {'type':'observer_done','exit_code':0,'callback_errors':0,'closed_readers':6,'requested_readers':6,
            'matched_writers_before_close':{'rt/button_event':0,'rt/remote_controller_state':1,'rt/button_even2':1,'rt/remote_controller_stat2':1},
            'rt/device_gateway':{'aggregate':{'callbacks_total':40,'min_connected_joint_count':22,'max_disconnected_joint_count':0,'empty_joint_callbacks':0}},
            'rt/low_state':{'samples_total':2000,'arrival_age_ms':2,'finite_checks':{name:{'nonfinite_values':0} for name in ('all_serial_q_dq','all_parallel_q_dq_tau_est','imu_rpy_gyro_acceleration')}}}


class FakeOps:
    def __init__(self):
        self.files={};self.events=[];self.clock=100.;self.boot='boot1';self.phase='NONE'
        self.targets=['old','old'];self.daemon=True;self.mode=0;self.app=False;self.locked=False
        self.services=True;self.old_idle=True;self.failures={};self.observation=health();self.driver=None;self.retry_active=False
    def hit(self,name):
        self.events.append(name)
        if self.failures.get(name,0):self.failures[name]-=1;raise RuntimeError('injected '+name)
    def now(self):return self.clock
    def boot_id(self):return self.boot
    def read(self,path):return copy.deepcopy(self.files.get(path))
    def write(self,path,value):self.hit('write:'+path.name);self.files[path]=copy.deepcopy(value)
    def read_raw(self,path):return self.files[path]
    def write_raw(self,path,raw):self.hit('raw:'+path.name);self.files[path]=raw
    def delete(self,path):self.files.pop(path,None)
    @contextmanager
    def lock(self):
        assert not self.locked,'recursive supervisor lock';self.locked=True
        try:yield
        finally:self.locked=False
    def preflight(self):self.hit('preflight');m.require(not self.retry_active,'old recovery timer active')
    def old_trial_idle(self):self.hit('old_trial_idle');m.require(self.old_idle,'old trial active')
    def no_body(self):self.hit('no_body');m.require(not self.app,'actor active')
    def originals(self):self.hit('originals');m.require(self.targets==['old','old'],'not originals')
    def patched(self):self.hit('patched');m.require(self.targets==['new','new'],'not patched')
    def inputs_online(self):self.hit('inputs_online');m.require(self.daemon,'daemon offline')
    def require_mode_zero(self):self.hit('query_only_mode');m.require(self.mode==0,'already standing/custom, refuse')
    def force_damping(self):self.hit('force_damping');self.mode=0
    def archive_core(self):
        self.hit('archive_core');m.require(self.phase in ('NONE','RESTORED'),'pending core');self.phase='NONE'
    def core_phase(self):return self.phase
    def core(self,action):
        self.hit('core:'+action)
        if action=='arm':self.phase='ARMED'
        elif action=='apply':
            assert not self.daemon;self.phase='APPLYING';self.targets[0]='new';self.hit('first_patch')
            self.targets[1]='new';self.phase='APPLIED'
        elif action=='restore':
            assert not self.daemon;self.targets=['old','old'];self.phase='RESTORED'
    def finish_unmodified_arm(self):self.hit('finish_unmodified_arm');self.originals();self.phase='RESTORED'
    def daemon_online(self):return self.daemon
    def daemon_stop(self):self.hit('daemon_stop');self.daemon=False
    def daemon_start(self):
        assert not self.locked,'start daemon/boot-guard while holding lock';self.hit('daemon_start')
        self.driver.boot_guard();self.daemon=True
    def offline(self):self.hit('offline');m.require(not self.daemon and not self.app,'not offline')
    def services_to_lease(self):
        self.hit('services_to_lease');m.require(self.services,'camera already owned');return list(m.LEASE_SERVICES)
    def pause_services(self,names,claim):
        self.hit('pause_services')
        for name in reversed(names):claim(name)
        self.services=False
    def services_offline(self,names):self.hit('services_offline');m.require(not self.services,'camera not exclusive')
    def restore_services(self,names):self.hit('restore_services');self.services=True
    def observe_health(self):self.hit('observe_health');return (json.dumps(self.observation)+'\n').encode()
    def app_hash(self):self.hit('app_hash')
    def start_app(self):
        assert not self.locked;self.hit('start_app');self.driver.unit_ready();self.app=True;self.mode=3
    def app_pid(self):m.require(self.app,'APP absent');return 42
    def match_running(self,state):return self.app and state['boot_id']==self.boot and state.get('pid')==42
    def wait_cognition(self,**kwargs):
        self.hit('wait_cognition');self.clock+=2
        if kwargs:
            assert not self.locked,'repeat readiness must not block stop/recovery lock'
            assert kwargs=={'timeout_seconds':6,'expected_pid':42,'fresh_after':self.clock-2}
    def stop_app_no_wait(self):assert not self.locked;self.hit('stop_app');self.app=False
    def wait_body_off(self):assert not self.locked;self.hit('wait_body_off');m.require(not self.app,'still active')
    def request_recovery(self):assert not self.locked;self.hit('request_recovery');self.driver.rollback()
    def schedule_retry(self):self.hit('schedule_retry');self.retry_active=True
    def cancel_retry(self):self.hit('cancel_retry');self.retry_active=False


class MatchControlTests(unittest.TestCase):
    def driver(self):
        ops=FakeOps();driver=m.Controller(ops);ops.driver=driver;return ops,driver
    def test_start_uses_query_only_before_mutation_and_keeps_no_deadline(self):
        o,c=self.driver();result=c.start(True)
        self.assertTrue(result['ready_for_operator']);self.assertFalse(result['match_ready'])
        self.assertLess(o.events.index('query_only_mode'),o.events.index('write:MATCH_STATE.json'))
        self.assertNotIn('force_damping',o.events);self.assertNotIn(m.START,o.files)
        self.assertEqual(c.state()['phase'],'RUNNING');self.assertTrue(o.app)
    def test_start_is_idempotent_no_mode_or_restart_while_running(self):
        o,c=self.driver();c.start(True);o.events=[];files=copy.deepcopy(o.files);result=c.start(True)
        self.assertEqual(o.events,['wait_cognition']);self.assertTrue(o.app)
        self.assertTrue(result['ready_for_operator']);self.assertTrue(result['reused_running_process'])
        self.assertEqual(files,o.files) # No state/evidence/service/mode mutation on repeat.
    def test_startup_progress_and_private_timeline_report_each_real_stage_duration(self):
        o,c=self.driver();output=io.StringIO()
        def observation():
            o.hit('observe_health');o.clock+=5
            return (json.dumps(o.observation)+'\n').encode()
        o.observe_health=observation
        with redirect_stdout(output):result=c.start(True)
        trace=o.files[m.STARTUP_TIMELINE]
        self.assertEqual(trace['outcome'],'ready_for_operator');self.assertEqual(trace['elapsed_seconds'],7.)
        self.assertEqual(result['startup_elapsed_seconds'],7.);self.assertEqual(len(trace['stages']),10)
        self.assertEqual(trace['stages'][7]['duration_seconds'],5.)
        self.assertEqual(trace['stages'][-1]['duration_seconds'],2.)
        self.assertTrue(all(row['status']=='complete' for row in trace['stages']))
        self.assertIn('08_five_second_live_health complete duration=5.00s',output.getvalue())
        live=object.__new__(m.LinuxOps);writes=[]
        live.module=SimpleNamespace(atomic_write=lambda *args:writes.append(args))
        live.write(m.STARTUP_TIMELINE,trace)
        self.assertEqual(writes[0][0],m.STARTUP_TIMELINE);self.assertEqual(writes[0][2:],(0o600,0,0))
    def test_failed_stage_timeline_keeps_first_error_and_does_not_mask_recovery(self):
        o,c=self.driver();o.failures['pause_services']=1
        with redirect_stdout(io.StringIO()),self.assertRaisesRegex(RuntimeError,'pause_services'):c.start(True)
        trace=o.files[m.STARTUP_TIMELINE]
        self.assertEqual(trace['outcome'],'failed');self.assertIn('pause_services',trace['error']['message'])
        self.assertEqual(trace['stages'][-1]['name'],'07_exclusive_camera_and_speech_lease')
        self.assertEqual(trace['stages'][-1]['status'],'failed');self.assertEqual(c.state()['phase'],'RESTORED')
    def test_timeline_write_failure_does_not_override_success_or_original_error(self):
        for failed_start in (False,True):
            o,c=self.driver();o.failures['write:STARTUP_TIMELINE.json']=1
            if failed_start:o.failures['pause_services']=1
            with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
                if failed_start:
                    with self.assertRaisesRegex(RuntimeError,'pause_services'):c.start(True)
                    self.assertEqual(c.state()['phase'],'RESTORED')
                else:self.assertTrue(c.start(True)['ready_for_operator'])
    def test_repeat_start_stale_cognition_reports_not_ready_and_never_recovers_standing_app(self):
        o,c=self.driver();c.start(True);o.events=[];o.mode=3;files=copy.deepcopy(o.files)
        o.failures['wait_cognition']=1;result=c.start(True)
        self.assertTrue(result['running']);self.assertFalse(result['ready_for_operator'])
        self.assertIn('wait_cognition',result['readiness_error']['message'])
        self.assertEqual(o.events,['wait_cognition']);self.assertEqual(files,o.files)
        self.assertTrue(o.app);self.assertEqual(o.mode,3)
    def test_repeat_start_detects_exit_without_requesting_recovery_or_new_ownership(self):
        o,c=self.driver();c.start(True);o.events=[]
        def exit_during_check(**kwargs):
            self.assertFalse(o.locked);o.hit('wait_cognition');o.app=False
            raise m.Rejected('existing APP exited')
        o.wait_cognition=exit_during_check;result=c.start(True)
        self.assertFalse(result['running']);self.assertFalse(result['ready_for_operator'])
        self.assertEqual(o.events,['wait_cognition']);self.assertNotIn('request_recovery',o.events)
    def test_repeat_start_failed_liveness_read_reports_unknown_not_stopped(self):
        o,c=self.driver();c.start(True);o.events=[];original=o.match_running
        def fail_check(**kwargs):
            o.hit('wait_cognition')
            o.match_running=lambda _:(_ for _ in ()).throw(m.Rejected('ELF identity drift'))
            raise m.Rejected('fresh Cognition unavailable')
        o.wait_cognition=fail_check;result=c.start(True)
        self.assertIsNone(result['running']);self.assertFalse(result['ready_for_operator'])
        self.assertIn('ELF identity drift',result['liveness_error']['message'])
        self.assertTrue(o.app);self.assertEqual(o.events,['wait_cognition']);o.match_running=original
    def test_standing_or_custom_initial_mode_is_rejected_without_damping_or_writes(self):
        for mode in (1,3):
            o,c=self.driver();o.mode=mode
            with self.assertRaises(m.Rejected):c.start(True)
            self.assertNotIn('force_damping',o.events);self.assertNotIn('daemon_stop',o.events)
            self.assertEqual(set(o.files),{m.STARTUP_TIMELINE})
    def test_missing_operator_and_active_old_trial_do_not_mutate_or_rollback(self):
        o,c=self.driver()
        with self.assertRaises(m.Rejected):c.start(False)
        self.assertEqual(o.events,[])
        o.old_idle=False
        with self.assertRaises(m.Rejected):c.start(True)
        self.assertEqual(set(o.files),{m.STARTUP_TIMELINE});self.assertNotIn('force_damping',o.events)
    def test_partial_file_patch_failure_restores_both_without_body_mode_write(self):
        o,c=self.driver();o.failures['first_patch']=1
        with self.assertRaises(RuntimeError):c.start(True)
        self.assertEqual(o.targets,['old','old']);self.assertTrue(o.daemon);self.assertFalse(o.app)
        self.assertEqual(c.state()['phase'],'RESTORED');self.assertNotIn('force_damping',o.events)
    def test_camera_pause_failure_restores_owned_camera_and_originals(self):
        o,c=self.driver();o.failures['pause_services']=1
        with self.assertRaises(RuntimeError):c.start(True)
        self.assertTrue(o.services);self.assertEqual(o.targets,['old','old'])
    def test_bad_five_second_health_not_resampled_or_started(self):
        o,c=self.driver();o.observation['rt/device_gateway']['aggregate']['max_disconnected_joint_count']=1
        with self.assertRaises(m.Rejected):c.start(True)
        self.assertEqual(o.events.count('observe_health'),1);self.assertNotIn('start_app',o.events)
        self.assertEqual(c.state()['phase'],'RESTORED')
        self.assertEqual(json.loads(o.files[m.ROOT/'health.log']),o.observation)
        self.assertLess(o.events.index('raw:health.log'),o.events.index('write:START_FAILURE.json'))
        self.assertIn('unhealthy joints',o.files[m.START_FAILURE]['startup_error']['message'])
    def test_startup_and_recovery_errors_both_preserved(self):
        o,c=self.driver();o.failures.update({'pause_services':1,'core:restore':1})
        with self.assertRaisesRegex(m.Rejected,'startup failed: injected pause_services; recovery failed: injected core:restore'):c.start(True)
        failure=o.files[m.START_FAILURE]
        self.assertEqual(failure['startup_error'],{'type':'RuntimeError','message':'injected pause_services'})
        self.assertEqual(failure['recovery_error'],{'type':'RuntimeError','message':'injected core:restore'})
        self.assertEqual(failure['phase_at_failure'],'PATCHED')
        self.assertIn('schedule_retry',o.events);self.assertNotIn('start_app',o.events)
        self.assertNotIn('environment',failure);self.assertNotIn('traceback',failure)
    def test_start_failure_evidence_write_error_never_masks_original_or_skips_recovery(self):
        o,c=self.driver();o.failures['pause_services']=1;o.failures['write:START_FAILURE.json']=2
        with patch.object(m.sys,'stderr',io.StringIO()),self.assertRaisesRegex(RuntimeError,'injected pause_services'):c.start(True)
        self.assertEqual(c.state()['phase'],'RESTORED');self.assertEqual(o.targets,['old','old'])
    def test_start_failure_evidence_uses_private_root_atomic_writer(self):
        o=object.__new__(m.LinuxOps);calls=[]
        o.module=SimpleNamespace(atomic_write=lambda *args:calls.append(args))
        o.write(m.START_FAILURE,{'startup_error':{'type':'Rejected','message':'test'}})
        self.assertEqual(calls[0][0],m.START_FAILURE);self.assertEqual(calls[0][2:],(0o600,0,0))
    def test_cognition_failure_stops_own_app_damps_and_recovers(self):
        o,c=self.driver();o.failures['wait_cognition']=1
        with self.assertRaises(RuntimeError):c.start(True)
        self.assertFalse(o.app);self.assertEqual(o.mode,0);self.assertEqual(o.targets,['old','old']);self.assertTrue(o.services)
    def test_start_failure_joins_recovery_unit_not_inline_cli_rollback(self):
        o,c=self.driver();o.failures['wait_cognition']=1
        def join():
            o.hit('request_recovery');m.Controller(o).rollback()
        o.request_recovery=join
        with patch.object(c,'rollback',side_effect=AssertionError('parallel CLI rollback')):
            with self.assertRaisesRegex(RuntimeError,'wait_cognition'):c.start(True)
        self.assertEqual(o.events.count('request_recovery'),1)
        self.assertEqual(c.state()['phase'],'RESTORED');self.assertFalse(o.app)
    def test_join_failure_keeps_startup_and_recovery_causes(self):
        o,c=self.driver();o.failures['wait_cognition']=1;o.failures['request_recovery']=1
        with self.assertRaises(m.Rejected):c.start(True)
        record=o.files[m.START_FAILURE]
        self.assertIn('wait_cognition',record['startup_error']['message'])
        self.assertIn('request_recovery',record['recovery_error']['message'])
    def test_linux_recovery_joins_one_fixed_systemd_unit(self):
        o=object.__new__(m.LinuxOps);calls=[]
        o.stop_app_no_wait=lambda:calls.append('stop_app_no_wait')
        o.run=lambda args,**kwargs:calls.append((args,kwargs))
        o.request_recovery()
        self.assertEqual(calls,['stop_app_no_wait',(['/usr/bin/systemctl','start',m.RECOVERY],{'timeout':365})])
    def vision_ops(self):
        o=object.__new__(m.LinuxOps);calls=[]
        proof={'schema':1,'serial':m.SERIAL,'app_sha256':m.APP_SHA,
               'models':[{'name':name,'model_sha256':'a'*64,'cached_reload_seconds':1.}
                         for name in ('FieldBoundary/net.onnx','RobotBallPerceptor/ball_robot_640.onnx')],
               'cache_files':{name:{'sha256':'b'*64} for name in ('field.engine','ball.engine')}}
        o.read=lambda _:proof;o.hash=lambda path,expected:calls.append((path,expected))
        return o,proof,calls
    def test_missing_cache_proof_rejects_without_any_service_action(self):
        o,proof,calls=self.vision_ops();o.read=lambda _:None
        with self.assertRaises(m.Rejected):o.vision_cache_ready()
        self.assertEqual(calls,[])
    def test_valid_cached_models_verify_both_models_and_engine_bytes(self):
        o,proof,calls=self.vision_ops();o.vision_cache_ready()
        self.assertEqual(len(calls),4)
    def test_stale_identity_slow_reload_and_escaped_cache_rejected(self):
        for mutation in ('serial','slow','escape'):
            o,proof,calls=self.vision_ops()
            if mutation=='serial':proof['serial']='other'
            elif mutation=='slow':proof['models'][0]['cached_reload_seconds']=31.
            else:proof['cache_files']['../outside.engine']={'sha256':'c'*64}
            with self.assertRaises(m.Rejected):o.vision_cache_ready()
    @staticmethod
    def cognition_row(frames=0,cognition_time=0,stamp='99990000',pid='42'):
        return {'MESSAGE':f'K1_OFFICIAL_LIVE INPUT ready=1 cognition_frames={frames} cognition_time={cognition_time}',
                '_PID':pid,'__MONOTONIC_TIMESTAMP':stamp}
    def cognition_ops(self,payload):
        o=object.__new__(m.LinuxOps);clock={'now':100.};calls=[]
        o.now=lambda:clock['now'];o.app_pid=lambda:42
        o.unit_state=lambda _: {'InvocationID':'a'*32}
        def run(args,**kwargs):
            self.assertEqual(args,['/usr/bin/journalctl','--no-pager','-o','json',
                                   '_SYSTEMD_INVOCATION_ID='+'a'*32,'-n','80'])
            calls.append(clock['now'])
            self.assertGreater(kwargs['timeout'],0);self.assertLessEqual(kwargs['timeout'],5)
            return SimpleNamespace(stdout=payload(clock['now'],len(calls)))
        o.run=run
        def sleep(seconds):
            self.assertEqual(seconds,.5);clock['now']+=seconds
        return o,clock,calls,sleep
    def test_real_cognition_wait_ignores_binary_model_rows_and_accepts_fresh_advancing_frames(self):
        def payload(now,call):
            binary=self.cognition_row();binary['MESSAGE']=[0,255,13,10]
            repeated=self.cognition_row();repeated['MESSAGE']=['K1_OFFICIAL_LIVE INPUT ',
                'cognition_frames=999 cognition_time=999']
            frame=self.cognition_row(0 if call==1 else 5,0 if call==1 else 170,
                                     str(round((now-.01)*1e6)))
            return '\n'.join(json.dumps(row) for row in (binary,repeated,frame))+'\n'
        o,clock,calls,sleep=self.cognition_ops(payload)
        with patch.object(m.time,'sleep',sleep):o.wait_cognition()
        self.assertEqual(len(calls),2);self.assertEqual(clock['now'],100.5)
    def test_journal_cognition_parser_rejects_nontext_missing_and_malformed_fields_without_coercion(self):
        valid=self.cognition_row(8,260)
        self.assertEqual(m.journal_cognition(valid,42,100.),(99990000,8,260))
        invalid=[None,False,7,3.1,'not a row',[],[valid],{}]
        for field,values in (
                ('MESSAGE',[None,False,0,3.1,[],[75,49],['K1_OFFICIAL_LIVE INPUT cognition_frames=999 cognition_time=999'],
                            {'text':'K1_OFFICIAL_LIVE INPUT cognition_frames=999 cognition_time=999'}]),
                ('_PID',[None,42,True,42.0,['42'],{'pid':'42'},'43']),
                ('__MONOTONIC_TIMESTAMP',[None,False,99990000,99990000.0,[],['99990000'],{},'',
                                          'nan','1.0','-1','+99990000','99990000 ', '9'*5000,'0',
                                          '100000001','94999999'])):
            for value in values:
                row=valid.copy();row[field]=value;invalid.append(row)
        for field in ('MESSAGE','_PID','__MONOTONIC_TIMESTAMP'):
            row=valid.copy();row.pop(field);invalid.append(row)
        for row in invalid:
            with self.subTest(row=row):self.assertIsNone(m.journal_cognition(row,42,100.))
    def test_real_cognition_only_binary_stale_wrong_pid_and_bad_json_times_out_without_false_pass(self):
        binary=self.cognition_row(999,999);binary['MESSAGE']=[75,49,0,255]
        repeated=self.cognition_row(999,999);repeated['MESSAGE']=[self.cognition_row(999,999)['MESSAGE']]
        stale=self.cognition_row(999,999,stamp='90000000')
        wrong=self.cognition_row(999,999,pid='43')
        malformed=self.cognition_row(999,999);malformed['__MONOTONIC_TIMESTAMP']=[]
        raw='\n'.join(['not JSON','{bad-json','null','[]']+
                      [json.dumps(row) for row in (binary,repeated,stale,wrong,malformed)])
        o,clock,calls,sleep=self.cognition_ops(lambda *_:raw)
        with patch.object(m.time,'sleep',sleep),self.assertRaisesRegex(m.Rejected,'fresh Cognition not ready'):
            o.wait_cognition()
        self.assertEqual(clock['now'],220.);self.assertEqual(len(calls),240)
    def test_real_cognition_minimum_progress_and_time_advance_are_both_required(self):
        for advance,elapsed in ((4,170),(5,0),(0,170)):
            def payload(now,call):
                row=self.cognition_row(0 if call==1 else advance,0 if call==1 else elapsed,
                                       str(round((now-.01)*1e6)))
                return json.dumps(row)+'\n'
            o,clock,calls,sleep=self.cognition_ops(payload)
            with patch.object(m.time,'sleep',sleep),self.assertRaises(m.Rejected):o.wait_cognition()
            self.assertEqual(clock['now'],220.)
    def test_real_cognition_replayed_reversed_timestamps_and_expired_baseline_cannot_pass(self):
        for kind in ('replayed','reversed','old_baseline'):
            def payload(now,call):
                if call==1:return json.dumps(self.cognition_row())
                if kind=='old_baseline' and now<106.:return json.dumps(self.cognition_row())
                stamp='99990000' if kind=='replayed' else '99980000' if kind=='reversed' else str(round((now-.01)*1e6))
                return json.dumps(self.cognition_row(5,170,stamp=stamp))
            o,clock,calls,sleep=self.cognition_ops(payload)
            with patch.object(m.time,'sleep',sleep),self.assertRaises(m.Rejected):o.wait_cognition()
            self.assertEqual(clock['now'],220.)
    def test_real_cognition_counter_reset_starts_a_new_fresh_progress_window(self):
        def payload(now,call):
            frames,ticks=((100,3000),(0,0),(4,140),(5,170))[min(call,4)-1]
            return json.dumps(self.cognition_row(frames,ticks,str(round((now-.01)*1e6))))
        o,clock,calls,sleep=self.cognition_ops(payload)
        with patch.object(m.time,'sleep',sleep):o.wait_cognition()
        self.assertEqual(len(calls),4);self.assertEqual(clock['now'],101.5)
    def test_repeat_cognition_rejects_recent_but_prerequest_telemetry_and_keeps_six_second_bound(self):
        rows='\n'.join(json.dumps(self.cognition_row(n,t,str(round(stamp*1e6))))
                       for n,t,stamp in ((100,3000,98.1),(160,5000,99.9)))
        o,clock,calls,sleep=self.cognition_ops(lambda *_:rows)
        with patch.object(m.time,'sleep',sleep),self.assertRaisesRegex(m.Rejected,'fresh Cognition not ready'):
            o.wait_cognition(timeout_seconds=6,expected_pid=42,fresh_after=100.)
        self.assertEqual(clock['now'],106.);self.assertEqual(len(calls),12)
    def test_repeat_cognition_requires_two_new_current_process_records(self):
        def payload(now,call):
            return json.dumps(self.cognition_row(call*15,call*500,str(round((now-.01)*1e6))))
        o,clock,calls,sleep=self.cognition_ops(payload)
        with patch.object(m.time,'sleep',sleep):
            o.wait_cognition(timeout_seconds=6,expected_pid=42,fresh_after=100.)
        self.assertEqual(len(calls),3);self.assertEqual(clock['now'],101.)
    def test_cognition_pid_or_invocation_drift_rejects_before_certification(self):
        for kind in ('pid','invocation'):
            o,clock,calls,sleep=self.cognition_ops(lambda now,call:json.dumps(
                self.cognition_row(call*15,call*500,str(round((now-.01)*1e6)))))
            if kind=='pid':o.app_pid=lambda:43
            else:
                reads={'count':0}
                def units(_):
                    reads['count']+=1
                    return {'InvocationID':('a' if reads['count']<=2 else 'b')*32}
                o.unit_state=units
            with patch.object(m.time,'sleep',sleep),self.assertRaisesRegex(m.Rejected,'PID changed|invocation changed'):
                o.wait_cognition(timeout_seconds=6,expected_pid=42,fresh_after=100.)
    def test_cognition_budget_and_freshness_boundary_are_bounded_finite_numbers(self):
        for value in (0,-1,121,float('nan'),float('inf'),True,'6'):
            o,*_=self.cognition_ops(lambda *_:'')
            with self.assertRaisesRegex(m.Rejected,'invalid Cognition check budget'):
                o.wait_cognition(timeout_seconds=value)
        for value in (-1,101,float('nan'),float('inf'),True,'100'):
            o,*_=self.cognition_ops(lambda *_:'')
            with self.assertRaisesRegex(m.Rejected,'invalid Cognition freshness boundary'):
                o.wait_cognition(timeout_seconds=6,fresh_after=value)
    def test_stop_warned_scoped_and_repeated_stop_does_not_damp_unrelated_future_body(self):
        o,c=self.driver();c.start(True);c.stop();self.assertEqual(o.mode,0)
        self.assertEqual(c.state()['phase'],'RESTORED');o.events=[];o.mode=1;c.stop()
        self.assertNotIn('force_damping',o.events);self.assertEqual(o.mode,1)
    def test_stop_without_owned_state_never_commands_mode(self):
        o,c=self.driver();o.mode=1;c.stop();c.rollback();self.assertEqual(o.mode,1);self.assertNotIn('force_damping',o.events)
    def test_new_boot_restores_files_offline_never_runs_app_or_sdk(self):
        o,c=self.driver();c.start(True);o.boot='boot2';o.app=False;o.daemon=False;o.events=[]
        c.boot_guard();self.assertEqual(o.targets,['old','old']);self.assertEqual(c.state()['phase'],'BOOT_RESTORED')
        for event in ('daemon_start','start_app','force_damping','query_only_mode'):self.assertNotIn(event,o.events)
    def test_stale_start_permit_and_wrong_boot_reject_before_mode(self):
        o,c=self.driver();c.start(True);o.app=False;o.mode=0;o.events=[]
        with self.assertRaises(m.Rejected):c.unit_ready()
        self.assertNotIn('query_only_mode',o.events)
    def test_boot_guard_same_boot_without_explicit_restart_permit_restores_not_repatches(self):
        o,c=self.driver();c.start(True);o.app=False;o.daemon=False;o.events=[]
        c.boot_guard();self.assertEqual(o.targets,['old','old']);self.assertNotIn('core:apply',o.events)
    def test_no_runtime_limit_no_install_autostart_no_fake_marker(self):
        self.assertNotIn('RuntimeMaxSec',m.UNITS[m.UNIT]);self.assertIn('Restart=no',m.UNITS[m.UNIT])
        self.assertNotIn('WantedBy',m.UNITS[m.UNIT]);self.assertNotIn('OnActiveSec',m.UNITS[m.UNIT])
        self.assertNotIn('deadline',m.UNITS[m.UNIT]);self.assertIn('--k1-987-match',m.UNITS[m.UNIT])
        self.assertIn('WARNING: stopping may switch to Damping',(HERE/'start_match.sh').read_text())
    def test_core_transformer_is_frozen_and_target_policy_is_separately_gated(self):
        raw=(HERE/'route_transaction.py').read_bytes()
        tree=ast.parse(raw)
        names={'digest','directory','metadata','read_file','atomic_write','Trial'}
        nodes=[row for row in tree.body if isinstance(row,(ast.FunctionDef,ast.ClassDef)) and row.name in names]
        for row in nodes:
            if isinstance(row,ast.ClassDef):
                # This target adds only a strict manifest-metadata authorization
                # helper and its call. The original arm/apply/restore transform
                # remains byte/AST frozen; new authorization is exercised by
                # test_route_permission_fix against the production class.
                row.body=[item for item in row.body if not (
                    isinstance(item,ast.FunctionDef) and item.name=='approved_original_metadata')]
                for method in row.body:
                    if isinstance(method,ast.FunctionDef) and method.name=='manifest':
                        class RemoveNewAuthorizationCall(ast.NodeTransformer):
                            def visit_Expr(self,node):
                                if (isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Attribute)
                                    and isinstance(node.value.func.value,ast.Name) and node.value.func.value.id=='self'
                                    and node.value.func.attr=='approved_original_metadata'):return None
                                return self.generic_visit(node)
                        RemoveNewAuthorizationCall().visit(method)
                    if isinstance(method,ast.FunctionDef) and method.name=='__init__':
                        for conditional in method.body:
                            if isinstance(conditional,ast.If):
                                conditional.body=[item for item in conditional.body if not (
                                    isinstance(item,ast.Expr) and isinstance(item.value,ast.Call)
                                    and isinstance(item.value.func,ast.Name)
                                    and item.value.func.id=='require_finalized_policy')]
        frozen=ast.dump(ast.Module(body=nodes,type_ignores=[]),include_attributes=False).encode()
        self.assertEqual(m.sha(frozen),'d00d11813c482fce85c5615764e0bd0cbf3b642e117d1e97bed8c91cd14753bf')
        if m.CORE_SHA is not None:self.assertEqual(m.sha(raw),m.CORE_SHA)
    def test_default_and_missing_operator_never_construct_real_ops(self):
        with patch.object(m,'LinuxOps',side_effect=AssertionError('real Ops forbidden')),redirect_stdout(io.StringIO()):
            self.assertEqual(m.main([]),0)
            self.assertEqual(m.main(['start','--execute']),2)
    def test_modequery_real_method_rejects_exit_zero_mode_one_or_bad_flag(self):
        o=object.__new__(m.LinuxOps);o.hash=lambda *args:None
        o.now=lambda:100.;o.boot_id=lambda:'boot1';o.write=lambda *args:None
        def result(mode,damping):return SimpleNamespace(returncode=0,stderr='',stdout=json.dumps({'kind':'k1_mode_query_only','serial':m.SERIAL,'result':0,'mode':mode,'damping':damping}))
        for mode,damping in ((1,False),(3,False),(0,False)):
            o.run=lambda *args,mode=mode,damping=damping,**kwargs:result(mode,damping)
            with self.assertRaises(m.Rejected):o.require_mode_zero()
        o.run=lambda *args,**kwargs:result(0,True);o.require_mode_zero()
    @staticmethod
    def mode_result(mode=0,damping=True,returncode=0,serial=m.SERIAL):
        return SimpleNamespace(returncode=returncode,stderr='',stdout=json.dumps(
            {'kind':'k1_mode_query_only','serial':serial,'result':0,'mode':mode,'damping':damping})+'\n')
    @staticmethod
    def rpc_timeout_result():
        return SimpleNamespace(returncode=4,stdout=json.dumps({'kind':'k1_mode_query_only','result':100,
            'mode':None,'attempts':3,'error':'query_failed'})+'\n',
            stderr='RPC request timed out. api_id = 2017, uuid = test-only, timeout_ms = 1000\n')
    def real_mode_on_fake(self,results):
        o,c=self.driver();remaining=list(results);calls=[]
        o.hash=lambda *args:None
        o.record_mode_query=lambda record:m.LinuxOps.record_mode_query(o,record)
        def run(args,timeout,check):
            calls.append((args,timeout,check));o.hit('mode_query_process');o.clock+=8.
            return remaining.pop(0) if remaining else self.mode_result()
        o.run=run;o.mode_value=lambda:m.LinuxOps.mode_value(o)
        o.require_mode_zero=lambda:m.LinuxOps.require_mode_zero(o)
        return o,c,calls
    def test_transient_rpc_timeout_retries_once_as_fresh_readonly_process_before_owned_state(self):
        o,c,calls=self.real_mode_on_fake([self.rpc_timeout_result(),self.mode_result()]);pauses=[]
        def sleep(seconds):pauses.append(seconds);o.clock+=seconds
        with patch.object(m.time,'sleep',sleep),redirect_stdout(io.StringIO()):c.start(True)
        self.assertEqual(pauses,[.25]);self.assertEqual(len(calls),4) # Initial retry, post-router check, unit-ready.
        self.assertLess(o.events.index('mode_query_process'),o.events.index('write:MATCH_STATE.json'))
        self.assertEqual(o.events[:o.events.index('write:MATCH_STATE.json')].count('mode_query_process'),2)
        self.assertNotIn('force_damping',o.events);self.assertTrue(o.app)
        for args,timeout,check in calls:
            self.assertEqual(args,['/usr/sbin/runuser','-u','booster','--',str(m.PROJECT/'bin/k1_mode_query'),'--query-only'])
            self.assertEqual(timeout,10);self.assertIs(check,False)
    def test_two_rpc_timeouts_leave_services_and_app_untouched_with_bounded_diagnostic(self):
        timeout=self.rpc_timeout_result();timeout.stderr+='x'*7000
        o,c,calls=self.real_mode_on_fake([timeout,timeout]);pauses=[]
        def sleep(seconds):pauses.append(seconds);o.clock+=seconds
        with patch.object(m.time,'sleep',sleep),redirect_stdout(io.StringIO()),self.assertRaisesRegex(m.Rejected,'after 2 bounded fresh'):c.start(True)
        self.assertEqual(len(calls),2);self.assertEqual(pauses,[.25]);self.assertLessEqual(o.clock-100,20.25)
        for event in ('daemon_stop','daemon_start','force_damping','start_app','write:MATCH_STATE.json','core:arm'):
            self.assertNotIn(event,o.events)
        self.assertNotIn(m.START_FAILURE,o.files)
        record=o.files[m.MODE_QUERY_LAST]
        self.assertEqual(record['operation'],'GetMode query-only');self.assertIs(record['read_only'],True)
        self.assertEqual(record['serial'],m.SERIAL);self.assertEqual(record['boot_id'],'boot1')
        self.assertEqual(record['returncode'],4);self.assertEqual(record['elapsed_seconds'],8.)
        self.assertLessEqual(len(record['stdout']),6000);self.assertLessEqual(len(record['stderr']),6000)
    def test_fresh_query_success_keeps_first_timeout_in_same_batch_without_recursive_history(self):
        failed=self.rpc_timeout_result();success=self.mode_result()
        o,c,calls=self.real_mode_on_fake([failed,success])
        with patch.object(m.time,'sleep',return_value=None),redirect_stdout(io.StringIO()):o.require_mode_zero()
        record=o.files[m.MODE_QUERY_LAST];self.assertEqual(record['returncode'],0)
        self.assertEqual(len(record['attempts']),2)
        self.assertEqual(record['attempts'][0]['stderr'],failed.stderr)
        self.assertEqual(record['attempts'][1]['stdout'],success.stdout)
        self.assertTrue(all('attempts' not in row for row in record['attempts']))
        o.require_mode_zero();record=o.files[m.MODE_QUERY_LAST]
        self.assertEqual(len(record['attempts']),1);self.assertEqual(record['attempts'][0]['returncode'],0)
    def test_successful_nonzero_mode_is_immediate_rejection_without_fresh_retry_or_mutation(self):
        for mode in (1,3):
            o,c,calls=self.real_mode_on_fake([self.mode_result(mode,False)])
            with patch.object(m.time,'sleep',side_effect=AssertionError('no retry')):
                with self.assertRaisesRegex(m.Rejected,'must already be Damping'):c.start(True)
            self.assertEqual(len(calls),1);self.assertNotIn('daemon_stop',o.events);self.assertNotIn('force_damping',o.events)
    def test_malformed_wrongserial_or_unknown_helper_error_are_never_retried(self):
        malformed=self.rpc_timeout_result();malformed.stdout='{bad-json\n'
        wrong=self.rpc_timeout_result();wrong.stdout=json.dumps({'kind':'k1_mode_query_only','serial':'wrong','result':100})
        unknown=self.rpc_timeout_result();unknown.stderr='RPC exception in other API: api_id = 2000\n'
        no_json=self.rpc_timeout_result();no_json.stdout=''
        for result in (malformed,wrong,unknown,no_json):
            o,c,calls=self.real_mode_on_fake([result])
            with patch.object(m.time,'sleep',side_effect=AssertionError('no retry')):
                with self.assertRaises(m.Rejected):c.start(True)
            self.assertEqual(len(calls),1);self.assertNotIn('daemon_stop',o.events)
            self.assertIn(m.MODE_QUERY_LAST,o.files)
    def test_known_eightsecond_helper_deadline_can_retry_but_outer_process_timeout_cannot(self):
        expired=SimpleNamespace(returncode=124,stderr='',stdout=json.dumps({'kind':'k1_mode_query_only','result':-124,
            'mode':None,'error':'deadline_8_seconds'})+'\n')
        o,c,calls=self.real_mode_on_fake([expired,self.mode_result()])
        with patch.object(m.time,'sleep',return_value=None),redirect_stdout(io.StringIO()):o.require_mode_zero()
        self.assertEqual(len(calls),2)
        o,c,calls=self.real_mode_on_fake([])
        def outer_timeout(*args,**kwargs):raise m.subprocess.TimeoutExpired(['fixed-query-only'],10,output=b'partial',stderr=b'deadline')
        o.run=outer_timeout
        with patch.object(m.time,'sleep',side_effect=AssertionError('no retry')):
            with self.assertRaisesRegex(m.Rejected,'exceeded 10s'):c.start(True)
        self.assertEqual(o.files[m.MODE_QUERY_LAST]['returncode'],None)
        self.assertEqual(o.files[m.MODE_QUERY_LAST]['stdout'],'partial')
        self.assertNotIn('daemon_stop',o.events)
    def test_modequery_hash_failure_is_fatal_before_query_and_diagnostic_uses_private_writer(self):
        o,c,calls=self.real_mode_on_fake([])
        def bad_hash(*args):raise m.Rejected('hash mismatch')
        o.hash=bad_hash
        with self.assertRaisesRegex(m.Rejected,'hash mismatch'):c.start(True)
        self.assertEqual(calls,[]);self.assertNotIn(m.MODE_QUERY_LAST,o.files)
        real=object.__new__(m.LinuxOps);writes=[]
        real.module=SimpleNamespace(atomic_write=lambda *args:writes.append(args))
        real.write(m.MODE_QUERY_LAST,{'read_only':True})
        self.assertEqual(writes[0][0],m.MODE_QUERY_LAST);self.assertEqual(writes[0][2:],(0o600,0,0))
    def test_running_proc_exe_uses_safe_fd_not_nofollow_source_reader(self):
        with tempfile.TemporaryDirectory(prefix='match-proc-pure-') as directory:
            binary=Path(directory)/'elf';binary.write_bytes(b'fake ELF bytes')
            o=object.__new__(m.LinuxOps);state={'ActiveState':'active','MainPID':'42','InvocationID':'a'*32}
            o.unit_state=lambda _:state.copy();o.hash=lambda *args:(_ for _ in ()).throw(AssertionError('ordinary nofollow reader must not see /proc exe'))
            original_open=os.open
            def opened(path,flags,*args):
                self.assertEqual(str(path),'/proc/42/exe');return original_open(binary,flags,*args)
            with patch.object(m,'APP_SHA',m.sha(binary.read_bytes())),patch.object(m.os,'readlink',return_value=str(m.PROJECT/'bin/bhuman')),patch.object(m.os,'open',opened):
                self.assertEqual(o.app_pid(),42)
    def test_running_proc_pid_change_after_hash_rejects(self):
        with tempfile.TemporaryDirectory(prefix='match-proc-race-pure-') as directory:
            binary=Path(directory)/'elf';binary.write_bytes(b'fake ELF bytes')
            o=object.__new__(m.LinuxOps);rows=iter([{'ActiveState':'active','MainPID':'42','InvocationID':'a'*32},{'ActiveState':'active','MainPID':'43','InvocationID':'b'*32}])
            o.unit_state=lambda _:next(rows);original_open=os.open
            with patch.object(m,'APP_SHA',m.sha(binary.read_bytes())),patch.object(m.os,'readlink',return_value=str(m.PROJECT/'bin/bhuman')),patch.object(m.os,'open',lambda path,flags:original_open(binary,flags)):
                with self.assertRaises(m.Rejected):o.app_pid()
    def test_owned_recovery_failure_keeps_independent_retry(self):
        o,c=self.driver();c.start(True);o.failures['force_damping']=1
        with self.assertRaises(RuntimeError):c.stop()
        self.assertIn('schedule_retry',o.events);self.assertNotEqual(c.state()['phase'],'RESTORED')
    def test_failed_recovery_then_manual_success_cancels_old_retry_before_new_start(self):
        o,c=self.driver();c.start(True);o.failures['force_damping']=1
        with self.assertRaises(RuntimeError):c.stop()
        self.assertTrue(o.retry_active)
        c.rollback();self.assertEqual(c.state()['phase'],'RESTORED');self.assertFalse(o.retry_active)
        c.start(True);o.events=[];o.clock+=16
        if o.retry_active:c.rollback() # Simulate the old timer's possible firing.
        self.assertTrue(o.app);self.assertEqual(c.state()['phase'],'RUNNING');self.assertNotIn('force_damping',o.events)
        for phase in ('RESTORED','BOOT_RESTORED'):
            c.stop();state=c.state();state['phase']=phase;o.files[m.STATE]=state;o.retry_active=True;o.events=[]
            c.rollback();self.assertFalse(o.retry_active);self.assertNotIn('force_damping',o.events)
    def test_real_preflight_requires_retry_timer_idle_no_job(self):
        o=object.__new__(m.LinuxOps);o.app_hash=lambda:None;calls=[]
        def unit_idle(unit):
            calls.append(unit)
            if unit==m.RETRY:raise m.Rejected('retry active or pending')
        o.unit_idle=unit_idle;o.daemon_online=lambda:True
        def read(path,*args,**kwargs):
            if path==m.HOOK:return m.HOOK_TEXT
            return m.UNITS[path.name]
        with patch.object(m,'APP_SHA',m.APP_SHA or 'a'*64),patch.object(m.Path,'read_text',read),patch.object(m.Path,'is_symlink',return_value=False):
            with self.assertRaisesRegex(m.Rejected,'retry'):o.preflight()
        self.assertEqual(calls,[m.UNIT,m.RECOVERY,m.RETRY])

    def service_ops(self,sequences,ops=None):
        o=ops or object.__new__(m.LinuxOps);clock={'now':100.};calls=[]
        if ops is not None:clock['now']=o.clock
        o.now=lambda:clock['now'];o.online=m.LinuxOps.online
        def bdb(action,name=None,timeout=5):
            calls.append((action,name,timeout))
            if action=='list':return self.inventory(sequences)
            values=sequences[name];value=values.pop(0) if len(values)>1 else values[0]
            if isinstance(value,BaseException):raise value
            return copy.deepcopy(value)
        o.bdb=bdb
        o.service_inventory=lambda **kwargs:m.LinuxOps.service_inventory(o,**kwargs)
        def sleep(seconds):
            self.assertLessEqual(seconds,.25);self.assertGreaterEqual(seconds,0)
            clock['now']+=seconds
        return o,clock,calls,sleep
    @staticmethod
    def inventory(names,endpoint='loopback'):
        return {'endpoints':[{'endpoint':endpoint,'services':[{'name':name,'endpoint':endpoint} for name in names]}]}
    @staticmethod
    def online_services():
        return {name:[{'stage':'ONLINE','pid':pid}] for pid,name in enumerate(m.LEASE_SERVICES,101)}
    def test_real_service_transition_and_config_warmup_waits_then_leases_without_restart(self):
        seq=self.online_services()
        seq['ros.camera']=[ConnectionError('config listener not yet available'),
                           m.subprocess.TimeoutExpired(['fixed','status'],1),
                           {'stage':'OFFLINE','pid':None},{'stage':'BOOTING','pid':None},
                           {'stage':'ONLINE','pid':101}]
        seq['ros.booster.video_stream']=[{'stage':'BOOTING','pid':None},{'stage':'ONLINE','pid':102}]
        seq['lui']=[{'stage':'STARTING','pid':None},{'stage':'ONLINE','pid':103}]
        o,clock,calls,sleep=self.service_ops(seq)
        with patch.object(m.time,'sleep',sleep):result=o.services_to_lease()
        self.assertEqual(result,list(m.LEASE_SERVICES))
        self.assertGreaterEqual(clock['now'],102.);self.assertLess(clock['now'],190.)
        self.assertTrue(all(action in ('list','status') and 0<timeout<=5 for action,_,timeout in calls))
    def test_real_optional_lui_requires_stable_offline_and_is_not_leased(self):
        seq=self.online_services();seq['lui']=[{'stage':'OFFLINE','pid':None}]
        o,clock,calls,sleep=self.service_ops(seq)
        with patch.object(m.time,'sleep',sleep):result=o.services_to_lease()
        self.assertEqual(result,[name for name in m.LEASE_SERVICES if name!='lui'])
        self.assertGreaterEqual(clock['now'],101.)
        self.assertGreaterEqual(sum(name=='lui' for _,name,_ in calls),5)
    def test_real_optional_lui_offline_flap_resets_stability_and_eventually_leases_online(self):
        seq=self.online_services();seq['lui']=[{'stage':'OFFLINE','pid':None}]*4+[{'stage':'ONLINE','pid':103}]
        o,clock,calls,sleep=self.service_ops(seq)
        with patch.object(m.time,'sleep',sleep):result=o.services_to_lease()
        self.assertIn('lui',result);self.assertGreaterEqual(clock['now'],102.)
    def test_real_required_camera_failed_or_exited_has_named_terminal_error(self):
        for name in ('ros.camera','ros.booster.video_stream'):
            for stage in ('FAILED','EXITED'):
                with self.subTest(name=name,stage=stage):
                    seq=self.online_services();seq[name]=[{'stage':stage,'pid':None,'error':'worker unavailable'}]
                    o,clock,calls,sleep=self.service_ops(seq)
                    with patch.object(m.time,'sleep',sleep),self.assertRaises(m.Rejected) as caught:o.services_to_lease()
                    self.assertIn(name,str(caught.exception));self.assertIn(stage,str(caught.exception))
                    self.assertIn('worker unavailable',str(caught.exception));self.assertEqual(clock['now'],100.)
    def test_real_service_timeout_retains_last_stage_and_errors_does_not_pause_or_start_app(self):
        o,c=self.driver();seq=self.online_services()
        seq['ros.camera']=[{'stage':'BOOTING','pid':None}]
        seq['lui']=[ConnectionError('listener warming')]
        o,clock,calls,sleep=self.service_ops(seq,o)
        o.services_to_lease=lambda:m.LinuxOps.services_to_lease(o)
        with patch.object(m.time,'sleep',sleep),self.assertRaises(m.Rejected) as caught:c.start(True)
        error=str(caught.exception)
        self.assertIn('90s',error);self.assertIn('ros.camera',error);self.assertIn('BOOTING',error)
        self.assertIn('lui',error);self.assertIn('ConnectionError',error)
        self.assertEqual(clock['now'],190.);self.assertNotIn('pause_services',o.events)
        self.assertNotIn('start_app',o.events);self.assertNotIn('force_damping',o.events)
        self.assertEqual(c.state()['phase'],'RESTORED');self.assertEqual(c.state()['services'],[])
        self.assertIn('service readiness timeout',o.files[m.START_FAILURE]['startup_error']['message'])
    def test_real_required_online_without_pid_never_passes(self):
        seq=self.online_services();seq['ros.camera']=[{'stage':'ONLINE','pid':None}]
        o,clock,calls,sleep=self.service_ops(seq)
        with patch.object(m.time,'sleep',sleep),self.assertRaisesRegex(m.Rejected,'ros.camera'):o.services_to_lease()
        self.assertEqual(clock['now'],190.)

    def lease_driver(self):
        """Full Controller with real lease/bdb/validation methods, fake OS only."""
        o,c=self.driver();calls=[];mutations=[];owned_at_command=[];boot_at={}
        o.service_states={name:{'name':name,'endpoint':'loopback','stage':'ONLINE','pid':pid}
                          for pid,name in enumerate(m.LEASE_SERVICES,101)}
        o.boot_delays={};o.service_status_overrides={}
        def run(args,timeout=25,check=True):
            self.assertEqual(args[:2],['/opt/booster/bin/bdb','service'])
            self.assertEqual(args[-1],'--json');self.assertIs(check,False)
            self.assertGreater(timeout,0);self.assertLessEqual(timeout,5)
            action=args[2];name=args[3] if action!='list' else None
            calls.append((action,name,o.clock))
            if action=='list':value=self.inventory(o.service_states)
            elif name not in o.service_states:
                return SimpleNamespace(returncode=1,stdout='',stderr=json.dumps(
                    {'cause':None,'error':'Service "'+name+'" not found','hint':None}))
            elif action=='status':
                if name in boot_at and o.clock>=boot_at[name]:
                    o.service_states[name].update(stage='ONLINE',pid=1000+m.LEASE_SERVICES.index(name))
                values=o.service_status_overrides.get(name)
                value=values.pop(0) if values and len(values)>1 else values[0] if values else o.service_states[name]
            else:
                o.hit('service:'+action+':'+name)
                mutations.append((action,name,o.clock));owned_at_command.append((action,name,c.state()['services']))
                if action=='kill':o.service_states[name].update(stage='OFFLINE',pid=None)
                elif action=='boot':
                    o.service_states[name].update(stage='BOOTING',pid=None)
                    boot_at[name]=o.clock+o.boot_delays.get(name,0)
                else:raise AssertionError('unfixed service action')
                o.hit('service:'+action+'_acted:'+name);value=o.service_states[name]
            return SimpleNamespace(returncode=0,stdout=json.dumps(value),stderr='')
        o.run=run;o.online=m.LinuxOps.online;o.off=m.LinuxOps.off
        for method in ('bdb','service_inventory','wait_service'):
            setattr(o,method,getattr(m.LinuxOps,method).__get__(o))
        for method in ('services_to_lease','pause_services','services_offline','restore_services'):
            def wrapped(*args,_method=method,**kwargs):
                o.hit(_method);return getattr(m.LinuxOps,_method)(o,*args,**kwargs)
            setattr(o,method,wrapped)
        def sleep(seconds):
            self.assertGreaterEqual(seconds,0);self.assertLessEqual(seconds,.25);o.clock+=seconds
        return o,c,calls,mutations,owned_at_command,sleep
    def test_real_lease_controller_start_pause_fail_joins_one_recovery_and_restores_six_in_order(self):
        o,c,calls,mutations,owned,sleep=self.lease_driver();o.failures['wait_cognition']=1
        with patch.object(m.time,'sleep',sleep),redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError,'wait_cognition'):c.start(True)
        self.assertEqual([(action,name) for action,name,_ in mutations],
                         [('kill',name) for name in reversed(m.LEASE_SERVICES)]+
                         [('boot',name) for name in m.LEASE_SERVICES])
        self.assertTrue(all(name in names for _,name,names in owned)) # Intent committed before every kill.
        self.assertEqual(o.events.count('request_recovery'),1);self.assertEqual(o.events.count('restore_services'),1)
        self.assertEqual(c.state()['phase'],'RESTORED');self.assertEqual(c.state()['services'],[])
        self.assertEqual(c.state()['lease_candidates'],[]);self.assertFalse(o.app)
        self.assertNotIn('schedule_retry',o.events)
        self.assertTrue(all(row['stage']=='ONLINE' for row in o.service_states.values()))
        self.assertTrue(all(name is None or name in m.LEASE_SERVICES for _,name,_ in calls))
        self.assertIn('wait_cognition',o.files[m.START_FAILURE]['startup_error']['message'])
    def test_real_partial_pause_ambiguous_kill_failure_restores_only_durable_intents(self):
        o,c,calls,mutations,owned,sleep=self.lease_driver()
        o.failures['service:kill_acted:ros.booster.auto_handeye_calib']=1
        with patch.object(m.time,'sleep',sleep),redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError,'kill_acted'):c.start(True)
        self.assertEqual([(action,name) for action,name,_ in mutations],
                         [('kill','lui'),('kill','ros.booster.auto_handeye_calib'),
                          ('boot','ros.booster.auto_handeye_calib'),('boot','lui')])
        self.assertEqual(owned[1][2],['ros.booster.auto_handeye_calib','lui'])
        self.assertEqual(o.events.count('request_recovery'),1);self.assertEqual(c.state()['phase'],'RESTORED')
        self.assertNotIn('start_app',o.events);self.assertNotIn('force_damping',o.events)
    def test_real_failed_pause_intent_write_never_kills_or_restores_unclaimed_candidate(self):
        o,c,calls,mutations,owned,sleep=self.lease_driver();write=o.write
        def guarded_write(path,value):
            if path==m.STATE and value.get('services')==list(m.LEASE_SERVICES[-3:]):
                raise RuntimeError('pause intent persistence failed')
            return write(path,value)
        o.write=guarded_write
        with patch.object(m.time,'sleep',sleep),redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError,'intent persistence'):c.start(True)
        self.assertEqual([(action,name) for action,name,_ in mutations],
                         [('kill','lui'),('kill','ros.booster.auto_handeye_calib'),
                          ('boot','ros.booster.auto_handeye_calib'),('boot','lui')])
        self.assertEqual(c.state()['phase'],'RESTORED');self.assertEqual(o.events.count('request_recovery'),1)
    def test_real_absent_and_originally_offline_optionals_are_not_claimed_or_booted(self):
        o,c,calls,mutations,owned,sleep=self.lease_driver();o.failures['wait_cognition']=1
        for name in ('booster_camera','ros.booster.vision_detection'):o.service_states.pop(name)
        o.service_states['ros.booster.auto_handeye_calib'].update(stage='OFFLINE',pid=None)
        output=io.StringIO()
        with patch.object(m.time,'sleep',sleep),redirect_stdout(output):
            with self.assertRaisesRegex(RuntimeError,'wait_cognition'):c.start(True)
        expected=['ros.camera','ros.booster.video_stream','lui']
        self.assertEqual([(action,name) for action,name,_ in mutations],
                         [('kill',name) for name in reversed(expected)]+[('boot',name) for name in expected])
        self.assertIn('SKIP_ABSENT_FACTORY_SERVICE: booster_camera',output.getvalue())
        self.assertEqual(o.service_states['ros.booster.auto_handeye_calib']['stage'],'OFFLINE')
        self.assertNotIn('booster_camera',[name for _,name,_ in calls])
    def test_real_online_pid_drift_before_pause_does_not_claim_changed_resource(self):
        o,c,calls,mutations,owned,sleep=self.lease_driver();pause=o.pause_services
        def drift(names,claim):
            o.service_states['ros.booster.video_stream']['pid']+=1
            return pause(names,claim)
        o.pause_services=drift
        with patch.object(m.time,'sleep',sleep),redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(m.Rejected,'ONLINE identity changed'):c.start(True)
        touched=list(m.LEASE_SERVICES[-3:])
        self.assertEqual([(action,name) for action,name,_ in mutations],
                         [('kill',name) for name in reversed(touched)]+[('boot',name) for name in touched])
        self.assertEqual(c.state()['phase'],'RESTORED');self.assertNotIn('start_app',o.events)
    def test_real_all_new_online_services_and_error_states_are_fully_validated(self):
        for name in m.LEASE_SERVICES:
            for stage in ('ERROR','FAILED','EXITED'):
                with self.subTest(name=name,stage=stage):
                    seq=self.online_services();seq[name]=[{'stage':stage,'pid':None,'error':'named fault'}]
                    o,clock,calls,sleep=self.service_ops(seq)
                    with patch.object(m.time,'sleep',sleep),redirect_stdout(io.StringIO()):
                        with self.assertRaisesRegex(m.Rejected,name):o.services_to_lease()
                    self.assertEqual(clock['now'],100.)
                    self.assertTrue(all(action in ('list','status') for action,_,_ in calls))
        for stage in ('OFFLINE','UNKNOWN'):
            seq=self.online_services();seq['booster_camera']=[{'stage':stage,'pid':None}]
            o,clock,calls,sleep=self.service_ops(seq)
            with patch.object(m.time,'sleep',sleep),redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(m.Rejected,'booster_camera'):o.services_to_lease()
            self.assertEqual(clock['now'],190.) # Existing native camera is conditionally REQUIRED.
    def test_real_unknown_optional_is_not_silently_treated_as_absent_or_healthy(self):
        seq=self.online_services();seq['ros.booster.vision_detection']=[{'stage':'UNRECOGNIZED','pid':None}]
        o,clock,calls,sleep=self.service_ops(seq)
        with patch.object(m.time,'sleep',sleep),redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(m.Rejected,'UNRECOGNIZED'):o.services_to_lease()
        self.assertEqual(clock['now'],190.)
    def test_real_new_native_online_without_valid_pid_cannot_certify_lease(self):
        for pid in (None,True,0,1,'101'):
            seq=self.online_services();seq['booster_camera']=[{'stage':'ONLINE','pid':pid}]
            o,clock,calls,sleep=self.service_ops(seq)
            with self.subTest(pid=pid),patch.object(m.time,'sleep',sleep),redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(m.Rejected,'booster_camera'):o.services_to_lease()
            self.assertEqual(clock['now'],190.);self.assertEqual(o._lease_online_pids,{})
    def test_real_restore_rejects_invalid_lists_before_any_bdb_operation(self):
        for names in (None,(),set(),['motion'],['device_gateway'],['remote_controller'],['robot_core'],
                      ['loopback.booster_camera'],['booster_camera','booster_camera'],[True],[[]]):
            o,c,calls,mutations,owned,sleep=self.lease_driver()
            with self.subTest(names=names),self.assertRaisesRegex(m.Rejected,'invalid owned service list'):
                o.restore_services(names)
            self.assertEqual(calls,[]);self.assertEqual(mutations,[])
    def test_real_restore_canonicalizes_valid_owned_list_without_reordering_dependencies(self):
        o,c,calls,mutations,owned,sleep=self.lease_driver()
        with patch.object(m.time,'sleep',sleep),redirect_stdout(io.StringIO()):
            c.start(True);o.app=False
            o.restore_services(list(reversed(m.LEASE_SERVICES)))
        self.assertEqual([name for action,name,_ in mutations if action=='boot'],list(m.LEASE_SERVICES))
    def test_real_restore_error_absent_or_unknown_never_boots_it_or_next_service(self):
        for kind in ('ERROR','absent','UNKNOWN'):
            o,c,calls,mutations,owned,sleep=self.lease_driver()
            name='booster_camera'
            if kind=='absent':o.service_states.pop(name)
            else:o.service_states[name].update(stage=kind,pid=None)
            with patch.object(m.time,'sleep',sleep),self.assertRaisesRegex(m.Rejected,'ERROR|absent|not settled'):
                o.restore_services([name,'lui'])
            self.assertEqual(mutations,[]);self.assertLessEqual(o.clock,190.)
            if kind=='UNKNOWN':self.assertEqual(o.clock,190.)
    def test_real_restore_has_ninety_second_total_budget_and_one_boot_per_service(self):
        o,c,calls,mutations,owned,sleep=self.lease_driver();o.failures['wait_cognition']=1
        o.boot_delays={name:8. for name in m.LEASE_SERVICES}
        with patch.object(m.time,'sleep',sleep),redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError,'wait_cognition'):c.start(True)
        boots=[row for row in mutations if row[0]=='boot']
        self.assertEqual([name for _,name,_ in boots],list(m.LEASE_SERVICES))
        self.assertGreater(boots[-1][2]-boots[0][2],20.)
        self.assertLess(o.clock-boots[0][2],90.)
        self.assertTrue(all(boots[i+1][2]-boots[i][2]>=9 for i in range(len(boots)-1)))
        o,c,calls,mutations,owned,sleep=self.lease_driver();o.failures['wait_cognition']=1
        o.boot_delays={'booster_camera':100.}
        with patch.object(m.time,'sleep',sleep),redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(m.Rejected,'recovery failed'):c.start(True)
        boots=[row for row in mutations if row[0]=='boot']
        self.assertEqual(len(boots),1);self.assertEqual(boots[0][1],'booster_camera')
        self.assertEqual(o.clock-boots[0][2],90.);self.assertTrue(o.retry_active)
    def timeout_boots(self,o,names,after_timeout=None):
        run=o.run
        def wrapped(args,timeout=25,check=True):
            result=run(args,timeout=timeout,check=check)
            if args[2]=='boot' and args[3] in names:
                name=args[3];o.clock+=timeout
                if after_timeout is not None:after_timeout(name)
                raise m.subprocess.TimeoutExpired(args,timeout,output=('partial '+name).encode(),stderr=b'client timeout evidence')
            return result
        o.run=wrapped
    def test_real_boot_timeout_after_submit_reconciles_lui_with_one_boot_and_preserves_first_evidence(self):
        o,c,calls,mutations,owned,sleep=self.lease_driver();o.failures['wait_cognition']=1
        o.boot_delays['lui']=8.;self.timeout_boots(o,{'lui'})
        persisted=[];write=o.write
        def capture(path,value):
            if path==m.SERVICE_RESTORE_LAST:persisted.append(copy.deepcopy(value))
            write(path,value)
        o.write=capture
        with patch.object(m.time,'sleep',sleep),redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError,'wait_cognition'):c.start(True)
        self.assertEqual(c.state()['phase'],'RESTORED');self.assertEqual(o.events.count('request_recovery'),1)
        self.assertNotIn('schedule_retry',o.events)
        self.assertEqual([name for action,name,_ in mutations if action=='boot'],list(m.LEASE_SERVICES))
        record=o.files[m.SERVICE_RESTORE_LAST];event=record['boot_timeouts'][0]
        self.assertEqual(record['outcome'],'restored');self.assertEqual(record['budget_seconds'],90)
        self.assertEqual(event['service'],'lui');self.assertEqual(event['error']['type'],'TimeoutExpired')
        self.assertEqual(event['stdout'],'partial lui');self.assertEqual(event['stderr'],'client timeout evidence')
        self.assertEqual(event['elapsed_seconds'],5.);self.assertTrue(event['reconciled'])
        self.assertGreater(event['online_pid'],1);self.assertGreaterEqual(event['reconciled_at']-event['at'],9.)
        self.assertFalse(persisted[0]['boot_timeouts'][0]['reconciled']) # Written BEFORE status reconciliation.
        self.assertEqual(o.files[m.START_FAILURE]['recovery_error'],None)
    def test_real_boot_timeout_reconciliation_requires_one_second_of_unchanged_positive_pid(self):
        o,c,calls,mutations,owned,sleep=self.lease_driver();o.failures['wait_cognition']=1
        def flap(name):
            o.service_status_overrides[name]=[
                {'name':name,'endpoint':'loopback','stage':'ONLINE','pid':pid} for pid in (2001,2002)]
        self.timeout_boots(o,{'lui'},flap)
        with patch.object(m.time,'sleep',sleep),redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError,'wait_cognition'):c.start(True)
        event=o.files[m.SERVICE_RESTORE_LAST]['boot_timeouts'][0]
        self.assertEqual(event['online_pid'],2002)
        self.assertEqual(event['reconciled_at']-event['at'],6.25) # 5s client timeout + PID reset + stable 1s.
        self.assertEqual(sum(action=='boot' and name=='lui' for action,name,_ in mutations),1)
    def test_real_multiple_boot_timeouts_keep_first_timeout_in_same_restore_attempt(self):
        o,c,calls,mutations,owned,sleep=self.lease_driver();o.failures['wait_cognition']=1
        self.timeout_boots(o,{'booster_camera','lui'})
        with patch.object(m.time,'sleep',sleep),redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError,'wait_cognition'):c.start(True)
        record=o.files[m.SERVICE_RESTORE_LAST]
        self.assertEqual([row['service'] for row in record['boot_timeouts']],['booster_camera','lui'])
        self.assertTrue(all(row['reconciled'] for row in record['boot_timeouts']))
        self.assertEqual(record['boot_timeouts'][0]['stdout'],'partial booster_camera')
        self.assertEqual(record['outcome'],'restored')
        self.assertEqual([name for action,name,_ in mutations if action=='boot'],list(m.LEASE_SERVICES))
    def test_real_boot_timeout_never_masks_terminal_unknown_or_invalid_online_pid(self):
        cases=[('ERROR',None),('FAILED',None),('EXITED',None),('UNKNOWN',None),
               ('ONLINE',None),('ONLINE',True),('ONLINE',1)]
        for stage,pid in cases:
            with self.subTest(stage=stage,pid=pid):
                o,c,calls,mutations,owned,sleep=self.lease_driver();o.failures['wait_cognition']=1
                def fault(name):
                    o.service_status_overrides[name]=[{'name':name,'endpoint':'loopback','stage':stage,'pid':pid}]
                self.timeout_boots(o,{'booster_camera'},fault)
                with patch.object(m.time,'sleep',sleep),redirect_stdout(io.StringIO()):
                    with self.assertRaisesRegex(m.Rejected,'recovery failed.*'+stage):c.start(True)
                self.assertEqual([name for action,name,_ in mutations if action=='boot'],['booster_camera'])
                self.assertNotEqual(c.state()['phase'],'RESTORED');self.assertTrue(o.retry_active)
                record=o.files[m.SERVICE_RESTORE_LAST]
                self.assertEqual(record['outcome'],'failed');self.assertIn(stage,record['error']['message'])
                self.assertEqual(record['boot_timeouts'][0]['error']['type'],'TimeoutExpired')
                self.assertFalse(record['boot_timeouts'][0]['reconciled'])
                if stage in ('ERROR','FAILED','EXITED'):self.assertEqual(record['elapsed_seconds'],5.)
                else:self.assertEqual(record['elapsed_seconds'],90.)
    def test_real_boot_timeout_reconciliation_cannot_reset_original_ninety_second_budget(self):
        o,c,calls,mutations,owned,sleep=self.lease_driver();o.failures['wait_cognition']=1
        o.boot_delays={'booster_camera':100.};self.timeout_boots(o,{'booster_camera'})
        with patch.object(m.time,'sleep',sleep),redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(m.Rejected,'recovery failed.*readiness timeout'):c.start(True)
        record=o.files[m.SERVICE_RESTORE_LAST]
        self.assertEqual(record['elapsed_seconds'],90.) # Not 95s after the 5s boot timeout.
        self.assertEqual(record['outcome'],'failed');self.assertEqual(len(record['boot_timeouts']),1)
        self.assertEqual([name for action,name,_ in mutations if action=='boot'],['booster_camera'])
    def test_real_non_timeout_boot_error_is_not_reconciled_even_if_service_became_online(self):
        o,c,calls,mutations,owned,sleep=self.lease_driver();o.failures['wait_cognition']=1;run=o.run
        def denied(args,**kwargs):
            result=run(args,**kwargs)
            if args[2]=='boot':return SimpleNamespace(returncode=7,stdout='',stderr='permission denied')
            return result
        o.run=denied
        with patch.object(m.time,'sleep',sleep),redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(m.Rejected,'recovery failed: bdb failure: booster_camera'):c.start(True)
        self.assertNotIn(m.SERVICE_RESTORE_LAST,o.files)
        self.assertEqual(calls[-1][0:2],('boot','booster_camera'))
        self.assertNotEqual(c.state()['phase'],'RESTORED')
    def test_real_boot_timeout_evidence_write_failure_retains_cause_in_stderr_without_masking_error(self):
        o,c,calls,mutations,owned,sleep=self.lease_driver();o.failures['wait_cognition']=1
        o.failures['write:SERVICE_RESTORE_LAST.json']=5
        def fault(name):o.service_status_overrides[name]=[{'name':name,'endpoint':'loopback','stage':'ERROR','pid':None}]
        self.timeout_boots(o,{'booster_camera'},fault);errors=io.StringIO()
        with patch.object(m.time,'sleep',sleep),redirect_stdout(io.StringIO()),redirect_stderr(errors):
            with self.assertRaisesRegex(m.Rejected,'recovery failed.*ERROR'):c.start(True)
        self.assertIn('TimeoutExpired',errors.getvalue());self.assertIn('partial booster_camera',errors.getvalue())
        self.assertIn('ERROR',errors.getvalue());self.assertNotEqual(c.state()['phase'],'RESTORED')
    def test_service_restore_timeout_evidence_uses_private_root_atomic_writer(self):
        o=object.__new__(m.LinuxOps);calls=[]
        o.module=SimpleNamespace(atomic_write=lambda *args:calls.append(args))
        o.write(m.SERVICE_RESTORE_LAST,{'boot_timeouts':[{'service':'lui','error':{'type':'TimeoutExpired'}}]})
        self.assertEqual(calls[0][0],m.SERVICE_RESTORE_LAST);self.assertEqual(calls[0][2:],(0o600,0,0))
    def test_real_final_exclusivity_checks_optional_previously_offline_consumer(self):
        o,c,calls,mutations,owned,sleep=self.lease_driver()
        o.service_states['ros.booster.vision_detection'].update(stage='OFFLINE',pid=None)
        original=o.observe_health
        def activate_unowned():
            o.service_states['ros.booster.vision_detection'].update(stage='ERROR',pid=None)
            return original()
        o.observe_health=activate_unowned
        with patch.object(m.time,'sleep',sleep),redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(m.Rejected,'vision_detection'):c.start(True)
        self.assertNotIn('ros.booster.vision_detection',[name for _,name,_ in mutations])
        self.assertFalse(o.app);self.assertEqual(c.state()['phase'],'RESTORED')
    def test_state_service_ownership_rejects_escape_duplicates_and_unclaimed_names_before_recovery(self):
        for mutation in ('escape','duplicate','unclaimed','legacy_new'):
            o,c=self.driver()
            with redirect_stdout(io.StringIO()):c.start(True)
            state=o.files[m.STATE]
            if mutation=='escape':state['services']=['motion']
            elif mutation=='duplicate':state['services']=['lui','lui']
            elif mutation=='unclaimed':state['lease_candidates']=['lui']
            else:state.pop('lease_candidates') # New services cannot masquerade as legacy three-service ownership.
            o.events=[]
            with self.assertRaises(m.Rejected):c.rollback()
            self.assertEqual(o.events,[]);self.assertTrue(o.app)
    def test_state_keeps_legacy_three_service_recovery_compatible(self):
        o,c=self.driver();legacy={'schema':1,'owner':'bh987-match','serial':m.SERIAL,
                                 'services':['ros.camera','ros.booster.video_stream','lui']}
        o.files[m.STATE]=legacy;self.assertEqual(c.state(),legacy);self.assertEqual(o.events,[])
    def test_inventory_only_complete_unambiguous_loopback_names_can_authorize_lease(self):
        valid=self.inventory(m.LEASE_SERVICES)
        remote=self.inventory(['unrelated.remote'],'remote')['endpoints'][0]
        cases=[]
        cases.append({})
        cases.append({'endpoints':[remote]})
        cases.append({'endpoints':[valid['endpoints'][0],copy.deepcopy(valid['endpoints'][0])]})
        bad=copy.deepcopy(valid);bad['endpoints'][0]['services'].append(copy.deepcopy(bad['endpoints'][0]['services'][0]));cases.append(bad)
        bad=copy.deepcopy(valid);bad['endpoints'][0]['services'][0]['endpoint']='remote';cases.append(bad)
        cases.append(self.inventory(['lui']))
        bad=copy.deepcopy(valid);bad['endpoints']+=self.inventory(['booster_camera'],'remote')['endpoints'];cases.append(bad)
        for value in cases:
            o=object.__new__(m.LinuxOps);o.bdb=lambda *args,_value=value,**kwargs:_value
            with self.subTest(value=value),self.assertRaises(m.Rejected):o.service_inventory()
        o.bdb=lambda *args,**kwargs:{'endpoints':valid['endpoints']+[remote]}
        self.assertEqual(o.service_inventory(),list(m.LEASE_SERVICES))
    def test_real_bdb_wrapper_rejects_wrong_identity_and_nonlease_mutations(self):
        o=object.__new__(m.LinuxOps);calls=[]
        def run(args,**kwargs):calls.append(args);return SimpleNamespace(returncode=0,stderr='',stdout='{}')
        o.run=run
        for name in ('motion','device_gateway','remote_controller','robot_core','ros.camera.extra'):
            for action in ('kill','boot'):
                with self.assertRaisesRegex(m.Rejected,'unsupported fixed'):o.bdb(action,name)
        self.assertEqual(calls,[])
        for value in ({},[],{'name':'booster_camera','endpoint':'remote','stage':'ONLINE','pid':4},
                      {'name':'other','endpoint':'loopback','stage':'ONLINE','pid':4}):
            o.run=lambda *args,_value=value,**kwargs:SimpleNamespace(returncode=0,stderr='',stdout=json.dumps(_value))
            with self.assertRaises(m.Rejected):o.bdb('status','booster_camera')
        o.run=lambda *args,**kwargs:SimpleNamespace(returncode=1,stdout='',stderr=json.dumps(
            {'cause':None,'error':'Service "booster_camera" not found','hint':None}))
        with self.assertRaises(m.ServiceAbsent):o.bdb('status','booster_camera')
        with self.assertRaises(m.Rejected) as caught:o.bdb('boot','booster_camera')
        self.assertNotIsInstance(caught.exception,m.ServiceAbsent)


if __name__=='__main__':unittest.main()
