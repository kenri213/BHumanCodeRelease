"""Compile and exercise Main's actual remote-X scope/evidence predicates."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from test_full_app_entry import condition

ROOT = Path(__file__).resolve().parents[2]


class RemoteStopMainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "Src/Apps/Booster/Main.cpp").read_text()

    def compile_run(self, code):
        with tempfile.TemporaryDirectory(prefix="remote-x-main-") as tmp:
            source, exe = Path(tmp) / "test.cpp", Path(tmp) / "test"
            source.write_text(code)
            result = subprocess.run(["clang++", "-std=c++20", "-Wall", "-Wextra", "-Werror", "-pedantic",
                                     str(source), "-o", str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_actual_cli_scope_cannot_enable_in_normal_or_old_entry(self):
        predicate = condition(self.source, "if(remoteStopX &&")
        self.compile_run("#include <array>\n" + r'''
bool rejected(bool remoteStopX, bool commissionAppDampingOnly, bool appSecondsSet, unsigned appSeconds) {
  return ''' + predicate + r''';
}
int main() {
  for(unsigned bits = 0; bits < 8; ++bits)
    for(unsigned seconds : std::array<unsigned, 5>{1, 179, 180, 181, 600}) {
      const bool remote = bits & 1, app = bits & 2, explicitTime = bits & 4;
      if(rejected(remote, app, explicitTime, seconds) != (remote && !(app && explicitTime && seconds <= 180))) return 1;
    }
  return 0;
}
''')
        self.assertIn('"--commission-remote-stop-x"', self.source)
        self.assertNotIn('"--commission-remote-stop-b"', self.source)
        self.assertIn("DENIED_DUPLICATE_REMOTE_STOP", self.source)
        self.assertLess(self.source.index("DENIED_REMOTE_STOP_SCOPE"), self.source.index("runAppDampingOnly(bhDir,"))

    def test_actual_evidence_rejects_initial_held_x_and_early_press(self):
        expression = self.source.split("const bool remoteEvidence =", 1)[1].split(";", 1)[0]
        self.compile_run(r'''
#include <cstdint>
static unsigned frames, neutralSamples;
static long long neutralAt, pressedAt;
unsigned remoteFrames() {return frames;}
unsigned remoteXFalseSamples() {return neutralSamples;}
long long firstRemoteXFalseTimeNs() {return neutralAt;}
long long firstRemoteXTimeNs() {return pressedAt;}
bool evidence(long long frameworkReadyAtNs) {return ''' + expression + r''';}
int main() {
  frames=2; neutralSamples=1; neutralAt=110; pressedAt=120;
  if(!evidence(100)) return 1;
  frames=0; if(evidence(100)) return 2; frames=2;
  neutralSamples=0; if(evidence(100)) return 3; neutralSamples=1;
  neutralAt=0; if(evidence(100)) return 4;
  neutralAt=130; if(evidence(100)) return 5;
  neutralAt=120; if(evidence(100)) return 6;
  neutralAt=110; if(evidence(121)) return 7;
  pressedAt=0; if(evidence(100)) return 8;
  return 0;
}
''')
        success = self.source.split("const bool pass = frameworkReady", 1)[1].split(";", 1)[0]
        for required in ("remoteStopX ? StopReason::remoteStop : StopReason::f1", "remoteEvidence",
                         "dampingBeforeStop", "finalDamping", "robotStopped", "!modeFailures.load()"):
            self.assertIn(required, success)


if __name__ == "__main__":
    unittest.main()
