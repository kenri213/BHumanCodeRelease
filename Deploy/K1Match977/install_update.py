#!/usr/bin/env python3
"""977 file-only official-mode update; never stop/start services or use the SDK.

Run as root on the verified 977 with a root-owned payload and an independently
verified manifest SHA. A failure is an inspection/manual-recovery condition, not
permission to stop an actor, retry installation, or restore files automatically.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import time

sys.dont_write_bytecode = True
PROJECT = Path('/home/booster/Workspace/BHuman_977_From996_20261023')
ROOT = Path('/opt/booster/bhuman_match_977_from996')
SERIAL = '1424225066425'
OLD_APP = '2cfaf1b04f9bb6eea0194e27ecd813b7babe7a69f97fc7920630f5a576052628'
OLD_CONTROLLER = '7179e78185e4a093e7ba7a4cb31729ae57d9ff6da2348661380f8fc249b43f1d'
CORE = '809c4414b634ee2a1a7dccf4dd0955c732c509cbfd89c17811ef354326a9a723'
BACKUP = ROOT / 'official_mode_backup_20261024_01'
PROOF = ROOT / 'VISION_CACHE_READY.json'
RECEIPT = PROJECT / 'OFFICIAL_MODE_RECEIPT_20261024.json'
RELEASE = PROJECT / 'OFFICIAL_MODE_RELEASE_METADATA_20261024.json'
PREVIOUS_RECEIPT = PROJECT / 'DEPLOYMENT_VERIFIED.json'
PREVIOUS_RECEIPT_SHA = '43bfd0323408f13bb9f419c5fbacd7b6ac2bc1d88d39944419c33d10c7b2f683'
HISTORICAL_METADATA = {
    'DEPLOYMENT_VERIFIED.json': PREVIOUS_RECEIPT_SHA,
    'BUILD_PROVENANCE.json': 'e95337d2ab19b10965433060ec158f674282f3df862b90db0cbaa167c931e891',
    'payload_manifest.json': 'f0de6bea113198d53046fb28038b02ec71f979def997794f9d726dbea9997d07',
}
TARGETS = {'bhuman': PROJECT / 'bin/bhuman', 'match_control.py': ROOT / 'match_control.py',
           'start_match.sh': PROJECT / 'scripts/start_match.sh', 'README_MATCH.md': PROJECT / 'README_MATCH.md'}
OLD_TARGET_SHAS = {
    'bhuman': OLD_APP, 'match_control.py': OLD_CONTROLLER,
    'start_match.sh': 'ca96caba1af1beb4fc925ea95fae2fc3c655ab8abd1883517c93b2caeb848378',
    'README_MATCH.md': 'bd4c6d7e031cc102abde1c2750621035991582565257d2a2942f08b5cfb88858',
}
PAYLOAD_NAMES = frozenset((*TARGETS, 'release_metadata.json'))


def require(value, message):
    if not value:
        raise RuntimeError(message)


def valid_sha(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def directory(path):
    """Reject symlink ancestors, including in the booster-owned project tree."""
    for part in (path, *path.parents):
        info = part.lstat()
        require(stat.S_ISDIR(info.st_mode), 'linked/non-directory ancestor: ' + str(part))


def sha(path):
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, 'not a single regular file: ' + str(path))
        value = hashlib.sha256()
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            value.update(block)
        after = os.fstat(fd)
        signature = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns, row.st_ctime_ns)
        require(signature(before) == signature(after), 'file changed while hashing: ' + str(path))
        require(path.lstat().st_ino == after.st_ino and path.lstat().st_dev == after.st_dev,
                'file replaced while hashing: ' + str(path))
        return value.hexdigest()
    finally:
        os.close(fd)


def file_metadata(path):
    directory(path.parent)
    digest = sha(path)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'target no longer regular: ' + str(path))
    return {'target': str(path), 'sha256': digest, 'uid': info.st_uid, 'gid': info.st_gid,
            'mode': stat.S_IMODE(info.st_mode)}


def protected():
    result = {}
    for folder in ('Config', 'lib'):
        base = PROJECT / folder
        directory(base)
        for path in sorted(base.rglob('*')):
            info = path.lstat()
            require(not stat.S_ISLNK(info.st_mode), 'symlink in protected tree: ' + str(path))
            require(stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode), 'special protected entry: ' + str(path))
            if stat.S_ISREG(info.st_mode):
                result[str(path.relative_to(PROJECT))] = sha(path)
    return result


def atomic_write(destination, write_content, uid, gid, mode):
    """Exclusive temporary + dirfd-relative replace; never follow a receipt link."""
    directory(destination.parent)
    parent = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    temporary = destination.name + '.977-official-mode-new'
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
        with os.fdopen(fd, 'wb') as writer:
            write_content(writer)
            os.fchown(writer.fileno(), uid, gid)
            os.fchmod(writer.fileno(), mode)
            writer.flush()
            os.fsync(writer.fileno())
        actual = destination.parent.lstat()
        original = os.fstat(parent)
        require(stat.S_ISDIR(actual.st_mode) and (actual.st_dev, actual.st_ino) == (original.st_dev, original.st_ino),
                'destination directory changed during copy')
        os.replace(temporary, destination.name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(parent) # Failed temporary files are evidence; do not unlink/retry.


def atomic_copy(source, destination, expected, uid, gid, mode):
    def copy(writer):
        digest = hashlib.sha256()
        with source.open('rb') as reader:
            for block in iter(lambda: reader.read(1024 * 1024), b''):
                writer.write(block)
                digest.update(block)
        require(digest.hexdigest() == expected, 'copy hash differs: ' + str(source))
    atomic_write(destination, copy, uid, gid, mode)
    require(sha(destination) == expected, 'installed hash differs: ' + str(destination))


def atomic_json(destination, value, uid=0, gid=0, mode=0o600):
    raw = (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()
    atomic_write(destination, lambda writer: writer.write(raw), uid, gid, mode)


def updated_metadata(old_provenance, old_manifest, manifest, release):
    """Rebase only version metadata; unchanged resources keep their original hashes."""
    require(type(old_provenance) is dict and type(old_manifest) is dict, 'invalid previous metadata')
    require(old_provenance.get('app_sha256') == OLD_APP and old_manifest.get('bin/bhuman') == OLD_APP
            and old_manifest.get('match_control.py') == OLD_CONTROLLER, 'previous metadata version differs')
    require(valid_sha(release.get('source_build_report_sha256'))
            and valid_sha(release.get('frozen_launcher_receipt_sha256')), 'source/frozen build receipt pins required')
    provenance = dict(old_provenance)
    provenance.update(app_sha256=manifest['bhuman'], controller_sha256=manifest['match_control.py'],
                      build_receipt_sha256=release['source_build_report_sha256'],
                      frozen_launcher_receipt_sha256=release['frozen_launcher_receipt_sha256'],
                      cpp_origin='996 37a2 base + 977 official F1/resident APP and native RB+X semantics',
                      previous_provenance_sha256=HISTORICAL_METADATA['BUILD_PROVENANCE.json'],
                      previous_deployment_receipt_sha256=PREVIOUS_RECEIPT_SHA,
                      current_release='OFFICIAL_MODE_RELEASE_METADATA_20261024.json',
                      body_started=False, physical_acceptance='NOT RUN')
    updated = dict(old_manifest)
    for key, payload_name in (('bin/bhuman', 'bhuman'), ('match_control.py', 'match_control.py'),
                              ('scripts/start_match.sh', 'start_match.sh'), ('README_MATCH.md', 'README_MATCH.md')):
        require(key in updated, 'previous payload missing target: ' + key)
        updated[key] = manifest[payload_name]
    updated['BUILD_PROVENANCE.json'] = hashlib.sha256((json.dumps(provenance, sort_keys=True, indent=2) + '\n').encode()).hexdigest()
    return provenance, updated


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_payload(payload, manifest_pin):
    require(payload.is_absolute() and valid_sha(manifest_pin), 'absolute payload/external SHA pin required')
    directory(payload)
    for part in (payload, *payload.parents):
        info = part.lstat()
        require(info.st_uid == 0 and not info.st_mode & 0o022, 'untrusted staging ancestor: ' + str(part))
    require({p.name for p in payload.iterdir()} == PAYLOAD_NAMES | {'manifest.json'}, 'unexpected payload files')
    for path in payload.iterdir():
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == 0
                and not info.st_mode & 0o022, 'untrusted staging file: ' + str(path))
    require(sha(payload / 'manifest.json') == manifest_pin, 'external manifest pin differs')
    manifest = json.loads((payload / 'manifest.json').read_text())
    require(type(manifest) is dict and set(manifest) == PAYLOAD_NAMES, 'invalid manifest entries')
    for name, expected in manifest.items():
        require(valid_sha(expected) and sha(payload / name) == expected, 'payload hash differs: ' + name)
    metadata = json.loads((payload / 'release_metadata.json').read_text())
    require(type(metadata) is dict and metadata.get('robot') == 977 and metadata.get('serial') == SERIAL
            and metadata.get('app_sha256') == manifest['bhuman']
            and metadata.get('previous_app_sha256') == OLD_APP
            and valid_sha(metadata.get('source_build_report_sha256'))
            and valid_sha(metadata.get('frozen_launcher_receipt_sha256')), 'release metadata identity/version differs')
    return manifest


def start_actor(argv):
    """Classify only this target's exact controller/wrapper paths, never a substring."""
    controller = str(ROOT / 'match_control.py')
    wrapper = str(PROJECT / 'scripts/start_match.sh')
    if controller in argv and 'start' in argv[argv.index(controller) + 1:]:
        return 'controller_start'
    if wrapper in argv:
        arguments = argv[argv.index(wrapper) + 1:]
        if not arguments or arguments[0] == 'start':
            return 'wrapper_start'
    return None


