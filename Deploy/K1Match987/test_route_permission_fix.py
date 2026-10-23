"""Synthetic filesystem transactions only; never touches /opt or a robot."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

import match_control as control
import route_transaction as routes

HERE = Path(__file__).resolve().parent
sha = lambda data: hashlib.sha256(data).hexdigest()


class PermissionFixTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve() # macOS /var is a symlink; production correctly refuses it.
        self.owner = (os.geteuid(), os.getegid())
        # Synthetic test data only: do not publish a robot's private capture
        # identity, boot record, or vendor-binary provenance in this repository.
        self.manifest = {'schema': 1, 'action': 'offline_controller_isolation_prepare_only',
                         'routing_policy': 'official_two_patch_remote_library_only',
                         'patch_location_count': 2, 'targets': {}}
        for name, (target, filename) in routes.TARGETS.items():
            self.manifest['targets'][name] = {
                'firmware_target_hint': target,
                'expected_original_sha256': '0' * 64,
                'original': {'path': 'originals/' + filename, 'sha256': '0' * 64, 'bytes': 0},
                'patched': {'path': 'patched/' + filename, 'sha256': '1' * 64, 'bytes': 0},
                'captured_source_identity': {'device': 0, 'inode': 0, 'size': 0,
                                             'mtime_ns': 0, 'ctime_ns': 0, 'mode': 0o600},
                'approved_original_metadata': {'mode': 0o755, 'uid': 0, 'gid': 0, 'bytes': 0},
                'source_identity': {'mode': 0o755},
                'approved_metadata_provenance': {
                    'scope': 'REMOTE_ORIGINAL_METADATA_READ_ONLY', 'serial': routes.SERIAL,
                    'boot_id': '00000000-0000-0000-0000-000000000000', 'diagnostic_sha256': '0' * 64},
            }
        self.policy = {}
        self.raw = {}
        self.root = self.base / str(routes.TRIAL_ROOT).lstrip('/')
        for directory in (self.root, self.root / 'bundle', self.root / 'bundle/originals', self.root / 'bundle/patched'):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory.chmod(0o700)
        serial = self.base / str(routes.SERIAL_FILE).lstrip('/')
        serial.parent.mkdir(parents=True)
        serial.write_text(routes.SERIAL)
        for name, old, new in (('device_gateway', b'rt/button_event', b'rt/button_even2'),
                               ('remote_library', b'rt/remote_controller_state', b'rt/remote_controller_stat2')):
            before, after = b'ELF_FIXTURE\x00' + old + b'\x00tail', b'ELF_FIXTURE\x00' + new + b'\x00tail'
            self.raw[name] = (before, after)
            row = self.manifest['targets'][name]
            row['expected_original_sha256'] = sha(before)
            row['original'].update(sha256=sha(before), bytes=len(before))
            row['patched'].update(sha256=sha(after), bytes=len(after))
            row['captured_source_identity'].update(size=len(before))
            row['approved_original_metadata'].update(uid=self.owner[0], gid=self.owner[1], bytes=len(before))
            self.policy[name] = {'old': sha(before), 'new': sha(after), 'patches': ((12, old, new),)}
            # Offset is derived from actual fixture bytes, not a hand-waved mock.
            self.policy[name]['patches'] = ((before.index(old), old, new),)
            for variant, raw in (('original', before), ('patched', after)):
                path = self.root / 'bundle' / row[variant]['path']
                path.write_bytes(raw)
                path.chmod(0o600)
            target = self.base / routes.TARGETS[name][0].lstrip('/')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(before)
            target.chmod(0o755)
        self.freeze_manifest()

    def freeze_manifest(self):
        raw = (json.dumps(self.manifest, sort_keys=True, indent=2) + '\n').encode()
        path = self.root / 'bundle/manifest.json'
        path.write_bytes(raw)
        path.chmod(0o600)
        self.policy['manifest_sha'] = sha(raw)
        side = self.root / 'bundle/manifest.sha256'
        side.write_text(sha(raw) + '\n')
        side.chmod(0o600)
        self.trial = routes.Trial(self.base, test_policy=self.policy)

    def assert_originals(self):
        for name in routes.TARGETS:
            target = self.base / routes.TARGETS[name][0].lstrip('/')
            self.assertEqual(target.read_bytes(), self.raw[name][0])
            self.assertEqual(target.stat().st_mode & 0o7777, 0o755)

    def real_readonly_originals(self):
        ops = object.__new__(control.LinuxOps)
        ops.module, ops.trial = routes, self.trial
        # Only policy data differs in the synthetic tiny fixture. All real
        # class methods, owner/mode checks and file I/O execute unchanged.
        from unittest.mock import patch
        with patch.object(routes, 'APPROVED', self.policy):
            ops.originals()

    def test_arm_apply_restore_with_real_755_and_private_600_preserves_permissions(self):
        self.real_readonly_originals()
        self.assertFalse(self.trial.checkpoint.exists())
        self.assertTrue(self.trial.execute('arm')['ok'])
        self.assertTrue(self.trial.execute('apply')['ok'])
        for name in routes.TARGETS:
            target = self.base / routes.TARGETS[name][0].lstrip('/')
            self.assertEqual(target.read_bytes(), self.raw[name][1])
            self.assertEqual(target.stat().st_mode & 0o7777, 0o755)
        self.assertTrue(self.trial.execute('restore')['ok'])
        self.assert_originals()

    def test_legacy_private_600_approved_manifest_is_rejected_before_checkpoint(self):
        for row in self.manifest['targets'].values():
            row['source_identity'] = {'mode': 0o600}
            row['approved_original_metadata']['mode'] = 0o600
        self.freeze_manifest()
        with self.assertRaisesRegex(routes.TrialError, 'approved original metadata'):
            self.trial.execute('arm')
        self.assertFalse(self.trial.checkpoint.exists())
        self.assert_originals()

    def test_readonly_preflight_rejects_actual_live_600_without_a_write(self):
        target = self.base / routes.TARGETS['device_gateway'][0].lstrip('/')
        target.chmod(0o600)
        before = target.read_bytes()
        with self.assertRaisesRegex(routes.TrialError, 'permissions drift'):
            self.real_readonly_originals()
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(target.stat().st_mode & 0o7777, 0o600)
        self.assertFalse(self.trial.checkpoint.exists())

    def test_bad_owner_or_extra_metadata_is_rejected(self):
        for bad in ({'uid': self.owner[0] + 1}, {'gid': self.owner[1] + 1}, {'mode': 0o777},
                    {'mode': True}, {'bytes': 999}, {'extra': 1}):
            with self.subTest(bad=bad):
                old = copy.deepcopy(self.manifest)
                self.manifest['targets']['device_gateway']['approved_original_metadata'].update(bad)
                self.freeze_manifest()
                with self.assertRaisesRegex(routes.TrialError, 'approved original metadata'):
                    self.trial.execute('arm')
                self.assertFalse(self.trial.checkpoint.exists())
                self.manifest = old

    def test_missing_capture_or_provenance_is_rejected(self):
        for name in ('captured_source_identity', 'approved_metadata_provenance', 'approved_original_metadata'):
            with self.subTest(field=name):
                old = copy.deepcopy(self.manifest)
                del self.manifest['targets']['device_gateway'][name]
                self.freeze_manifest()
                with self.assertRaises(routes.TrialError):
                    self.trial.execute('arm')
                self.assertFalse(self.trial.checkpoint.exists())
                self.manifest = old

    def test_restore_rejects_forged_checkpoint_mode(self):
        self.trial.execute('arm')
        state = json.loads(self.trial.checkpoint.read_text())
        state['targets']['device_gateway']['original_metadata']['mode'] = 0o600
        self.trial.checkpoint.write_text(json.dumps(state))
        with self.assertRaisesRegex(routes.TrialError, 'original metadata mismatch'):
            self.trial.execute('restore')
        self.assert_originals()

    def test_readonly_validation_really_checks_pairs_not_only_sha(self):
        row = self.manifest['targets']['remote_library']
        target = self.root / 'bundle' / row['patched']['path']
        target.write_bytes(target.read_bytes() + b'bad')
        with self.assertRaisesRegex(routes.TrialError, 'hash or size mismatch'):
            self.real_readonly_originals()
        self.assertFalse(self.trial.checkpoint.exists())

    def test_wording_matches_996_stand_hold_not_later_auto_continuity(self):
        text = (HERE / 'start_match.sh').read_text()
        self.assertNotIn('手柄正常断联不再锁死自动比赛', text)
        self.assertIn('不论之前遥控或自动', text)
        self.assertIn('旧杆量不续跑', text)


if __name__ == '__main__':
    unittest.main()
