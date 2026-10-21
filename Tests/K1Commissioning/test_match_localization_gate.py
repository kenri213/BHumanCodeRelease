"""SDK-free tests of actual match permission, evidence and human gesture code."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]

HARNESS = r'''
#include "Platform/BoosterMatchSafety.h"
#include <array>
#include <atomic>
#include <cstdlib>
#include <limits>
#include <string>
#include <thread>
#include <vector>
namespace M = BoosterMatchSafety;
static void check(bool value) {if(!value) std::abort();}
static M::EligibilityInput input() {
  M::EligibilityInput i;
  i.gcActive = i.readyOrSet = i.rawReadyOrSet = i.authorized = true;
  i.stableStand = i.poseQuality = i.poseFinite = true;
  i.stopped = i.penalized = false;
  i.now = 10000; i.packetTime = 9000; i.frameTime = 2000;
  i.positionStdMm = 100.f; i.headingStdRad = .05f; return i;
}
static M::VisualEvidence evidence(std::uint64_t epoch) {return {epoch, 1900, 6, 1, 2, 3};}
int main(int argc, char** argv) {
  check(argc == 2); const std::string test = argv[1];
  const auto originalEpoch = M::epoch();
  check(!M::enabled() && !M::confirmed() && !M::confirmationGeneration());
  if(test == "default") {
    for(unsigned address : {0u, 0xc0a8321du, 0xc0a83224u, 0xc0a83225u, 0xc0a83226u, 0xffffffffu})
      check(M::acceptGameControllerSource(address));
    M::invalidateConfirmation(); M::noteGcInitial(true);
    M::noteVisualRegistration(100, false, 1, 1, 1);
    check(!M::confirm(originalEpoch) && !M::consumeJoystickRelease());
    check(M::epoch() == originalEpoch && !M::confirmed() && !M::visualEvidence().consecutiveFrames);
    return 0;
  }
  M::enable(); const auto epoch = M::epoch();
  if(test == "gc_source") {
    check(M::allowedGameControllerAddress == 0xc0a83225u);
    check(M::acceptGameControllerSource(0xc0a83225u));
    check(M::confirm(epoch));
    for(unsigned address : {0u, 0xc0a8321du, 0xc0a83224u, 0xc0a83226u, 0x2532a8c0u, 0xffffffffu}) {
      check(!M::acceptGameControllerSource(address));
      check(M::confirmed() && M::epoch() == epoch && M::confirmationGeneration() == 1);
    }
  } else if(test == "epochs") {
    check(M::confirm(epoch) && M::confirmed());
    check(!M::confirm(epoch) && M::confirmationGeneration() == 1);
    M::invalidateConfirmation(); check(M::epoch() == epoch + 1 && !M::confirmed());
    check(!M::confirm(epoch) && M::confirmationGeneration() == 1);
    check(M::confirm(epoch + 1) && M::confirmationGeneration() == 2);
    M::invalidateConfirmation(); M::invalidateConfirmation();
    check(M::epoch() == epoch + 3 && !M::confirmed());
    M::enable(); check(!M::confirmed() && M::epoch() == epoch + 3);
  } else if(test == "initial_transition") {
    check(M::confirm(epoch)); M::noteGcInitial(false);
    check(M::confirmed() && M::epoch() == epoch);
    M::noteGcInitial(true); check(!M::confirmed() && M::epoch() == epoch + 1);
    M::noteGcInitial(true); check(M::epoch() == epoch + 1);
    check(M::confirm(epoch + 1)); M::noteGcInitial(false);
    check(M::confirmed()); M::noteGcInitial(true);
    check(!M::confirmed() && M::epoch() == epoch + 2);
  } else if(test == "visual_distinct") {
    M::noteVisualRegistration(100, true, 4, 5, 6);
    check(M::visualEvidence().consecutiveFrames == 0);
    M::noteVisualRegistration(200, false, 1, 0, 0);
    for(unsigned frame : {200u, 199u, 0u}) M::noteVisualRegistration(frame, false, 0, 0, 9);
    auto v = M::visualEvidence(); check(v.frameTime == 200 && v.consecutiveFrames == 1 && v.poses == 1 && v.lines == 0);
    for(unsigned frame = 250; frame <= 450; frame += 50) M::noteVisualRegistration(frame, false, 0, 0, 3);
    v = M::visualEvidence(); check(v.epoch == epoch && v.frameTime == 450 && v.consecutiveFrames == 6 && v.lines == 3);
  } else if(test == "visual_reset") {
    for(unsigned frame = 100; frame <= 350; frame += 50) M::noteVisualRegistration(frame, false, 0, 1, 0);
    check(M::visualEvidence().consecutiveFrames == 6);
    M::noteVisualRegistration(850, false, 0, 1, 0); check(M::visualEvidence().consecutiveFrames == 7);
    M::noteVisualRegistration(1351, false, 0, 1, 0); check(M::visualEvidence().consecutiveFrames == 1);
    M::noteVisualRegistration(1400, false, 0, 0, 0);
    check(M::visualEvidence().frameTime == 1351 && M::visualEvidence().consecutiveFrames == 1);
    M::noteVisualRegistration(1852, false, 0, 0, 0); check(!M::visualEvidence().consecutiveFrames);
    M::noteVisualRegistration(1900, false, 0, 0, 2); check(M::visualEvidence().consecutiveFrames == 1);
    M::noteVisualRegistration(1950, true, 1, 1, 1); check(!M::visualEvidence().consecutiveFrames);
    M::invalidateConfirmation(); M::noteVisualRegistration(1, false, 1, 0, 0);
    check(M::visualEvidence().epoch == epoch + 1 && M::visualEvidence().frameTime == 1 && M::visualEvidence().consecutiveFrames == 1);
  } else if(test == "visual_transient") {
    for(unsigned frame = 100; frame <= 350; frame += 50) M::noteVisualRegistration(frame, false, 1, 2, 3);
    for(unsigned frame : {400u, 800u, 850u}) {
      M::noteVisualRegistration(frame, false, 0, 0, 0);
      const auto v = M::visualEvidence();
      check(v.epoch == epoch && v.frameTime == 350 && v.consecutiveFrames == 6);
      check(v.poses == 1 && v.landmarks == 2 && v.lines == 3);
    }
    auto i = input(); i.frameTime = 850;
    check(M::eligible(i, M::visualEvidence(), epoch));
    i.frameTime = 851; check(!M::eligible(i, M::visualEvidence(), epoch));
    M::noteVisualRegistration(851, false, 0, 0, 0);
    check(!M::visualEvidence().consecutiveFrames);
    M::noteVisualRegistration(900, false, 0, 0, 4);
    check(M::visualEvidence().frameTime == 900 && M::visualEvidence().consecutiveFrames == 1);
  } else if(test == "visual_resume") {
    for(unsigned frame = 100; frame <= 350; frame += 50) M::noteVisualRegistration(frame, false, 1, 0, 0);
    M::noteVisualRegistration(600, false, 0, 0, 0);
    check(M::visualEvidence().frameTime == 350 && M::visualEvidence().consecutiveFrames == 6);
    M::noteVisualRegistration(700, false, 0, 1, 0);
    check(M::visualEvidence().frameTime == 700 && M::visualEvidence().consecutiveFrames == 7);
    M::noteVisualRegistration(1100, false, 0, 0, 0);
    check(M::visualEvidence().frameTime == 700 && M::visualEvidence().consecutiveFrames == 7);
    M::noteVisualRegistration(1201, false, 0, 0, 1);
    check(M::visualEvidence().frameTime == 1201 && M::visualEvidence().consecutiveFrames == 1);
  } else if(test == "joystick_once") {
    check(!M::consumeJoystickRelease());
    check(M::confirm(epoch) && M::consumeJoystickRelease());
    check(!M::consumeJoystickRelease() && !M::consumeJoystickRelease());
    check(!M::confirm(epoch) && !M::consumeJoystickRelease());
    M::invalidateConfirmation(); check(!M::consumeJoystickRelease());
    check(!M::confirm(epoch) && !M::consumeJoystickRelease());
    check(M::confirm(epoch + 1) && M::consumeJoystickRelease());
    check(!M::consumeJoystickRelease());
    M::invalidateConfirmation(); check(M::confirm(epoch + 2));
    check(M::consumeJoystickRelease() && !M::consumeJoystickRelease());
  } else if(test == "eligibility_flags") {
    const auto v = evidence(epoch); auto i = input(); check(M::eligible(i, v, epoch));
    for(auto member : {&M::EligibilityInput::gcActive, &M::EligibilityInput::readyOrSet,
                      &M::EligibilityInput::rawReadyOrSet, &M::EligibilityInput::authorized,
                      &M::EligibilityInput::stableStand, &M::EligibilityInput::poseQuality,
                      &M::EligibilityInput::poseFinite}) {
      i = input(); i.*member = false; check(!M::eligible(i, v, epoch));
    }
    for(auto member : {&M::EligibilityInput::stopped, &M::EligibilityInput::penalized}) {
      i = input(); i.*member = true; check(!M::eligible(i, v, epoch));
    }
    i = input(); auto bad = v; bad.epoch++; check(!M::eligible(i, bad, epoch));
    bad = v; bad.consecutiveFrames = 5; check(!M::eligible(i, bad, epoch));
    bad = v; bad.frameTime = 0; check(!M::eligible(i, bad, epoch));
  } else if(test == "eligibility_time") {
    auto i = input(); auto v = evidence(epoch);
    i.packetTime = i.now - 2000; check(M::eligible(i, v, epoch));
    i.packetTime--; check(!M::eligible(i, v, epoch));
    i.packetTime = i.now + 1; check(!M::eligible(i, v, epoch));
    i.packetTime = 0; check(!M::eligible(i, v, epoch));
    i = input(); v.frameTime = i.frameTime - 500; check(M::eligible(i, v, epoch));
    v.frameTime--; check(!M::eligible(i, v, epoch));
    v.frameTime = i.frameTime + 1; check(!M::eligible(i, v, epoch));
  } else if(test == "eligibility_finite") {
    auto i = input(); const auto v = evidence(epoch);
    i.positionStdMm = 500.f; i.headingStdRad = .261799388f; check(M::eligible(i, v, epoch));
    for(float value : {-1.f, 500.01f, std::numeric_limits<float>::quiet_NaN(), std::numeric_limits<float>::infinity()}) {
      i = input(); i.positionStdMm = value; check(!M::eligible(i, v, epoch));
    }
    for(float value : {-1.f, .2618f, std::numeric_limits<float>::quiet_NaN(), std::numeric_limits<float>::infinity()}) {
      i = input(); i.headingStdRad = value; check(!M::eligible(i, v, epoch));
    }
    i = input(); i.positionStdMm = i.headingStdRad = 0.f; check(M::eligible(i, v, epoch));
  } else if(test == "gesture_fresh") {
    M::ConfirmationGesture gesture;
    check(!gesture.update(epoch, 100, true, false, true));
    check(!gesture.update(epoch, 5000, true, false, true));
    check(!gesture.update(epoch, 5001, true, true, false));
    check(!gesture.update(epoch, 5002, true, false, true));
    check(!gesture.update(epoch, 7001, true, false, true));
    check(gesture.update(epoch, 7002, true, false, true));
    check(!gesture.update(epoch, 10000, true, false, true));
    check(!gesture.update(epoch, 10001, true, true, false));
    check(!gesture.update(epoch, 10002, true, false, true));
    check(gesture.update(epoch, 12002, true, false, true));
  } else if(test == "gesture_interrupted") {
    for(unsigned interruption = 0; interruption < 4; ++interruption) {
      M::ConfirmationGesture gesture;
      check(!gesture.update(epoch, 100, true, true, false));
      check(!gesture.update(epoch, 101, true, false, true));
      if(interruption == 0) check(!gesture.update(epoch, 200, false, false, true));
      if(interruption == 1) check(!gesture.update(epoch + 1, 200, true, false, true));
      if(interruption == 2) check(!gesture.update(epoch, 101, true, false, true));
      if(interruption == 3) check(!gesture.update(epoch, 100, true, false, true));
      const auto current = interruption == 1 ? epoch + 1 : epoch;
      check(!gesture.update(current, 5000, true, false, true));
      check(!gesture.update(current, 5001, true, true, false));
      check(!gesture.update(current, 5002, true, false, true));
      check(gesture.update(current, 7002, true, false, true));
    }
  } else if(test == "gesture_wrong_combo") {
    M::ConfirmationGesture gesture;
    check(!gesture.update(epoch, 100, false, true, false));
    check(!gesture.update(epoch, 101, true, false, true));
    check(!gesture.update(epoch, 5000, true, false, true));
    check(!gesture.update(epoch, 5001, true, true, false));
    check(!gesture.update(epoch, 5002, true, false, true));
    check(!gesture.update(epoch, 6000, true, false, false));
    check(!gesture.update(epoch, 6001, true, false, true));
    check(!gesture.update(epoch, 8000, true, false, true));
    check(gesture.update(epoch, 8001, true, false, true));
  } else if(test == "race") {
    std::atomic<bool> start{false}; std::vector<std::thread> workers;
    for(unsigned i = 0; i < 12; ++i) workers.emplace_back([&] {
      while(!start.load()) std::this_thread::yield();
      for(unsigned j = 0; j < 100; ++j) static_cast<void>(M::confirm(epoch));
    });
    workers.emplace_back([&] {while(!start.load()) std::this_thread::yield(); M::invalidateConfirmation();});
    start.store(true); for(auto& thread : workers) thread.join();
    check(!M::confirmed() && M::epoch() == epoch + 1 && M::confirmationGeneration() <= 1);
    check(!M::confirm(epoch));
  } else return 2;
  return 0;
}
'''


class MatchLocalizationGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="k1-match-gate-cpu-")
        folder = Path(cls.temporary.name)
        cpp, cls.executable = folder / "test.cpp", folder / "test"
        cpp.write_text(HARNESS)
        result = subprocess.run(["clang++", "-std=c++20", "-pthread", "-DNDEBUG", "-Wall", "-Wextra",
                                 "-Werror", "-pedantic", "-I", str(ROOT / "Src/Libs"), str(cpp),
                                 "-o", str(cls.executable)], capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_cases(self, *cases, repeats=1):
        for case in cases:
            for repeat in range(repeats):
                with self.subTest(case=case, repeat=repeat):
                    result = subprocess.run([str(self.executable), case], capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_disabled_noop_and_gc_address_rejection_cannot_reset_permission(self):
        self.run_cases("default", "gc_source")

    def test_epoch_confirm_once_old_token_denied_initial_rising_invalidates(self):
        self.run_cases("epochs", "initial_transition")

    def test_visual_frames_distinct_noninitial_and_gap_reset(self):
        self.run_cases("visual_distinct", "visual_reset", "visual_transient", "visual_resume")

    def test_joystick_released_once_per_new_confirmed_generation(self):
        self.run_cases("joystick_once")

    def test_eligibility_every_flag_clock_covariance_and_finite_value(self):
        self.run_cases("eligibility_flags", "eligibility_time", "eligibility_finite")

    def test_human_gesture_requires_new_release_and_two_seconds(self):
        self.run_cases("gesture_fresh", "gesture_interrupted", "gesture_wrong_combo")

    def test_concurrent_invalidation_cannot_be_overwritten_by_old_confirm(self):
        self.run_cases("race", repeats=20)

    def test_historical_option_keeps_exact_fresh_rb_y_mask_but_is_not_selected(self):
        folder = ROOT / "Src/Modules/BehaviorControl/SkillBehaviorControl/Options"
        option = (folder / "HandleMatchLocalization.cpp").read_text()
        self.assertIn("option(HandleMatchLocalization);", (folder / "Options.h").read_text())
        start = option.index("const bool neutralSticks =")
        end = option.index("const bool confirmRequested", start)
        conditions = option[start:end]
        self.assertIn("theJoystickState.buttons == rbYMask", conditions)
        self.assertIn("(1u << 5) | (1u << 3)", conditions)
        self.assertIn("candidateReady, inputNeutral, rbYOnly", option)
        self.assertNotIn("HandleMatchLocalization", (ROOT / "Config/Scenarios/Default/optionPlaySoccer.cfg").read_text())
        self.assertIn("confirmRequested && BoosterMatchSafety::confirm(currentEpoch)", option)
        code = r'''
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdlib>
#include <limits>
#include <utility>
static void check(bool value) {if(!value) std::abort();}
struct Joystick {bool valid = true; std::array<float, 4> axes{}; unsigned buttons = 0;};
static std::pair<bool, bool> evaluate(const Joystick& theJoystickState) {
''' + conditions + r'''
  return {inputNeutral, rbYOnly};
}
int main() {
  Joystick joystick;
  for(unsigned mask = 0; mask < 65536; ++mask) {
    joystick.buttons = mask; const auto values = evaluate(joystick);
    check(values.first == (mask == 0) && values.second == (mask == 40));
  }
  joystick.buttons = 40; joystick.valid = false; check(!evaluate(joystick).second);
  joystick.valid = true;
  for(unsigned axis = 0; axis < 4; ++axis) {
    for(float invalid : {.12001f, -.12001f, std::numeric_limits<float>::quiet_NaN(), std::numeric_limits<float>::infinity()}) {
      joystick.axes[axis] = invalid; check(!evaluate(joystick).first && !evaluate(joystick).second);
    }
    joystick.axes[axis] = -.12f; check(evaluate(joystick).second);
    joystick.axes[axis] = 0.f;
  }
  return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="k1-match-mask-cpu-") as temporary:
            cpp, binary = Path(temporary) / "test.cpp", Path(temporary) / "test"
            cpp.write_text(code)
            result = subprocess.run(["clang++", "-std=c++20", "-DNDEBUG", "-Wall", "-Wextra", "-Werror",
                                     "-pedantic", str(cpp), "-o", str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(subprocess.run([str(binary)], timeout=10).returncode, 0)

    def test_joystick_handoff_one_shot_does_not_remove_future_manual_activation(self):
        path = ROOT / "Src/Modules/BehaviorControl/SkillBehaviorControl/Options/HandleJoystick.cpp"
        source = path.read_text()
        self.assertEqual(source.count("consumeJoystickRelease()"), 1)
        self.assertIn("goto inactive;", source[source.index("consumeJoystickRelease()"):source.index("initial_state(inactive)")])
        activate = source[source.index("initial_state(inactive)"):source.index("state(active)")]
        self.assertIn("joystickState.valid", activate)
        self.assertIn("allPressed(activateButtons)", activate)
        self.assertIn("!theMotionInfo.isMotion(MotionPhase::playDead)", activate)
        self.assertIn("goto active;", activate)
        self.assertIn("consumeJoystickAfterGetUp(getUpManualReady)", activate)
        order = (ROOT / "Config/Scenarios/Default/optionPlaySoccer.cfg").read_text()
        self.assertNotIn("HandleMatchLocalization", order)
        self.assertLess(order.index("HandleStoppedState"), order.index("HandleJoystick"))
        self.assertLess(order.index("HandleJoystick"), order.index("HandlePlayerState"))

    def test_gc_rejection_precedes_all_received_target_age_and_epoch_mutations(self):
        source = (ROOT / "Src/Modules/Communication/GameControllerDataProvider/GameControllerDataProvider.cpp").read_text()
        guard = source.index("if(!BoosterMatchSafety::acceptGameControllerSource(from))")
        validation = source.index("if(size == sizeof(buffer)", guard)
        self.assertIn("continue;", source[guard:validation])
        self.assertNotIn("noteGcInitial", source[guard:validation])
        for mutation in ("socket.setTarget(", "static_cast<RoboCup::RoboCupGameControlData&>(theGameControllerData) = buffer",
                         "theGameControllerData.timeLastPacketReceived =", "BoosterMatchSafety::noteGcInitial("):
            self.assertLess(validation, source.index(mutation))
        for validation_check in ("GAMECONTROLLER_STRUCT_HEADER", "buffer.version == GAMECONTROLLER_STRUCT_VERSION",
                                 "buffer.teams[0].teamNumber", "buffer.teams[1].teamNumber"):
            self.assertIn(validation_check, source[validation:source.index("unsigned ip =", validation)])

    def test_staggering_stand_waits_in_gate_but_cannot_confirm_native_fall_passes(self):
        option = (ROOT / "Src/Modules/BehaviorControl/SkillBehaviorControl/Options/HandleMatchLocalization.cpp").read_text()
        start = option.index("const bool match =")
        end = option.index("const bool finiteCovariance", start)
        predicates = option[start:end]
        self.assertIn("theFallDownState.state != FallDownState::upright && theFallDownState.state != FallDownState::staggering", predicates)
        self.assertIn("const bool stableStand = theFallDownState.state == FallDownState::upright", predicates)
        code = r'''
#include "Platform/BoosterMatchSafety.h"
#include <array>
#include <cmath>
#include <cstdlib>
constexpr float operator""_deg(unsigned long long value) {return static_cast<float>(value) * .01745329252f;}
static void check(bool value) {if(!value) std::abort();}
struct FallDownState {enum State {pickedUp, upright, staggering, falling, fallen, squatting}; State state = upright;};
struct MotionPhase {enum Type {playDead, stand, walk, fall, getUp, prepare}; Type executedPhase = stand;};
struct GameState {
  enum Player {active, unstiff}; Player playerState = active;
  bool initial = false, ready = true, set = false, playing = false;
  bool isInitial() const {return initial;} bool isReady() const {return ready;}
  bool isSet() const {return set;} bool isPlaying() const {return playing;}
};
struct Vector {float a = 0.f, b = 0.f, c = 0.f; float x() const {return a;} float y() const {return b;} float z() const {return c;}};
struct Inputs {
  struct {bool emergencyMode = false;} theSystemSensorData;
  GameState theGameState; MotionPhase theMotionInfo; FallDownState theFallDownState;
  struct {Vector angle, gyro;} theInertialData;
  std::array<bool, 3> evaluate() const {
''' + predicates + r'''
    return {physicalBusy, needsGate, stableStand};
  }
};
int main() {
  BoosterMatchSafety::enable();
  for(auto state : {FallDownState::pickedUp, FallDownState::upright, FallDownState::staggering,
                    FallDownState::falling, FallDownState::fallen, FallDownState::squatting})
    for(auto phase : {MotionPhase::playDead, MotionPhase::stand, MotionPhase::walk,
                      MotionPhase::fall, MotionPhase::getUp, MotionPhase::prepare}) {
      Inputs input; input.theFallDownState.state = state; input.theMotionInfo.executedPhase = phase;
      const auto values = input.evaluate();
      const bool standWalk = phase == MotionPhase::stand || phase == MotionPhase::walk;
      const bool uprightStaggering = state == FallDownState::upright || state == FallDownState::staggering;
      check(values[0] == !(standWalk && uprightStaggering));
      check(values[1] == (standWalk && uprightStaggering));
      check(values[2] == (phase == MotionPhase::stand && state == FallDownState::upright));
    }
  Inputs input; input.theSystemSensorData.emergencyMode = true; check(!input.evaluate()[1]);
  input = {}; input.theGameState.playerState = GameState::unstiff; check(!input.evaluate()[1]);
  input = {}; input.theGameState.initial = true; check(!input.evaluate()[1]);
  input = {}; input.theInertialData.angle.a = 15_deg; check(!input.evaluate()[2]);
  input = {}; input.theInertialData.gyro.c = 5_deg; check(!input.evaluate()[2]);
  input = {}; check(input.evaluate()[1] && input.evaluate()[2]);
  check(BoosterMatchSafety::confirm(BoosterMatchSafety::epoch()));
  check(!input.evaluate()[1]); return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="k1-match-stagger-cpu-") as temporary:
            cpp, binary = Path(temporary) / "test.cpp", Path(temporary) / "test"
            cpp.write_text(code)
            result = subprocess.run(["clang++", "-std=c++20", "-DNDEBUG", "-Wall", "-Wextra", "-Werror",
                                     "-pedantic", "-I", str(ROOT / "Src/Libs"), str(cpp), "-o", str(binary)],
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(subprocess.run([str(binary)], timeout=10).returncode, 0)

    def test_registration_telemetry_does_not_change_official_filter_algorithm(self):
        path = "Src/Modules/Modeling/SelfLocator/SelfLocator.cpp"
        result = subprocess.run(["git", "show", "2e3374ba896da8288607f9a1881b876642fe18b8:" + path],
                                cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        source = (ROOT / path).read_text()
        start = "void SelfLocator::sensorUpdate()"
        end = "bool SelfLocator::currentMotionIsUnsafe()"
        actual = source[source.index(start):source.index(end)]
        baseline = result.stdout[result.stdout.index(start):result.stdout.index(end)]
        actual, count = re.subn(r"^  unsigned registeredPosesForMatch = 0, registeredLandmarksForMatch = 0, registeredLinesForMatch = 0;\n", "", actual, flags=re.M)
        self.assertEqual(count, 1)
        actual, count = re.subn(r"^      registered(?:Poses|Landmarks|Lines)ForMatch = std::max\([^\n]+\n", "", actual, flags=re.M)
        self.assertEqual(count, 3)
        actual, count = re.subn(r"  // Diagnostics of actual image registrations only; no filter/weight changes\.\n"
                               r"  // Never count twelve hypotheses as twelve independent image frames\.\n"
                               r"  BoosterMatchSafety::noteVisualRegistration\(theFrameInfo.time, theGameState.isInitial\(\),\n"
                               r"      registeredPosesForMatch, registeredLandmarksForMatch, registeredLinesForMatch\);\n\n", "", actual)
        self.assertEqual(count, 1)
        self.assertEqual(actual, baseline)


if __name__ == "__main__":
    unittest.main()
