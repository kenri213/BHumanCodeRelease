"""Only synthetic ELF fixtures in fresh private temporary directories are used."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "Tools/K1Commissioning/controller_isolation.py"
SPEC = importlib.util.spec_from_file_location("controller_isolation", TOOL)
isolation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(isolation)


def elf(*topics):
    header = bytearray(64)
    header[:7] = b"\x7fELF\x02\x01\x01"
    header[16:18] = (3).to_bytes(2, "little")
    header[18:20] = (183).to_bytes(2, "little")
    return bytes(header) + b"\0safe-fixture\0" + b"\0padding\0".join(topics) + b"\0unchanged-tail\0"


class ControllerIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="k1-isolation-test-")
        self.root = Path(self.temporary.name).resolve()
        self.gateway = self.root / "copied_gateway"
        self.library = self.root / "copied_remote.so"
        self.gateway_data = elf(b"rt/button_event", b"rt/remote_controller_state")
        self.library_data = elf(b"rt/remote_controller_state")
        self.gateway.write_bytes(self.gateway_data)
        self.library.write_bytes(self.library_data)
        self.gateway.chmod(0o755)
        self.library.chmod(0o644)
        self.output = self.root / "prepared"

    def tearDown(self):
        self.temporary.cleanup()

    def prepare(self, **overrides):
        arguments = dict(gateway_source=self.gateway, gateway_sha256=isolation.digest(self.gateway.read_bytes()),
                         remote_source=self.library, remote_sha256=isolation.digest(self.library.read_bytes()),
                         output_dir=self.output)
        arguments.update(overrides)
        return isolation.prepare(**arguments)

    def rewrite_manifest(self, manifest):
        content = isolation.json_bytes(manifest)
        value = isolation.digest(content)
        (self.output / "manifest.json").write_bytes(content)
        (self.output / "manifest.sha256").write_text(value + "\n")
        return value

    def test_two_official_replacements_are_mechanical_and_sources_unchanged(self):
        result = self.prepare()
        manifest = isolation.check_bundle(self.output, result["manifest_sha256"])
        self.assertEqual(result["patch_locations"], 2)
        self.assertEqual(manifest["patch_location_count"], 2)
        self.assertEqual(manifest["routing_policy"], "official_two_patch_remote_library_only")
        self.assertEqual(len(manifest["targets"]["device_gateway"]["patches"]), 1)
        self.assertEqual(len(manifest["targets"]["remote_library"]["patches"]), 1)
        self.assertEqual(self.gateway.read_bytes(), self.gateway_data)
        self.assertEqual(self.library.read_bytes(), self.library_data)
        changed = 0
        for target in manifest["targets"].values():
            original = self.output / target["original"]["path"]
            patched = self.output / target["patched"]["path"]
            self.assertEqual(original.stat().st_size, patched.stat().st_size)
            changed += sum(a != b for a, b in zip(original.read_bytes(), patched.read_bytes()))
            self.assertEqual(stat.S_IMODE(original.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(patched.stat().st_mode), 0o600)
        self.assertEqual(changed, 2)
        self.assertFalse(manifest["rollback"]["live_firmware_modified"])

    def test_restore_only_stages_both_originals_non_executable(self):
        result = self.prepare()
        restored = self.root / "restored_private"
        isolation.restore(self.output, result["manifest_sha256"], restored)
        self.assertEqual((restored / "device_gateway").read_bytes(), self.gateway_data)
        self.assertEqual((restored / "libremote_controller.so").read_bytes(), self.library_data)
        self.assertEqual(stat.S_IMODE((restored / "device_gateway").stat().st_mode), 0o600)
        self.assertEqual(json.loads((restored / "restore_manifest.json").read_text())["action"],
                         "offline_original_file_restoration_only")

    def test_bad_source_hash_creates_nothing(self):
        with self.assertRaises(isolation.ValidationError):
            self.prepare(gateway_sha256="0" * 64)
        self.assertFalse(self.output.exists())

    def test_hash_must_be_explicit_valid_lowercase(self):
        for invalid in ("", "A" * 64, "0" * 63, "not-a-hash"):
            with self.subTest(value=invalid), self.assertRaises(isolation.ValidationError):
                self.prepare(gateway_sha256=invalid)

    def test_duplicate_original_topic_is_rejected(self):
        for topic in (b"rt/button_event", b"rt/remote_controller_state"):
            with self.subTest(topic=topic):
                self.gateway.write_bytes(self.gateway_data + topic)
                with self.assertRaises(isolation.ValidationError):
                    self.prepare()
                self.assertFalse(self.output.exists())

    def test_already_renamed_original_is_rejected(self):
        self.library.write_bytes(self.library_data.replace(b"rt/remote_controller_state", b"rt/remote_controller_stat2"))
        with self.assertRaises(isolation.ValidationError):
            self.prepare()

    def test_missing_gateway_unchanged_remote_topic_is_rejected(self):
        self.gateway.write_bytes(elf(b"rt/button_event"))
        with self.assertRaises(isolation.ValidationError):
            self.prepare()

    def test_gateway_remote_preserved_and_only_library_uses_isolated_remote(self):
        result = self.prepare()
        manifest = isolation.check_bundle(self.output, result["manifest_sha256"])
        gateway = (self.output / "patched/device_gateway").read_bytes()
        library = (self.output / "patched/libremote_controller.so").read_bytes()
        self.assertEqual(gateway, self.gateway_data.replace(b"rt/button_event", b"rt/button_even2"))
        self.assertEqual(gateway.count(b"rt/remote_controller_state"), 1)
        self.assertNotIn(b"rt/remote_controller_stat2", gateway)
        self.assertEqual(gateway.index(b"rt/remote_controller_state"), self.gateway_data.index(b"rt/remote_controller_state"))
        self.assertEqual(library, self.library_data.replace(b"rt/remote_controller_state", b"rt/remote_controller_stat2"))
        self.assertEqual(library.count(b"rt/remote_controller_stat2"), 1)
        self.assertNotIn(b"rt/remote_controller_state", library)
        self.assertEqual([item["old_topic"] for item in manifest["targets"]["device_gateway"]["patches"]], ["rt/button_event"])

    def test_gateway_with_previously_moved_remote_source_is_rejected(self):
        self.gateway.write_bytes(self.gateway_data.replace(b"rt/remote_controller_state", b"rt/remote_controller_stat2"))
        with self.assertRaises(isolation.ValidationError):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_historical_three_patch_bundle_is_not_silently_reinterpreted(self):
        self.prepare()
        manifest = json.loads((self.output / "manifest.json").read_text())
        manifest["patch_location_count"] = 3
        manifest.pop("routing_policy")
        record = manifest["targets"]["device_gateway"]["patched"]
        artifact = self.output / record["path"]
        historical = artifact.read_bytes().replace(b"rt/remote_controller_state", b"rt/remote_controller_stat2")
        artifact.write_bytes(historical)
        record["sha256"] = isolation.digest(historical)
        value = self.rewrite_manifest(manifest)
        before = artifact.read_bytes()
        with self.assertRaises(isolation.ValidationError):
            isolation.check_bundle(self.output, value)
        self.assertEqual(artifact.read_bytes(), before)

    def test_wrong_elf_architecture_is_rejected(self):
        changed = bytearray(self.gateway_data)
        changed[18:20] = (62).to_bytes(2, "little")
        self.gateway.write_bytes(changed)
        with self.assertRaises(isolation.ValidationError):
            self.prepare()

    def test_symlink_source_is_rejected(self):
        link = self.root / "source_link"
        link.symlink_to(self.gateway)
        with self.assertRaises((isolation.ValidationError, OSError)):
            self.prepare(gateway_source=link)

    def test_symlink_parent_is_rejected(self):
        link = self.root / "linked_parent"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises((isolation.ValidationError, OSError)):
            self.prepare(output_dir=link / "nested_output")

    def test_nonregular_fifo_is_rejected_without_open_blocking(self):
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        with self.assertRaises(isolation.ValidationError):
            self.prepare(gateway_source=fifo)

    def test_existing_output_directory_is_never_overwritten(self):
        self.output.mkdir()
        sentinel = self.output / "user-file"
        sentinel.write_text("keep")
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual(sentinel.read_text(), "keep")

    def test_output_symlink_is_never_followed(self):
        victim = self.root / "victim"
        victim.mkdir()
        self.output.symlink_to(victim, target_is_directory=True)
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual(list(victim.iterdir()), [])

    def test_system_and_nonnormal_paths_are_rejected(self):
        for path in ("/opt/booster/private-copy", "/etc/new", "/boot/new", "/usr/local/new", "/", "relative", str(self.root) + "/../new"):
            with self.subTest(path=path), self.assertRaises(isolation.ValidationError):
                isolation.private_path(path)

    def test_source_inode_drift_is_rejected_even_with_identical_bytes(self):
        snapshot = isolation.read_regular(self.gateway)
        self.gateway.rename(self.root / "old_inode")
        self.gateway.write_bytes(self.gateway_data)
        with self.assertRaises(isolation.ValidationError):
            isolation.assert_unchanged(snapshot)

    def test_midway_failure_removes_only_new_private_artifacts(self):
        original_write = isolation.FreshDirectory.write
        calls = 0
        def failing(stage, name, data):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("injected write failure")
            return original_write(stage, name, data)
        with mock.patch.object(isolation.FreshDirectory, "write", failing):
            with self.assertRaises(OSError):
                self.prepare()
        self.assertFalse(self.output.exists())
        self.assertEqual(self.gateway.read_bytes(), self.gateway_data)
        self.assertEqual(self.library.read_bytes(), self.library_data)

    def test_partial_file_write_failure_is_cleaned(self):
        original_write = os.write
        calls = 0
        def failing(descriptor, data):
            nonlocal calls
            calls += 1
            if calls == 1:
                return original_write(descriptor, data[:3])
            raise OSError("injected full disk")
        with mock.patch.object(isolation.os, "write", failing), self.assertRaises(OSError):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_output_parent_path_drift_is_detected_and_cleanup_is_fd_anchored(self):
        parent = self.root / "parent"
        parent.mkdir()
        output = parent / "prepared"
        moved = self.root / "parent_moved"
        original_write = isolation.FreshDirectory.write
        calls = 0
        def drifting(stage, name, data):
            nonlocal calls
            result = original_write(stage, name, data)
            calls += 1
            if calls == 1:
                parent.rename(moved)
                parent.mkdir()
                (parent / "user-file").write_text("keep")
            return result
        with mock.patch.object(isolation.FreshDirectory, "write", drifting):
            with self.assertRaises((isolation.ValidationError, OSError)):
                self.prepare(output_dir=output)
        self.assertEqual((parent / "user-file").read_text(), "keep")
        self.assertFalse((moved / "prepared").exists())

    def test_external_manifest_hash_prevents_manifest_tamper(self):
        result = self.prepare()
        manifest = json.loads((self.output / "manifest.json").read_text())
        manifest["targets"]["device_gateway"]["source_path"] = str(self.root / "fake")
        self.rewrite_manifest(manifest)
        with self.assertRaises(isolation.ValidationError):
            isolation.check_bundle(self.output, result["manifest_sha256"])

    def test_tampered_artifact_cannot_be_restored(self):
        result = self.prepare()
        (self.output / "originals/device_gateway").write_bytes(self.gateway_data + b"x")
        restored = self.root / "restore_should_not_exist"
        with self.assertRaises(isolation.ValidationError):
            isolation.restore(self.output, result["manifest_sha256"], restored)
        self.assertFalse(restored.exists())

    def test_manifest_cannot_redirect_artifact_paths(self):
        self.prepare()
        manifest = json.loads((self.output / "manifest.json").read_text())
        manifest["targets"]["device_gateway"]["original"]["path"] = "../copied_gateway"
        value = self.rewrite_manifest(manifest)
        with self.assertRaises(isolation.ValidationError):
            isolation.check_bundle(self.output, value)

    def test_rehashed_patch_with_other_changed_bytes_is_rejected(self):
        self.prepare()
        manifest = json.loads((self.output / "manifest.json").read_text())
        record = manifest["targets"]["device_gateway"]["patched"]
        artifact = self.output / record["path"]
        modified = bytearray(artifact.read_bytes())
        modified[-1] ^= 1
        artifact.write_bytes(modified)
        record["sha256"] = isolation.digest(modified)
        value = self.rewrite_manifest(manifest)
        with self.assertRaises(isolation.ValidationError):
            isolation.check_bundle(self.output, value)

    def test_artifact_symlink_is_rejected(self):
        result = self.prepare()
        artifact = self.output / "patched/device_gateway"
        artifact.unlink()
        artifact.symlink_to(self.gateway)
        with self.assertRaises((isolation.ValidationError, OSError)):
            isolation.check_bundle(self.output, result["manifest_sha256"])

    def test_manifest_permissions_must_stay_private(self):
        result = self.prepare()
        (self.output / "manifest.json").chmod(0o644)
        with self.assertRaises(isolation.ValidationError):
            isolation.check_bundle(self.output, result["manifest_sha256"])

    def test_additional_bundle_entries_are_rejected(self):
        result = self.prepare()
        (self.output / "not-part-of-the-bundle").write_text("keep")
        with self.assertRaises(isolation.ValidationError):
            isolation.check_bundle(self.output, result["manifest_sha256"])
        self.assertEqual((self.output / "not-part-of-the-bundle").read_text(), "keep")

    def test_tool_has_no_process_network_or_live_apply_api(self):
        tree = ast.parse(TOOL.read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)
            elif isinstance(node, ast.Call):
                name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                self.assertNotIn(name, {"system", "popen", "exec", "eval", "run", "execv", "execvp"})
        self.assertFalse(imported & {"subprocess", "socket", "ctypes", "paramiko"})


if __name__ == "__main__":
    unittest.main()
