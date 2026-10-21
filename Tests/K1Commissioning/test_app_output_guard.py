"""Pure local tests: actual guard header, fake messages; no SDK/DDS/devices."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
INCLUDE = ROOT / "Src/Libs"

HARNESS = r'''
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
// CABSL defines this macro before including the real header in behavior code.
#define state(name) static_assert(false, "CABSL state macro unexpectedly expanded")
#include "Platform/BoosterAppCommissioning.h"
#undef state
#include <array>
#include <cstdlib>
#include <limits>
#include <random>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
namespace C = BoosterAppCommissioning;
static void check(bool value) {if(!value) std::abort();}
struct Motor {
 int modeValue = 255; std::array<float, 6> fields{};
 void mode(int value) {modeValue = value;}
 void q(float value) {fields[0] = value;} float q() const {return fields[0];}
 void dq(float value) {fields[1] = value;} float dq() const {return fields[1];}
 void tau(float value) {fields[2] = value;} float tau_est() const {return fields[2];}
 void kp(float value) {fields[3] = value;}
 void kd(float value) {fields[4] = value;}
 void weight(float value) {fields[5] = value;}
};
enum Serial {serial = 7};
struct Command {
 int type = -1; std::vector<Motor> motors = std::vector<Motor>(22);
 auto& motor_cmd() {return motors;}
 void cmd_type(Serial value) {type = value;}
};
static void zeroed(const Command& command) {
 check(command.type == serial && command.motors.size() == 22);
 for(const auto& motor : command.motors) {
   check(motor.modeValue == 0);
   for(float field : motor.fields) check(std::isfinite(field) && field == 0.f);
 }
}
struct Publisher {
 unsigned calls = 0; bool succeeds = true, throws = false;
 bool Write(Command* command) {
   ++calls; zeroed(*command);
   if(throws) throw std::runtime_error("fake write failure");
   return succeeds;
 }
};
struct Imu {
 std::array<float, 3> angles{}, angular{}, acceleration{};
 const auto& rpy() const {return angles;}
 const auto& gyro() const {return angular;}
 const auto& acc() const {return acceleration;}
};
struct LowState {
 Imu imu; std::vector<Motor> serials = std::vector<Motor>(22), parallels = std::vector<Motor>(22);
 const auto& imu_state() const {return imu;}
 const auto& motor_state_serial() const {return serials;}
 const auto& motor_state_parallel() const {return parallels;}
};
int main(int argc, char** argv) {
 check(argc == 2); const std::string test = argv[1];
 if(test == "normal") {
   check(!C::enabled()); C::requestStop(C::StopReason::f1);
   C::noteMotionFrame(); C::noteOutputFrame(); C::noteCameraFrame(); C::noteLowStateFrame(); C::noteBehaviorFrame();
   Command command; Publisher publisher;
   check(!C::sendZeroLowCommand(publisher, command, serial));
   check(!publisher.calls && command.type == -1 && !C::stopRequested() && !C::motionFrames()
         && !C::outputFrames() && !C::cameraFrames() && !C::lowStateFrames() && !C::behaviorFrames()); return 0;
 }
 C::enable();
 if(test == "random") {
   std::mt19937 random(42); std::uniform_real_distribution<float> value(-100000.f, 100000.f);
   Publisher publisher;
   for(unsigned round = 0; round < 2000; ++round) {
     Command command;
     for(auto& motor : command.motors) {
       motor.modeValue = static_cast<int>(random());
       for(float& field : motor.fields) field = round % 2 ? value(random) : std::numeric_limits<float>::quiet_NaN();
     }
     check(C::sendZeroLowCommand(publisher, command, serial)); zeroed(command);
   }
   check(publisher.calls == 2000 && C::outputFrames() == 2000 && !C::stopRequested());
 } else if(test == "wrong_size") {
   Publisher publisher;
   for(unsigned size : {0u, 1u, 21u, 23u, 29u}) {
     Command command; command.motors.resize(size); check(!C::sendZeroLowCommand(publisher, command, serial));
   }
   check(!publisher.calls && C::stopReason() == static_cast<unsigned>(C::StopReason::outputFault));
   C::enable(); C::requestStop(C::StopReason::f1);
   check(C::enabled() && C::stopReason() == static_cast<unsigned>(C::StopReason::outputFault));
 } else if(test == "write_false" || test == "write_throw") {
   Command command; Publisher publisher; publisher.succeeds = false; publisher.throws = test == "write_throw";
   try {check(!C::sendZeroLowCommand(publisher, command, serial)); check(!publisher.throws);}
   catch(const std::runtime_error&) {check(publisher.throws);}
   check(publisher.calls == 1 && !C::outputFrames() && C::stopReason() == static_cast<unsigned>(C::StopReason::outputFault));
 } else if(test == "health") {
   LowState state; check(C::validLowState(state));
   state.serials.resize(21); check(!C::validLowState(state)); state.serials.resize(22);
   state.parallels.resize(23); check(!C::validLowState(state)); state.parallels.resize(22);
   for(auto* motors : {&state.serials, &state.parallels})
     for(unsigned field = 0; field < 3; ++field) {
       (*motors)[9].fields[field] = std::numeric_limits<float>::quiet_NaN(); check(!C::validLowState(state)); (*motors)[9].fields[field] = 0.f;
     }
   for(auto* axis : {&state.imu.angles, &state.imu.angular, &state.imu.acceleration})
     for(unsigned i = 0; i < 3; ++i) {
       (*axis)[i] = std::numeric_limits<float>::infinity(); check(!C::validLowState(state)); (*axis)[i] = 0.f;
     }
   check(C::validLowState(state)); C::noteLowStateFrame(); check(C::lowStateFrames() == 1 && C::lastLowStateTimeNs() > 0);
 } else if(test == "threads") {
   std::vector<std::thread> workers;
   for(unsigned i = 0; i < 6; ++i) workers.emplace_back([] {
     for(unsigned step = 0; step < 1000; ++step) {
       C::noteMotionFrame(); C::noteOutputFrame(); C::noteCameraFrame(); C::noteLowStateFrame(); C::noteBehaviorFrame();
     }
   });
   for(auto& worker : workers) worker.join();
   check(C::motionFrames() == 6000 && C::outputFrames() == 6000 && C::cameraFrames() == 6000 && C::lowStateFrames() == 6000 && C::behaviorFrames() == 6000);
   C::requestStop(C::StopReason::f1); C::enable(); C::requestStop(C::StopReason::deadline);
   check(C::enabled() && C::stopReason() == static_cast<unsigned>(C::StopReason::f1));
 } else return 2;
 return 0;
}
'''


class AppOutputGuardTests(unittest.TestCase):
    def compile(self, source, output, extra=()):
        result = subprocess.run(["clang++", "-std=c++20", "-pthread", "-Wall", "-Wextra", "-Werror", "-pedantic",
                                 "-I", str(INCLUDE), str(source), *map(str, extra), "-o", str(output)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_actual_header_random_nan_failures_health_and_concurrency_without_sdk(self):
        with tempfile.TemporaryDirectory(prefix="k1-output-fake-") as temporary:
            source, output = Path(temporary) / "test.cpp", Path(temporary) / "test"
            source.write_text(HARNESS)
            self.compile(source, output)
            for case in ("normal", "random", "wrong_size", "write_false", "write_throw", "health", "threads"):
                with self.subTest(case=case):
                    result = subprocess.run([str(output), case], capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_cross_translation_unit_shared_state_and_monolithic_link(self):
        with tempfile.TemporaryDirectory(prefix="k1-output-tu-") as temporary:
            one, two, output = Path(temporary) / "one.cpp", Path(temporary) / "two.cpp", Path(temporary) / "test"
            one.write_text('#include "Platform/BoosterAppCommissioning.h"\nvoid fromOne(){BoosterAppCommissioning::enable();BoosterAppCommissioning::noteMotionFrame();}\n')
            two.write_text('#include "Platform/BoosterAppCommissioning.h"\nvoid fromOne();int main(){fromOne();return !(BoosterAppCommissioning::enabled() && BoosterAppCommissioning::motionFrames()==1);}\n')
            self.compile(one, output, (two,))
            self.assertEqual(subprocess.run([str(output)], timeout=10).returncode, 0)
        self.assertIn("add_library(Platform${TARGET_SUFFIX} OBJECT", (ROOT / "Make/CMake/Platform.cmake").read_text())
        self.assertIn("add_library(B-Human${TARGET_SUFFIX} OBJECT", (ROOT / "Make/CMake/B-Human.cmake").read_text())

    def test_source_fences_are_before_actuation_and_phase_updates(self):
        provider = (ROOT / "Src/Modules/Infrastructure/BoosterProvider/BoosterProvider.cpp").read_text()
        finish = provider[provider.index("void BoosterProvider::finishFrame2()") :]
        self.assertLess(finish.index("sendZeroLowCommand"), finish.index("theJointRequest"))
        self.assertLess(finish.index("return;"), finish.index("DEBUG_RESPONSE"))
        constructor = provider[provider.index("BoosterProvider::BoosterProvider()") : provider.index("BoosterProvider::~BoosterProvider()")]
        self.assertLess(constructor.index("jointMapping.size() == 22"), constructor.index("lowCmdPublisher.InitChannel()"))
        self.assertIn("index >= seen.size() || seen[index]", constructor)
        motion = (ROOT / "Src/Modules/MotionControl/MotionEngine/MotionEngine.cpp").read_text()
        update = motion[motion.index("void MotionEngine::update(JointRequest& jointRequest)") :]
        self.assertLess(update.index("return;"), update.index("phase->update()"))
        self.assertIn("jointRequest.angles.fill(JointAngles::off)", update[:update.index("return;")])
        self.assertIn("stiffnesses.fill(0)", update[:update.index("return;")])
        self.assertIn("StopReason::f1", provider)
        self.assertIn("StopReason::inputFault", provider)


if __name__ == "__main__":
    unittest.main()
