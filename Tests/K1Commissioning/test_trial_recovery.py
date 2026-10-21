"""Only temporary synthetic files; never --live, hardware, services, or SDK."""
import ast
import copy
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "Tools/K1Commissioning/trial_recovery.py"
SPEC = importlib.util.spec_from_file_location("trial_recovery", TOOL)
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)


class TrialRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="k1-route-recovery-test-")
        self.base = Path(self.tmp.name).resolve()
        self.policy = copy.deepcopy(recovery.APPROVED)
        self.originals, self.patched = {}, {}
        self.trial_root = self.path(recovery.TRIAL_ROOT)
        self.trial_root.mkdir(parents=True, mode=0o700)
        self.trial_root.chmod(0o700)
        self.bundle = self.trial_root / "bundle"
        self.bundle.mkdir(mode=0o700)
        for name in ("originals", "patched"):
            (self.bundle / name).mkdir(mode=0o700)
        manifest = {"schema": 1, "action": "offline_controller_isolation_prepare_only",
                    "routing_policy": "official_two_patch_remote_library_only",
                    "patch_location_count": 2, "targets": {}}
        for name, (target, filename) in recovery.TARGETS.items():
            topics = [(item[1], item[2]) for item in self.policy[name]["patches"]]
            original = b"synthetic-only\x00" + b"#" * 32
            patches = []
            for old, new in topics:
                patches.append((len(original), old, new))
                original += old + b"\x00" + b"#" * 32
            original += b"tail\x00"
            patched = original
            for _, old, new in patches:
                patched = patched.replace(old, new, 1)
            self.originals[name], self.patched[name] = original, patched
            self.policy[name] = {"old": recovery.digest(original), "new": recovery.digest(patched), "patches": tuple(patches)}
            mode = 0o755 if name == "device_gateway" else 0o644
            destination = self.path(target)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(original)
            destination.chmod(mode)
            row = {"firmware_target_hint": target, "expected_original_sha256": self.policy[name]["old"],
                   "source_identity": {"mode": mode}}
            for variant, content in (("original", original), ("patched", patched)):
                relative = ("originals/" if variant == "original" else "patched/") + filename
                source = self.bundle / relative
                source.write_bytes(content)
                source.chmod(0o600)
                row[variant] = {"path": relative, "sha256": recovery.digest(content), "bytes": len(content)}
            manifest["targets"][name] = row
        content = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
        self.policy["manifest_sha"] = recovery.digest(content)
        (self.bundle / "manifest.json").write_bytes(content)
        (self.bundle / "manifest.sha256").write_text(self.policy["manifest_sha"] + "\n")
        (self.bundle / "manifest.json").chmod(0o600)
        (self.bundle / "manifest.sha256").chmod(0o600)
        serial = self.path(recovery.SERIAL_FILE)
        serial.parent.mkdir(parents=True)
        serial.write_bytes(recovery.SERIAL.encode() + b"\x00")
        self.trial = recovery.Trial(self.base, test_policy=self.policy)

    def tearDown(self):
        self.tmp.cleanup()

    def path(self, absolute):
        return self.base / str(absolute).lstrip("/")

    def target(self, name):
        return self.path(recovery.TARGETS[name][0])

    def state(self):
        return json.loads(self.trial.checkpoint.read_text())

    def assert_originals(self):
        for name, data in self.originals.items():
            self.assertEqual(self.target(name).read_bytes(), data)
            self.assertEqual(stat.S_IMODE(self.target(name).stat().st_mode), 0o755 if name == "device_gateway" else 0o644)

    def test_current_policy_matches_two_official_substitutions(self):
        self.assertEqual(len(recovery.APPROVED["device_gateway"]["patches"]), 1)
        self.assertEqual(recovery.APPROVED["device_gateway"]["patches"][0][1:],
                         (b"rt/button_event", b"rt/button_even2"))
        self.assertEqual(len(recovery.APPROVED["remote_library"]["patches"]), 1)
        self.assertEqual(recovery.APPROVED["remote_library"]["patches"][0][1:],
                         (b"rt/remote_controller_state", b"rt/remote_controller_stat2"))

    def test_legacy_three_patch_manifest_is_not_reinterpreted(self):
        path = self.bundle / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["patch_location_count"] = 3
        manifest.pop("routing_policy")
        raw = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
        path.write_bytes(raw)
        self.policy["manifest_sha"] = recovery.digest(raw)
        (self.bundle / "manifest.sha256").write_text(self.policy["manifest_sha"] + "\n")
        with self.assertRaises(recovery.TrialError):
            self.trial.execute("arm")
        self.assertFalse(self.trial.checkpoint.exists())
        self.assert_originals()

    def test_arm_apply_restore_exact_bytes_metadata_and_root_private_checkpoint(self):
        self.assertEqual(self.trial.execute("arm")["phase"], "ARMED")
        self.assertEqual(stat.S_IMODE(self.trial.checkpoint.stat().st_mode), 0o600)
        self.assertEqual(self.state()["serial"], recovery.SERIAL)
        self.assertEqual(self.state()["manifest_sha256"], self.policy["manifest_sha"])
        self.assertTrue(self.trial.execute("apply")["ok"])
        for name, data in self.patched.items():
            self.assertEqual(self.target(name).read_bytes(), data)
            self.assertEqual(stat.S_IMODE(self.target(name).stat().st_mode), 0o755)
        self.assertTrue(self.trial.execute("restore")["ok"])
        self.assert_originals()
        self.assertEqual(self.state()["phase"], "RESTORED")

    def test_second_replacement_failure_restores_both_originals(self):
        self.trial.execute("arm")
        original_install = self.trial.install
        attempts = []
        def failing(name, data, meta):
            attempts.append((name, recovery.digest(data)))
            if name == "remote_library" and data == self.patched[name]:
                raise OSError("injected second-file replacement failure")
            return original_install(name, data, meta)
        with mock.patch.object(self.trial, "install", side_effect=failing):
            result = self.trial.execute("apply")
        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "RESTORED")
        self.assert_originals()
        for name in recovery.TARGETS:
            self.assertIn((name, self.policy[name]["old"]), attempts)

    def test_first_restore_failure_still_attempts_second_and_checkpoint_is_retained(self):
        self.trial.execute("arm")
        self.trial.execute("apply")
        original_install = self.trial.install
        attempts = []
        def failing(name, data, meta):
            attempts.append(name)
            if name == "device_gateway":
                raise OSError("injected first restore failure")
            return original_install(name, data, meta)
        with mock.patch.object(self.trial, "install", side_effect=failing):
            result = self.trial.execute("restore")
        self.assertEqual(attempts, list(recovery.TARGETS))
        self.assertFalse(result["ok"])
        self.assertEqual(self.state()["phase"], "RESTORE_FAILED")
        self.assertEqual(self.target("remote_library").read_bytes(), self.originals["remote_library"])
        self.assertTrue(self.trial.checkpoint.exists())
        self.assertTrue(self.trial.execute("restore")["ok"])
        self.assert_originals()

    def test_crash_after_first_rename_before_journal_update_is_recoverable(self):
        self.trial.execute("arm")
        state = self.state()
        state["phase"] = "APPLYING"
        self.trial.save(state)
        name = "device_gateway"
        meta = dict(state["targets"][name]["original_metadata"], mode=0o755)
        self.trial.install(name, self.patched[name], meta)
        with self.assertRaises(recovery.TrialError):
            self.trial.execute("apply")
        restarted = recovery.Trial(self.base, test_policy=self.policy)
        self.assertTrue(restarted.execute("restore")["ok"])
        self.assert_originals()

    def test_journal_disk_failure_does_not_prevent_two_restore_attempts(self):
        self.trial.execute("arm")
        original_save = self.trial.save
        count = 0
        def failing(state):
            nonlocal count
            count += 1
            if count >= 2:
                raise OSError("injected journal disk full")
            return original_save(state)
        with mock.patch.object(self.trial, "save", side_effect=failing):
            result = self.trial.execute("apply")
        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "RESTORE_FAILED")
        self.assert_originals()
        self.assertEqual(self.state()["phase"], "APPLYING")
        self.assertTrue(self.trial.execute("restore")["ok"])

    def test_apply_idempotent_restore_repeatable_but_arm_never_overwrites(self):
        self.trial.execute("arm")
        for phase in ("armed", "applied", "restored"):
            if phase == "applied":
                self.trial.execute("apply")
                self.assertTrue(self.trial.execute("apply")["idempotent"])
            elif phase == "restored":
                self.trial.execute("restore")
                self.assertTrue(self.trial.execute("restore")["ok"])
            checkpoint = self.trial.checkpoint.read_bytes()
            with self.assertRaises(recovery.TrialError):
                self.trial.execute("arm")
            self.assertEqual(self.trial.checkpoint.read_bytes(), checkpoint)

    def test_hash_drift_after_arm_is_rejected_before_either_file_changes(self):
        self.trial.execute("arm")
        self.target("remote_library").write_bytes(b"unapproved vendor update")
        with self.assertRaises(recovery.TrialError):
            self.trial.execute("apply")
        self.assertEqual(self.target("device_gateway").read_bytes(), self.originals["device_gateway"])
        self.assertEqual(self.state()["phase"], "ARMED")

    def test_restore_unknown_hash_preserves_it_but_restores_the_other_file(self):
        self.trial.execute("arm")
        self.trial.execute("apply")
        self.target("device_gateway").write_bytes(b"unapproved external drift")
        result = self.trial.execute("restore")
        self.assertFalse(result["ok"])
        self.assertEqual(result["targets"]["remote_library"], "restored")
        self.assertEqual(self.target("device_gateway").read_bytes(), b"unapproved external drift")
        self.assertEqual(self.state()["phase"], "RESTORE_FAILED")

    def test_corrupted_backup_rejects_arm_and_restore_still_attempts_other_backup(self):
        self.trial.execute("arm")
        self.trial.execute("apply")
        (self.bundle / "originals/device_gateway").write_bytes(b"corrupt backup")
        result = self.trial.execute("restore")
        self.assertFalse(result["ok"])
        self.assertEqual(result["targets"]["remote_library"], "restored")
        self.assertEqual(self.state()["phase"], "RESTORE_FAILED")

    def test_manifest_and_checkpoint_tamper_are_rejected(self):
        self.trial.execute("arm")
        state = self.state()
        state["targets"]["remote_library"]["path"] = "/etc/passwd"
        self.trial.save(state)
        with self.assertRaises(recovery.TrialError):
            self.trial.execute("apply")
        self.assert_originals()
        (self.bundle / "manifest.json").write_text("{}\n")
        with self.assertRaises(recovery.TrialError):
            self.trial.execute("restore")

    def test_existing_checkpoint_and_checkpoint_symlink_never_overwritten(self):
        victim = self.base / "victim"
        victim.write_text("keep")
        self.trial.checkpoint.symlink_to(victim)
        with self.assertRaises(recovery.TrialError):
            self.trial.execute("arm")
        self.assertEqual(victim.read_text(), "keep")
        self.trial.checkpoint.unlink()
        self.trial.checkpoint.write_text("existing invalid checkpoint")
        self.trial.checkpoint.chmod(0o600)
        with self.assertRaises(recovery.TrialError):
            self.trial.execute("arm")
        self.assertEqual(self.trial.checkpoint.read_text(), "existing invalid checkpoint")

    def test_target_symlink_hardlink_and_parent_symlink_rejected(self):
        target = self.target("remote_library")
        copy_path = self.base / "safe-original"
        target.rename(copy_path)
        target.symlink_to(copy_path)
        with self.assertRaises(OSError):
            self.trial.execute("arm")
        target.unlink()
        os.link(copy_path, target)
        with self.assertRaises(recovery.TrialError):
            self.trial.execute("arm")
        target.unlink()
        copy_path.rename(target)
        parent = target.parent
        moved = self.base / "moved-lib"
        parent.rename(moved)
        parent.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(OSError):
            self.trial.execute("arm")

    def test_bundle_artifact_symlink_and_checkpoint_permissions_rejected(self):
        artifact = self.bundle / "patched/libremote_controller.so"
        artifact.unlink()
        artifact.symlink_to(self.target("remote_library"))
        with self.assertRaises(OSError):
            self.trial.execute("arm")
        artifact.unlink()
        artifact.write_bytes(self.patched["remote_library"])
        artifact.chmod(0o600)
        self.trial.execute("arm")
        self.trial.checkpoint.chmod(0o644)
        with self.assertRaises(recovery.TrialError):
            self.trial.execute("apply")

    def test_serial_and_normal_actuator_marker_guards(self):
        self.path(recovery.SERIAL_FILE).write_bytes(b"wrong\x00")
        with self.assertRaises(recovery.TrialError):
            self.trial.execute("arm")
        self.path(recovery.SERIAL_FILE).write_bytes(recovery.SERIAL.encode() + b"\x00")
        marker = self.path(recovery.ACTUATOR_MARKER)
        marker.parent.mkdir(parents=True)
        marker.touch()
        with self.assertRaises(recovery.TrialError):
            self.trial.execute("arm")
        marker.unlink()
        self.trial.execute("arm")
        self.trial.execute("apply")
        marker.touch()
        with self.assertRaises(recovery.TrialError):
            self.trial.execute("apply")
        self.assertTrue(self.trial.execute("restore")["ok"])

    def test_live_requires_explicit_root_without_test_policy(self):
        with self.assertRaises(recovery.TrialError):
            recovery.Trial(Path("/"))
        with mock.patch.object(recovery.os, "geteuid", return_value=501), self.assertRaises(recovery.TrialError):
            recovery.Trial(Path("/"), live=True)
        with mock.patch.object(recovery.os, "geteuid", return_value=0), self.assertRaises(recovery.TrialError):
            recovery.Trial(Path("/"), live=True, test_policy=self.policy)

    def test_double_slash_root_cannot_bypass_explicit_live(self):
        for path in (Path("//"), Path("//opt"), Path("relative")):
            with self.subTest(path=str(path)), self.assertRaises(recovery.TrialError):
                recovery.Trial(path, test_policy=self.policy)

    def test_corrupted_patched_payload_and_wrong_patch_offset_reject_before_mutation(self):
        payload = self.bundle / "patched/device_gateway"
        payload.write_bytes(self.patched["device_gateway"] + b"bad")
        with self.assertRaises(recovery.TrialError):
            self.trial.execute("arm")
        self.assert_originals()
        self.assertFalse(self.trial.checkpoint.exists())
        payload.write_bytes(self.patched["device_gateway"])
        offset, old, new = self.policy["device_gateway"]["patches"][0]
        self.policy["device_gateway"]["patches"] = ((offset + 1, old, new),) + self.policy["device_gateway"]["patches"][1:]
        with self.assertRaises(recovery.TrialError):
            self.trial.execute("arm")
        self.assert_originals()

    def test_bad_checkpoint_old_hash_and_metadata_cannot_redirect_restore(self):
        self.trial.execute("arm")
        saved = self.state()
        for corrupt in ("hash", "mode", "uid", "serial"):
            bad = copy.deepcopy(saved)
            if corrupt == "hash":
                bad["targets"]["device_gateway"]["old_sha256"] = "0" * 64
            elif corrupt == "serial":
                bad["serial"] = "other"
            else:
                bad["targets"]["device_gateway"]["original_metadata"][corrupt] += 1
            self.trial.save(bad)
            with self.subTest(field=corrupt), self.assertRaises(recovery.TrialError):
                self.trial.execute("restore")
            self.assert_originals()

    def test_lock_excludes_concurrent_writer(self):
        self.trial.execute("arm")
        with self.trial.lock(), self.assertRaises(BlockingIOError):
            self.trial.execute("apply")

    def test_partial_temporary_write_failure_leaves_original_and_cleans_own_temp(self):
        target = self.target("device_gateway")
        original_write, calls = recovery.os.write, 0
        def failing(fd, data):
            nonlocal calls
            calls += 1
            if calls == 1:
                return original_write(fd, data[:3])
            raise OSError("injected short write/disk full")
        with mock.patch.object(recovery.os, "write", side_effect=failing), self.assertRaises(OSError):
            recovery.atomic_write(target, self.patched["device_gateway"], 0o755, os.geteuid(), os.getegid())
        self.assertEqual(target.read_bytes(), self.originals["device_gateway"])
        self.assertFalse(list(target.parent.glob(".bhuman-route-*")))

    def test_default_status_no_scope_is_nonlive_no_write_and_no_arbitrary_shell_option(self):
        result = subprocess.run([sys.executable, "-B", str(TOOL)], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), {"ok": True, "live": False, "phase": "NOT_REQUESTED", "writes": False})
        for options in (("apply",), ("--command", "echo unsafe")):
            bad = subprocess.run([sys.executable, "-B", str(TOOL), *options], capture_output=True, text=True)
            self.assertEqual(bad.returncode, 2)
        tree = ast.parse(TOOL.read_text())
        imports = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        self.assertFalse({"subprocess", "booster", "rclpy", "rospy"} & imports)
        self.assertFalse(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                             and node.func.attr in ("system", "popen", "fork", "execv", "execve") for node in ast.walk(tree)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
