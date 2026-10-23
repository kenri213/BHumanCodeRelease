"""Offline target/freeze/installer boundaries only; no robot or SDK access."""
import ast
from contextlib import redirect_stdout
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import patch

import match_control as control
import route_transaction as routes

HERE = Path(__file__).resolve().parent


def module(name):
    spec = importlib.util.spec_from_file_location('from996_' + name, HERE / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class Target977IdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.install = module('install_update')

    def test_exact_soc_and_new_project_control_units(self):
        self.assertEqual(control.SERIAL, '1424225066425')
        self.assertEqual(routes.SERIAL, control.SERIAL)
        self.assertEqual(str(control.PROJECT), '/home/booster/Workspace/BHuman_977_From996_20261023')
        self.assertEqual(str(control.ROOT), '/opt/booster/bhuman_match_977_from996')
        self.assertEqual(control.APP_ARGS, ['-c', str(control.PROJECT), '--k1-977-match'])
        self.assertEqual(routes.TRIAL_ROOT, control.ROOT)
        self.assertEqual(set(control.UNITS), {'bh977-from996-match.service', 'bh977-from996-match-recovery.service',
                                            'bh977-from996-match-recovery-retry.timer'})
        self.assertEqual(self.install.PROJECT, control.PROJECT)
        self.assertEqual(self.install.ROOT, control.ROOT)
        self.assertEqual(self.install.SERIAL, control.SERIAL)
        self.assertEqual(set(self.install.TARGETS), {'bhuman', 'match_control.py', 'start_match.sh', 'README_MATCH.md'})

    def test_role_is_977_goalkeeper_not_996_player2(self):
        text = (HERE / 'README_MATCH.md').read_text()
        self.assertIn('114队 / 1号守门员', text)
        self.assertNotIn('115队 / 2号', text)

    def test_unfrozen_controller_rejects_before_platform_or_target_access(self):
        with patch.object(control, 'RELEASE_FROZEN', False), \
                patch.object(control.platform, 'system', side_effect=AssertionError('host access')):
            with self.assertRaisesRegex(control.Rejected, 'UNFINALIZED'):
                control.LinuxOps()

    def test_unfrozen_route_rejects_before_uid_or_target_access(self):
        with patch.object(routes, 'POLICY_FROZEN', False), \
                patch.object(routes.os, 'geteuid', side_effect=AssertionError('host access')):
            with self.assertRaisesRegex(routes.TrialError, 'UNFINALIZED'):
                routes.Trial(Path('/'), live=True)

    def test_unpinned_update_rejects_before_staging_access(self):
        with patch.object(self.install, 'directory', side_effect=AssertionError('target access')):
            with self.assertRaisesRegex(RuntimeError, 'external SHA pin'):
                self.install.validate_payload(Path('/not-used'), 'not-used')

    def test_plan_does_not_instantiate_real_ops(self):
        with patch.object(control, 'LinuxOps', side_effect=AssertionError('real ops')), redirect_stdout(io.StringIO()):
            self.assertEqual(control.main([]), 0)
            self.assertEqual(control.main(['start', '--execute']), 2)

    def test_six_resources_and_ninety_second_service_budget_unchanged(self):
        self.assertEqual(control.LEASE_SERVICES, ('booster_camera', 'ros.camera', 'ros.booster.video_stream',
                         'ros.booster.vision_detection', 'ros.booster.auto_handeye_calib', 'lui'))
        self.assertEqual(control.SERVICE_BUDGET, 90)
        for forbidden in ('RuntimeMax', 'WantedBy', 'OnActiveSec'):
            self.assertNotIn(forbidden, control.UNITS[control.UNIT])
        self.assertIn('Restart=no\n', control.UNITS[control.UNIT])

    def test_installer_reuses_real_arm_permission_and_pair_checks_before_creation(self):
        text = (HERE / 'install_update.py').read_text()
        idle = text[text.index('def idle('):text.index('\ndef policy_snapshot')]
        self.assertIn('ops.originals()', idle)
        install = text[text.index('def install('):text.index('\ndef main(')]
        self.assertLess(install.index('ops.preflight(); ops.old_trial_idle()'), install.index('BACKUP.mkdir('))
        self.assertLess(install.index('idle(ops, old)'), install.index('BACKUP.mkdir('))
        self.assertNotIn('ChangeMode', text)
        self.assertNotIn("'systemctl', 'start'", text)

    def test_old_977_hook_and_state_are_checked_not_silently_deleted(self):
        text = (HERE / 'match_control.py').read_text()
        section = text[text.index('    def old_trial_idle(self):'):text.index('    def preflight(self):')]
        for required in ('bh977-match.conf', 'bh977-match.service', 'MATCH_STATE.json', 'RESTORED', 'BOOT_RESTORED'):
            self.assertIn(required, section)
        for forbidden in ('unlink(', 'daemon_stop(', 'force_damping(', 'os.remove('):
            self.assertNotIn(forbidden, section)

    def test_wording_matches_frozen_996_algorithm_not_new_head_or_auto_features(self):
        text = (HERE / 'README_MATCH.md').read_text()
        self.assertIn('996实际37a2c4ba', text)
        self.assertIn('没有AUTO断联连续性例外', text)
        self.assertIn('不迁移977头隔离', text)
        self.assertIn('114队 / 1号守门员', text)
        self.assertIn('仅192.168.50.37', text)


if __name__ == '__main__':
    unittest.main()
