"""977 exact-target entry over frozen 996; no SDK, SSH, or body actuation."""
from hashlib import sha256
import io
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "Src/Apps/Booster/Main.cpp"
BASE_COMMIT = "35b4d3cee3e8f3d8e8774a01f73cefde44ee0fd4"
SERIAL = "1424225066425"
DIRECTORY = "/home/booster/Workspace/BHuman_977_From996_20261023"


def block(source, marker):
    start = source.index(marker)
    index = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[index] == "{") - (source[index] == "}")
        index += 1
    return source[start:index]


def source_files(root):
    return {path.relative_to(root): path for path in root.rglob("*") if path.is_file()}


def baseline_main():
    result = subprocess.run(["git", "show", BASE_COMMIT + ":Src/Apps/Booster/Main.cpp"],
                            cwd=ROOT, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise AssertionError("Required public baseline is missing; use a full Git checkout: " + result.stderr)
    return result.stdout


def baseline_source_hashes():
    result = subprocess.run(["git", "archive", "--format=tar", BASE_COMMIT, "Src"],
                            cwd=ROOT, capture_output=True, timeout=30)
    if result.returncode:
        raise AssertionError("Required public baseline is missing; do not skip comparison: "
                             + result.stderr.decode(errors="replace"))
    hashes = {}
    # Read archived bytes in memory. Never extract a reference tree or execute it.
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
        for member in archive.getmembers():
            if member.isdir():
                continue
            if not member.isfile() or not member.name.startswith("Src/"):
                raise AssertionError("Unexpected baseline archive entry: " + member.name)
            relative = Path(member.name).relative_to("Src")
            if relative in hashes:
                raise AssertionError("Duplicate baseline source entry: " + member.name)
            hashes[relative] = sha256(archive.extractfile(member).read()).hexdigest()
    return hashes


class Match977From996TargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = MAIN.read_text()
        cls.main = cls.source[cls.source.index("int main(int argc"):]
        cls.scope = block(cls.main, "if(officialMatch977)\n    {")

    def compile_run(self, code, cases):
        with tempfile.TemporaryDirectory(prefix="bh977-from996-target-") as directory:
            cpp, binary = Path(directory) / "test.cpp", Path(directory) / "test"
            cpp.write_text(code)
            built = subprocess.run(["clang++", "-std=c++20", "-DNDEBUG", "-Wall", "-Wextra", "-Werror",
                                    "-pedantic", "-I", str(ROOT / "Src/Libs"), str(cpp), "-o", str(binary)],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stderr)
            for arguments, expected in cases:
                with self.subTest(arguments=arguments):
                    result = subprocess.run([str(binary), *arguments], capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, expected, result.stderr)

    def test_real_predicate_requires_exact_soc_and_exact_nonnull_directory(self):
        predicate = block(self.source, "static bool match977IdentityAllowed(")
        cases = [((SERIAL, DIRECTORY), 0), ((SERIAL, "--null"), 1), ((SERIAL, ""), 1),
                 ((SERIAL, DIRECTORY + "/"), 1), ((SERIAL, DIRECTORY + "/Config"), 1),
                 ((SERIAL, DIRECTORY + "_other"), 1), ((SERIAL, DIRECTORY + "/../BHuman_977_From996_20261023"), 1),
                 ((SERIAL, "/home/booster/Workspace/BHuman_977_Match_20261023"), 1),
                 ((SERIAL, "/home/booster/Workspace/BHuman_996_Match_20261019"), 1),
                 ((SERIAL, "/home/booster/Workspace/BHuman_987_Match_20261023"), 1),
                 ((SERIAL, "/tmp/Config"), 1)]
        cases.extend(((serial, DIRECTORY), 1) for serial in
                     ("", "1424725032007", "1424725033885", "1422925046663", "1424225066424", SERIAL + "\n"))
        self.compile_run(r'''
#include <string>
#include <cstring>
''' + predicate + r'''
int main(int argc, char** argv) {
  if(argc != 3) return 9;
  const char* directory = !strcmp(argv[2], "--null") ? nullptr : argv[2];
  return match977IdentityAllowed(argv[1], directory) ? 0 : 1;
}
''', cases)

    def test_real_flags_reject_duplicate_and_mixed_live_targets(self):
        start = self.main.index('else if(!strcmp(argv[i], "--k1-977-match"))')
        end = self.main.index('else if(!strcmp(argv[i], "--commission-emergency-only"))', start)
        flags = self.main[start:end].replace("else if", "if", 1)
        targets = ("--k1-977-match", "--k1-996-match", "--k1-996-live-test")
        cases = [((targets[0],), 5), ((targets[1],), 4), ((targets[2],), 3)]
        cases.extend(((first, second), 1) for first in targets for second in targets)
        self.compile_run(r'''
#include <cstring>
#include <cstdlib>
int main(int argc, char** argv) {
  bool officialLiveTest = false, officialMatchMode = false, officialMatch977 = false;
  for(int i = 1; i < argc; ++i) {
''' + flags + r'''
    else return 9;
  }
  return officialMatch977 ? 5 : !officialLiveTest ? 2 : officialMatchMode ? 4 : 3;
}
''', cases)

    def test_real_strict_scope_rejects_watchdog_commission_and_duration(self):
        predicate = self.scope[self.scope.index("if(watchdog"):self.scope.index("std::ifstream serialFile")]
        self.compile_run(r'''
#include <cstdio>
#include <cstdlib>
int main(int argc, char** argv) {
  if(argc != 2) return 9;
  const unsigned mask = static_cast<unsigned>(std::atoi(argv[1]));
  const bool watchdog = mask & 1, commissionEmergencyOnly = mask & 2, commissionAppDampingOnly = mask & 4;
  const bool remoteStopX = mask & 8, commissionSecondsSet = mask & 16, appSecondsSet = mask & 32;
  const char* commissionExpectedSerial = mask & 64 ? "" : nullptr;
''' + predicate + r'''
  return 0;
}
''', [((str(mask),), int(mask != 0)) for mask in range(128)])

    def test_entry_enables_existing_gates_and_977_only_official_mode_before_robot_initialization(self):
        self.assertLess(self.scope.index("DENIED_MIXED_SCOPE"), self.scope.index("match977IdentityAllowed("))
        self.assertLess(self.scope.index("match977IdentityAllowed("), self.scope.index("BoosterOfficialLiveTest::enable();"))
        for call in ("BoosterOfficialLiveTest::enable();", "BoosterMatchSafety::enable();", "BoosterRemoteStand::enable();"):
            self.assertEqual(self.scope.count(call), 1)
        self.assertEqual(self.scope.count("BoosterOfficialLiveTest::enableOfficialButtonMode();"), 1)
        self.assertEqual(self.main.count("BoosterOfficialLiveTest::enableOfficialButtonMode();"), 1)
        for forbidden in ("BoosterAppCommissioning::enable", "enableBodyGap", "enableAutoOwner", "ChangeMode", "Write(",
                          "BoosterHead", "BoosterFeedbackIngress", "BoosterFeedbackRecovery"):
            self.assertNotIn(forbidden, self.scope)
        for marker in ("if(!initInstance())", "client.Init();", "FunctionList::execute();", "getRobotType(name)"):
            self.assertLess(self.main.index(self.scope), self.main.index(marker))
        for message in ("GC_SOURCE=192.168.50.37", "NO_FIXED_RUNTIME_LIMIT", "FROM_996_37A2C4BA",
                        "REMOTE_REMOVE_STAND_HOLD", "MANUAL_RECOVERY_INITIAL_READY_PLAY"):
            self.assertIn(message, self.scope)
        self.assertIn("signal(SIGINT, sigHandlerOfficialLiveShutdown);", self.scope)
        self.assertIn("signal(SIGTERM, sigHandlerOfficialLiveShutdown);", self.scope)

    def test_original_996_entry_and_both_original_flag_branches_are_byte_unchanged(self):
        baseline = baseline_main()
        old_entry = block(self.main, "if(officialLiveTest)\n    {")
        self.assertEqual(old_entry, block(baseline, "if(officialLiveTest)\n    {"))
        for flag in ("--k1-996-live-test", "--k1-996-match"):
            marker = f'else if(!strcmp(argv[i], "{flag}"))'
            self.assertEqual(block(self.main, marker), block(baseline, marker))
        self.assertIn('actualSerial != "1424725032007"', old_entry)
        self.assertIn("K1_996_MATCH ENTRY", old_entry)
        self.assertNotIn(SERIAL, old_entry)
        self.assertNotIn("K1_977_MATCH", old_entry)

    def test_only_authorized_official_mode_production_files_differ_from_frozen_996(self):
        current = source_files(ROOT / "Src")
        baseline = baseline_source_hashes()
        self.assertTrue(baseline, "Frozen 996 Src reference must be present; never silently skip the comparison")
        self.assertEqual(set(current), set(baseline), "Production Src files were added or removed")
        authorized = {Path(name) for name in (
            "Apps/Booster/Main.cpp", "Libs/Platform/BoosterOfficialLiveTest.h",
            "Modules/Infrastructure/BoosterProvider/BoosterProvider.cpp",
            "Modules/Infrastructure/GameStateProvider/GameStateProvider.cpp")}
        for relative in sorted(baseline):
            with self.subTest(source=str(relative)):
                actual = sha256(current[relative].read_bytes()).hexdigest()
                original = baseline[relative]
                if relative in authorized:
                    self.assertNotEqual(actual, original, "Authorized delta must remain explicit: " + str(relative))
                else:
                    self.assertEqual(actual, original, str(relative))

    def test_no_head_bodygap_or_new_low_receiver_is_imported(self):
        provider = (ROOT / "Src/Modules/Infrastructure/BoosterProvider/BoosterProvider.cpp").read_text()
        header = (ROOT / "Src/Modules/Infrastructure/BoosterProvider/BoosterProvider.h").read_text()
        for forbidden in ("BoosterHeadIsolation", "HEAD_ONLY_STAND_ISOLATION", "BoosterMotionGapPolicy",
                          "BoosterFeedbackIngress", "BoosterFeedbackRecovery", "LatestOnly", "LOW_STATE_RX_METRICS",
                          "GetCurrentDdsMessageContext", "AUTO_OWNER_BODY_GAP", "FUSED_C9C0"):
            self.assertNotIn(forbidden, self.source + provider + header)
        platform = ROOT / "Src/Libs/Platform"
        for name in ("BoosterHeadIsolation.h", "BoosterMotionGapPolicy.h", "BoosterFeedbackIngress.h", "BoosterFeedbackRecovery.h"):
            self.assertFalse((platform / name).exists(), name)


if __name__ == "__main__":
    unittest.main()
