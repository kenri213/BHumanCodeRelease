"""CPU-only updater and operator contract checks, not a physical mode test."""
import ast
import copy
import hashlib
import json
from pathlib import Path
import unittest

import install_update as update
import match_control as control

HERE = Path(__file__).resolve().parent


class OfficialModeReleaseTests(unittest.TestCase):
    def test_operator_text_separates_resident_f1_native_rb_x_and_explicit_exit(self):
        text = (HERE / 'start_match.sh').read_text()
        self.assertIn('实体F1进入Emergency/Damping但不退出APP', text)
        self.assertIn('单按X不再全局终止APP', text)
        self.assertIn('RB+X保留官方映射F1/unstiff语义', text)
        self.assertIn('同一APP内长按F1松开可回Unstiff', text)
        self.assertIn('不自动Prepare或起身', text)
        self.assertIn('退出整个项目用本脚本 stop', text)
        self.assertNotIn('X/F1停止', text)
        self.assertIn('无需RB+B', text)
        self.assertIn('无需RB+Y', text)

    def test_core_helpers_units_and_no_fixed_runtime_unchanged(self):
        self.assertEqual(control.CORE_SHA, update.CORE)
        self.assertEqual(control.SERIAL, update.SERIAL)
        self.assertEqual(control.SERVICE_BUDGET, 90)
        self.assertIn('Restart=no\n', control.UNITS[control.UNIT])
        self.assertNotIn('RuntimeMax', control.UNITS[control.UNIT])
        self.assertEqual(hashlib.sha256((HERE / 'route_transaction.py').read_bytes()).hexdigest(), update.CORE)

    def test_updater_has_no_body_sdk_or_service_mutation_calls(self):
        tree = ast.parse((HERE / 'install_update.py').read_text())
        calls = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)}
        self.assertFalse(calls & {'force_damping', 'stop_app', 'start_app', 'daemon_stop', 'daemon_start',
                                  'pause_services', 'restore_services', 'ChangeMode'})

    def fixture(self):
        old = {'app_sha256': update.OLD_APP, 'models_verified': {'kept': True}, 'physical_acceptance': 'NOT RUN'}
        inventory = {'bin/bhuman': update.OLD_APP, 'match_control.py': update.OLD_CONTROLLER,
                     'scripts/start_match.sh': '1' * 64, 'README_MATCH.md': '2' * 64,
                     'Config/test.cfg': '3' * 64, 'lib/runtime.so': '4' * 64,
                     'BUILD_PROVENANCE.json': update.HISTORICAL_METADATA['BUILD_PROVENANCE.json']}
        payload = {'bhuman': 'a' * 64, 'match_control.py': 'b' * 64,
                   'start_match.sh': 'c' * 64, 'README_MATCH.md': 'd' * 64}
        return old, inventory, payload, {'source_build_report_sha256': 'e' * 64,
                                         'frozen_launcher_receipt_sha256': 'f' * 64}

    def test_metadata_rebase_only_updates_changed_targets_and_provenance(self):
        old, inventory, payload, release = self.fixture()
        untouched = copy.deepcopy((old, inventory))
        provenance, current = update.updated_metadata(old, inventory, payload, release)
        self.assertEqual((old, inventory), untouched)
        self.assertEqual(current['Config/test.cfg'], inventory['Config/test.cfg'])
        self.assertEqual(current['lib/runtime.so'], inventory['lib/runtime.so'])
        self.assertEqual(current['bin/bhuman'], payload['bhuman'])
        self.assertEqual(current['match_control.py'], payload['match_control.py'])
        self.assertEqual(provenance['models_verified'], old['models_verified'])
        self.assertFalse(provenance['body_started'])
        self.assertEqual(provenance['physical_acceptance'], 'NOT RUN')
        raw = (json.dumps(provenance, sort_keys=True, indent=2) + '\n').encode()
        self.assertEqual(current['BUILD_PROVENANCE.json'], hashlib.sha256(raw).hexdigest())

    def test_metadata_rebase_rejects_wrong_prior_or_missing_source_evidence(self):
        for field in ('app_sha256', 'missing_source', 'missing_manifest_target'):
            old, inventory, payload, release = self.fixture()
            if field == 'app_sha256': old[field] = '0' * 64
            elif field == 'missing_source': release.clear()
            else: del inventory['scripts/start_match.sh']
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                update.updated_metadata(old, inventory, payload, release)

    def test_metadata_backups_are_pinned_originals_not_changed_or_synthetic_evidence(self):
        self.assertEqual(set(update.OLD_TARGET_SHAS), set(update.TARGETS))
        self.assertEqual(update.OLD_TARGET_SHAS['bhuman'], update.OLD_APP)
        self.assertEqual(update.OLD_TARGET_SHAS['match_control.py'], update.OLD_CONTROLLER)
        self.assertEqual(update.HISTORICAL_METADATA['DEPLOYMENT_VERIFIED.json'], update.PREVIOUS_RECEIPT_SHA)
        text = (HERE / 'install_update.py').read_text()
        for filename in ('BUILD_PROVENANCE.json', 'payload_manifest.json', 'DEPLOYMENT_VERIFIED.json'):
            self.assertIn(filename, text)
        self.assertIn('historical_camera_cache_evidence', text)
        self.assertIn('cognition_or_cold_start_certified=False', text)
        self.assertIn('acceptance_scope=', text)


if __name__ == '__main__':
    unittest.main()
