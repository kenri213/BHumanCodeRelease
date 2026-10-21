"""Fresh physical F1 Live interlock: real code, fake inputs, CPU only.

No SDK, DDS, robot, mode RPC, service operation, or ARM executable is used.
Compiled checks deliberately run with NDEBUG and use an explicit abort helper.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "Src/Apps/Booster/Main.cpp"
PROVIDER = ROOT / "Src/Modules/Infrastructure/BoosterProvider/BoosterProvider.cpp"
HEADER = ROOT / "Src/Libs/Platform/BoosterOfficialLiveTest.h"


def block(source, marker):
    start = source.index(marker)
    opening = source.index("{", start)
    index, depth = opening + 1, 1
    while depth:
        depth += (source[index] == "{") - (source[index] == "}")
        index += 1
    return source[start:index]


PREAMBLE = r'''
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#define state(name) static_assert(false, "CABSL macro unexpectedly expanded")
#include "Platform/BoosterOfficialLiveTest.h"
#include "Platform/BoosterAppCommissioning.h"
#include "Platform/BoosterMatchSafety.h"
#undef state
#include <array>
#include <cstdlib>
#include <limits>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
namespace C = BoosterOfficialLiveTest;
static void check(bool value) {if(!value) std::abort();}
constexpr std::int64_t second = 1000000000LL;
static void cognition(std::int64_t now, unsigned count = 5) {
  for(unsigned frame = 1; frame <= count; ++frame)
    C::noteCognitionForF1Authorization(frame, now);
}
[[maybe_unused]] static void grant() {
  C::observePhysicalF1Down(second);
  cognition(2 * second);
  check(C::authorizePhysicalF1Long(2 * second) && C::bodyAuthorized());
}
struct Remote {
  unsigned code = 1;
  bool pressedX = false;
  std::array<float, 4> axes{};
  unsigned event() const {return code;}
  bool x() const {return pressedX;}
  float lx() const {return axes[0];} float ly() const {return axes[1];}
  float rx() const {return axes[2];} float ry() const {return axes[3];}
};
'''


HEADER_HARNESS = PREAMBLE + r'''
int main(int argc, char** argv) {
  check(argc == 2); const std::string test = argv[1];
  check(!C::enabled() && !C::stopRequested() && !C::bodyAuthorized());
  if(test == "default") {
    C::observePhysicalF1Down(second); cognition(2 * second);
    check(!C::authorizePhysicalF1Long(2 * second));
    C::observePhysicalF1Release(); C::requestStop();
    Remote input; input.pressedX = true; C::observeRemoteStopX(input, 2u);
    check(!C::enabled() && !C::stopRequested() && !C::bodyAuthorized());
    C::enable(); check(!C::bodyAuthorized() && !C::stopRequested());
    check(!C::authorizePhysicalF1Long(2 * second)); return 0;
  }
  C::enable();
  if(test == "isolated_long") {
    cognition(2 * second); check(!C::authorizePhysicalF1Long(2 * second));
  } else if(test == "release_cancels") {
    C::observePhysicalF1Down(second); cognition(2 * second);
    C::observePhysicalF1Release(); check(!C::authorizePhysicalF1Long(2 * second));
  } else if(test == "missing_cognition") {
    C::observePhysicalF1Down(second); check(!C::authorizePhysicalF1Long(2 * second));
  } else if(test == "four_frames") {
    C::observePhysicalF1Down(second); cognition(2 * second, 4);
    check(!C::authorizePhysicalF1Long(2 * second));
  } else if(test == "duplicate_frames") {
    C::observePhysicalF1Down(second);
    for(unsigned frame : {1u, 1u, 0u, 2u, 2u, 1u, 3u, 4u, 4u})
      C::noteCognitionForF1Authorization(frame, 2 * second);
    check(!C::authorizePhysicalF1Long(2 * second));
    C::noteCognitionForF1Authorization(5, 2 * second);
    check(C::authorizePhysicalF1Long(2 * second) && C::bodyAuthorized());
    return 0;
  } else if(test == "stale_cognition") {
    C::observePhysicalF1Down(second); cognition(second);
    check(!C::authorizePhysicalF1Long(2 * second));
  } else if(test == "future_cognition") {
    C::observePhysicalF1Down(second); cognition(2 * second + 1);
    check(!C::authorizePhysicalF1Long(2 * second));
  } else if(test == "future_down") {
    C::observePhysicalF1Down(2 * second + 1); cognition(2 * second);
    check(!C::authorizePhysicalF1Long(2 * second));
  } else if(test == "expired_down") {
    C::observePhysicalF1Down(second); cognition(11 * second + 1);
    check(!C::authorizePhysicalF1Long(11 * second + 1));
  } else if(test == "invalid_down") {
    cognition(2 * second);
    for(const auto time : {std::int64_t{0}, std::int64_t{-1}, 2 * second}) {
      C::observePhysicalF1Down(time); check(!C::authorizePhysicalF1Long(2 * second));
    }
  } else if(test == "invalid_cognition") {
    C::observePhysicalF1Down(second);
    for(unsigned frame = 1; frame <= 10; ++frame)
      C::noteCognitionForF1Authorization(frame, frame % 2 ? 0 : -1);
    check(!C::authorizePhysicalF1Long(2 * second));
  } else if(test == "down_limit") {
    C::observePhysicalF1Down(second); cognition(11 * second);
    check(C::authorizePhysicalF1Long(11 * second) && C::bodyAuthorized()); return 0;
  } else if(test == "cognition_limit") {
    C::observePhysicalF1Down(second); cognition(1500000000LL);
    check(C::authorizePhysicalF1Long(2 * second) && C::bodyAuthorized()); return 0;
  } else if(test == "past_cognition_limit") {
    C::observePhysicalF1Down(second); cognition(1499999999LL);
    check(!C::authorizePhysicalF1Long(2 * second));
  } else if(test == "grant_release") {
    grant(); C::observePhysicalF1Release(); C::enable();
    check(C::bodyAuthorized() && !C::stopRequested());
    check(!C::authorizePhysicalF1Long(2 * second)); return 0;
  } else if(test == "down_revokes") {
    grant(); C::observePhysicalF1Down(2 * second);
    check(!C::bodyAuthorized() && !C::authorizePhysicalF1Long(2 * second));
    C::observePhysicalF1Release();
    check(!C::authorizePhysicalF1Long(2 * second + 1));
  } else if(test == "stop" || test == "x" || test == "remove" || test == "bad_axis") {
    grant();
    if(test == "stop") C::requestStop();
    else {
      Remote input;
      if(test == "x") input.pressedX = true;
      else if(test == "remove") input.code = 2;
      else input.axes[2] = std::numeric_limits<float>::quiet_NaN();
      C::observeRemoteStopX(input, 2u);
    }
    check(C::stopRequested() && !C::bodyAuthorized());
    C::enable(); C::observePhysicalF1Down(3 * second);
    C::noteCognitionForF1Authorization(6, 4 * second);
    check(!C::authorizePhysicalF1Long(4 * second));
    C::observePhysicalF1Release();
    check(C::stopRequested() && !C::bodyAuthorized()); return 0;
  } else if(test == "race_stop_grant") {
    C::observePhysicalF1Down(second); cognition(2 * second);
    std::atomic<bool> start{false}; std::vector<std::thread> threads;
    for(unsigned worker = 0; worker < 12; ++worker)
      threads.emplace_back([&] {
        while(!start.load()) std::this_thread::yield();
        for(unsigned turn = 0; turn < 300; ++turn)
          static_cast<void>(C::authorizePhysicalF1Long(2 * second));
      });
    threads.emplace_back([&] {
      while(!start.load()) std::this_thread::yield(); C::requestStop();
    });
    start.store(true); for(auto& thread : threads) thread.join();
    check(C::stopRequested() && !C::bodyAuthorized());
    C::enable(); C::observePhysicalF1Down(second);
    check(!C::authorizePhysicalF1Long(2 * second)); return 0;
  } else if(test == "diagnostic_cognition") {
    for(unsigned frame = 1; frame <= 5; ++frame)
      C::noteGameDiagnostic(frame, 0, 0, 0, false);
    const auto now = C::steadyTimeNs(); check(now > 0);
    C::observePhysicalF1Down(now);
    check(C::authorizePhysicalF1Long(now + 1) && C::bodyAuthorized()); return 0;
  } else return 2;
  check(!C::bodyAuthorized() && !C::stopRequested()); return 0;
}
'''


class FreshF1GuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="k1-fresh-f1-cpu-")
        cls.directory = Path(cls.temporary.name)
        cls.main = MAIN.read_text()
        cls.provider = PROVIDER.read_text()
        cls.header = HEADER.read_text()
        cls.compile_cpp("header", HEADER_HARNESS)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @classmethod
    def compile_cpp(cls, name, source):
        input_path, output = cls.directory / (name + ".cpp"), cls.directory / name
        input_path.write_text(source)
        result = subprocess.run(["clang++", "-std=c++20", "-pthread", "-DNDEBUG", "-Wall", "-Wextra",
                                 "-Werror", "-pedantic", "-I", str(ROOT / "Src/Libs"),
                                 str(input_path), "-o", str(output)], capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stderr)
        return output

    def run_cases(self, binary, cases, repetitions=1):
        for case in cases:
            for repetition in range(repetitions):
                with self.subTest(case=case, repetition=repetition):
                    result = subprocess.run([str(self.directory / binary), case], capture_output=True,
                                            text=True, timeout=15)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_default_disabled_is_noop(self):
        self.run_cases("header", ("default",))

    def test_missing_cancelled_repeated_or_frozen_evidence_is_denied(self):
        self.run_cases("header", ("isolated_long", "release_cancels", "missing_cognition", "four_frames",
                                  "duplicate_frames", "invalid_cognition"))

    def test_expired_future_and_boundary_timestamps(self):
        self.run_cases("header", ("stale_cognition", "future_cognition", "future_down", "expired_down",
                                  "invalid_down", "down_limit", "cognition_limit", "past_cognition_limit"))

    def test_five_fresh_frames_grant_release_preserves_down_revokes(self):
        self.run_cases("header", ("grant_release", "down_revokes", "diagnostic_cognition"))

    def test_stop_x_remove_bad_axis_never_rearm(self):
        self.run_cases("header", ("stop", "x", "remove", "bad_axis"))

    def test_stop_grant_race_finishes_irrevocably_stopped(self):
        self.run_cases("header", ("race_stop_grant",), repetitions=20)

    def test_actual_main_callback_requires_physical_down_then_allowed_long(self):
        callback = block(self.main, "static void buttonEventHandler(const void* msg)")
        harness = PREAMBLE + r'''
namespace Main {void stopOfficialLiveTest() {C::requestStop();}}
namespace booster_interface::msg {
  enum ButtonEventType {PRESS_DOWN, PRESS_UP, LONG_PRESS_START, LONG_PRESS_END};
  struct ButtonEventMsg {
    unsigned buttonValue = 0; ButtonEventType eventValue = PRESS_DOWN;
    unsigned button() const {return buttonValue;}
    ButtonEventType event() const {return eventValue;}
  };
}
struct EmergencyHandler {
  enum State {noEmergency, emergencyPressed, emergencyReleased};
  std::atomic<State> state{emergencyReleased}; unsigned changes = 0;
  void setState(State target, bool = true) {state.store(target); ++changes;}
};
static EmergencyHandler emergencyHandler;
''' + callback + r'''
static void button(booster_interface::msg::ButtonEventType event, unsigned index = 0) {
  booster_interface::msg::ButtonEventMsg input; input.eventValue = event; input.buttonValue = index;
  buttonEventHandler(&input);
}
static void freshFrames() {cognition(C::steadyTimeNs());}
int main(int argc, char** argv) {
  check(argc == 2); const std::string test = argv[1];
  using namespace booster_interface::msg;
  if(test == "default") {
    button(LONG_PRESS_START); check(emergencyHandler.state == EmergencyHandler::noEmergency);
    check(!C::enabled() && !C::bodyAuthorized()); return 0;
  }
  C::enable();
  if(test == "isolated_long") {
    freshFrames(); button(LONG_PRESS_START);
  } else if(test == "wrong_button") {
    button(PRESS_DOWN, 1); freshFrames(); button(LONG_PRESS_START, 1);
  } else if(test == "released_pending") {
    button(PRESS_DOWN); freshFrames(); button(PRESS_UP); button(LONG_PRESS_START);
  } else if(test == "wrong_state") {
    emergencyHandler.state = EmergencyHandler::emergencyPressed;
    button(PRESS_DOWN); freshFrames(); button(LONG_PRESS_START);
    check(emergencyHandler.state == EmergencyHandler::emergencyPressed && !C::bodyAuthorized());
    button(PRESS_UP); button(LONG_PRESS_START);
  } else if(test == "stop") {
    C::requestStop(); button(PRESS_DOWN); freshFrames(); button(LONG_PRESS_START);
  } else if(test == "valid" || test == "revoke") {
    button(PRESS_DOWN); freshFrames(); button(LONG_PRESS_START);
    check(C::bodyAuthorized() && emergencyHandler.state == EmergencyHandler::noEmergency);
    button(PRESS_UP); check(C::bodyAuthorized());
    if(test == "valid") return 0;
    button(PRESS_DOWN);
    check(!C::bodyAuthorized() && emergencyHandler.state == EmergencyHandler::emergencyPressed);
    button(PRESS_UP); button(LONG_PRESS_START);
  } else return 2;
  check(!C::bodyAuthorized() && emergencyHandler.state == EmergencyHandler::emergencyReleased);
  return 0;
}
'''
        self.compile_cpp("main_callback", harness)
        self.run_cases("main_callback", ("default", "isolated_long", "wrong_button", "released_pending",
                                          "wrong_state", "stop", "valid", "revoke"))

    def test_actual_provider_unarmed_lambda_zeroes_all_fields_and_fails_closed(self):
        finish = block(self.provider, "void BoosterProvider::finishFrame2()")
        zero_lambda = block(finish, "const auto writeUnarmedLiveZero = [&]") + ";"
        guard = block(finish, "if(BoosterOfficialLiveTest::enabled() && !BoosterOfficialLiveTest::bodyAuthorized())")
        harness = PREAMBLE + r'''
namespace booster_interface::msg {enum Serial {SERIAL = 17};}
namespace Main {unsigned stops = 0; void stopOfficialLiveTest() {++stops;}}
struct Motor {
  int modeValue = 255;
  std::array<float, 6> fields = {1.f, -2.f, 3.f, 4.f, 5.f, 6.f};
  void mode(int value) {modeValue = value;}
  void q(float value) {fields[0] = value;} void dq(float value) {fields[1] = value;}
  void tau(float value) {fields[2] = value;} void kp(float value) {fields[3] = value;}
  void kd(float value) {fields[4] = value;} void weight(float value) {fields[5] = value;}
};
struct Command {
  int type = -1; std::vector<Motor> motors = std::vector<Motor>(22);
  auto& motor_cmd() {return motors;}
  void cmd_type(booster_interface::msg::Serial value) {type = value;}
};
static void zeroed(const Command& command) {
  check(command.type == booster_interface::msg::SERIAL && command.motors.size() == 22);
  for(const auto& motor : command.motors) {
    check(motor.modeValue == 0);
    for(float field : motor.fields) check(std::isfinite(field) && field == 0.f);
  }
}
struct Publisher {
  unsigned calls = 0; bool succeeds = true, throws = false;
  bool Write(Command* command) {
    ++calls; zeroed(*command);
    if(throws) throw std::runtime_error("fake output failure");
    return succeeds;
  }
};
struct Provider {
  Command lowCmd; Publisher lowCmdPublisher;
  struct {unsigned executedPhase = 3;} theMotionInfo;
  unsigned normalWrites = 0;
  void finish(bool revokeDuringCalculation = false) {
''' + zero_lambda + r'''
    if(revokeDuringCalculation) C::observePhysicalF1Down(3 * second);
''' + guard + r'''
    ++normalWrites;
  }
};
int main(int argc, char** argv) {
  check(argc == 2); const std::string test = argv[1]; Provider provider;
  if(test == "default") {
    provider.finish(); check(provider.normalWrites == 1 && !provider.lowCmdPublisher.calls);
    check(!C::bodyAuthorized() && !Main::stops); return 0;
  }
  C::enable();
  if(test == "armed") {
    grant(); provider.finish(); check(provider.normalWrites == 1 && !provider.lowCmdPublisher.calls); return 0;
  }
  if(test == "bad_size") provider.lowCmd.motors.resize(23);
  else if(test == "write_false") provider.lowCmdPublisher.succeeds = false;
  else if(test == "write_exception") provider.lowCmdPublisher.throws = true;
  else if(test == "revoke") grant();
  else if(test != "zero_22") return 2;
  for(auto& motor : provider.lowCmd.motors)
    motor.fields.fill(std::numeric_limits<float>::quiet_NaN());
  bool exception = false;
  try {provider.finish(test == "revoke");}
  catch(const std::runtime_error&) {exception = true;}
  check(!provider.normalWrites && !C::bodyAuthorized());
  if(test == "bad_size" || test == "write_false" || test == "write_exception") {
    check(C::stopRequested() && Main::stops == 1 && !C::outputFrames());
    check(provider.lowCmdPublisher.calls == (test == "bad_size" ? 0u : 1u));
    check(exception == (test == "write_exception"));
  } else {
    zeroed(provider.lowCmd);
    check(!exception && !C::stopRequested() && !Main::stops && provider.lowCmdPublisher.calls == 1);
    check(C::outputFrames() == 1 && C::lastPhase() == 3 && C::maxKp() == 0.f);
  }
  check(!BoosterAppCommissioning::enabled()); return 0;
}
'''
        self.compile_cpp("provider_lambda", harness)
        self.run_cases("provider_lambda", ("default", "armed", "zero_22", "bad_size", "write_false",
                                            "write_exception", "revoke"))

    def test_authorization_call_sites_are_physical_only_and_diagnostics_are_wired(self):
        callback = block(self.main, "static void buttonEventHandler(const void* msg)")
        self.assertEqual(self.main.count("authorizePhysicalF1Long("), 1)
        self.assertEqual(self.main.count("observePhysicalF1Down("), 1)
        self.assertEqual(self.main.count("observePhysicalF1Release("), 1)
        physical = block(callback, "if(buttonEvent.button() == 0)")
        self.assertIn("observePhysicalF1Down", physical)
        self.assertIn("observePhysicalF1Release", physical)
        released = physical[physical.index("case EmergencyHandler::emergencyReleased:"):]
        self.assertIn("LONG_PRESS_START", released)
        self.assertIn("authorizePhysicalF1Long", released)
        self.assertLess(released.index("authorizePhysicalF1Long"), released.index("setState(EmergencyHandler::noEmergency)"))
        remote = block(self.provider, "void BoosterProvider::remoteControllerStateHandler(")
        for forbidden in ("observePhysicalF1Down", "observePhysicalF1Release", "authorizePhysicalF1Long"):
            self.assertNotIn(forbidden, remote)
        game_note = block(self.header, "inline void noteGameDiagnostic(")
        self.assertIn("noteCognitionForF1Authorization(time, steadyTimeNs())", game_note)
        self.assertIn("stop=%d armed=%d", self.main)

    def test_two_unarmed_output_fences_preserve_both_existing_stop_fences(self):
        finish = block(self.provider, "void BoosterProvider::finishFrame2()")
        marker = "if(BoosterOfficialLiveTest::enabled() && !BoosterOfficialLiveTest::bodyAuthorized())"
        self.assertEqual(finish.count(marker), 2)
        self.assertEqual(finish.count("if(BoosterOfficialLiveTest::stopRequested())"), 2)
        first = finish.index(marker)
        second_guard = finish.index(marker, first + len(marker))
        normal_calculation = finish.index("theJointRequest")
        final_write = finish.index("VERIFY(lowCmdPublisher.Write(&lowCmd));", second_guard)
        self.assertLess(first, normal_calculation)
        self.assertLess(normal_calculation, second_guard)
        self.assertLess(second_guard, final_write)
        for at in (first, second_guard):
            fence = block(finish[at:], marker)
            self.assertIn("writeUnarmedLiveZero();", fence)
            self.assertIn("return;", fence)
        zero_lambda = block(finish, "const auto writeUnarmedLiveZero = [&]")
        self.assertIn("~StopOnFailedZeroOutput()", zero_lambda)
        self.assertIn("BoosterOfficialLiveTest::requestStop();", zero_lambda)
        self.assertIn("Main::stopOfficialLiveTest();", zero_lambda)
        self.assertIn("noteOutput(static_cast<unsigned>(theMotionInfo.executedPhase), 0.f)", zero_lambda)
        self.assertLess(zero_lambda.index("lowCmdPublisher.Write(&lowCmd)"), zero_lambda.index("attempt.completed = true"))
        for forbidden in ("ChangeMode(", "Publisher", "Write("):
            self.assertNotIn(forbidden, self.header)


if __name__ == "__main__":
    unittest.main()
