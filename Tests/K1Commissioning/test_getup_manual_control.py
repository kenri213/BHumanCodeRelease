"""CPU regressions for one-shot get-up handoff and manual-before-auto permission.

Compiles the real permission header and actual option predicates. No Booster SDK,
SSH, cameras, motion model, or physical robot is used.
"""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
OPTIONS = ROOT / "Src/Modules/BehaviorControl/SkillBehaviorControl/Options"


def condition_after(source, marker):
    start = source.index("if(", source.index(marker)) + 3
    depth, end = 1, start
    while depth:
        depth += (source[end] == "(") - (source[end] == ")")
        end += 1
    return source[start:end - 1]


def harness():
    source = (OPTIONS / "HandleJoystick.cpp").read_text()
    readiness = source.split("const bool getUpManualReady = ", 1)[1].split("  // An explicit", 1)[0].strip().removesuffix(";")
    activation = condition_after(source, "initial_state(inactive)")
    cancellation = condition_after(source, "  // An explicit return to automatic")
    return r'''
#include "Platform/BoosterMatchSafety.h"
#include "Platform/BoosterPickupControl.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdlib>
#include <limits>
#include <string>
#include <vector>
namespace M = BoosterMatchSafety;
namespace BoosterOfficialLiveTest {bool authorized = true; bool bodyAuthorized() {return authorized;}}
namespace FallDownState {enum State {upright, staggering, fallen};}
namespace MotionPhase {enum Phase {stand, walk, getUp, prepare, fall, playDead};}
struct Fall {FallDownState::State state = FallDownState::upright;} theFallDownState;
struct Motion {
  MotionPhase::Phase executedPhase = MotionPhase::stand;
  bool isMotion(MotionPhase::Phase value) const {return executedPhase == value;}
} theMotionInfo;
struct Joystick {
  bool valid = true;
  unsigned buttons = 0;
  std::array<float, 8> axes{};
  bool pressed(int button) const {return buttons & (1u << button);}
} joystickState;
static void check(bool value) {if(!value) std::abort();}
static bool activate() {
  const std::vector<int> activateButtons{6, 9};
  const std::vector<int> deactivateButtons{6, 8};
  const auto allPressed = [&](const std::vector<int>& buttons) {
    return std::all_of(buttons.begin(), buttons.end(), [&](int button) {return joystickState.pressed(button);});
  };
  const bool getUpManualReady = ''' + readiness + r''';
  if(''' + cancellation + r''') M::cancelJoystickAfterGetUp();
  if(''' + activation + r''') {M::cancelJoystickAfterGetUp(); return true;}
  return false;
}
int main(int argc, char** argv) {
  check(argc == 2); const std::string test = argv[1];
  if(test == "opt_in") {
    M::requestJoystickAfterGetUp(); check(!activate());
    M::enable(); check(!activate()); return 0;
  }
  M::enable();
  if(test == "one_shot") {
    check(!activate()); M::requestJoystickAfterGetUp();
    check(activate()); check(!activate());
  } else if(test == "getup_wait") {
    M::requestJoystickAfterGetUp();
    for(auto phase : {MotionPhase::getUp, MotionPhase::prepare, MotionPhase::fall, MotionPhase::playDead}) {
      theMotionInfo.executedPhase = phase; check(!activate());
    }
    theMotionInfo.executedPhase = MotionPhase::walk; check(activate()); check(!activate());
  } else if(test == "upright") {
    M::requestJoystickAfterGetUp();
    for(auto state : {FallDownState::staggering, FallDownState::fallen}) {
      theFallDownState.state = state; check(!activate());
    }
    theFallDownState.state = FallDownState::upright; check(activate());
  } else if(test == "authorization") {
    M::requestJoystickAfterGetUp(); BoosterOfficialLiveTest::authorized = false; check(!activate());
    BoosterOfficialLiveTest::authorized = true; check(activate());
  } else if(test == "neutral") {
    M::requestJoystickAfterGetUp();
    for(unsigned button = 0; button < 16; ++button) {joystickState.buttons = 1u << button; check(!activate());}
    joystickState.buttons = 0;
    for(unsigned axis = 0; axis < 8; ++axis) {
      for(float value : {.10001f, -.10001f, 1.f, -1.f, std::numeric_limits<float>::quiet_NaN(),
                         std::numeric_limits<float>::infinity()}) {
        joystickState.axes[axis] = value; check(!activate());
      }
      joystickState.axes[axis] = 0.f;
    }
    check(activate());
  } else if(test == "connection") {
    M::requestJoystickAfterGetUp(); joystickState.valid = false; check(!activate());
    joystickState.valid = true; check(activate());
  } else if(test == "cancel") {
    M::requestJoystickAfterGetUp(); M::cancelJoystickAfterGetUp(); check(!activate());
    joystickState.buttons = (1u << 6) | (1u << 9); check(activate());
    joystickState.buttons = 0; check(!activate());
  } else if(test == "explicit_exit") {
    M::requestJoystickAfterGetUp();
    theMotionInfo.executedPhase = MotionPhase::getUp;
    joystickState.buttons = (1u << 6) | (1u << 8); check(!activate());
    theMotionInfo.executedPhase = MotionPhase::stand;
    joystickState.buttons = 0; check(!activate()); check(!activate());
    joystickState.buttons = (1u << 6) | (1u << 9); check(activate());
  } else if(test == "confirmation") {
    M::requestJoystickAfterGetUp(); check(M::confirm(M::epoch())); check(!activate());
    M::requestJoystickAfterGetUp(); check(M::consumeJoystickRelease()); check(!activate());
    check(!M::consumeJoystickRelease()); check(!activate());
  } else if(test == "invalidation") {
    M::requestJoystickAfterGetUp(); M::invalidateConfirmation(); check(!activate());
  } else if(test == "ordinary_manual") {
    joystickState.buttons = (1u << 6) | (1u << 9); check(activate());
    theMotionInfo.executedPhase = MotionPhase::playDead; check(!activate());
  } else return 2;
}
'''


class GetUpManualControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="bh-getup-manual-")
        cpp = Path(cls.temporary.name) / "test.cpp"
        cls.binary = Path(cls.temporary.name) / "test"
        cpp.write_text(harness())
        result = subprocess.run(["clang++", "-std=c++20", "-Wall", "-Wextra", "-Werror", "-pedantic",
                                 "-I", str(ROOT / "Src/Libs"), str(cpp), "-o", str(cls.binary)],
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_actual_handoff_predicate_and_pending_lifecycle(self):
        for case in ("opt_in", "one_shot", "getup_wait", "upright", "authorization", "neutral", "connection",
                     "cancel", "explicit_exit", "confirmation", "invalidation", "ordinary_manual"):
            with self.subTest(case=case):
                self.assertEqual(subprocess.run([str(self.binary), case], timeout=10).returncode, 0)

    def test_native_match_has_no_extra_localization_gate_physical_stop_still_first(self):
        options = re.findall(r"\bHandle\w+\b", (ROOT / "Config/Scenarios/Default/optionPlaySoccer.cfg").read_text())
        self.assertEqual(options[:4], ["HandlePhysicalRobot", "HandleStoppedState", "HandleJoystick", "HandlePlayerState"])
        self.assertNotIn("HandleMatchLocalization", options)
        # In select_option, an active manual option handles the frame before the
        # unconfirmed automatic gate; without manual, that unchanged gate stands.
        for physical, stopped, manual, unconfirmed, expected in (
            (False, False, True, True, "HandleJoystick"),
            (False, False, False, True, "automatic"),
            (False, False, True, False, "HandleJoystick"),
            (False, False, False, False, "automatic"),
            (False, True, True, True, "HandleStoppedState"),
            (True, False, True, True, "HandlePhysicalRobot"),
        ):
            active = {"HandlePhysicalRobot": physical, "HandleStoppedState": stopped,
                      "HandleJoystick": manual}
            chosen = next((name for name in options if active.get(name, False)), "automatic")
            self.assertEqual(chosen, expected)
        localization = (OPTIONS / "HandleMatchLocalization.cpp").read_text()
        self.assertIn("!BoosterMatchSafety::confirmed()", localization)
        self.assertIn("Stand();", localization)

    def test_pending_request_only_inside_accepted_fresh_getup_branch(self):
        physical = (OPTIONS / "HandlePhysicalRobot.cpp").read_text()
        self.assertEqual(physical.count("requestJoystickAfterGetUp()"), 3)
        start = physical.index("if(liveGetUpRequested)", physical.index("state(prepare)"))
        end = physical.index("goto fall;", start)
        accepted = physical[start:end]
        self.assertIn("LIVE_GETUP_ACCEPTED", accepted)
        self.assertIn("requestJoystickAfterGetUp()", accepted)
        combined = accepted[accepted.index("if(combinedGetUp.requestGetUp)"):]
        self.assertIn("LIVE_COMBINED_GETUP_ACCEPTED", combined)
        self.assertIn("requestJoystickAfterGetUp()", combined)
        upright_start = physical.index("if(combinedGetUp.requestActive)")
        upright_end = physical.index("goto active;", upright_start)
        self.assertIn("LIVE_COMBINED_PREPARE_ACTIVE", physical[upright_start:upright_end])
        self.assertIn("requestJoystickAfterGetUp()", physical[upright_start:upright_end])
        self.assertIn("theMotionInfo.executedPhase == MotionPhase::prepare", physical[physical.index("state(prepare)"):start])

    def test_explicit_exit_and_auto_handoff_cancel_pending_before_inactive(self):
        joystick = (OPTIONS / "HandleJoystick.cpp").read_text()
        before_common = joystick[:joystick.index("common_transition")]
        self.assertRegex(before_common, r"if\(joystickState.valid && allPressed\(deactivateButtons\)\)\s+BoosterMatchSafety::cancelJoystickAfterGetUp\(\);")
        common = joystick[joystick.index("common_transition"):joystick.index("initial_state(inactive)")]
        edge = common[common.index("if(automaticEntryHandoff)"):common.index("// Retained compatibility")]
        self.assertLess(edge.index("cancelJoystickAfterGetUp()"), edge.index("goto inactive;"))
        compatibility = common[common.index("consumeJoystickRelease()"):]
        self.assertLess(compatibility.index("consumeJoystickRelease()"), compatibility.index("cancelJoystickAfterGetUp()"))
        self.assertLess(compatibility.index("cancelJoystickAfterGetUp()"), compatibility.index("goto inactive;"))
        config = (ROOT / "Config/Scenarios/Default/optionHandleJoystick.cfg").read_text()
        self.assertIn("activateButtons = [6, 9]", config)
        self.assertIn("deactivateButtons = [6, 8]", config)


if __name__ == "__main__":
    unittest.main()
