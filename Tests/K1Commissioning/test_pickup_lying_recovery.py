"""Real Physical/Combined lying-recovery handoff, compiled without SDK/devices."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PHYSICAL = ROOT / "Src/Modules/BehaviorControl/SkillBehaviorControl/Options/HandlePhysicalRobot.cpp"
JOYSTICK = ROOT / "Src/Modules/BehaviorControl/SkillBehaviorControl/Options/HandleJoystick.cpp"


def body(source, marker):
    start = source.index("{", source.index(marker))
    end = start + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


HARNESS = r'''
#include "Platform/BoosterPickupControl.h"
#include "Platform/BoosterRemoteStand.h"
#include "Platform/BoosterMatchSafety.h"
#include "Platform/BoosterOfficialLiveTest.h"
#include "Tools/BehaviorControl/CombinedLiveGetUpGesture.h"
#include <cstdio>
#include <cstdlib>
#include <string>
namespace P = BoosterPickupControl;
namespace R = BoosterRemoteStand;
namespace G = CombinedLiveGetUpGesture;
namespace C = BoosterOfficialLiveTest;
namespace MotionPhase {enum Type {stand, walk, getUp, prepare, playDead};}
namespace FallDownState {enum State {upright, fallen, pickedUp};}
struct {bool emergencyMode = false;} theSystemSensorData;
struct {unsigned state = 3;} theGameControllerData;
struct {FallDownState::State state = FallDownState::upright;} theFallDownState;
struct {MotionPhase::Type executedPhase = MotionPhase::getUp;unsigned lastStandTimeStamp = 10000;} theMotionInfo;
struct Frame {
  unsigned time = 10000;
  unsigned getTimeSince(unsigned stamp) const {return time - stamp;}
} theFrameInfo;
/* REAL_STAND_PREDICATE */
static void check(bool value, const char* why) {
  if(!value) {std::fprintf(stderr, "%s\n", why);std::abort();}
}
static void authorize() {
  C::enable();C::observePhysicalF1Down(1000000000LL);
  for(unsigned frame = 1; frame <= 5; ++frame) C::noteCognitionForF1Authorization(frame, 2000000000LL);
  check(C::authorizePhysicalF1Long(2000000000LL) && C::bodyAuthorized(), "test requires real authorization state");
  C::observePhysicalF1Release();R::enable();BoosterMatchSafety::enable();
}
static G::Input good(unsigned frame = 200) {
  G::Input in;
  in.enabled = in.authorized = in.joystickValid = in.sticksNeutral = in.neutralButtons = true;
  in.rawRecoveryAllowed = in.unpenalized = in.imuFinite = in.canPrepare = in.lyingStable = in.executedPrepare = true;
  in.emergency = in.stopped = in.rawInitial = false;
  in.frameTime = frame;in.gcNow = frame + 100;in.gcPacketTime = frame + 90;in.epoch = 7;
  return in;
}
class Replay {
public:
  P::Gate physicalPickupGate;
  G::Gate combinedGetUpGate;
  bool liveGetUpReleaseSeen = false, liveGetUpHolding = false;
  bool lastLyingHandover = false;
  Replay() {
    P::Input in;
    in.enabled = in.authorized = in.imuFinite = in.standOrWalk = true;in.emergency = false;
    in.frame = 100;in.buttons = P::physicalButtons();physicalPickupGate.update(in);
    P::observePhysicalButton(1, true);in.frame = 130;in.buttons = P::physicalButtons();
    check(physicalPickupGate.update(in).entered, "set up real physical STAND withdrawal");
    P::observePhysicalButton(1, false);P::setHeld(true);
  }
  G::Result frame(G::Input combinedInput, bool entered = false) {
    const P::Result pickup{.entered = entered};
    const bool liveAuthorized = C::bodyAuthorized();
    theGameControllerData.state = combinedInput.rawInitial ? 0 : combinedInput.rawRecoveryAllowed ? 3 : 2;
    /* REAL_PHYSICAL_HANDOFF */
    (void)combinedWasPending;(void)combinedPreviousReason;(void)matchRecoveryPermitted;(void)pickupLyingHandover;
    lastLyingHandover = pickupLyingHandover;
    return combinedGetUp;
  }
};
static G::Result at(Replay& replay, G::Input& in, unsigned frame, bool chord) {
  in.frameTime = frame;in.gcNow = frame + 100;
  // Preserve deliberately malformed/stale stamps in dedicated bad-input cases.
  in.gcPacketTime = frame + 90;in.neutralButtons = !chord;in.rbAOnly = chord;
  return replay.frame(in);
}
static void neutral(Replay& replay, G::Input& in) {
  check(!at(replay, in, 200, false).requestPrepare, "new authorization must not arm");
  check(!at(replay, in, 230, false).requestPrepare, "real neutral establishes original gate");
}
static void fresh(Replay& replay, G::Input& in) {
  neutral(replay, in);check(!at(replay, in, 260, true).requestPrepare, "fresh RB+A starts hold");
}
static void accepted(Replay& replay, G::Input& in) {
  fresh(replay, in);check(!at(replay, in, 2259, true).requestPrepare, "1999ms cannot enter");
  check(at(replay, in, 2260, true).requestPrepare, "original fresh lawful 2000ms hold accepted");
  check(replay.lastLyingHandover, "real handoff branch reports its one-time generation handover");
  check(!replay.physicalPickupGate.active() && !P::held(), "successful original gate releases only Pickup latch");
  check(R::held() && R::bodyRecoveryPending(), "existing body recovery Stand hold replaces Pickup latch");
  check(!P::consumeAutomaticRelease(), "lying recovery never offers automatic football");
  check(!P::manualTakeoverAllowed(), "fresh neutral is required after lying handoff");
}
int main(int argc, char** argv) {
  check(argc == 2, "one named test");const std::string name = argv[1];authorize();Replay replay;auto in = good();
  if(name == "fresh_lying_handoff") {
    P::offerAutomaticRelease(7);accepted(replay, in);
  } else if(name == "old_held_rba") {
    for(unsigned frame = 200; frame <= 10000; frame += 40)
      check(!at(replay, in, frame, true).requestPrepare && replay.physicalPickupGate.active(), "old held RB+A without release cannot escape Pickup");
    check(P::held() && !R::bodyRecoveryPending() && !P::consumeAutomaticRelease(), "old chord leaves authority unchanged");
  } else if(name == "penalized" || name == "gc_stop" || name == "gc_set" || name == "stale_gc"
            || name == "future_gc" || name == "invalid_remote" || name == "nonneutral_sticks"
            || name == "imu_invalid" || name == "emergency" || name == "unauthorized") {
    fresh(replay, in);
    in.frameTime = 2260;in.gcNow = 2360;in.gcPacketTime = 2350;
    if(name == "penalized") in.unpenalized = false;
    if(name == "gc_stop") in.stopped = true;
    if(name == "gc_set") in.rawRecoveryAllowed = false;
    if(name == "stale_gc") in.gcPacketTime = 359;
    if(name == "future_gc") in.gcPacketTime = 2361;
    if(name == "invalid_remote") in.joystickValid = false;
    if(name == "nonneutral_sticks") in.sticksNeutral = false;
    if(name == "imu_invalid") in.imuFinite = false;
    if(name == "emergency") in.emergency = true;
    if(name == "unauthorized") in.authorized = false;
    const auto out = replay.frame(in);
    check(!out.requestPrepare && !out.requestGetUp && !out.requestActive && replay.physicalPickupGate.active(), "original safety gates cannot be bypassed by Pickup lying exit");
    check(P::held() && !R::bodyRecoveryPending() && !P::consumeAutomaticRelease(), "rejected input cannot transfer authority");
    check(!replay.lastLyingHandover, "rejected input cannot suppress ordinary body-recovery routing");
  } else if(name == "new_stand_revokes_old_combined_pending") {
    G::Gate old;
    auto oldInput = good(200);old.update(oldInput);oldInput.frameTime = 230;old.update(oldInput);
    oldInput.frameTime = 260;oldInput.neutralButtons = false;oldInput.rbAOnly = true;old.update(oldInput);
    oldInput.frameTime = 2260;check(old.update(oldInput).requestPrepare && old.pending(), "build real old pending Combined ticket");
    replay.combinedGetUpGate = old;in = oldInput;in.frameTime = 2290;
    const auto out = replay.frame(in, true);
    check(!out.requestPrepare && !out.requestGetUp && replay.physicalPickupGate.active()
          && !replay.lastLyingHandover && P::held(), "new STAND cancels old pending RB+A rather than escaping immediately");
  } else if(name == "must_reach_original_getup") {
    accepted(replay, in);
    check(!at(replay, in, 2300, false).requestGetUp, "Prepare interpolation begins after real acceptance");
    check(!at(replay, in, 4299, false).requestGetUp, "original actual Prepare 2000ms required");
    const auto out = at(replay, in, 4300, false);
    check(out.requestGetUp && !out.requestActive, "same original gate continues lawful lying GetUp");
    check(R::held() && R::bodyRecoveryPending() && !P::consumeAutomaticRelease(), "GetUp grant is not AUTO football grant");
  } else if(name == "not_lying_does_not_yield") {
    in.lyingStable = false;in.uprightStable = true;
    neutral(replay, in);
    for(unsigned frame = 260; frame <= 4300; frame += 40)
      check(!at(replay, in, frame, true).requestPrepare && replay.physicalPickupGate.active(), "upright RB+A cannot escape manual Pickup");
  } else if(name == "not_prepare_phase_does_not_yield") {
    in.canPrepare = false;neutral(replay, in);
    for(unsigned frame = 260; frame <= 4300; frame += 40)
      check(!at(replay, in, frame, true).requestPrepare && replay.physicalPickupGate.active(), "GetUp/Fall phase is not a new admission");
  } else if(name == "standing_needs_new_neutral_lt") {
    accepted(replay, in);
    R::observeSample(0, true);R::observeSample(320, true);
    check(!R::tryResume(R::generation(), 2), "pre-Stand neutral/LT cannot resume while body recovery pending");
    theMotionInfo.executedPhase = MotionPhase::getUp;theFrameInfo.time = 10500;
    check(!R::onRecoveryStanding(realStanding()), "GetUp is not real Stand even with old timestamp");
    theMotionInfo.executedPhase = MotionPhase::stand;theMotionInfo.lastStandTimeStamp = 11000;
    theFrameInfo.time = 11299;check(!R::onRecoveryStanding(realStanding()), "real Stand <300ms cannot clear body pending");
    theFrameInfo.time = 11300;check(R::onRecoveryStanding(realStanding()), "native real Stand300ms clears body pending");
    check(R::held() && !R::bodyRecoveryPending() && R::resumeChoice() == 0, "real Stand discards all earlier remote evidence");
    R::observeSample(320, true);check(!R::tryResume(R::generation(), 2), "old held LT-minus cannot resume");
    R::observeSample(0, true);R::observeSample(64, true);R::observeSample(320, true);R::observeSample(0, true);
    check(R::tryResume(R::generation(), 2) && !R::held(), "fresh post-Stand neutral/LT-minus may hand back to AUTO");
    check(!P::consumeAutomaticRelease(), "lying chain never minted a Pickup AUTO ticket");
  } else return 2;
  return 0;
}
'''


class PickupLyingRecoveryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        physical = PHYSICAL.read_text(encoding="utf-8")
        first = physical.index("  const bool pickupLyingAttempt =")
        marker = "if(physicalPickupGate.active() && pickupLyingAttempt && combinedGetUp.requestPrepare)"
        end_start = physical.index(marker, first)
        handoff = physical[first:end_start] + marker + body(physical, marker)
        joystick = JOYSTICK.read_text(encoding="utf-8")
        standing = re.search(r"const bool realUprightStand\s*=\s*(.*?);", joystick, re.S)
        if standing is None:
            raise AssertionError("real Stand recovery predicate not found")
        source = HARNESS.replace("/* REAL_PHYSICAL_HANDOFF */", handoff)
        source = source.replace("/* REAL_STAND_PREDICATE */", "static bool realStanding() {return " + standing.group(1) + ";}")
        cls.temp = tempfile.TemporaryDirectory(prefix="bhuman-pickup-lying-")
        folder = Path(cls.temp.name)
        cpp = folder / "lying.cpp"
        cpp.write_text(source, encoding="utf-8")
        cls.binary = folder / "lying"
        built = subprocess.run([shutil.which("c++") or "c++", "-std=c++20", "-Wall", "-Wextra", "-Werror",
                                "-I", str(ROOT / "Src/Libs"), "-I", str(ROOT / "Src"), str(cpp), "-o", str(cls.binary)],
                               capture_output=True, text=True)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def replay(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, f"{name}: {result.stdout}{result.stderr}")


CASES = (
    "fresh_lying_handoff", "old_held_rba", "penalized", "gc_stop", "gc_set", "stale_gc", "future_gc",
    "invalid_remote", "nonneutral_sticks", "imu_invalid", "emergency", "unauthorized",
    "must_reach_original_getup", "not_lying_does_not_yield", "not_prepare_phase_does_not_yield",
    "standing_needs_new_neutral_lt",
    "new_stand_revokes_old_combined_pending",
)


def _case(name):
    def test(self):
        self.replay(name)
    return test


for _name in CASES:
    setattr(PickupLyingRecoveryTest, "test_" + _name, _case(_name))


if __name__ == "__main__":
    unittest.main()
