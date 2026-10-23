"""Replay production Controller methods; no SSH/services/SDK/body execution."""
import unittest
from unittest.mock import patch

import match_control as control
from test_match_control import FakeOps


class CrossBootRecoveryTests(unittest.TestCase):
    def stale_recovery(self):
        ops = FakeOps()
        driver = control.Controller(ops)
        ops.driver = driver
        driver.start(True)
        state = driver.state()
        state.update(phase='RECOVERING', files_restored=True, body_start_intent=True, daemon_stop_intent=True,
                     services=['ros.camera', 'lui'], lease_candidates=list(control.LEASE_SERVICES))
        ops.files[control.STATE] = state
        ops.boot, ops.mode, ops.app, ops.daemon, ops.phase = 'boot2', 3, False, True, 'RESTORED'
        ops.targets, ops.events = ['old', 'old'], []
        return ops, driver

    def assert_no_new_boot_actuator_or_service_writes(self, ops):
        for forbidden in ('force_damping', 'stop_app', 'daemon_stop', 'daemon_start', 'restore_services',
                          'pause_services', 'start_app', 'core:apply', 'query_only_mode'):
            self.assertNotIn(forbidden, ops.events)
        self.assertEqual(ops.mode, 3)
        self.assertFalse(ops.app)
        self.assertTrue(ops.daemon)
        self.assertEqual(ops.targets, ['old', 'old'])

    def test_old_recovering_body_intent_cannot_damp_new_boot_stock_custom_after_boot_guard(self):
        ops, driver = self.stale_recovery()
        driver.boot_guard()
        driver.rollback()
        self.assert_no_new_boot_actuator_or_service_writes(ops)
        self.assertEqual(ops.events.count('query_current_mode_read_only'), 1)
        state = driver.state()
        self.assertEqual(state['phase'], 'BOOT_RESTORED')
        self.assertFalse(state['body_start_intent'])
        self.assertFalse(state['daemon_stop_intent'])
        self.assertEqual(state['services'], [])
        self.assertEqual(state['previous_boot_reconciliation']['observed_mode']['mode'], 3)
        self.assertEqual(state['previous_boot_reconciliation']['state_boot_id'], 'boot1')
        self.assertEqual(state['previous_boot_reconciliation']['current_boot_id'], 'boot2')

    def test_rollback_itself_rechecks_boot_before_any_stop_or_damping(self):
        ops, driver = self.stale_recovery()
        driver.rollback() # Do not rely on boot_guard having run first.
        self.assert_no_new_boot_actuator_or_service_writes(ops)
        self.assertIn('query_current_mode_read_only', ops.events)
        self.assertEqual(driver.state()['phase'], 'BOOT_RESTORED')

    def test_old_intent_does_not_stop_a_different_current_boot_body(self):
        ops, driver = self.stale_recovery()
        ops.app = True
        with self.assertRaisesRegex(control.Rejected, 'actor active'):
            driver.rollback()
        self.assertTrue(ops.app)
        self.assertEqual(ops.mode, 3)
        self.assertNotIn('stop_app', ops.events)
        self.assertNotIn('force_damping', ops.events)
        self.assertNotIn('daemon_stop', ops.events)

    def test_online_new_boot_bad_originals_refuse_without_force_damping_or_service_stop(self):
        ops, driver = self.stale_recovery()
        ops.targets[0] = 'drift'
        with self.assertRaisesRegex(control.Rejected, 'not originals'):
            driver.rollback()
        for name in ('force_damping', 'stop_app', 'daemon_stop', 'query_current_mode_read_only'):
            self.assertNotIn(name, ops.events)
        self.assertEqual(ops.mode, 3)

    def test_same_boot_owned_app_rollback_still_damps_and_restores(self):
        ops = FakeOps()
        driver = control.Controller(ops)
        ops.driver = driver
        driver.start(True)
        ops.events = []
        driver.rollback()
        self.assertEqual(ops.mode, 0)
        self.assertEqual(ops.events.count('force_damping'), 2)
        self.assertEqual(driver.state()['phase'], 'RESTORED')

    def test_dangling_previous_hook_is_rejected_not_treated_as_absent(self):
        ops = object.__new__(control.LinuxOps)
        ops.unit_idle = lambda _: None
        ops.read = lambda _: None
        with patch.object(control.os.path, 'lexists', return_value=True), \
                patch.object(control.Path, 'exists', return_value=False):
            with self.assertRaisesRegex(control.Rejected, 'boot hook'):
                ops.old_trial_idle()


if __name__ == '__main__':
    unittest.main()
