"""Installer tests: only extracted functions/blocks run, with side effects mocked.

Never execute the real installer. Mock commands record labels, not arguments,
so inherited upstream credential literals cannot appear in the test output.
"""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
CLIENT = ROOT / "Install/installRobot"
REMOTE = ROOT / "Install/Files/install"


def function(source, name):
    match = re.search(r"^" + re.escape(name) + r"\(\) \{\n.*?^\}\n", source, re.M | re.S)
    if not match:
        raise AssertionError("Missing shell function: " + name)
    return match.group(0)


def shell(script, args=()):
    return subprocess.run(["bash", "-s", "--", *args], input="set -e\n" + script,
                          text=True, capture_output=True, check=False)


class InstallCameraBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = CLIENT.read_text()
        cls.remote = REMOTE.read_text()

    def test_shell_syntax(self):
        for path in (CLIENT, REMOTE):
            result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_validation_precedes_setup_and_remote_side_effects(self):
        self.assertLess(self.client.index('parseOptions "$@" ""'),
                        self.client.index('source "${bhDir}/Make/Common/setupSSH"'))
        self.assertLess(self.remote.index('parseOptions "$@"'),
                        self.remote.index("# Stop bhuman service"))

    def test_remote_option_parser(self):
        cases = (
            (("TestK1",), "x5|TestK1|||"),
            (("--camera", "realsense", "TestK1", "Asia/Shanghai"),
             "realsense|TestK1|Asia/Shanghai||"),
            (("-d", "--camera", "realsense", "TestK1"), "realsense|TestK1||1|"),
            (("--camera", "x5", "-D", "TestK1"), "x5|TestK1||1|1"),
        )
        for args, expected in cases:
            with self.subTest(args=args):
                result = shell(
                    "cameraBackend=x5; DELETE=; DELETE_ALL=; robotName=; timezone=\n"
                    + function(self.remote, "parseOptions") + '\nparseOptions "$@"\n'
                    + 'printf "%s|%s|%s|%s|%s" "$cameraBackend" "$robotName" '
                    '"$timezone" "$DELETE" "$DELETE_ALL"\n', args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected)

    def test_invalid_camera_is_rejected_before_remote_calls(self):
        for source, sentinel in ((self.client, ' ""'), (self.remote, "")):
            for args in (("--camera",), ("--camera", "unknown", "TestK1")):
                with self.subTest(client=bool(sentinel), args=args):
                    result = shell(
                        'cameraBackend=x5; DELETE=; DELETE_ALL=\n'
                        'ssh() { echo "unexpected SSH"; exit 99; }\n'
                        'ping() { echo "unexpected ping"; exit 99; }\n'
                        'sudo() { echo "unexpected sudo"; exit 99; }\n'
                        + function(source, "parseOptions")
                        + '\nparseOptions "$@"' + sentinel + '\n', args)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("--camera requires x5 or realsense", result.stderr)
                    self.assertEqual(result.stdout, "")

    def test_client_options_keep_x5_default_and_update_realsense(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            robot = root / "Config/Robots/TestK1"
            robot.mkdir(parents=True)
            (robot / "network.cfg").write_text('lan = "198.51.100.42";\n')
            robots = root / "robots.cfg"
            robots.write_text('{name = "TestK1"; robotType = k1;}\n')
            setup = (
                'bhDir="$1"; robotsFile="$2"; shift 2\n'
                'cameraBackend=x5; robotIp=198.51.100.1; pingOptions=\n'
                'fatal() { echo "unexpected fatal" >&2; exit 90; }\n'
                'ping() { return 0; }\n'
                + function(self.client, "parseOptions") + '\nparseOptions "$@" ""\n'
                + 'printf "%s|%s" "$cameraBackend" "$robotIp"\n')
            result = shell(setup, (str(root), str(robots), "TestK1"))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "x5|198.51.100.1")
            result = shell(setup, (str(root), str(robots), "--update", "--camera", "realsense", "TestK1"))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "realsense|198.51.100.42")

    def test_realsense_never_copies_x5_updater(self):
        with tempfile.TemporaryDirectory() as directory:
            for backend in ("realsense", "x5"):
                with self.subTest(backend=backend):
                    calls = Path(directory) / "calls"
                    calls.write_text("")
                    result = shell(
                        'baseDir="$1/Install"; bhDir="$1"; calls="$2"; cameraBackend="$3"\n'
                        'SSHOPTIONS=; rsyncOptions=\n'
                        'message() { :; }; fatal() { exit 90; }\n'
                        'sshpass() { printf "auth\\n" >>"$calls"; }\n'
                        'ssh() { printf "ssh\\n" >>"$calls"; }\n'
                        'rsync() { case "$*" in *X5Camera/update_x5*) '
                        'printf "x5copy\\n" >>"$calls";; *) printf "copy\\n" >>"$calls";; esac; }\n'
                        + function(self.client, "copyFiles") + '\ncopyFiles 198.51.100.42 booster\n',
                        (str(ROOT), str(calls), backend))
                    self.assertEqual(result.returncode, 0, "Mocked copy helper failed")
                    labels = calls.read_text().splitlines()
                    self.assertEqual(labels.count("x5copy"), 0 if backend == "realsense" else 1)
                    self.assertEqual(labels.count("copy"), 2)

    def test_camera_backend_is_forwarded_to_remote_installer(self):
        result = shell(
            'cameraBackend=realsense; copyTo=/tmp/mock-install; delete=-d; robotName=TestK1\n'
            'SSHOPTIONS=; message() { :; }; fatal() { exit 90; }\n'
            'readlink() { printf "/usr/share/zoneinfo/Asia/Shanghai\\n"; }\n'
            'ssh() { case "$*" in *"--camera realsense TestK1 Asia/Shanghai"*) '
            'echo forwarded;; *) echo "backend not forwarded" >&2; return 99;; esac; }\n'
            + function(self.client, "runInstallation") + '\nrunInstallation 198.51.100.42 booster\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "forwarded")

    def test_realsense_install_and_uninstall_do_not_touch_x5(self):
        start = self.remote.index("    # Only X5 installations may touch the head camera")
        end = self.remote.index("    # Configure microphones", start)
        block = self.remote[start:end]
        with tempfile.TemporaryDirectory() as directory:
            for backend in ("realsense", "x5"):
                for delete in ("", "1"):
                    with self.subTest(backend=backend, uninstall=bool(delete)):
                        calls = Path(directory) / "calls"
                        calls.write_text("")
                        result = shell(
                            'calls="$1"; cameraBackend="$2"; DELETE="$3"\n'
                            'chmod() { printf "chmod\\n" >>"$calls"; }\n'
                            'sudo() { case "$*" in *update_x5*) printf "x5update\\n" >>"$calls";; '
                            '*) printf "sudo\\n" >>"$calls";; esac; }\n'
                            'sshpass() { printf "x5restore\\n" >>"$calls"; }\n'
                            + block, (str(calls), backend, delete))
                        self.assertEqual(result.returncode, 0, "Mocked camera branch failed")
                        labels = calls.read_text().splitlines()
                        if backend == "realsense":
                            self.assertEqual(labels, [])
                        elif delete:
                            self.assertEqual(labels, ["x5restore"])
                        else:
                            self.assertEqual(labels, ["chmod", "x5update", "sudo"])

    def test_realsense_service_changes_exclude_camera_services(self):
        result = shell(
            'cameraBackend=realsense; DELETE=\n'
            'sudo() { case "$*" in *camera*|*video*|*perception*|*handeye*|*detection*) '
            'echo "camera service changed" >&2; return 99;; *) echo common-only;; esac; }\n'
            + function(self.remote, "configureServices") + '\nconfigureServices\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "common-only")

    def service_phase(self, config, backend, delete=""):
        # Only ordinary file operations in the temporary test directory are
        # allowed. Never call the system sudo executable or installer body.
        return shell(
            'cameraBackend="$2"; DELETE="$3"\n'
            'sudo() { case "$1" in test|cp|mv) "$@";; '
            'sed) shift; if [ "$1" = -i ] && [ "$(uname)" = Darwin ]; then '
            'shift; command sed -i "" "$@"; else command sed "$@"; fi;; '
            '*) echo "unexpected privileged operation" >&2; return 99;; esac; }\n'
            + function(self.remote, "backupContainerConfig")
            + function(self.remote, "configureServices")
            + '\nbackupContainerConfig "$1"\nconfigureServices "$1"\n',
            (str(config), backend, delete))

    def test_realsense_restore_preserves_x5_and_unmanaged_services(self):
        initial = (
            '- name: ros.camera\n  enable: true\n'
            '- name: booster_camera\n  enable: false\n'
            '- name: lui\n  enable: true\n'
            '- name: audio\n  enable: false\n'
            '- name: battery_daemon\n  enable: true\n'
            '- name: audio_extra\n  enable: true\n'
            '- name: unrelated\n  enable: true\n')
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.yaml"
            legacy_backup = Path(directory) / "config.bak"
            config.write_text(initial)
            legacy_backup.write_text("legacy X5 backup must not be consumed\n")
            result = self.service_phase(config, "realsense")
            self.assertEqual(result.returncode, 0, result.stderr)
            installed = initial.replace('name: lui\n  enable: true', 'name: lui\n  enable: false')
            installed = installed.replace('name: battery_daemon\n  enable: true',
                                          'name: battery_daemon\n  enable: false')
            self.assertEqual(config.read_text(), installed)
            # Another process/user may edit camera and unrelated settings
            # after installation. RealSense uninstall must keep those edits.
            changed = installed.replace('name: ros.camera\n  enable: true',
                                        'name: ros.camera\n  enable: false')
            changed = changed.replace('name: booster_camera\n  enable: false',
                                      'name: booster_camera\n  enable: true')
            changed = changed.replace('name: unrelated\n  enable: true',
                                      'name: unrelated\n  enable: false')
            config.write_text(changed)
            result = self.service_phase(config, "realsense", "1")
            self.assertEqual(result.returncode, 0, result.stderr)
            restored = changed.replace('name: lui\n  enable: false', 'name: lui\n  enable: true')
            restored = restored.replace('name: battery_daemon\n  enable: false',
                                        'name: battery_daemon\n  enable: true')
            self.assertEqual(config.read_text(), restored)
            self.assertEqual(legacy_backup.read_text(), "legacy X5 backup must not be consumed\n")
            self.assertEqual((Path(directory) / "config.realsense.bak").read_text(), initial)

    def test_realsense_uninstall_never_falls_back_to_legacy_x5_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.yaml"
            current = '- name: ros.camera\n  enable: false\n- name: audio\n  enable: false\n'
            config.write_text(current)
            legacy_backup = Path(directory) / "config.bak"
            legacy_backup.write_text("old X5 data\n")
            result = self.service_phase(config, "realsense", "1")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(config.read_text(), current)
            self.assertEqual(legacy_backup.read_text(), "old X5 data\n")

    def test_native_x5_backup_restore_behavior_is_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.yaml"
            original = '- name: ros.camera\n  enable: true\n'
            config.write_text(original)
            result = self.service_phase(config, "x5")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotEqual(config.read_text(), original)
            result = self.service_phase(config, "x5", "1")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(config.read_text(), original)


if __name__ == "__main__":
    unittest.main()