def no_start_processes(proc_root=Path('/proc')):
    """A live start CLI may wait outside match.lock; refuse it without signalling it."""
    require(proc_root.is_dir(), 'proc process inventory unavailable')
    actors = []
    for process in proc_root.iterdir():
        if not process.name.isdecimal() or int(process.name) == os.getpid():
            continue
        try:
            raw = (process / 'cmdline').read_bytes()
        except (FileNotFoundError, ProcessLookupError):
            continue # The actor exited while the read-only inventory was taken.
        except OSError as error:
            raise RuntimeError('cannot inspect process ' + process.name + ': ' + type(error).__name__) from error
        argv = [part.decode('utf-8', errors='replace') for part in raw.split(b'\0') if part]
        kind = start_actor(argv)
        if kind:
            actors.append({'pid': int(process.name), 'entry': kind})
    require(not actors, '977 start actor pending; inspect without killing: ' + json.dumps(actors, sort_keys=True))


def idle(ops, module):
    """Queries only. An active actor is a refusal, never a request to stop it."""
    no_start_processes()
    ops.no_body()
    units = {name: ops.unit_state(name) for name in (module.UNIT, module.RECOVERY, module.RETRY)}
    for name, value in units.items():
        pid = value.get('MainPID', '0') if name.endswith('.timer') else value.get('MainPID')
        require(value.get('ActiveState') == 'inactive' and pid == '0'
                and value.get('Job') in ('', '0'), 'unit active/not strictly inactive: ' + name)
    state = module.Controller(ops).state()
    require(type(state) is dict and state.get('phase') == 'RESTORED' and state.get('files_restored') is True
            and state.get('pid') is None and state.get('services') == []
            and state.get('lease_candidates', []) == [], 'match not fully RESTORED')
    require(not os.path.lexists(module.START) and not os.path.lexists(module.PERMIT),
            'pending match start/permit; inspect without clearing it')
    require(ops.core_phase() in ('NONE', 'RESTORED'), 'route transaction not finalized')
    ops.originals()
    return units, state


