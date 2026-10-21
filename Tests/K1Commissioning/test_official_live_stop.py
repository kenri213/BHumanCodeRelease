"""Independent official overlay, actual Provider branch and fake input only."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PROVIDER = ROOT / "Src/Modules/Infrastructure/BoosterProvider/BoosterProvider.cpp"


def block(source, marker):
    start = source.index(marker)
    opening = source.index("{", start)
    index, depth = opening + 1, 1
    while depth:
        depth += (source[index] == "{") - (source[index] == "}")
        index += 1
    return source[start:index]


class OfficialLiveStopTests(unittest.TestCase):
    def test_actual_provider_overlay_keeps_normal_inputs_and_only_stops(self):
        callback = block(PROVIDER.read_text(), "void BoosterProvider::remoteControllerStateHandler(")
        old_guard = block(callback, "if(BoosterAppCommissioning::enabled())")
        live_guard = block(callback, "if(BoosterOfficialLiveTest::enabled())")
        harness = r'''
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#define state(name) static_assert(false, "CABSL state macro expanded")
#include "Platform/BoosterOfficialLiveTest.h"
#include "Platform/BoosterAppCommissioning.h"
#undef state
#include <array>
#include <cstdlib>
#include <limits>
#include <string>
namespace C = BoosterOfficialLiveTest;
namespace booster::robot::b1 {constexpr unsigned REMOVE = 0x606;}
namespace Main {unsigned stops = 0; void stopOfficialLiveTest() {++stops;}}
static void check(bool value) {if(!value) std::abort();}
struct Remote {
 unsigned eventValue = 0x603;
 std::array<float,4> axes{};
 bool xPressed = false, bPressed = false, aPressed = false;
 unsigned event() const {return eventValue;}
 float lx() const {return axes[0];} float ly() const {return axes[1];}
 float rx() const {return axes[2];} float ry() const {return axes[3];}
 bool x() const {return xPressed;} bool b() const {return bPressed;} bool a() const {return aPressed;}
 bool y() const {return false;} bool lb() const {return false;} bool rb() const {return false;}
 bool lt() const {return false;} bool rt() const {return false;}
 bool back() const {return false;} bool start() const {return false;}
 bool ls() const {return false;} bool rs() const {return false;}
};
struct Provider {
 unsigned normalInputs = 0;
 void callback(const Remote& remoteControllerState) {
''' + old_guard + "\n" + live_guard + r'''
 ++normalInputs;
 }
};
int main(int argc, char** argv) {
 check(argc == 2); const std::string mode = argv[1];
 Provider provider; Remote input;
 check(!C::enabled() && !C::stopRequested() && !BoosterAppCommissioning::enabled());
 if(mode == "default") {
   input.xPressed = true; C::requestStop(); provider.callback(input);
   check(provider.normalInputs == 1 && !Main::stops && !C::stopRequested()); return 0;
 }
 if(mode == "old_damp") {
   BoosterAppCommissioning::enable(); C::enable(); input.xPressed = true; provider.callback(input);
   check(!provider.normalInputs && !Main::stops && !C::stopRequested()); return 0;
 }
 C::enable(); check(C::enabled() && !BoosterAppCommissioning::enabled());
 if(mode == "pass") {
   input.axes = {1.f,-1.f,.5f,-.5f}; input.bPressed = input.aPressed = true;
   provider.callback(input); check(provider.normalInputs == 1 && !Main::stops && !C::stopRequested());
   C::noteOutput(2, 10.f); C::noteOutput(3, 8.f);
   check(C::outputFrames() == 2 && C::lastPhase() == 3 && C::maxKp() == 10.f);
 } else {
   if(mode == "x") input.xPressed = true;
   else if(mode == "remove") input.eventValue = 0x606;
   else if(mode.size() == 4 && mode.substr(0,3) == "bad") input.axes[mode.back()-'0'] = std::numeric_limits<float>::quiet_NaN();
   else return 2;
   provider.callback(input);
   check(C::stopRequested() && Main::stops == 1 && !provider.normalInputs && !BoosterAppCommissioning::enabled());
   input.xPressed = false; input.eventValue = 0x604; input.axes.fill(0.f); C::enable(); provider.callback(input);
   check(C::stopRequested() && Main::stops == 2 && !provider.normalInputs);
 }
 return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="k1-official-live-pure-") as temporary:
            source, executable = Path(temporary) / "test.cpp", Path(temporary) / "test"
            source.write_text(harness)
            compiled = subprocess.run(["clang++", "-std=c++20", "-Wall", "-Wextra", "-Werror", "-pedantic",
                                       "-I", str(ROOT / "Src/Libs"), str(source), "-o", str(executable)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            for case in ("default", "old_damp", "pass", "x", "remove", "bad0", "bad1", "bad2", "bad3"):
                with self.subTest(case=case):
                    result = subprocess.run([str(executable), case], capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_stopped_output_has_precalculation_and_final_write_fences(self):
        source = PROVIDER.read_text()
        finish = block(source, "void BoosterProvider::finishFrame2()")
        self.assertEqual(finish.count("if(BoosterOfficialLiveTest::stopRequested())"), 2)
        first = block(finish, "if(BoosterOfficialLiveTest::stopRequested())")
        self.assertIn("zeroLowCommand", first)
        self.assertIn("lowCmdPublisher.Write(&lowCmd)", first)
        self.assertIn("return;", first)
        self.assertLess(finish.index(first), finish.index("theJointRequest"))
        self.assertIn("noteOutput(static_cast<unsigned>(theMotionInfo.executedPhase), frameMaxKp)", finish)
        header = (ROOT / "Src/Libs/Platform/BoosterOfficialLiveTest.h").read_text()
        self.assertNotIn("BoosterAppCommissioning::enable", header)
        self.assertNotIn("ChangeMode", header)
        self.assertNotIn("Write(", header)


if __name__ == "__main__":
    unittest.main()
