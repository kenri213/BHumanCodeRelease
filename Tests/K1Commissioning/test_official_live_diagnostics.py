"""Diagnostic snapshots only: fake values, no SDK, robot or control output."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def block(source, marker):
    start = source.index(marker)
    opening = source.index("{", start)
    index, depth = opening + 1, 1
    while depth:
        depth += (source[index] == "{") - (source[index] == "}")
        index += 1
    return source[start:index]


class OfficialLiveDiagnosticTests(unittest.TestCase):
    def test_actual_header_diagnostics_do_not_enable_or_stop_control(self):
        harness = r'''
#include <atomic>
#include <cmath>
#include <cstdint>
#define state(name) static_assert(false, "CABSL macro expanded")
#include "Platform/BoosterOfficialLiveTest.h"
#undef state
#include <cstdlib>
namespace C = BoosterOfficialLiveTest;
static void check(bool value) {if(!value) std::abort();}
static void gameFrame(bool earlyReturn) {
 auto scope = C::onDiagnosticExit([] {C::noteGameDiagnostic(1234, 8, 2100, 3, false);});
 if(earlyReturn) return;
}
int main() {
 C::noteRemoteDiagnostic(0x603, 132); C::noteKeyDiagnostic(false, false, true, true); C::notePreviousPlayerState(9);
 C::noteMotionDiagnostic(10, 3, 0, 8, false, false, true); gameFrame(true);
 auto d = C::diagnostics();
 check(!d.remoteCallbacks && !d.keyFrames && !d.cognitionFrames && !d.motionDiagnosticFrames && !C::enabled());
 C::enable();
 C::noteRemoteDiagnostic(0x603, 132); C::noteKeyDiagnostic(false, false, true, true); C::notePreviousPlayerState(9);
 C::noteMotionDiagnostic(10, 3, 0, 8, false, false, true); gameFrame(true); gameFrame(false);
 d = C::diagnostics();
 check(d.remoteCallbacks == 1 && d.remoteEvent == 0x603 && d.remoteButtons == 132);
 check(d.keyFrames == 1 && d.keyBits == 4 && d.systemEmergency && d.previousPlayerState == 9);
 check(d.cognitionFrames == 2 && d.cognitionTime == 1234 && d.playerState == 8
       && d.standHeldMs == 2100 && d.walkHitStreak == 3 && !d.damping);
 check(d.motionDiagnosticFrames == 1 && d.motionTime == 10 && d.requestedMotion == 3 && d.effectiveMotion == 0
       && d.olderPerceptionTime == 8 && !d.offsetFinished && !d.imuBad && d.forceSitDown);
 C::noteKeyDiagnostic(true, true, false);
 C::noteMotionDiagnostic(11, 3, 3, 9, true, true, false);
 d = C::diagnostics(); check(d.keyBits == 3 && d.offsetFinished && d.imuBad && !d.forceSitDown);
 check(!C::stopRequested() && !C::outputFrames());
 return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="k1-official-diagnostics-") as temporary:
            source, output = Path(temporary) / "test.cpp", Path(temporary) / "test"
            source.write_text(harness)
            compiled = subprocess.run(["clang++", "-std=c++20", "-Wall", "-Wextra", "-Werror", "-pedantic",
                                       "-I", str(ROOT / "Src/Libs"), str(source), "-o", str(output)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(output)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_official_pd_loop_byte_hash_unchanged(self):
        provider = (ROOT / "Src/Modules/Infrastructure/BoosterProvider/BoosterProvider.cpp").read_text()
        marker = "for(size_t joint = 0; joint < lowCmd.motor_cmd().size(); ++joint)"
        baseline = subprocess.run(["git", "show", "2e3374ba896da8288607f9a1881b876642fe18b8:Src/Modules/Infrastructure/BoosterProvider/BoosterProvider.cpp"],
                                  cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        def loops(source):
            return [block(source[match.start():], marker) for match in re.finditer(re.escape(marker), source)]
        self.assertEqual(loops(provider), loops(baseline.stdout))
        callback = block(provider, "void BoosterProvider::remoteControllerStateHandler(")
        self.assertNotIn("printf(", callback)
        self.assertIn("noteRemoteDiagnostic", callback)
        game = (ROOT / "Src/Modules/Infrastructure/GameStateProvider/GameStateProvider.cpp").read_text()
        self.assertLess(game.index("onDiagnosticExit("), game.index("auto setToUnstiff ="))
        motion = (ROOT / "Src/Modules/MotionControl/MotionEngine/MotionEngine.cpp").read_text()
        note = motion.index("noteMotionDiagnostic(")
        self.assertLess(motion.index("if(forceSitDown)"), note)
        self.assertLess(note, motion.index("const bool getUp ="))


if __name__ == "__main__":
    unittest.main()
