#!/usr/bin/env python3
"""Read-only local release consistency checks. Never starts the APP or a robot."""
import argparse
import ast
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import subprocess
import sys


REPO = Path(__file__).resolve().parents[2]
SAFETY = Path('Src/Libs/Platform/BoosterMatchSafety.h')
MAIN = Path('Src/Apps/Booster/Main.cpp')
GC = Path('Src/Modules/Communication/GameControllerDataProvider/GameControllerDataProvider.cpp')
RELEASE_FILES = ('match_control.py', 'route_transaction.py', 'README_MATCH.md',
                 'start_match.sh', 'test_match_control.py')
REPO_TESTS = ('test_match_main.py', 'test_match_localization_gate.py',
              'test_live_fresh_f1_guard.py', 'test_joystick_neutral_stand.py')
IP = r'(?:\d{1,3}\.){3}\d{1,3}'


def literal_pins(source):
    """Read assignments, not code: the production controller is never imported."""
    pins = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.endswith('_SHA'):
                    pins[target.id] = ast.literal_eval(node.value)
    for name in ('APP_SHA', 'CORE_SHA'):
        if name not in pins:
            raise ValueError('missing literal ' + name)
    for name, value in pins.items():
        if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
            raise ValueError('unfinalized SHA: ' + name)
    return pins


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def run_test_suite(argv, cwd, runner):
    """Only explicit Python unittest commands reach this function; no shell."""
    try:
        result = runner(argv, cwd=str(cwd), capture_output=True, text=True,
                        timeout=300, check=False)
        return {'argv': argv, 'cwd': str(cwd), 'ok': result.returncode == 0,
                'returncode': result.returncode,
                'output_tail': (result.stdout + result.stderr)[-8000:]}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {'argv': argv, 'cwd': str(cwd), 'ok': False, 'error': str(error)}


def check_release(release_root, app, repo=REPO, run_tests=False, runner=None):
    """Inspect local bytes; opt-in tests are offline mocked/unit regressions only."""
    release_root, app, repo = map(Path, (release_root, app, repo))
    report = {'scope': 'OFFLINE_ONLY', 'live_accepted': False,
              'note': 'No SSH, SDK, service action, build, APP start or hardware validation.',
              'release_root': str(release_root), 'app': str(app),
              'checks': [], 'tests': [], 'tests_requested': run_tests}

    def check(name, action):
        try:
            detail = action()
            report['checks'].append({'name': name, 'ok': True, 'detail': detail})
            return detail
        except (OSError, ValueError, SyntaxError, TypeError) as error:
            report['checks'].append({'name': name, 'ok': False, 'error': str(error)})
            return None

    def require(value, message):
        if not value:
            raise ValueError(message)

    def files():
        require(release_root.is_absolute(), '--release-root must be absolute')
        require(app.is_absolute(), '--app must be absolute')
        require(repo.is_absolute(), '--repo must be absolute')
        expected = [release_root / name for name in RELEASE_FILES]
        expected += [repo / name for name in (SAFETY, MAIN, GC)]
        expected += [repo / 'Tests/K1Commissioning' / name for name in REPO_TESTS]
        expected.append(app)
        missing = [str(path) for path in expected if not path.is_file()]
        require(not missing, 'missing files: ' + ', '.join(missing))
        return {'required_files': len(expected)}

    if check('required_files', files) is not None:
        pins = check('literal_SHA_pins', lambda: literal_pins(
            (release_root / 'match_control.py').read_text()))
        if pins is not None:
            def app_hash():
                with app.open('rb') as stream:
                    require(stream.read(4) == b'\x7fELF', 'APP is not an ELF artifact')
                actual = digest(app)
                require(actual == pins['APP_SHA'], 'APP_SHA does not match given ELF: ' + actual)
                return actual

            def core_hash():
                actual = digest(release_root / 'route_transaction.py')
                require(actual == pins['CORE_SHA'], 'CORE_SHA does not match route_transaction.py: ' + actual)
                return actual

            check('APP_SHA', app_hash)
            check('CORE_SHA', core_hash)

        def gc_consistency():
            safety = (repo / SAFETY).read_text()
            matches = re.findall(r'allowedGameControllerAddress\s*=\s*(0x[0-9a-fA-F]+|\d+)[uUlL]*\s*;', safety)
            require(len(matches) == 1, 'expected one numeric allowedGameControllerAddress')
            address = str(ipaddress.IPv4Address(int(matches[0], 0)))
            for name, path, pattern in (
                ('README_MATCH', release_root / 'README_MATCH.md', IP),
                ('Main diagnostic', repo / MAIN, r'GC_SOURCE=(' + IP + ')'),
                ('GC diagnostic', repo / GC, r'allowed=(' + IP + ')')):
                found = set(re.findall(pattern, path.read_text()))
                require(found == {address}, name + ' IP mismatch: ' + repr(sorted(found)) + ' != ' + address)
            return {'allowed_game_controller': address}

        check('GC_source_and_documentation', gc_consistency)

    if run_tests and all(item['ok'] for item in report['checks']):
        runner = runner or subprocess.run
        for cwd, argv in (
            (release_root, [sys.executable, '-m', 'unittest', 'test_match_control.py']),
            (repo, [sys.executable, '-m', 'unittest', 'discover', '-s',
                    'Tests/K1Commissioning', '-p', 'test_*.py'])):
            report['tests'].append(run_test_suite(argv, cwd, runner))
    report['ok'] = (all(item['ok'] for item in report['checks'])
                    and all(item['ok'] for item in report['tests'])
                    and (not run_tests or len(report['tests']) == 2))
    report['status'] = 'PASS' if report['ok'] else 'FAIL'
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-root', required=True, type=Path)
    parser.add_argument('--app', required=True, type=Path)
    parser.add_argument('--repo', default=REPO, type=Path)
    parser.add_argument('--run-tests', action='store_true', help='explicitly run offline unittest suites')
    args = parser.parse_args(argv)
    report = check_release(args.release_root, args.app, args.repo, args.run_tests)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
