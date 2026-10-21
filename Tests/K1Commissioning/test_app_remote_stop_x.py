"""Actual X-only Provider guard + header; fake remote, no SDK/DDS/devices."""
from pathlib import Path
import hashlib
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
HEADER = ROOT / "Src/Libs/Platform/BoosterAppCommissioning.h"
PROVIDER = ROOT / "Src/Modules/Infrastructure/BoosterProvider/BoosterProvider.cpp"


def block(source, marker):
    start = source.index(marker)
    opening = source.index("{", start)
    index, depth = opening + 1, 1
    while depth:
        depth += (source[index] == "{") - (source[index] == "}")
        index += 1
    return source[start:index]


class AppRemoteStopTests(unittest.TestCase):
    def test_real_header_and_provider_branch_conditions_without_sdk(self):
        callback = block(PROVIDER.read_text(), "void BoosterProvider::remoteControllerStateHandler(")
        branch = block(callback, "if(BoosterAppCommissioning::enabled())")
        harness = r'''
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#define state(name) static_assert(false, "CABSL macro expanded")
#include "Platform/BoosterAppCommissioning.h"
#undef state
#include <array>
#include <cstdlib>
#include <limits>
#include <string>
namespace C = BoosterAppCommissioning;
namespace booster::robot::b1 {constexpr unsigned REMOVE = 0x606;}
static void check(bool value) {if(!value) std::abort();}
struct Remote {
 unsigned eventValue = 0x603;
 std::array<float,4> axes{};
 bool pressedX = false, pressedB = false;
 bool otherKeys = true;
 unsigned event() const {return eventValue;}
 float lx() const {return axes[0];} float ly() const {return axes[1];}
 float rx() const {return axes[2];} float ry() const {return axes[3];}
 bool b() const {return pressedB;}
 bool x() const {return pressedX;}
};
struct Provider {
 unsigned normalActions = 0;
 void callback(const Remote& remoteControllerState) {
''' + branch + r'''
 ++normalActions;
 }
};
int main(int argc, char** argv) {
 check(argc == 2); const std::string mode = argv[1];
 static_assert(static_cast<unsigned>(C::StopReason::f1) == 1);
 static_assert(static_cast<unsigned>(C::StopReason::remoteStop) == 7);
 Provider provider; Remote remote;
 check(!C::remoteStopXEnabled()); C::enableRemoteStopX(); check(!C::remoteStopXEnabled());
 if(mode == "normal") {
   provider.callback(remote); check(provider.normalActions == 1 && !C::remoteFrames()); return 0;
 }
 C::enable(); check(!C::remoteStopXEnabled());
 if(mode == "disabled") {
   remote.pressedX = true; provider.callback(remote);
   remote.eventValue = 0x606; remote.axes[0] = std::numeric_limits<float>::quiet_NaN(); provider.callback(remote);
   check(!C::stopRequested() && !C::remoteFrames() && !provider.normalActions); return 0;
 }
 C::enableRemoteStopX(); check(C::remoteStopXEnabled());
 if(mode == "valid") {
   remote.axes = {1.f,-1.f,0.7f,-0.8f};
   provider.callback(remote);
   remote.eventValue = 0x604; provider.callback(remote);
   check(C::remoteFrames() == 2 && C::remoteXFalseSamples() == 2 && !C::remoteXPresses()
         && C::firstRemoteXFalseTimeNs() > 0 && !C::stopRequested() && !provider.normalActions);
 } else if(mode == "b_only") {
   remote.pressedB = true; remote.pressedX = false; provider.callback(remote);
   check(!C::stopRequested() && C::remoteFrames() == 1 && !C::remoteXPresses()
         && C::remoteXFalseSamples() == 1 && !provider.normalActions);
 } else if(mode == "x" || mode == "initial_x") {
   if(mode == "x") provider.callback(remote);
   const auto before = std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
   remote.pressedX = true; provider.callback(remote);
   check(C::stopReason() == 7 && C::remoteXPresses() == 1 && C::firstRemoteXTimeNs() >= before);
   check(C::remoteXFalseSamples() == (mode == "x" ? 1u : 0u));
   if(mode == "x") check(C::firstRemoteXFalseTimeNs() > 0 && C::firstRemoteXFalseTimeNs() < C::firstRemoteXTimeNs());
   else check(!C::firstRemoteXFalseTimeNs());
   const auto first = C::firstRemoteXTimeNs();
   remote.pressedX = false; provider.callback(remote); C::enable(); C::enableRemoteStopX();
   check(C::stopReason() == 7 && C::firstRemoteXTimeNs() == first && !provider.normalActions);
 } else if(mode == "remove" || mode == "bad0" || mode == "bad1" || mode == "bad2" || mode == "bad3") {
   remote.pressedX = true;
   if(mode == "remove") remote.eventValue = 0x606;
   else remote.axes[mode.back()-'0'] = mode.back()%2 ? std::numeric_limits<float>::infinity() : std::numeric_limits<float>::quiet_NaN();
   provider.callback(remote);
   check(C::stopReason() == static_cast<unsigned>(C::StopReason::inputFault) && !C::remoteFrames()
         && !C::remoteXPresses() && !C::firstRemoteXTimeNs() && !provider.normalActions);
 } else if(mode == "f1_first") {
   C::requestStop(C::StopReason::f1); remote.pressedX = true; provider.callback(remote);
   check(C::stopReason() == 1 && C::remoteXPresses() == 1 && !provider.normalActions);
 } else return 2;
 return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="k1-remote-stop-pure-") as temporary:
            source, executable = Path(temporary) / "test.cpp", Path(temporary) / "test"
            source.write_text(harness)
            compiled = subprocess.run(["clang++", "-std=c++20", "-Wall", "-Wextra", "-Werror", "-pedantic",
                                       "-I", str(ROOT / "Src/Libs"), str(source), "-o", str(executable)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            for case in ("normal", "disabled", "valid", "b_only", "x", "initial_x", "remove", "bad0", "bad1", "bad2", "bad3", "f1_first"):
                with self.subTest(case=case):
                    result = subprocess.run([str(executable), case], capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_zero_output_helpers_are_byte_for_byte_unchanged(self):
        header = HEADER.read_text()
        expected = {
            "template<typename Command, typename Serial> bool zeroLowCommand": "151a22c4f4ef276db7ec5ca13cb8a7c5c2584b11b7283655bac9363415914e93",
            "template<typename Publisher, typename Command, typename Serial>": "2a08db071fceec564f27c71a22a21597759079c8dd3bb4b8ee6924cfc8cc830e",
        }
        for marker, digest in expected.items():
            self.assertEqual(hashlib.sha256(("  " + block(header, marker) + "\n").encode()).hexdigest(), digest)
        callback = block(PROVIDER.read_text(), "void BoosterProvider::remoteControllerStateHandler(")
        branch = block(callback, "if(BoosterAppCommissioning::enabled())")
        self.assertIn("handleRemoteStopX", branch)
        self.assertIn("return;", branch)
        self.assertNotIn("joystickState", branch)
        self.assertNotIn("ChangeMode", branch)
        self.assertNotIn("Write(", branch)


if __name__ == "__main__":
    unittest.main()
