#!/usr/bin/env python3
"""Offline tests for this 977 installer only; no robot paths are accessed."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

sys.dont_write_bytecode = True
INSTALLER = Path(__file__).with_name('install_update.py')
SPEC = importlib.util.spec_from_file_location('installer977_feedback_test', INSTALLER)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


def digest(content):
    return hashlib.sha256(content.encode()).hexdigest()


class MemoryPath:
    """Only the methods used by validate_payload; never opens a real path."""
    def __init__(self, name, content='', directory=False):
        self.name = name
        self.content = content
        self.parents = ()
        self.absolute = True
        self.entries = {}
        self.info = SimpleNamespace(st_mode=(stat.S_IFDIR | 0o700) if directory else (stat.S_IFREG | 0o600),
                                    st_uid=0, st_nlink=1)

    def __str__(self):
        return '/memory977/' + self.name

    def __truediv__(self, name):
        return self.entries[name]

    def __getitem__(self, name):
        return self.entries[name]

    def is_absolute(self):
        return self.absolute

    def lstat(self):
        return self.info

    def iterdir(self):
        return iter(self.entries.values())

    def read_text(self):
        return self.content

    def read_bytes(self):
        return self.content.encode()

    def is_dir(self):
        return stat.S_ISDIR(self.info.st_mode)


def payload_fixture():
    payload = MemoryPath('payload', directory=True)
    for name in installer.TARGETS:
        payload.entries[name] = MemoryPath(name, 'new 977 payload: ' + name)
    metadata = {'robot': 977, 'serial': installer.SERIAL,
                'app_sha256': digest(payload.entries['bhuman'].content),
                'previous_app_sha256': installer.OLD_APP,
                'source_build_report_sha256': 'b' * 64, 'frozen_launcher_receipt_sha256': 'c' * 64}
    payload.entries['release_metadata.json'] = MemoryPath('release_metadata.json', json.dumps(metadata))
    refresh_manifest(payload)
    return payload


def refresh_manifest(payload):
    manifest = {name: digest(payload.entries[name].content) for name in installer.PAYLOAD_NAMES}
    payload.entries['manifest.json'] = MemoryPath('manifest.json', json.dumps(manifest))
    return digest(payload.entries['manifest.json'].content)


def rewrite_metadata(payload, **changes):
    value = json.loads(payload.entries['release_metadata.json'].content)
    value.update(changes)
    payload.entries['release_metadata.json'].content = json.dumps(value)
    return refresh_manifest(payload)


class InstallerIdentityTests(unittest.TestCase):
    def test_exact_977_identity(self):
        self.assertEqual(str(installer.PROJECT), '/home/booster/Workspace/BHuman_977_From996_20261023')
        self.assertEqual(str(installer.ROOT), '/opt/booster/bhuman_match_977_from996')
        self.assertEqual(installer.SERIAL, '1424225066425')
        self.assertEqual(installer.OLD_APP, '2cfaf1b04f9bb6eea0194e27ecd813b7babe7a69f97fc7920630f5a576052628')
        self.assertEqual(installer.OLD_CONTROLLER, '7179e78185e4a093e7ba7a4cb31729ae57d9ff6da2348661380f8fc249b43f1d')
        self.assertEqual(installer.CORE, '809c4414b634ee2a1a7dccf4dd0955c732c509cbfd89c17811ef354326a9a723')
        self.assertFalse(hasattr(installer, 'PROTECTED_COUNT'))

    def test_receipt_and_backup_scope(self):
        self.assertEqual(installer.BACKUP.name, 'official_mode_backup_20261024_01')
        self.assertEqual(installer.RECEIPT.name, 'OFFICIAL_MODE_RECEIPT_20261024.json')
        self.assertEqual(installer.RELEASE.name, 'OFFICIAL_MODE_RELEASE_METADATA_20261024.json')
        self.assertEqual(set(installer.TARGETS), {'bhuman', 'match_control.py', 'start_match.sh', 'README_MATCH.md'})

    def test_sha_format_is_strict(self):
        self.assertTrue(installer.valid_sha('a' * 64))
        for invalid in ('A' * 64, 'a' * 63, 'a' * 65, 'g' * 64, None, 0, ['a'] * 64):
            with self.subTest(value=invalid):
                self.assertFalse(installer.valid_sha(invalid))


class PayloadTests(unittest.TestCase):
    def setUp(self):
        self.payload = payload_fixture()
        self.directory_mock = mock.patch.object(installer, 'directory', lambda path: None)
        self.sha_mock = mock.patch.object(installer, 'sha', lambda path: digest(path.content))
        self.directory_mock.start()
        self.sha_mock.start()
        self.addCleanup(self.directory_mock.stop)
        self.addCleanup(self.sha_mock.stop)

    def validate(self, pin=None):
        return installer.validate_payload(self.payload, pin or digest(self.payload['manifest.json'].content))

    def test_valid_payload_uses_manifest_app_not_hardcoded_candidate(self):
        result = self.validate()
        self.assertEqual(result['bhuman'], digest(self.payload['bhuman'].content))
        self.assertNotEqual(result['bhuman'], installer.OLD_APP)

    def test_wrong_robot_serial_previous_app_or_app_rejected(self):
        mutations = ({'robot': 642}, {'serial': '1422925046977'}, {'previous_app_sha256': 'f' * 64},
                     {'app_sha256': 'f' * 64})
        for changes in mutations:
            with self.subTest(changes=changes):
                self.payload = payload_fixture()
                rewrite_metadata(self.payload, **changes)
                with self.assertRaisesRegex(RuntimeError, 'metadata identity/version'):
                    self.validate()

    def test_external_manifest_pin_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'external manifest pin differs'):
            self.validate('0' * 64)

    def test_relative_payload_rejected(self):
        self.payload.absolute = False
        with self.assertRaisesRegex(RuntimeError, 'absolute payload'):
            self.validate()

    def test_extra_or_missing_payload_file_rejected(self):
        self.payload.entries['unexpected'] = MemoryPath('unexpected')
        with self.assertRaisesRegex(RuntimeError, 'unexpected payload files'):
            self.validate()
        del self.payload.entries['unexpected']
        del self.payload.entries['README_MATCH.md']
        with self.assertRaisesRegex(RuntimeError, 'unexpected payload files'):
            self.validate()

    def test_modified_payload_rejected(self):
        self.payload['bhuman'].content += ' changed'
        with self.assertRaisesRegex(RuntimeError, 'payload hash differs: bhuman'):
            self.validate()

    def test_bad_manifest_hash_value_rejected(self):
        value = json.loads(self.payload['manifest.json'].content)
        value['bhuman'] = 'A' * 64
        self.payload['manifest.json'].content = json.dumps(value)
        with self.assertRaisesRegex(RuntimeError, 'payload hash differs: bhuman'):
            self.validate()

    def test_manifest_entry_set_rejected(self):
        value = json.loads(self.payload['manifest.json'].content)
        del value['README_MATCH.md']
        self.payload['manifest.json'].content = json.dumps(value)
        with self.assertRaisesRegex(RuntimeError, 'invalid manifest entries'):
            self.validate()

    def test_untrusted_ancestor_rejected(self):
        for uid, mode in ((501, stat.S_IFDIR | 0o700), (0, stat.S_IFDIR | 0o777)):
            with self.subTest(uid=uid, mode=mode):
                ancestor = MemoryPath('ancestor', directory=True)
                ancestor.info.st_uid, ancestor.info.st_mode = uid, mode
                self.payload.parents = (ancestor,)
                with self.assertRaisesRegex(RuntimeError, 'untrusted staging ancestor'):
                    self.validate()

    def test_untrusted_symlink_hardlink_and_writable_file_rejected(self):
        for field, value in (('st_uid', 501), ('st_mode', stat.S_IFREG | 0o666),
                             ('st_mode', stat.S_IFLNK | 0o600), ('st_nlink', 2)):
            with self.subTest(field=field, value=value):
                self.payload = payload_fixture()
                setattr(self.payload['bhuman'].info, field, value)
                with self.assertRaisesRegex(RuntimeError, 'untrusted staging file'):
                    self.validate()


class IdleTests(unittest.TestCase):
    def setUp(self):
        self.start_patch = mock.patch.object(installer, 'no_start_processes')
        self.start_check = self.start_patch.start()
        self.addCleanup(self.start_patch.stop)
        self.pending_patch = mock.patch.object(installer.os.path, 'lexists', return_value=False)
        self.pending_check = self.pending_patch.start()
        self.addCleanup(self.pending_patch.stop)
        self.state = {'phase': 'RESTORED', 'files_restored': True, 'pid': None,
                      'services': [], 'lease_candidates': []}
        self.module = SimpleNamespace(UNIT='bh977-match.service', RECOVERY='bh977-match-recovery.service',
                                      RETRY='bh977-match-recovery-retry.timer',
                                      START=Path('/memory977/START.json'), PERMIT=Path('/memory977/PERMIT.json'))
        self.units = {name: {'ActiveState': 'inactive', 'MainPID': '0', 'Job': ''}
                      for name in (self.module.UNIT, self.module.RECOVERY, self.module.RETRY)}
        self.module.Controller = lambda ops: SimpleNamespace(state=lambda: self.state)
        self.ops = SimpleNamespace(no_body=mock.Mock(), unit_state=lambda name: self.units[name],
                                   core_phase=lambda: 'RESTORED', originals=mock.Mock())

    def test_only_fully_idle_restored_state_passes(self):
        units, state = installer.idle(self.ops, self.module)
        self.assertEqual(units, self.units)
        self.assertEqual(state, self.state)
        self.ops.no_body.assert_called_once_with()
        self.ops.originals.assert_called_once_with()

    def test_each_active_actor_nonzero_pid_or_job_refused(self):
        for unit in self.units:
            for field, value in (('ActiveState', 'active'), ('MainPID', '12'), ('Job', '999')):
                with self.subTest(unit=unit, field=field):
                    old = self.units[unit][field]
                    self.units[unit][field] = value
                    with self.assertRaisesRegex(RuntimeError, 'not strictly inactive'):
                        installer.idle(self.ops, self.module)
                    self.units[unit][field] = old

    def test_service_missing_pid_refused_but_timer_allows_no_mainpid(self):
        del self.units[self.module.UNIT]['MainPID']
        with self.assertRaisesRegex(RuntimeError, 'not strictly inactive'):
            installer.idle(self.ops, self.module)
        self.units[self.module.UNIT]['MainPID'] = '0'
        del self.units[self.module.RETRY]['MainPID']
        installer.idle(self.ops, self.module)

    def test_incomplete_restore_or_residual_lease_refused(self):
        baseline = copy.deepcopy(self.state)
        for field, value in (('phase', 'RUNNING'), ('files_restored', False), ('pid', 12),
                             ('services', ['camera']), ('lease_candidates', ['camera'])):
            with self.subTest(field=field):
                self.state = copy.deepcopy(baseline)
                self.state[field] = value
                with self.assertRaisesRegex(RuntimeError, 'not fully RESTORED'):
                    installer.idle(self.ops, self.module)

    def test_pending_start_marker_or_permit_refused(self):
        self.pending_check.return_value = True
        with self.assertRaisesRegex(RuntimeError, 'pending match start/permit'):
            installer.idle(self.ops, self.module)

    def test_active_start_cli_refused_before_body_or_unit_queries(self):
        self.start_check.side_effect = RuntimeError('start actor pending')
        with self.assertRaisesRegex(RuntimeError, 'start actor pending'):
            installer.idle(self.ops, self.module)
        self.ops.no_body.assert_not_called()

    def test_unfinalized_route_refused(self):
        self.ops.core_phase = lambda: 'PINNED'
        with self.assertRaisesRegex(RuntimeError, 'not finalized'):
            installer.idle(self.ops, self.module)

    def test_body_present_refused_before_unit_queries(self):
        self.ops.no_body.side_effect = RuntimeError('body actor present')
        self.ops.unit_state = mock.Mock(side_effect=AssertionError('must not query later'))
        with self.assertRaisesRegex(RuntimeError, 'body actor present'):
            installer.idle(self.ops, self.module)
        self.ops.unit_state.assert_not_called()



class StartActorTests(unittest.TestCase):
    def test_controller_start_absolute_and_sudo_are_refused(self):
        path = str(installer.ROOT / 'match_control.py')
        for argv in (['python3', '-I', path, 'start', '--execute', '--operator-present'],
                     ['sudo', '/usr/bin/python3', path, '--execute', 'start']):
            with self.subTest(argv=argv):
                self.assertEqual(installer.start_actor(argv), 'controller_start')

    def test_wrapper_default_and_explicit_start_are_refused(self):
        path = str(installer.PROJECT / 'scripts/start_match.sh')
        self.assertEqual(installer.start_actor(['bash', path]), 'wrapper_start')
        self.assertEqual(installer.start_actor(['bash', path, 'start']), 'wrapper_start')

    def test_other_targets_status_and_substrings_do_not_match(self):
        controller = str(installer.ROOT / 'match_control.py')
        wrapper = str(installer.PROJECT / 'scripts/start_match.sh')
        cases = (['python3', controller, 'status', '--execute'],
                 ['bash', wrapper, 'status'],
                 ['python3', '/opt/booster/bhuman_match_642/match_control.py', 'start'],
                 ['echo', controller + '.copy', 'start'],
                 ['echo', 'not-a-path:' + controller, 'start'])
        for argv in cases:
            with self.subTest(argv=argv):
                self.assertIsNone(installer.start_actor(argv))

    def test_live_start_process_is_refused_without_signalling(self):
        proc = MemoryPath('proc', directory=True)
        process = MemoryPath('49126', directory=True)
        process.entries['cmdline'] = MemoryPath('cmdline',
            '\0'.join(['python3', str(installer.ROOT / 'match_control.py'), 'start', '--execute']) + '\0')
        proc.entries[process.name] = process
        with mock.patch.object(installer.os, 'getpid', return_value=9):
            with self.assertRaisesRegex(RuntimeError, '49126'):
                installer.no_start_processes(proc)

    def test_current_process_and_nonnumeric_entries_are_ignored(self):
        proc = MemoryPath('proc', directory=True)
        proc.entries['9'] = MemoryPath('9', directory=True)
        proc.entries['self'] = MemoryPath('self', directory=True)
        with mock.patch.object(installer.os, 'getpid', return_value=9):
            installer.no_start_processes(proc)

    def test_unavailable_process_inventory_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'inventory unavailable'):
            installer.no_start_processes(MemoryPath('not_proc'))

    def test_process_read_error_is_a_refusal(self):
        proc = MemoryPath('proc', directory=True)
        process = MemoryPath('49126', directory=True)
        bad = MemoryPath('cmdline')
        bad.read_bytes = mock.Mock(side_effect=PermissionError('denied'))
        process.entries['cmdline'] = bad
        proc.entries['49126'] = process
        with mock.patch.object(installer.os, 'getpid', return_value=9):
            with self.assertRaisesRegex(RuntimeError, 'cannot inspect process 49126'):
                installer.no_start_processes(proc)


class InstallEarlyRefusalTests(unittest.TestCase):
    def test_nonroot_refuses_before_payload_or_hardware_read(self):
        with mock.patch.object(installer.os, 'geteuid', return_value=501), \
             mock.patch.object(installer, 'validate_payload') as validate, \
             mock.patch.object(Path, 'read_bytes', side_effect=AssertionError('hardware access forbidden')):
            with self.assertRaisesRegex(RuntimeError, 'wrong hardware/root'):
                installer.install(Path('/memory977'), 'a' * 64)
            validate.assert_not_called()

    def test_wrong_hardware_refuses_before_payload(self):
        with mock.patch.object(installer.os, 'geteuid', return_value=0), \
             mock.patch.object(Path, 'read_bytes', return_value=b'1422925046977\0'), \
             mock.patch.object(installer, 'validate_payload') as validate:
            with self.assertRaisesRegex(RuntimeError, 'wrong hardware/root'):
                installer.install(Path('/memory977'), 'a' * 64)
            validate.assert_not_called()

    def test_old_version_mismatch_refuses_before_import(self):
        with mock.patch.object(installer.os, 'geteuid', return_value=0), \
             mock.patch.object(Path, 'read_bytes', return_value=installer.SERIAL.encode()), \
             mock.patch.object(installer, 'validate_payload', return_value={}), \
             mock.patch.object(installer, 'sha', return_value='0' * 64), \
             mock.patch.object(installer, 'load') as load:
            with self.assertRaisesRegex(RuntimeError, 'installed version/core changed'):
                installer.install(Path('/memory977'), 'a' * 64)
            load.assert_not_called()


if __name__ == '__main__':
    unittest.main()
