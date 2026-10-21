#!/usr/bin/env python3
"""Offline, non-executable controller topic copies. No live apply operation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys


class ValidationError(Exception):
    pass


ROUTING_POLICY = "official_two_patch_remote_library_only"
PATCH_LOCATION_COUNT = 2
SPECS = {
    "device_gateway": {
        "filename": "device_gateway",
        "firmware_target_hint": "/opt/booster/bin/container/device_gateway",
        "patches": (
            {"old": b"rt/button_event", "new": b"rt/button_even2"},
        ),
        "unchanged_topics": ({"present": b"rt/remote_controller_state", "absent": b"rt/remote_controller_stat2"},),
    },
    "remote_library": {
        "filename": "libremote_controller.so",
        "firmware_target_hint": "/opt/booster/lib/libremote_controller.so",
        "patches": ({"old": b"rt/remote_controller_state", "new": b"rt/remote_controller_stat2"},),
    },
}
PROTECTED = tuple(Path(path) for path in (
    "/opt", "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/boot", "/dev", "/proc", "/sys", "/run"))
MAX_FILE_BYTES = 256 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def expected_digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValidationError("An explicit lowercase 64-character SHA-256 is required")
    return value


def private_path(value):
    text = os.fspath(value)
    if not os.path.isabs(text) or os.path.normpath(text) != text:
        raise ValidationError("Use a canonical absolute private path, without dot components")
    path = Path(text)
    if path == Path("/") or any(path == root or root in path.parents for root in PROTECTED):
        raise ValidationError("System/live firmware paths are not accepted; use private original copies")
    return path


def identity(info):
    return {"device": info.st_dev, "inode": info.st_ino, "size": info.st_size,
            "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns,
            "mode": stat.S_IMODE(info.st_mode)}


def inode(info):
    return info.st_dev, info.st_ino


def open_directory(path):
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValidationError("This tool requires POSIX O_NOFOLLOW support")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open("/", flags)
    try:
        for component in Path(path).parts[1:]:
            following = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def read_regular(path, maximum=MAX_FILE_BYTES):
    path = private_path(path)
    parent = open_directory(path.parent)
    descriptor = None
    try:
        parent_identity = inode(os.fstat(parent))
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum:
            raise ValidationError("Source/artifact must be a bounded non-empty regular file")
        chunks = []
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise ValidationError("File grew beyond the read limit")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if identity(before) != identity(after) or total != before.st_size:
            raise ValidationError("File changed during the read")
        current_parent = open_directory(path.parent)
        try:
            current = os.stat(path.name, dir_fd=current_parent, follow_symlinks=False)
            if inode(os.fstat(current_parent)) != parent_identity or identity(current) != identity(before):
                raise ValidationError("Source path or parent directory drifted during the read")
        finally:
            os.close(current_parent)
        data = b"".join(chunks)
        return {"path": str(path), "data": data, "sha256": digest(data),
                "identity": identity(before), "parent_identity": list(parent_identity)}
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def assert_unchanged(source):
    current = read_regular(source["path"])
    if any(current[key] != source[key] for key in ("sha256", "identity", "parent_identity")):
        raise ValidationError("Original source path/content drifted after validation")


def validate_elf(data):
    if (len(data) < 64 or data[:4] != b"\x7fELF" or data[4:7] != b"\x02\x01\x01"
            or int.from_bytes(data[18:20], "little") != 183
            or int.from_bytes(data[16:18], "little") not in (2, 3)):
        raise ValidationError("Expected an ELF64 little-endian AArch64 executable/shared library")


def mechanical_patch(original, spec):
    validate_elf(original)
    for requirement in spec.get("unchanged_topics", ()):
        if original.count(requirement["present"]) != 1 or original.count(requirement["absent"]) != 0:
            raise ValidationError("Require exactly one unchanged gateway remote topic and no isolated remote topic")
    patched = original
    records = []
    intervals = []
    for replacement in spec["patches"]:
        old, new = replacement["old"], replacement["new"]
        if len(old) != len(new) or original.count(old) != 1 or original.count(new) != 0:
            raise ValidationError("Require exactly one original topic and zero renamed topics for every replacement")
        offset = original.index(old)
        if any(offset < end and offset + len(old) > start for start, end in intervals):
            raise ValidationError("Replacement regions overlap")
        intervals.append((offset, offset + len(old)))
        patched = patched[:offset] + new + patched[offset + len(old):]
        records.append({"old_topic": old.decode(), "new_topic": new.decode(), "byte_offset": offset,
                        "changed_byte_offsets": [offset + i for i, pair in enumerate(zip(old, new)) if pair[0] != pair[1]]})
    for replacement in spec["patches"]:
        if (len(patched) != len(original) or patched.count(replacement["old"]) != 0
                or patched.count(replacement["new"]) != 1):
            raise ValidationError("Mechanical topic replacement invariants failed")
    for requirement in spec.get("unchanged_topics", ()):
        if (patched.count(requirement["present"]) != 1 or patched.count(requirement["absent"]) != 0
                or patched.index(requirement["present"]) != original.index(requirement["present"])):
            raise ValidationError("Gateway remote topic must remain unchanged at its original byte offset")
    return patched, records


class FreshDirectory:
    """All writes are anchored to no-follow directory FDs, never live paths."""
    def __init__(self, output):
        self.path = private_path(output)
        self.parent = None
        self.directories = {}
        self.created_directories = []
        self.files = []

    def __enter__(self):
        self.parent = open_directory(self.path.parent)
        try:
            os.mkdir(self.path.name, 0o700, dir_fd=self.parent)
        except BaseException:
            os.close(self.parent)
            self.parent = None
            raise
        try:
            root = os.open(self.path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.parent)
            self.directories[""] = root
            self.created_directories.append((self.parent, self.path.name, inode(os.fstat(root))))
            self.verify_location()
            return self
        except BaseException:
            self.cleanup()
            self.close()
            raise

    def verify_location(self):
        parent = open_directory(self.path.parent)
        try:
            root_info = os.stat(self.path.name, dir_fd=parent, follow_symlinks=False)
            if (inode(os.fstat(parent)) != inode(os.fstat(self.parent))
                    or inode(root_info) != inode(os.fstat(self.directories[""]))
                    or not stat.S_ISDIR(root_info.st_mode)):
                raise ValidationError("Output directory path drifted")
        finally:
            os.close(parent)

    def directory(self, name):
        if name in self.directories:
            return self.directories[name]
        if name not in ("originals", "patched"):
            raise ValidationError("Unexpected artifact subdirectory")
        root = self.directories[""]
        os.mkdir(name, 0o700, dir_fd=root)
        descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        self.created_directories.append((root, name, inode(os.fstat(descriptor))))
        self.directories[name] = descriptor
        return descriptor

    def write(self, relative, data):
        relative = Path(relative)
        if relative.is_absolute() or len(relative.parts) not in (1, 2) or any(p in (".", "..") for p in relative.parts):
            raise ValidationError("Unexpected artifact path")
        parent = self.directory(relative.parts[0]) if len(relative.parts) == 2 else self.directories[""]
        descriptor = os.open(relative.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
        self.files.append((parent, relative.name, inode(os.fstat(descriptor))))
        try:
            position = 0
            while position < len(data):
                written = os.write(descriptor, data[position:])
                if written <= 0:
                    raise OSError("Short artifact write")
                position += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def cleanup(self):
        failures = []
        for parent, name, original_inode in reversed(self.files):
            try:
                if inode(os.stat(name, dir_fd=parent, follow_symlinks=False)) != original_inode:
                    raise ValidationError("Refusing to remove an externally replaced artifact")
                os.unlink(name, dir_fd=parent)
            except FileNotFoundError:
                pass
            except (OSError, ValidationError):
                failures.append(name)
        for parent, name, original_inode in reversed(self.created_directories):
            try:
                if inode(os.stat(name, dir_fd=parent, follow_symlinks=False)) != original_inode:
                    raise ValidationError("Refusing to remove an externally replaced directory")
                os.rmdir(name, dir_fd=parent)
            except FileNotFoundError:
                pass
            except (OSError, ValidationError):
                failures.append(name)
        if failures:
            raise ValidationError("Preparation failed; private artifacts remain because safe cleanup could not finish")

    def close(self):
        for descriptor in reversed(list(self.directories.values())):
            os.close(descriptor)
        if self.parent is not None:
            os.close(self.parent)
            self.parent = None

    def __exit__(self, kind, value, traceback):
        try:
            if kind is not None:
                self.cleanup()
        finally:
            self.close()


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8")


def prepare(gateway_source, gateway_sha256, remote_source, remote_sha256, output_dir):
    sources = {}
    for name, path, expected in (("device_gateway", gateway_source, gateway_sha256),
                                 ("remote_library", remote_source, remote_sha256)):
        expected = expected_digest(expected)
        source = read_regular(path)
        if source["sha256"] != expected:
            raise ValidationError("Original SHA-256 mismatch for " + name)
        source["patched"], source["patch_records"] = mechanical_patch(source["data"], SPECS[name])
        sources[name] = source
    if inode_from_record(sources["device_gateway"]) == inode_from_record(sources["remote_library"]):
        raise ValidationError("The two originals must be distinct regular files")
    for source in sources.values():
        assert_unchanged(source)
    manifest = {"schema": 1, "action": "offline_controller_isolation_prepare_only", "targets": {},
                "routing_policy": ROUTING_POLICY, "patch_location_count": PATCH_LOCATION_COUNT,
                "rollback": {"operation": "restore stages original files in another fresh private directory only",
                             "live_firmware_modified": False,
                             "automatic_service_or_mode_changes": False}}
    with FreshDirectory(output_dir) as stage:
        for name, spec in SPECS.items():
            source = sources[name]
            original_path = "originals/" + spec["filename"]
            patched_path = "patched/" + spec["filename"]
            stage.write(original_path, source["data"])
            stage.write(patched_path, source["patched"])
            manifest["targets"][name] = {
                "firmware_target_hint": spec["firmware_target_hint"],
                "source_path": source["path"], "source_identity": source["identity"],
                "source_parent_identity": source["parent_identity"],
                "expected_original_sha256": source["sha256"],
                "original": {"path": original_path, "sha256": source["sha256"], "bytes": len(source["data"])},
                "patched": {"path": patched_path, "sha256": digest(source["patched"]), "bytes": len(source["patched"])},
                "patches": source["patch_records"]}
        for source in sources.values():
            assert_unchanged(source)
        stage.verify_location()
        content = json_bytes(manifest)
        manifest_sha = digest(content)
        stage.write("manifest.json", content)
        stage.write("manifest.sha256", (manifest_sha + "\n").encode())
        check_bundle(stage.path, manifest_sha)
        stage.verify_location()
    return {"ok": True, "operation": "prepare", "output_dir": str(private_path(output_dir)),
            "manifest_sha256": manifest_sha, "patch_locations": PATCH_LOCATION_COUNT, "live_firmware_modified": False}


def inode_from_record(source):
    return source["identity"]["device"], source["identity"]["inode"]


def check_private_directory(path, expected_entries):
    descriptor = open_directory(path)
    try:
        if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o700 or set(os.listdir(descriptor)) != set(expected_entries):
            raise ValidationError("Private bundle directory permissions or entry list changed")
    finally:
        os.close(descriptor)


def check_bundle(bundle_dir, manifest_sha256):
    bundle = private_path(bundle_dir)
    expected = expected_digest(manifest_sha256)
    check_private_directory(bundle, ("manifest.json", "manifest.sha256", "originals", "patched"))
    filenames = [spec["filename"] for spec in SPECS.values()]
    for name in ("originals", "patched"):
        check_private_directory(bundle / name, filenames)
    manifest_file = read_regular(bundle / "manifest.json", 1024 * 1024)
    sidecar = read_regular(bundle / "manifest.sha256", 1024)
    if manifest_file["identity"]["mode"] != 0o600 or sidecar["identity"]["mode"] != 0o600:
        raise ValidationError("Manifest files must remain private and non-executable")
    if manifest_file["sha256"] != expected or sidecar["data"] != (expected + "\n").encode():
        raise ValidationError("Manifest SHA-256 mismatch against the external trusted value")
    manifest = json.loads(manifest_file["data"])
    if (manifest.get("schema") != 1 or manifest.get("action") != "offline_controller_isolation_prepare_only"
            or manifest.get("routing_policy") != ROUTING_POLICY
            or manifest.get("patch_location_count") != PATCH_LOCATION_COUNT or set(manifest.get("targets", {})) != set(SPECS)):
        raise ValidationError("Unsupported or incomplete bundle manifest")
    for name, spec in SPECS.items():
        target = manifest["targets"][name]
        files = {}
        for variant in ("original", "patched"):
            directory = "originals" if variant == "original" else "patched"
            record = target[variant]
            if record.get("path") != directory + "/" + spec["filename"]:
                raise ValidationError("Manifest artifact path drift")
            source = read_regular(bundle / record["path"])
            if (source["identity"]["mode"] != 0o600 or source["sha256"] != expected_digest(record["sha256"])
                    or len(source["data"]) != record["bytes"]):
                raise ValidationError("Artifact identity/content mismatch")
            files[variant] = source["data"]
        expected_patch, patch_records = mechanical_patch(files["original"], spec)
        if (files["patched"] != expected_patch
                or target["expected_original_sha256"] != digest(files["original"])
                or target["firmware_target_hint"] != spec["firmware_target_hint"]
                or target["patches"] != patch_records):
            raise ValidationError("Mechanical-only patch or rollback metadata mismatch")
    return manifest


def restore(bundle_dir, manifest_sha256, output_dir):
    bundle = private_path(bundle_dir)
    manifest = check_bundle(bundle, manifest_sha256)
    originals = {name: read_regular(bundle / target["original"]["path"])
                 for name, target in manifest["targets"].items()}
    for name, source in originals.items():
        if source["sha256"] != manifest["targets"][name]["original"]["sha256"]:
            raise ValidationError("Original changed between bundle verification and private restoration")
    rollback = {"schema": 1, "action": "offline_original_file_restoration_only",
                "source_manifest_sha256": manifest_sha256, "live_firmware_modified": False, "outputs": {}}
    with FreshDirectory(output_dir) as stage:
        for name, source in originals.items():
            assert_unchanged(source)
            filename = SPECS[name]["filename"]
            stage.write(filename, source["data"])
            rollback["outputs"][filename] = {"sha256": source["sha256"], "bytes": len(source["data"]),
                "original_source_mode": manifest["targets"][name]["source_identity"]["mode"]}
        check_bundle(bundle, manifest_sha256)
        for source in originals.values():
            assert_unchanged(source)
        stage.verify_location()
        stage.write("restore_manifest.json", json_bytes(rollback))
        stage.verify_location()
    return {"ok": True, "operation": "restore_private_copies_only", "output_dir": str(private_path(output_dir)),
            "live_firmware_modified": False}


def main(arguments=None):
    parser = argparse.ArgumentParser(description="Offline-only controller isolation copies; no live apply, services, or robot commands")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    for option in ("gateway-source", "gateway-sha256", "remote-library-source", "remote-library-sha256", "output-dir"):
        prepare_parser.add_argument("--" + option, required=True)
    check_parser = commands.add_parser("check")
    restore_parser = commands.add_parser("restore", help="Stage originals in a fresh private directory; never restore live firmware")
    for subparser in (check_parser, restore_parser):
        subparser.add_argument("--bundle-dir", required=True)
        subparser.add_argument("--manifest-sha256", required=True)
    restore_parser.add_argument("--output-dir", required=True)
    options = parser.parse_args(arguments)
    try:
        if options.command == "prepare":
            result = prepare(options.gateway_source, options.gateway_sha256, options.remote_library_source,
                             options.remote_library_sha256, options.output_dir)
        elif options.command == "check":
            check_bundle(options.bundle_dir, options.manifest_sha256)
            result = {"ok": True, "operation": "check", "targets_verified": len(SPECS), "live_firmware_modified": False}
        else:
            result = restore(options.bundle_dir, options.manifest_sha256, options.output_dir)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValidationError, OSError, KeyError, TypeError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error), "live_firmware_modified": False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
