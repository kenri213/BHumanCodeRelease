"""Synthetic local files and subprocess mocks; never imports robot controllers."""
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


SOURCE = Path(__file__).resolve().parents[2] / 'Tools/K1Commissioning/check_match_release.py'
SPEC = importlib.util.spec_from_file_location('match_release_preflight', SOURCE)
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


class ReleasePreflightTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.release = self.root / 'release'
        self.repo = self.root / 'repo'
        self.app = self.root / 'bhuman'
        self.app.write_bytes(b'\x7fELFsynthetic test bytes, never executable')
        self.release.mkdir()
        (self.release / 'route_transaction.py').write_text('# not imported\n')
        self.write_pins()
        for name in ('start_match.sh', 'test_match_control.py'):
            (self.release / name).write_text('# synthetic\n')
        self.write_ip('192.168.50.37')
        for name in preflight.REPO_TESTS:
            path = self.repo / 'Tests/K1Commissioning' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# synthetic\n')

    def write_pins(self, extra=''):
        app_sha = hashlib.sha256(self.app.read_bytes()).hexdigest()
        core_sha = hashlib.sha256((self.release / 'route_transaction.py').read_bytes()).hexdigest()
        (self.release / 'match_control.py').write_text(
            "raise RuntimeError('controller must never be imported')\n"
            f"APP_SHA='{app_sha}'\nCORE_SHA='{core_sha}'\n" + extra)

    def write_ip(self, address):
        numeric = int(preflight.ipaddress.IPv4Address(address))
        for path, text in (
            (self.repo / preflight.SAFETY, f'constexpr unsigned allowedGameControllerAddress = {numeric:#x}u;'),
            (self.repo / preflight.MAIN, f'printf("GC_SOURCE={address}");'),
            (self.repo / preflight.GC, f'printf("allowed={address}");'),
            (self.release / 'README_MATCH.md', f'GameController: {address}\n')):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    def check(self, **kwargs):
        return preflight.check_release(self.release, self.app, self.repo, **kwargs)

    def failure_names(self, report):
        return [row['name'] for row in report['checks'] if not row['ok']]

    def test_pass_is_offline_only_and_does_not_run_tests_by_default(self):
        runner = Mock(side_effect=AssertionError('unexpected subprocess'))
        report = self.check(runner=runner)
        self.assertTrue(report['ok'])
        self.assertEqual(report['scope'], 'OFFLINE_ONLY')
        self.assertFalse(report['live_accepted'])
        self.assertEqual(report['tests'], [])
        runner.assert_not_called()

    def test_address_is_derived_not_hardcoded(self):
        self.write_ip('10.12.34.56')
        report = self.check()
        self.assertTrue(report['ok'])
        self.assertEqual(report['checks'][-1]['detail']['allowed_game_controller'], '10.12.34.56')

    def test_wrong_app_hash_fails_closed(self):
        self.app.write_bytes(b'\x7fELFchanged')
        self.assertIn('APP_SHA', self.failure_names(self.check()))

    def test_wrong_core_hash_fails_closed(self):
        (self.release / 'route_transaction.py').write_text('# changed')
        self.assertIn('CORE_SHA', self.failure_names(self.check()))

    def test_non_elf_is_not_accepted_even_with_matching_pin(self):
        self.app.write_bytes(b'not an elf')
        self.write_pins()
        self.assertIn('APP_SHA', self.failure_names(self.check()))

    def test_each_diagnostic_or_document_ip_mismatch_fails(self):
        for path in (self.release / 'README_MATCH.md', self.repo / preflight.MAIN, self.repo / preflight.GC):
            original = path.read_text()
            path.write_text(original.replace('192.168.50.37', '192.168.50.29'))
            self.assertIn('GC_source_and_documentation', self.failure_names(self.check()))
            path.write_text(original)

    def test_missing_required_file_fails(self):
        (self.release / 'test_match_control.py').unlink()
        self.assertEqual(self.failure_names(self.check()), ['required_files'])

    def test_unfinalized_helper_pin_is_rejected(self):
        self.write_pins("MODE_QUERY_SHA='TODO'\n")
        self.assertIn('literal_SHA_pins', self.failure_names(self.check()))

    def test_computed_sha_assignment_is_not_executed(self):
        (self.release / 'match_control.py').write_text('APP_SHA = execute_robot()\n')
        self.assertIn('literal_SHA_pins', self.failure_names(self.check()))

    def test_absolute_paths_required(self):
        report = preflight.check_release(Path('relative'), self.app, self.repo)
        self.assertEqual(self.failure_names(report), ['required_files'])

    def test_explicit_tests_use_only_python_unittest_without_shell(self):
        runner = Mock(return_value=SimpleNamespace(returncode=0, stdout='', stderr='OK'))
        report = self.check(run_tests=True, runner=runner)
        self.assertTrue(report['ok'])
        self.assertEqual(runner.call_count, 2)
        for call in runner.call_args_list:
            self.assertEqual(call.args[0][:3], [sys.executable, '-m', 'unittest'])
            self.assertNotIn('shell', call.kwargs)
            self.assertEqual(call.kwargs['timeout'], 300)

    def test_any_test_failure_or_timeout_fails_release(self):
        for first in (SimpleNamespace(returncode=1, stdout='', stderr='failed assertion'),
                      subprocess.TimeoutExpired('unittest', 300)):
            runner = Mock(side_effect=[first, SimpleNamespace(returncode=0, stdout='', stderr='')])
            report = self.check(run_tests=True, runner=runner)
            self.assertFalse(report['ok'])
            self.assertEqual(report['status'], 'FAIL')
            self.assertFalse(report['tests'][0]['ok'])

    def test_no_tests_run_for_inconsistent_release(self):
        runner = Mock(side_effect=AssertionError('unexpected subprocess'))
        self.app.write_bytes(b'\x7fELFchanged')
        report = self.check(run_tests=True, runner=runner)
        self.assertFalse(report['ok'])
        runner.assert_not_called()


if __name__ == '__main__':
    unittest.main()
