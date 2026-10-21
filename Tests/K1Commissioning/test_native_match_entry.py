"""Native READY flow with one automatic handoff, not a permanent manual ban."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
OPTIONS = ROOT / "Src/Modules/BehaviorControl/SkillBehaviorControl/Options"

HARNESS = r'''
#include "Platform/BoosterMatchSafety.h"
#include <cstdlib>
#include <initializer_list>
static void check(bool value) {if(!value) std::abort();}
int main() {
  using BoosterMatchSafety::InitialHandoff;
  InitialHandoff match;
  check(!match.update(true, true, true));
  check(match.update(true, true, false)); // INITIAL -> READY
  for(unsigned i = 0; i < 100; ++i) check(!match.update(true, true, false));
  // Later explicit manual remains active through READY/SET/PLAY: no new edge.
  bool manual = true;
  if(match.update(true, true, false)) manual = false;
  check(manual);
  check(!match.update(true, true, true)); // next INITIAL starts another cycle
  check(match.update(true, true, false));
  check(!match.update(true, true, false));
  InitialHandoff stale;
  check(!stale.update(true, false, false)); // no stale packet may consume edge
  check(stale.update(true, true, false));
  check(!stale.update(true, true, false));
  InitialHandoff bench;
  for(bool initial : {true, false, true, false}) check(!bench.update(false, true, initial));
  InitialHandoff startedReady;
  check(startedReady.update(true, true, false));
  check(!startedReady.update(true, true, false));
  auto& persistent = BoosterMatchSafety::initialHandoff();
  check(persistent.update(true, true, false));
  check(&persistent == &BoosterMatchSafety::initialHandoff());
  check(!BoosterMatchSafety::initialHandoff().update(true, true, false));
  // INITIAL entirely hidden by Physical still rearms via the real GC callback.
  BoosterMatchSafety::enable();
  BoosterMatchSafety::noteGcInitial(true);
  check(persistent.update(true, true, false));
  check(!persistent.update(true, true, false));
  BoosterMatchSafety::noteGcInitial(true); // repeated same state must not rearm
  check(!persistent.update(true, true, false));
  BoosterMatchSafety::noteGcInitial(false);
  BoosterMatchSafety::noteGcInitial(true);
  check(persistent.update(true, true, false));
  check(!persistent.update(true, true, false));
}
'''


class NativeMatchEntryTests(unittest.TestCase):
    def test_actual_handoff_is_one_shot_and_bench_noop(self):
        with tempfile.TemporaryDirectory(prefix="bh-native-entry-") as temporary:
            cpp, binary = Path(temporary) / "test.cpp", Path(temporary) / "test"
            cpp.write_text(HARNESS)
            result = subprocess.run(["clang++", "-std=c++20", "-Wall", "-Wextra", "-Werror", "-pedantic",
                                     "-I", str(ROOT / "Src/Libs"), str(cpp), "-o", str(binary)],
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(subprocess.run([str(binary)], timeout=10).returncode, 0)

    def test_native_game_state_and_self_locator_are_reached_without_rb_y(self):
        order = (ROOT / "Config/Scenarios/Default/optionPlaySoccer.cfg").read_text()
        self.assertNotIn("HandleMatchLocalization", order)
        self.assertLess(order.index("HandlePhysicalRobot"), order.index("HandleJoystick"))
        self.assertLess(order.index("HandleStoppedState"), order.index("HandleJoystick"))
        self.assertLess(order.index("HandleJoystick"), order.index("HandleGameState"))
        native = (OPTIONS / "HandleGameState.cpp").read_text()
        self.assertIn("WalkToPointReady", native)
        self.assertIn("state(set)", native)
        locator = (ROOT / "Src/Modules/Modeling/SelfLocator/SelfLocator.cpp").read_text()
        self.assertIn("getNewPoseAtWalkInPosition()", locator)
        self.assertIn("theExtendedGameState.wasInitial()", locator)
        self.assertNotIn("BoosterMatchSafety::confirm(", locator)
        main = (ROOT / "Src/Apps/Booster/Main.cpp").read_text()
        self.assertIn("NATIVE_GC_ENTRY READY_AUTO_HANDOFF MANUAL_OVERRIDE_ALLOWED", main)
        self.assertNotIn("LOCALIZATION_CONFIRM_REQUIRED", main)

    def test_edge_cancels_pending_but_later_explicit_manual_is_gc_independent(self):
        source = (OPTIONS / "HandleJoystick.cpp").read_text()
        edge = source[source.index("if(automaticEntryHandoff)"):source.index("initial_state(inactive)")]
        self.assertIn("cancelJoystickAfterGetUp()", edge)
        self.assertIn("goto inactive;", edge)
        activate = source[source.index("initial_state(inactive)"):source.index("state(active)")]
        for required in ("joystickState.valid", "allPressed(activateButtons)", "!theMotionInfo.isMotion(MotionPhase::playDead)",
                         "consumeJoystickAfterGetUp(getUpManualReady)", "goto active;"):
            self.assertIn(required, activate)
        self.assertNotIn("theGameState", activate)
        self.assertNotIn("matchManualAllowed", source)
        self.assertNotIn("automaticEntryIntent", source)
        self.assertNotIn("rbYMask", source)
        self.assertIn("BoosterMatchSafety::initialHandoff().update(", source)
        self.assertNotIn("(BoosterMatchSafety::InitialHandoff)", source)
        self.assertIn("freshHandoffGc", source)
        self.assertIn("theGameState.isInitial() && theGameControllerData.state == STATE_INITIAL", source)


if __name__ == "__main__":
    unittest.main()