def policy_snapshot(module):
    paths = [ROOT / 'route_transaction.py', module.HOOK, Path(module.PROFILE)]
    paths += [Path('/etc/systemd/system') / name for name in module.UNITS]
    helpers = {'k1_mode_query': module.MODE_QUERY_SHA, 'k1_dds_observer': module.OBSERVER_SHA,
               'k1_damping_only': module.DAMPING_SHA}
    for name, expected in helpers.items():
        require(sha(PROJECT / 'bin' / name) == expected, 'helper pin differs: ' + name)
        paths.append(PROJECT / 'bin' / name)
    return {str(path): file_metadata(path) for path in paths}


def error_record(error):
    return {'type': type(error).__name__, 'message': str(error)[:2000]}


def install(payload, manifest_pin):
    require(os.geteuid() == 0 and Path('/sys/firmware/devicetree/base/serial-number').read_bytes().rstrip(b'\0\r\n') == SERIAL.encode(),
            'wrong hardware/root')
    manifest = validate_payload(payload, manifest_pin)
    require(sha(ROOT / 'match_control.py') == OLD_CONTROLLER and sha(PROJECT / 'bin/bhuman') == OLD_APP
            and sha(ROOT / 'route_transaction.py') == CORE, 'installed version/core changed')
    for name, target in TARGETS.items():
        require(sha(target) == OLD_TARGET_SHAS[name], 'installed target changed: ' + name)
    require(sha(PREVIOUS_RECEIPT) == PREVIOUS_RECEIPT_SHA, 'previous deployment receipt changed')
    for name, expected in HISTORICAL_METADATA.items():
        require(sha(PROJECT / name) == expected, 'historical metadata changed: ' + name)
    old = load(ROOT / 'match_control.py', 'before977_official_mode') # Hash verified BEFORE executing module definitions.
    new = load(payload / 'match_control.py', 'candidate977_official_mode')
    require(new.APP_SHA == manifest['bhuman'] and new.APP_SHA != OLD_APP, 'new APP/controller pin differs')
    for name in ('SERIAL', 'PROJECT', 'ROOT', 'CORE_SHA', 'MODE_QUERY_SHA', 'OBSERVER_SHA', 'DAMPING_SHA',
                 'PROFILE', 'APP_ARGS', 'UNIT', 'RECOVERY', 'RETRY', 'STATE', 'PERMIT', 'START',
                 'UNITS', 'HOOK', 'HOOK_TEXT', 'LEASE_SERVICES', 'SERVICE_BUDGET'):
        require(getattr(new, name) == getattr(old, name), 'entry/unit/helper policy changed: ' + name)
    require(new.SERIAL == SERIAL and new.PROJECT == PROJECT and new.ROOT == ROOT and new.CORE_SHA == CORE,
            'candidate targets differ')
    ops = old.LinuxOps()
    with ops.lock():
        ops.preflight(); ops.old_trial_idle()
        units, state = idle(ops, old)
        proof = ops.read(PROOF)
        release = json.loads((payload / 'release_metadata.json').read_text())
        provenance, payload_inventory = updated_metadata(
            json.loads((PROJECT / 'BUILD_PROVENANCE.json').read_text()),
            json.loads((PROJECT / 'payload_manifest.json').read_text()), manifest, release)
        ops.vision_cache_ready() # Validate ORIGINAL APP/model/engine proof before any replacement.
        before = protected()
        require(before and all(any(name.startswith(folder + '/') for name in before) for folder in ('Config', 'lib')),
                'empty Config/lib protection inventory; inspect before installing')
        policy = policy_snapshot(old)
        require(not os.path.lexists(BACKUP) and not os.path.lexists(RECEIPT) and not os.path.lexists(RELEASE),
                'update/receipt already exists; preserve it and inspect before retrying')
        BACKUP.mkdir(mode=0o700)
        progress = {'schema': 1, 'robot': 977, 'serial': SERIAL, 'boot_id': ops.boot_id(),
                    'started_at_unix_seconds': time.time(), 'manifest_sha256': manifest_pin,
                    'payload_hashes': manifest, 'stage': 'backup', 'intents': [], 'completed': [],
                    'units_before': units, 'match_state_before': state, 'body_started': False,
                    'sdk_called': False, 'services_restarted': False, 'auto_rollback': False}
        try:
            atomic_json(BACKUP / 'INSTALL_STATE.json', progress)
            saved = {}
            for name, target in {**TARGETS, 'VISION_CACHE_READY.json': PROOF,
                                 **{key: PROJECT / key for key in HISTORICAL_METADATA}}.items():
                saved[name] = file_metadata(target)
                atomic_copy(target, BACKUP / name, saved[name]['sha256'], 0, 0, 0o600)
                require(file_metadata(target) == saved[name], 'target changed during backup: ' + name)
            atomic_json(BACKUP / 'backup_manifest.json', saved)
            atomic_json(BACKUP / 'protected_manifest_before.json', before)
            atomic_json(BACKUP / 'policy_manifest_before.json', policy)
            require(saved['bhuman']['sha256'] == OLD_APP and saved['match_control.py']['sha256'] == OLD_CONTROLLER,
                    'installed version changed before commit')
            require(all(saved[name]['sha256'] == expected for name, expected in OLD_TARGET_SHAS.items()),
                    'installed target changed before commit')
            require(saved['DEPLOYMENT_VERIFIED.json']['sha256'] == PREVIOUS_RECEIPT_SHA,
                    'previous receipt changed before commit')
            idle(ops, old)
            require(protected() == before and policy_snapshot(old) == policy, 'protected/policy bytes changed before commit')
            progress['stage'] = 'replace'
            for name, target in TARGETS.items():
                require(file_metadata(target) == saved[name], 'target changed before replacement: ' + name)
                progress['intents'].append(name); atomic_json(BACKUP / 'INSTALL_STATE.json', progress)
                row = saved[name]
                atomic_copy(payload / name, target, manifest[name], row['uid'], row['gid'], row['mode'])
                progress['completed'].append(name); atomic_json(BACKUP / 'INSTALL_STATE.json', progress)
            require(protected() == before and policy_snapshot(old) == policy, 'configuration/model/cache/runtime/policy changed')
            progress['stage'] = 'rebind_cache_proof'
            progress['intents'].append('VISION_CACHE_READY.json'); atomic_json(BACKUP / 'INSTALL_STATE.json', progress)
            require(file_metadata(PROOF) == saved['VISION_CACHE_READY.json'], 'original cache proof changed during update')
            proof['rebound_from_app_sha256'] = OLD_APP
            proof['app_sha256'] = new.APP_SHA
            proof['rebind_scope'] = 'same_target_models_engines_runtime_unchanged; no physical/cold-start certification'
            row = saved['VISION_CACHE_READY.json']
            atomic_json(PROOF, proof, row['uid'], row['gid'], row['mode']) # Original proof is retained verbatim.
            progress['completed'].append('VISION_CACHE_READY.json')
            progress['stage'] = 'post_preflight'; atomic_json(BACKUP / 'INSTALL_STATE.json', progress)
            installed = load(ROOT / 'match_control.py', 'installed977_official_mode')
            current = installed.LinuxOps()
            current.preflight(); current.old_trial_idle(); idle(current, installed)
            require(current.boot_id() == progress['boot_id'], 'boot changed during installation')
            for name, target in TARGETS.items():
                row = file_metadata(target)
                require(row['sha256'] == manifest[name]
                        and all(row[key] == saved[name][key] for key in ('uid', 'gid', 'mode')), 'installed target drift: ' + name)
            require(protected() == before and policy_snapshot(installed) == policy, 'post-preflight protected/policy drift')
            require(file_metadata(PREVIOUS_RECEIPT) == saved['DEPLOYMENT_VERIFIED.json'], 'previous receipt drift')
            progress['stage'] = 'receipts'; atomic_json(BACKUP / 'INSTALL_STATE.json', progress)
            for name, value in (('BUILD_PROVENANCE.json', provenance), ('payload_manifest.json', payload_inventory)):
                target = PROJECT / name
                require(file_metadata(target) == saved[name], 'historical metadata drift: ' + name)
                row = saved[name]
                progress['intents'].append(name); atomic_json(BACKUP / 'INSTALL_STATE.json', progress)
                atomic_json(target, value, row['uid'], row['gid'], row['mode'])
                progress['completed'].append(name); atomic_json(BACKUP / 'INSTALL_STATE.json', progress)
            atomic_copy(payload / 'release_metadata.json', RELEASE, manifest['release_metadata.json'], 1000, 1000, 0o644)
            receipt = {'robot': 977, 'serial': SERIAL, 'app_sha256': new.APP_SHA, 'previous_app_sha256': OLD_APP,
                       'controller_sha256': manifest['match_control.py'], 'manifest_sha256': manifest_pin,
                       'backup': str(BACKUP), 'boot_id': progress['boot_id'], 'protected_files_unchanged': len(before),
                       'protected_manifest_sha256': hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
                       'cache_proof_rebound': True, 'files_preflight_ok': True, 'routes_original': True,
                       'unit_hook_core_unchanged': True, 'body_started': False, 'sdk_called': False,
                       'services_restarted': False, 'model_pd_config_changed': False, 'github_mutation': False,
                       'physical_acceptance': 'NOT RUN', 'no_fixed_match_runtime': True}
            receipt.update(core_sha256=CORE, source_build_report_sha256=release['source_build_report_sha256'],
                           frozen_launcher_receipt_sha256=release['frozen_launcher_receipt_sha256'],
                           previous_deployment_receipt_sha256=PREVIOUS_RECEIPT_SHA,
                           payload_manifest_sha256=sha(PROJECT / 'payload_manifest.json'),
                           historical_camera_cache_evidence=str(BACKUP / 'DEPLOYMENT_VERIFIED.json'),
                           cognition_or_cold_start_certified=False, match_ready=False,
                           acceptance_scope='FILES_ONLY; old camera/cache evidence retained as history, not new APP acceptance')
            atomic_json(RECEIPT, receipt, 1000, 1000, 0o644)
            row = saved['DEPLOYMENT_VERIFIED.json']
            require(file_metadata(PREVIOUS_RECEIPT) == row, 'previous receipt changed before supersession')
            atomic_json(PREVIOUS_RECEIPT, receipt, row['uid'], row['gid'], row['mode'])
            require(protected() == before and policy_snapshot(installed) == policy, 'protected bytes changed during receipt writes')
            idle(current, installed)
            progress['stage'] = 'complete'; progress['receipt'] = str(RECEIPT)
            atomic_json(BACKUP / 'INSTALL_STATE.json', progress)
            print(json.dumps(receipt, sort_keys=True))
            return receipt
        except BaseException as error:
            progress['failure'] = error_record(error)
            progress['failed_at_unix_seconds'] = time.time()
            progress['manual_review_required'] = True
            progress['target_snapshot'] = {}
            for name, target in {**TARGETS, 'VISION_CACHE_READY.json': PROOF,
                                 **{key: PROJECT / key for key in HISTORICAL_METADATA}}.items():
                try: progress['target_snapshot'][name] = file_metadata(target)
                except BaseException as inspect_error: progress['target_snapshot'][name] = {'error': error_record(inspect_error)}
            for label, probe in (('protected_after_failure', protected), ('units_after_failure',
                                  lambda: {name: ops.unit_state(name) for name in (old.UNIT, old.RECOVERY, old.RETRY)})):
                try: progress[label] = probe()
                except BaseException as inspect_error: progress[label] = {'error': error_record(inspect_error)}
            try: atomic_json(BACKUP / 'INSTALL_FAILURE.json', progress)
            except BaseException as evidence_error:
                print('WARNING: failure evidence write failed: ' + str(evidence_error) + ' ' + json.dumps(progress, sort_keys=True), file=sys.stderr)
            raise # No next target, proof rebind, SDK, service recovery, or automatic file rollback.


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('payload', type=Path)
    parser.add_argument('manifest_sha256')
    args = parser.parse_args(argv)
    try:
        install(args.payload, args.manifest_sha256)
    except BaseException as error:
        print(json.dumps({'ok': False, 'error': error_record(error), 'backup': str(BACKUP),
                          'action': 'STOP; inspect evidence and restore manually only while safely idle'}), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
