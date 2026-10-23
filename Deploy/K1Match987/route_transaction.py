#!/usr/bin/env python3
"""Pinned 987 F1 route trial: file recovery only, no SDK/services/shell calls.

The current prepared policy follows the two official topic substitutions:
gateway buttons to even2 and remote-library input to stat2. Gateway remote
input remains on its original topic. The historical three-byte merged-input
policy and bundle are archived separately and must not be reinterpreted here.
This file only changes/restores files; it never commands body motion.
Each file uses atomic replace+fsync. The PAIR is a write-ahead transaction, not a
single filesystem-atomic operation: the independent root watchdog must restore
after any crash/timeout before controller consumers are restarted.
Default invocation is non-live status and does not write anything.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time
import uuid


SERIAL = "1424725033885"
TRIAL_ROOT = Path("/opt/booster/bhuman_match_987")
SERIAL_FILE = Path("/sys/firmware/devicetree/base/serial-number")
ACTUATOR_MARKER = Path("/home/booster/Workspace/BHuman_987_Match_20261023/ACTUATOR_COMMISSIONED")
MANIFEST_SHA = "068fb200d1e7af5396a7109f168b3ef24e1b2f9443f8b1f95cbb3a4e3b5bb4b2"
TARGETS = {
    "device_gateway": ("/opt/booster/bin/container/device_gateway", "device_gateway"),
    "remote_library": ("/opt/booster/lib/libremote_controller.so", "libremote_controller.so"),
}
APPROVED = {
    "manifest_sha": MANIFEST_SHA,
    "device_gateway": {
        "old": "435ea4e28d81aa05e7a174cecf7a68cc0395d86d78eea8c5d94bc3b354c7cb7a",
        "new": "3f155f2f68de5ad29a11c3f2ad8ccc54d6a39df0d82fbaf06d77909a59d24856",
        "patches": ((16000048, b"rt/button_event", b"rt/button_even2"),),
    },
    "remote_library": {
        "old": "9936b8bf3bdc0803d2bfa7b16c7aefc2819698c72eb1fb60e14f7f541437a23c",
        "new": "7c42d1668b3045b0de9086d73ff61acfa8289f3d1ac8254ad33d8852f4840c6f",
        "patches": ((6727440, b"rt/remote_controller_state", b"rt/remote_controller_stat2"),),
    },
}
PHASES = {"ARMED", "APPLYING", "APPLIED", "RESTORING", "RESTORED", "RESTORE_FAILED"}
MAX_BYTES = 256 * 1024 * 1024


class TrialError(RuntimeError):
    pass



def require_finalized_policy():
    valid = lambda value: type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    if not valid(MANIFEST_SHA) or APPROVED.get("manifest_sha") != MANIFEST_SHA:
        raise TrialError("UNFINALIZED 987 route bundle pin")
    for name in TARGETS:
        row = APPROVED[name]
        if not valid(row.get("old")) or not valid(row.get("new")) or row["old"] == row["new"]:
            raise TrialError("UNFINALIZED 987 original/patched route pins: " + name)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def directory(path):
    """Open every directory component with O_NOFOLLOW, not only the last one."""
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in Path(path).parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def metadata(info):
    return {"mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid, "bytes": info.st_size}


def read_file(path, limit=MAX_BYTES):
    parent, fd = directory(path.parent), None
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= limit:
            raise TrialError("Expected bounded nonempty non-hardlinked regular file: " + str(path))
        data = bytearray()
        while len(data) <= limit:
            block = os.read(fd, min(1024 * 1024, limit + 1 - len(data)))
            if not block:
                break
            data.extend(block)
        after = os.fstat(fd)
        current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_mode, s.st_uid, s.st_gid)
        if len(data) != before.st_size or identity(before) != identity(after) or identity(before) != identity(current):
            raise TrialError("File drift during read: " + str(path))
        return bytes(data), metadata(before)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def atomic_write(path, data, mode, uid, gid):
    parent, fd = directory(path.parent), None
    name = ".bhuman-route-" + uuid.uuid4().hex
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        view = memoryview(data)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise OSError("Short atomic write")
            view = view[count:]
        info = os.fstat(fd)
        if (info.st_uid, info.st_gid) != (uid, gid):
            os.fchown(fd, uid, gid)
        os.fchmod(fd, mode)
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.replace(name, path.name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(name, dir_fd=parent)
        except FileNotFoundError:
            pass
        os.close(parent)


class Trial:
    def __init__(self, base_fs, *, live=False, test_policy=None):
        base_fs = Path(base_fs)
        if base_fs.anchor != "/" or str(base_fs) != os.path.normpath(base_fs) or base_fs.is_symlink():
            raise TrialError("base filesystem must be absolute/canonical and not a symlink")
        if live:
            require_finalized_policy() # No root directory/state or firmware operation before real target pins.
            if base_fs != Path("/") or os.geteuid() != 0 or test_policy is not None:
                raise TrialError("Live requires root, base / and the immutable approved policy")
        elif base_fs == Path("/"):
            raise TrialError("Base / is forbidden without explicit --live")
        self.base, self.live = base_fs, live
        self.policy = APPROVED if test_policy is None else test_policy
        self.root = self.path(TRIAL_ROOT)
        self.bundle = self.root / "bundle"
        self.checkpoint = self.root / "ARMED.json"
        self.owner = (0, 0) if live else (os.geteuid(), os.getegid())

    def path(self, absolute):
        return self.base / str(absolute).lstrip("/")

    def trusted_metadata(self, actual, mode, path):
        if actual["mode"] != mode or (actual["uid"], actual["gid"]) != self.owner:
            raise TrialError("Incorrect private owner/permissions: " + str(path))

    def private_directory(self, path):
        fd = directory(path)
        try:
            self.trusted_metadata(metadata(os.fstat(fd)), 0o700, path)
        finally:
            os.close(fd)

    @contextmanager
    def lock(self):
        self.private_directory(self.root)
        parent = directory(self.root)
        try:
            fd = os.open("trial.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        finally:
            os.close(parent)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise TrialError("Untrusted trial lock")
            self.trusted_metadata(metadata(info), 0o600, "trial.lock")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)

    def environment(self, action):
        serial, _ = read_file(self.path(SERIAL_FILE), 256)
        if serial.rstrip(b"\x00\r\n").decode("ascii") != SERIAL:
            raise TrialError("Hardware serial mismatch")
        marker = self.path(ACTUATOR_MARKER)
        if action in ("arm", "apply") and (marker.exists() or marker.is_symlink()):
            raise TrialError("Normal ACTUATOR_COMMISSIONED exists; emergency-only trial forbidden")

    def manifest(self):
        for path in (self.bundle, self.bundle / "originals", self.bundle / "patched"):
            self.private_directory(path)
        raw, meta = read_file(self.bundle / "manifest.json", 1024 * 1024)
        self.trusted_metadata(meta, 0o600, "manifest.json")
        sidecar, meta = read_file(self.bundle / "manifest.sha256", 1024)
        self.trusted_metadata(meta, 0o600, "manifest.sha256")
        expected = self.policy["manifest_sha"]
        if digest(raw) != expected or sidecar != (expected + "\n").encode():
            raise TrialError("Approved bundle manifest hash mismatch")
        result = json.loads(raw)
        if (result.get("schema") != 1 or result.get("action") != "offline_controller_isolation_prepare_only"
                or result.get("routing_policy") != "official_two_patch_remote_library_only"
                or result.get("patch_location_count") != 2 or set(result.get("targets", {})) != set(TARGETS)):
            raise TrialError("Invalid pinned bundle schema")
        for name, (target, filename) in TARGETS.items():
            row, approved = result["targets"][name], self.policy[name]
            if row.get("firmware_target_hint") != target or row.get("expected_original_sha256") != approved["old"]:
                raise TrialError("Pinned target changed")
            for variant, sha in (("original", approved["old"]), ("patched", approved["new"])):
                record = row[variant]
                if record.get("path") != ("originals/" if variant == "original" else "patched/") + filename or record.get("sha256") != sha:
                    raise TrialError("Bundle path/hash redirect rejected")
            self.approved_original_metadata(name, result)
        return result

    def approved_original_metadata(self, name, manifest):
        """Live original permissions are not the private capture file's mode."""
        row = manifest["targets"][name]
        approved = row.get("approved_original_metadata")
        if (not isinstance(approved, dict) or set(approved) != {"mode", "uid", "gid", "bytes"}
                or any(type(value) is not int for value in approved.values())
                or approved["mode"] != 0o755
                or (approved["uid"], approved["gid"]) != self.owner
                or approved["bytes"] != row["original"]["bytes"]
                or row.get("source_identity") != {"mode": approved["mode"]}):
            raise TrialError("Invalid approved original metadata: " + name)
        captured = row.get("captured_source_identity")
        if (not isinstance(captured, dict) or set(captured) != {"device", "inode", "size", "mtime_ns", "ctime_ns", "mode"}
                or any(type(value) is not int for value in captured.values())
                or captured["size"] != approved["bytes"]):
            raise TrialError("Missing separate capture identity: " + name)
        provenance = row.get("approved_metadata_provenance")
        if (not isinstance(provenance, dict)
                or set(provenance) != {"scope", "serial", "boot_id", "diagnostic_sha256"}
                or provenance.get("serial") != SERIAL
                or provenance.get("scope") != "REMOTE_ORIGINAL_METADATA_READ_ONLY"
                or not isinstance(provenance.get("boot_id"), str)
                or len(provenance["boot_id"]) != 36
                or not isinstance(provenance.get("diagnostic_sha256"), str)
                or len(provenance["diagnostic_sha256"]) != 64
                or any(char not in "0123456789abcdef" for char in provenance["diagnostic_sha256"])):
            raise TrialError("Missing verified remote metadata provenance: " + name)
        return dict(approved)

    def payload(self, name, variant, manifest):
        row = manifest["targets"][name][variant]
        data, meta = read_file(self.bundle / row["path"])
        self.trusted_metadata(meta, 0o600, row["path"])
        if digest(data) != self.policy[name]["old" if variant == "original" else "new"] or len(data) != row["bytes"]:
            raise TrialError("Backup/patched artifact hash or size mismatch: " + name)
        return data

    def validate_pair(self, name, manifest):
        original = self.payload(name, "original", manifest)
        patched = self.payload(name, "patched", manifest)
        expected, changed = bytearray(original), 0
        for offset, old, new in self.policy[name]["patches"]:
            if len(old) != len(new) or original[offset:offset + len(old)] != old:
                raise TrialError("Pinned topic offset mismatch")
            expected[offset:offset + len(old)] = new
            changed += sum(a != b for a, b in zip(old, new))
        if bytes(expected) != patched or changed != 1:
            raise TrialError("Patch exceeds the approved one byte per file")

    def save(self, state):
        state["updated_ns"] = time.time_ns()
        atomic_write(self.checkpoint, (json.dumps(state, sort_keys=True, indent=2) + "\n").encode(), 0o600, *self.owner)

    def state(self, manifest):
        raw, meta = read_file(self.checkpoint, 1024 * 1024)
        self.trusted_metadata(meta, 0o600, self.checkpoint)
        state = json.loads(raw)
        if state.get("schema") != 1 or state.get("serial") != SERIAL or state.get("manifest_sha256") != self.policy["manifest_sha"] or state.get("phase") not in PHASES or set(state.get("targets", {})) != set(TARGETS):
            raise TrialError("Untrusted/incomplete checkpoint")
        for name, (path, _) in TARGETS.items():
            row = state["targets"][name]
            if row.get("path") != path or row.get("old_sha256") != self.policy[name]["old"] or row.get("new_sha256") != self.policy[name]["new"]:
                raise TrialError("Checkpoint target/hash redirect rejected")
            original = row.get("original_metadata", {})
            if set(original) != {"mode", "uid", "gid", "bytes"} or any(type(v) is not int for v in original.values()) or original["mode"] != manifest["targets"][name]["source_identity"]["mode"] or original["bytes"] != manifest["targets"][name]["original"]["bytes"] or (original["uid"], original["gid"]) != self.owner:
                raise TrialError("Checkpoint original metadata mismatch")
        return state

    def verify_target(self, name, allowed, meta=None):
        data, actual = read_file(self.path(TARGETS[name][0]))
        if digest(data) not in allowed or (actual["uid"], actual["gid"]) != self.owner or (meta is not None and actual != meta):
            raise TrialError("Target content/owner/permissions drift: " + name)
        return actual

    def install(self, name, data, meta):
        atomic_write(self.path(TARGETS[name][0]), data, meta["mode"], meta["uid"], meta["gid"])

    def arm(self, manifest):
        if self.checkpoint.exists() or self.checkpoint.is_symlink():
            raise TrialError("Existing checkpoint is never re-armed; restore and archive explicitly")
        rows = {}
        for name, (path, _) in TARGETS.items():
            self.validate_pair(name, manifest)
            original = self.verify_target(name, {self.policy[name]["old"]})
            if original["mode"] != manifest["targets"][name]["source_identity"]["mode"]:
                raise TrialError("Original permissions differ from approved manifest")
            rows[name] = {"path": path, "old_sha256": self.policy[name]["old"], "new_sha256": self.policy[name]["new"], "original_metadata": original}
        state = {"schema": 1, "serial": SERIAL, "manifest_sha256": self.policy["manifest_sha"], "phase": "ARMED", "targets": rows, "events": []}
        self.save(state)
        return {"ok": True, "phase": "ARMED"}

    def restore(self, manifest, state):
        errors, journal_errors, results = {}, [], {}
        state["phase"] = "RESTORING"
        try:
            self.save(state)
        except Exception as error:
            journal_errors.append(str(error))
        # Always attempt BOTH originals, even if the first or a journal write fails.
        for name in TARGETS:
            try:
                original = self.payload(name, "original", manifest)
                self.verify_target(name, {self.policy[name]["old"], self.policy[name]["new"]})
                meta = state["targets"][name]["original_metadata"]
                self.install(name, original, meta)
                self.verify_target(name, {self.policy[name]["old"]}, meta)
                results[name] = "restored"
            except Exception as error:
                errors[name], results[name] = str(error), "failed"
        state["phase"] = "RESTORE_FAILED" if errors or journal_errors else "RESTORED"
        state["restore_results"], state["restore_errors"], state["journal_errors"] = results, errors, journal_errors
        try:
            self.save(state)
        except Exception as error:
            journal_errors.append(str(error))
        return {"ok": not errors and not journal_errors, "phase": state["phase"] if not journal_errors else "RESTORE_FAILED", "targets": results, "errors": errors, "journal_errors": journal_errors}

    def apply(self, manifest, state):
        if state["phase"] == "APPLIED":
            for name in TARGETS:
                self.verify_target(name, {self.policy[name]["new"]}, dict(state["targets"][name]["original_metadata"], mode=0o755))
            return {"ok": True, "phase": "APPLIED", "idempotent": True}
        if state["phase"] != "ARMED":
            raise TrialError("Only ARMED can apply; interrupted/failed phases require restore")
        for name in TARGETS:
            self.validate_pair(name, manifest)
            self.verify_target(name, {self.policy[name]["old"]}, state["targets"][name]["original_metadata"])
        state["phase"] = "APPLYING"
        self.save(state)  # Durable before either target is changed.
        try:
            for name in TARGETS:
                meta = dict(state["targets"][name]["original_metadata"], mode=0o755)
                self.verify_target(name, {self.policy[name]["old"]}, state["targets"][name]["original_metadata"])
                self.install(name, self.payload(name, "patched", manifest), meta)
                self.verify_target(name, {self.policy[name]["new"]}, meta)
                state["events"].append({"target": name, "phase": "patched"})
                self.save(state)
            state["phase"] = "APPLIED"
            self.save(state)
            return {"ok": True, "phase": "APPLIED"}
        except Exception as error:
            state["apply_error"] = str(error)
            result = self.restore(manifest, state)
            result["apply_error"], result["ok"] = str(error), False
            return result

    def execute(self, action):
        if action not in ("arm", "apply", "restore", "status"):
            raise TrialError("Unknown fixed action")
        with self.lock():
            self.environment(action)
            manifest = self.manifest()
            if action == "arm":
                return self.arm(manifest)
            state = self.state(manifest)
            if action == "status":
                return {"ok": True, "phase": state["phase"], "checkpoint": str(self.checkpoint)}
            return self.apply(manifest, state) if action == "apply" else self.restore(manifest, state)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", default="status", choices=("arm", "apply", "restore", "status"))
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--base-fs", type=Path, help="Synthetic filesystem root only; never /.")
    scope.add_argument("--live", action="store_true", help="Explicit root-only pinned 987 operation.")
    args = parser.parse_args(argv)
    if not args.live and args.base_fs is None:
        if args.action != "status":
            parser.error("Mutating actions require explicit --live or a synthetic --base-fs")
        print(json.dumps({"ok": True, "live": False, "phase": "NOT_REQUESTED", "writes": False}))
        return 0
    try:
        result = Trial(Path("/") if args.live else args.base_fs, live=args.live).execute(args.action)
    except Exception as error:
        result = {"ok": False, "error": type(error).__name__ + ": " + str(error), "checkpoint_retained": True}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
