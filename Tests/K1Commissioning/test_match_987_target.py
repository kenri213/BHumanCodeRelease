"""987-only entry extraction tests; no SDK, SSH, or physical actuation."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "Src/Apps/Booster/Main.cpp"


def block(source, marker):
    start = source.index(marker)
    index = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[index] == "{") - (source[index] == "}")
        index += 1
    return source[start:index]


class Match987TargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = MAIN.read_text()
        cls.main = cls.source[cls.source.index("int main(int argc"):]
        cls.scope = block(cls.main, "if(officialMatch987)\n    {")

    def compile_run(self, code, cases):
        with tempfile.TemporaryDirectory(prefix="bh987-target-") as directory:
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

    def test_real_identity_predicate_requires_exact_soc_and_directory(self):
        predicate = block(self.source, "static bool match987IdentityAllowed(")
        self.compile_run(r'''
#include <string>
#include <cstring>
''' + predicate + r'''
int main(int argc, char** argv) {
  return argc == 3 && match987IdentityAllowed(argv[1], argv[2]) ? 0 : 1;
}
''', [(("1424725033885", "/home/booster/Workspace/BHuman_987_Match_20261023"), 0),
      (("1424725032007", "/home/booster/Workspace/BHuman_987_Match_20261023"), 1),
      (("1424725033885", "/home/booster/Workspace/BHuman_996_Match_20261019"), 1),
      (("1424725033885", "/home/booster/Workspace/BHuman_987_Match_20261023/"), 1),
      (("1424725033885", "/tmp/Config"), 1),
      (("", "/home/booster/Workspace/BHuman_987_Match_20261023"), 1)])

    def test_production_flag_chain_rejects_all_duplicate_and_mixed_targets(self):
        start = self.main.index('else if(!strcmp(argv[i], "--k1-987-match"))')
        end = self.main.index('else if(!strcmp(argv[i], "--commission-emergency-only"))', start)
        flags = self.main[start:end].replace("else if", "if", 1)
        self.compile_run(r'''
#include <cstring>
#include <cstdlib>
int main(int argc, char** argv) {
  bool officialLiveTest = false, officialMatchMode = false, officialMatch987 = false;
  for(int i = 1; i < argc; ++i) {
''' + flags + r'''
    else return 9;
  }
  return officialMatch987 ? 5 : !officialLiveTest ? 2 : officialMatchMode ? 4 : 3;
}
''', [(("--k1-987-match",), 5), (("--k1-996-match",), 4), (("--k1-996-live-test",), 3),
      (("--k1-987-match", "--k1-987-match"), 1),
      (("--k1-987-match", "--k1-996-match"), 1),
      (("--k1-996-match", "--k1-987-match"), 1),
      (("--k1-987-match", "--k1-996-live-test"), 1),
      (("--k1-996-live-test", "--k1-987-match"), 1)])

    def test_scope_checks_reject_watchdog_commission_and_duration_before_enabling(self):
        predicate = self.scope[self.scope.index("if(watchdog"):self.scope.index("std::ifstream serialFile")]
        self.compile_run(r'''
#include <cstring>
#include <cstdio>
#include <cstdlib>
int main(int argc, char** argv) {
  if(argc != 2) return 9;
  bool watchdog = false, commissionEmergencyOnly = false, commissionAppDampingOnly = false;
  bool remoteStopX = false, commissionSecondsSet = false, appSecondsSet = false;
  const char* commissionExpectedSerial = nullptr;
  const int flag = std::atoi(argv[1]);
  switch(flag) {
    case 1: watchdog = true; break;
    case 2: commissionEmergencyOnly = true; break;
    case 3: commissionAppDampingOnly = true; break;
    case 4: remoteStopX = true; break;
    case 5: commissionSecondsSet = true; break;
    case 6: appSecondsSet = true; break;
    case 7: commissionExpectedSerial = "1424725033885"; break;
  }
''' + predicate + r'''
  return 0;
}
''', [((str(flag),), int(flag != 0)) for flag in range(8)])

    def test_entry_keeps_same_996_opt_ins_no_new_motion_or_emergency_policy(self):
        self.assertLess(self.scope.index("DENIED_MIXED_SCOPE"), self.scope.index("match987IdentityAllowed("))
        self.assertLess(self.scope.index("match987IdentityAllowed("), self.scope.index("BoosterOfficialLiveTest::enable();"))
        for call in ("BoosterOfficialLiveTest::enable();", "BoosterMatchSafety::enable();", "BoosterRemoteStand::enable();"):
            self.assertIn(call, self.scope)
        for forbidden in ("BoosterAppCommissioning::enable", "enableBodyGap", "enableAutoOwner", "ChangeMode", "Write("):
            self.assertNotIn(forbidden, self.scope)
        for marker in ("if(!initInstance())", "client.Init();", "FunctionList::execute();", "getRobotType(name)"):
            self.assertLess(self.main.index(self.scope), self.main.index(marker))
        self.assertIn("GC_SOURCE=192.168.50.37", self.scope)
        self.assertIn("NO_FIXED_RUNTIME_LIMIT", self.scope)

    def test_996_identity_and_preexisting_entry_are_not_rebound(self):
        old = block(self.main, "if(officialLiveTest)\n    {")
        self.assertIn('actualSerial != "1424725032007"', old)
        self.assertNotIn("1424725033885", old)
        self.assertIn("K1_996_MATCH ENTRY", old)
        self.assertNotIn("K1_987_MATCH", old)


if __name__ == "__main__":
    unittest.main()
