"""Offline supervisor/release contracts; no SDK, SSH, APP or physical F1 test."""
import ast
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import match_control as m
from test_match_control import FakeOps

HERE = Path(__file__).resolve().parent


class F1ResidentReleaseTests(unittest.TestCase):
    def driver(self):
        ops = FakeOps()
        driver = m.Controller(ops)
        ops.driver = driver
        return ops, driver

    def test_unknown_new_app_refuses_before_platform_files_or_service_access(self):
        with patch.object(m, 'APP_SHA', None), patch.object(m.platform, 'system', side_effect=AssertionError('platform access')):
            with self.assertRaisesRegex(m.Rejected, 'UNFINALIZED 987 release pins: APP_SHA'):
                m.LinuxOps()

    def test_f1_mode_damping_does_not_look_like_an_app_exit_to_supervisor(self):
        ops, driver = self.driver()
        with redirect_stdout(io.StringIO()):
            driver.start(True)
        original_state = driver.state()
        ops.mode = 0  # Synthetic native Emergency/Damping, same process still running.
        ops.events = []
        with redirect_stdout(io.StringIO()):
            result = driver.start(True)
        self.assertTrue(result['reused_running_process'])
        self.assertTrue(ops.app)
        self.assertEqual(driver.state(), original_state)
        self.assertEqual(ops.events, ['wait_cognition'])
        self.assertEqual(ops.mode, 0)  # Repeat-start is not an F1/Unstiff command.

    def test_return_from_f1_mode_keeps_same_owner_and_does_not_restart_services(self):
        ops, driver = self.driver()
        with redirect_stdout(io.StringIO()):
            driver.start(True)
        state = driver.state()
        for mode in (0, 3, 0, 3):
            ops.mode = mode
            ops.events = []
            with redirect_stdout(io.StringIO()):
                result = driver.start(True)
            self.assertTrue(result['reused_running_process'])
            self.assertEqual(driver.state()['pid'], state['pid'])
            self.assertEqual(driver.state()['id'], state['id'])
            self.assertEqual(ops.events, ['wait_cognition'])
            self.assertEqual(ops.mode, mode)

    def test_explicit_stop_still_exits_and_recovers_owned_resources(self):
        ops, driver = self.driver()
        with redirect_stdout(io.StringIO()):
            driver.start(True)
        ops.mode = 0
        ops.events = []
        driver.stop()
        self.assertFalse(ops.app)
        self.assertIn('stop_app', ops.events)
        self.assertIn('core:restore', ops.events)
        self.assertEqual(driver.state()['phase'], 'RESTORED')

    def test_supervisor_logic_exactly_preserves_987_base_no_crossboot_migration(self):
        tree = ast.parse((HERE / 'match_control.py').read_text())
        nodes = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef))
                 and node.name in ('Controller', 'LinuxOps', 'main')]
        frozen = ast.dump(ast.Module(body=nodes, type_ignores=[]), include_attributes=False).encode()
        self.assertEqual(hashlib.sha256(frozen).hexdigest(),
                         '17a70af41f3843450d6b26938bc65ae80cf4a008619eedd60c0ff1ef8f5d6049')

    def test_987_identity_helpers_audio_and_ninety_second_budget_are_not_977_values(self):
        self.assertEqual(m.SERIAL, '1424725033885')
        self.assertEqual(str(m.PROJECT), '/home/booster/Workspace/BHuman_987_Match_20261023')
        self.assertEqual(str(m.ROOT), '/opt/booster/bhuman_match_987')
        self.assertEqual(m.CORE_SHA, '0cf7b635e2138ad34b195efdd97e96f08b48f8ff521ce15b6b55feba72b0e78a')
        self.assertEqual(m.MODE_QUERY_SHA, '9983299bf74aa0839b163e9e314dc31de6cc830f9ffe739431e9f3dde3f19840')
        self.assertEqual(m.DAMPING_SHA, '94ac4ff09c33faa2290cc32c4149301fd0b7322139e4732aede9a192bb619d5e')
        self.assertEqual(m.SERVICE_BUDGET, 90)
        self.assertEqual(m.PULSE_SOURCE, 'alsa_input.usb-NationalChip_NationalChip_Uac_Speaker_20161029-00.multichannel-input')
        self.assertEqual(m.APP_ARGS, ['-c', str(m.PROJECT), '--k1-987-match'])

    def test_unit_exits_only_from_real_app_exit_or_explicit_stop_not_button_timer(self):
        unit = m.UNITS[m.UNIT]
        self.assertIn('KillSignal=SIGINT', unit)
        self.assertIn('Restart=no', unit)
        self.assertNotIn('RuntimeMaxSec', unit)
        self.assertNotIn('OnActiveSec', unit)
        self.assertNotIn(' -w', unit)
        self.assertIn('OnSuccess=' + m.RECOVERY, unit)
        self.assertIn('OnFailure=' + m.RECOVERY, unit)

    def test_current_operator_text_distinguishes_f1_unstiff_x_and_project_stop(self):
        start = (HERE / 'start_match.sh').read_text()
        readme = (HERE / 'README_MATCH.md').read_text()
        for text in (start, readme):
            self.assertIn('单按X不再全局', text)
            self.assertIn('RB+X保留原生', text)
            self.assertIn('Unstiff', text)
            self.assertIn('不自动Prepare或起身', text)
            self.assertIn('无需第二次WALK', text)
            self.assertIn('stop', text)
            self.assertNotIn('X/F1', text)
        self.assertIn('APP常驻', start)
        self.assertIn('SIGINT/SIGTERM', readme)
        self.assertIn('清除旧Pickup锁和待AUTO票', readme)
        self.assertIn('不增加回场状态机', readme)

    def test_current_release_does_not_inherit_base_physical_acceptance(self):
        release = json.loads((HERE / 'RELEASE.json').read_text())
        self.assertEqual(release['base_commit'], '48ce90279f27f8a51680661f448184fb2f2ecb9b')
        self.assertEqual(release['f1_source_commit'], '1cf8bf14a6cc079999b807a946d6df31c528ed16')
        self.assertEqual(release['deployment_status'], 'not_deployed')
        self.assertEqual(release['physical_validation'], 'not_tested')
        self.assertNotIn('pickup_reentry', release['validation'])
        self.assertEqual(release['base_history']['pickup_reentry']['evidence_type'], 'operator_reported')
        self.assertFalse(release['complete_installer'])
        self.assertFalse(release['cross_boot_recovery_changes_migrated'])
        self.assertFalse((HERE / 'manifest.json').exists())
        self.assertFalse((HERE / 'bundle').exists())

    def test_release_matches_new_app_pin_or_is_explicitly_unfinalized(self):
        release = json.loads((HERE / 'RELEASE.json').read_text())
        self.assertEqual(release['app_sha256'], m.APP_SHA)
        self.assertEqual(release['route_core_sha256'], m.CORE_SHA)
        if m.APP_SHA is None:
            self.assertEqual(release['artifact_status'], 'UNFINALIZED')
            self.assertIsNone(release['controller_sha256'])
            self.assertEqual(release['validation']['fresh_arm64_build'], 'pending')
        else:
            self.assertEqual(release['artifact_status'], 'LOCAL_FROZEN')
            self.assertNotEqual(m.APP_SHA, release['base_history']['app_sha256'])
            self.assertEqual(release['controller_sha256'], hashlib.sha256((HERE / 'match_control.py').read_bytes()).hexdigest())
            self.assertEqual(release['validation']['fresh_arm64_build'], 'passed')


if __name__ == '__main__':
    unittest.main()
