"""Replay the real physical STAND/WALK gate; no SDK, services or devices.

All button snapshots come from the production atomic ButtonEvent observer.
Every case runs in a fresh process so process-lifetime storage cannot leak.
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PHYSICAL = ROOT / "Src/Modules/BehaviorControl/SkillBehaviorControl/Options/HandlePhysicalRobot.cpp"
FALL_OPTIONS = ROOT / "Src/Modules/Sensing/FallDownStateDetector/Options.h"
GETUP_ENGINE = ROOT / "Src/Modules/MotionControl/GetUpEngine/GetUpEngine.cpp"
FALL_ENGINE = ROOT / "Src/Modules/MotionControl/FallEngine/RestrictiveFallEngine.cpp"


def _body(source, marker):
    start = source.index("{", source.index(marker))
    depth = 1
    end = start + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


HARNESS = r'''
#include "Platform/BoosterPickupControl.h"
#include <cstdio>
#include <cstdlib>
#include <string>
#include <thread>
namespace P = BoosterPickupControl;
/* PRODUCTION_PREDICATES */
static void check(bool value, const char* why) {
  if(!value) {std::fprintf(stderr, "%s\n", why);std::abort();}
}
static P::Input good(unsigned frame = 100) {
  P::Input in;
  in.enabled = in.authorized = in.imuFinite = in.standOrWalk = in.uprightStable = in.gcLegal = true;
  in.emergency = false;in.frame = frame;in.epoch = 7;in.remoteGeneration = 13;
  in.buttons = P::physicalButtons();return in;
}
static P::Input previousInput;
static P::Gate* previousGate = nullptr;
static P::Result rawTick(P::Gate& gate, P::Input& in, unsigned frame) {
  in.frame = frame;in.buttons = P::physicalButtons();
  const auto out = gate.update(in);previousInput = in;previousGate = &gate;return out;
}
static P::Result tick(P::Gate& gate, P::Input& in, unsigned frame) {
  // Replay every intervening real feedback frame with the previous phase,
  // buttons and evidence. The new physical event occurs only at `frame`.
  if(previousGate == &gate && frame > previousInput.frame)
    while(frame - previousInput.frame > 40) {
      previousInput.frame += 40;gate.update(previousInput);
    }
  return rawTick(gate, in, frame);
}
static void press(unsigned button) {P::observePhysicalButton(button, true);}
static void release(unsigned button) {P::observePhysicalButton(button, false);}
static void enter(P::Gate& gate, P::Input& in) {
  check(!tick(gate, in, 100).entered, "synchronize only");
  press(1);const auto out = tick(gate, in, 130);
  check(out.entered && out.prepare && !out.stand && gate.active(), "fresh STAND must enter Prepare");
  release(1);in.standOrWalk = false;in.executedPrepare = true;tick(gate, in, 160);
}
static void startWalk(P::Gate& gate, P::Input& in, unsigned frame = 200) {
  press(2);check(!tick(gate, in, frame).automaticReady, "WALK cannot immediately grant AUTO");
}
static void requestStand(P::Gate& gate, P::Input& in, unsigned frame = 2201) {
  const auto out = tick(gate, in, frame);
  check(out.stand && !out.prepare && !out.automaticReady, "WALK must first request Stand");
  in.executedPrepare = false;in.standOrWalk = in.actualStand = true;
}
static void standReady(P::Gate& gate, P::Input& in, unsigned start = 2230) {
  check(!tick(gate, in, start).automaticReady, "first real Stand frame is not sufficient");
  check(!tick(gate, in, start + 299).automaticReady, "real Stand must last 300ms");
  check(tick(gate, in, start + 300).automaticReady, "legal fresh WALK must complete AUTO ticket");
}
int main(int argc, char** argv) {
  check(argc == 2, "one case required");const std::string name = argv[1];
  P::Gate gate;auto in = good();
  if(name == "button_edges") {
    auto value = P::physicalButtons();
    check(!value.standDown() && !value.walkDown() && !value.standPress() && !value.walkPress(), "initial buttons");
    press(1);press(1);value = P::physicalButtons();
    check(value.standDown() && value.standPress() == 1 && value.walkPress() == 0, "repeat DOWN is not a new edge");
    release(1);release(1);press(1);press(2);value = P::physicalButtons();
    check(value.standDown() && value.walkDown() && value.standPress() == 2 && value.walkPress() == 1, "independent edge counters");
    release(1);release(2);value = P::physicalButtons();
    check(!value.standDown() && !value.walkDown() && value.standPress() == 2 && value.walkPress() == 1, "UP preserves edge history");
  } else if(name == "unknown_buttons") {
    for(unsigned button : {0u, 3u, 5u, 32u, 576u}) {press(button);release(button);}
    check(P::physicalButtons().value == 0, "remote/F1/unknown codes cannot synthesize STAND/WALK");
  } else if(name == "atomic_button_counters") {
    std::thread a([] {for(unsigned i = 0; i < 10000; ++i) {press(1);release(1);}});
    std::thread b([] {for(unsigned i = 0; i < 10000; ++i) {press(2);release(2);}});
    a.join();b.join();const auto value = P::physicalButtons();
    check(value.standPress() == 10000 && value.walkPress() == 10000 && !value.standDown() && !value.walkDown(), "CAS must preserve concurrent independent button events");
  } else if(name == "withdraw_gc_illegal") {
    in.gcLegal = false;enter(gate, in);check(tick(gate, in, 9000).prepare, "GC cannot refuse or release withdrawal");
  } else if(name == "withdraw_remote_unrelated") {
    in.remoteGeneration = 9999;enter(gate, in);in.remoteGeneration = 10000;
    check(tick(gate, in, 9000).prepare, "remote generation cannot revoke physical Prepare");
  } else if(name == "withdraw_normal_walk") {
    in.actualStand = false;enter(gate, in);check(gate.active(), "walking withdrawal remains latched");
  } else if(name == "held_before_authorization") {
    in.authorized = false;press(1);check(!tick(gate, in, 100).entered, "no authorization");
    in.authorized = true;check(!tick(gate, in, 130).entered, "held before authorization must only synchronize");
    check(!tick(gate, in, 9000).entered && !gate.active(), "old held STAND is not an edge");
    release(1);tick(gate, in, 9030);press(1);check(tick(gate, in, 9060).entered, "new authorized STAND admitted");
  } else if(name == "no_authority" || name == "disabled" || name == "emergency" || name == "imu_invalid") {
    tick(gate, in, 100);
    if(name == "no_authority") in.authorized = false;
    if(name == "disabled") in.enabled = false;
    if(name == "emergency") in.emergency = true;
    if(name == "imu_invalid") in.imuFinite = false;
    press(1);const auto out = tick(gate, in, 130);
    check(!out.entered && !out.prepare && !out.stand && !out.automaticReady && !gate.active(), "unsafe or non-match input must not be admitted");
  } else if(name == "unsafe_phase") {
    in.standOrWalk = false;tick(gate, in, 100);press(1);const auto out = tick(gate, in, 130);
    check(!out.entered && !gate.active(), "STAND cannot create Prepare over Fall/GetUp/PlayDead");
    in.standOrWalk = true;check(!tick(gate, in, 160).entered, "old button edge cannot be replayed when phase becomes safe");
  } else if(name == "legal_walk_to_auto") {
    enter(gate, in);startWalk(gate, in);requestStand(gate, in);standReady(gate, in);
    gate.finishAutomatic();check(!gate.active(), "explicit successful handoff releases gate");
    check(!tick(gate, in, 6000).stand, "continued old WALK cannot retrigger gate");
  } else if(name == "walk_duration_boundary") {
    enter(gate, in);startWalk(gate, in);
    check(tick(gate, in, 2199).prepare, "1999ms WALK is too short");
    check(tick(gate, in, 2200).prepare, "strict WALK >2000ms boundary");
    requestStand(gate, in, 2201);
  } else if(name == "actual_prepare_time") {
    enter(gate, in);in.executedPrepare = false;in.standOrWalk = true;startWalk(gate, in);
    check(tick(gate, in, 2300).prepare, "requested Prepare time is not executed Prepare time");
    in.executedPrepare = true;in.standOrWalk = false;tick(gate, in, 2400);
    check(tick(gate, in, 4399).prepare, "executed Prepare needs its own full 2000ms");
    requestStand(gate, in, 4400);
  } else if(name == "prepare_continuity") {
    enter(gate, in);startWalk(gate, in);tick(gate, in, 2000);
    in.executedPrepare = false;in.standOrWalk = true;check(tick(gate, in, 2100).prepare, "brief actual phase exit cannot complete Prepare");
    in.executedPrepare = true;in.standOrWalk = false;tick(gate, in, 2200);
    check(tick(gate, in, 4199).prepare, "disjoint Prepare intervals cannot be added");
    requestStand(gate, in, 4200);
  } else if(name == "upright_continuity") {
    enter(gate, in);startWalk(gate, in);in.uprightStable = false;tick(gate, in, 2100);
    in.uprightStable = true;check(tick(gate, in, 2201).prepare, "first stable upright frame insufficient");
    check(tick(gate, in, 2500).prepare, "upright stability needs continuous 300ms");
    requestStand(gate, in, 2501);
  } else if(name == "illegal_walk_later_legal") {
    enter(gate, in);in.gcLegal = false;startWalk(gate, in);requestStand(gate, in);
    in.gcLegal = true;check(!tick(gate, in, 2300).automaticReady, "later legal GC cannot legalize old WALK");
    check(!tick(gate, in, 10000).automaticReady && gate.active(), "no delayed automatic revival");
    release(2);tick(gate, in, 10030);startWalk(gate, in, 10060);
    check(!tick(gate, in, 12060).automaticReady, "new WALK strict 2s");
    requestStand(gate, in, 12061);
    // Already in real Stand: the new confirmation interval begins only
    // when this new WALK hold completes, not from the old Stand timestamp.
    check(!tick(gate, in, 12360).automaticReady, "new successful WALK must still confirm 300ms Stand");
    check(tick(gate, in, 12361).automaticReady, "new legal WALK and new Stand interval may release");
  } else if(name == "gc_gap_revokes_ticket" || name == "epoch_revokes_ticket" || name == "remote_generation_revokes_ticket") {
    enter(gate, in);startWalk(gate, in);requestStand(gate, in);tick(gate, in, 2230);
    if(name == "gc_gap_revokes_ticket") in.gcLegal = false;
    if(name == "epoch_revokes_ticket") ++in.epoch;
    if(name == "remote_generation_revokes_ticket") ++in.remoteGeneration;
    check(!tick(gate, in, 2300).automaticReady, "intervening evidence invalidates ticket");
    in.gcLegal = true;in.epoch = 7;in.remoteGeneration = 13;
    check(!tick(gate, in, 3000).automaticReady && gate.active(), "old ticket cannot resurrect after evidence restoration");
  } else if(name == "new_stand_revokes_ticket") {
    enter(gate, in);startWalk(gate, in);requestStand(gate, in);tick(gate, in, 2230);
    release(2);press(1);const auto out = tick(gate, in, 2300);
    check(out.entered && out.prepare && !out.automaticReady, "new STAND must supersede pending AUTO ticket");
    release(1);in.executedPrepare = true;in.actualStand = in.standOrWalk = false;
    check(tick(gate, in, 6000).prepare, "old WALK cannot end renewed withdrawal");
  } else if(name == "walk_held_before_withdrawal") {
    press(2);enter(gate, in);check(tick(gate, in, 9000).prepare, "WALK held before STAND cannot re-enter");
    release(2);tick(gate, in, 9030);startWalk(gate, in, 9060);requestStand(gate, in, 11061);
  } else if(name == "simultaneous_stand_walk") {
    tick(gate, in, 100);press(1);press(2);check(tick(gate, in, 130).entered, "STAND wins simultaneous request");
    release(1);in.standOrWalk = false;in.executedPrepare = true;tick(gate, in, 160);
    check(tick(gate, in, 9000).prepare, "simultaneous WALK is not a fresh re-entry");
  } else if(name == "walk_release_cancels_hold") {
    enter(gate, in);startWalk(gate, in);release(2);tick(gate, in, 2100);
    check(tick(gate, in, 9000).prepare, "short WALK release cancels pending hold");
  } else if(name == "frame_reversal_cancels_hold") {
    enter(gate, in);startWalk(gate, in);tick(gate, in, 2000);
    check(!tick(gate, in, 1999).automaticReady, "reversed frame revokes held gesture");
    check(tick(gate, in, 9000).prepare, "old held WALK cannot resume after time reversal");
  } else if(name == "frame_gap_cancels_hold") {
    enter(gate, in);startWalk(gate, in);tick(gate, in, 2000);
    check(!rawTick(gate, in, 2251).automaticReady, "251ms missing feedback revokes held gesture");
    check(tick(gate, in, 9000).prepare, "old held WALK cannot resume after feedback gap");
  } else if(name == "duplicate_frame_cancels_hold") {
    enter(gate, in);startWalk(gate, in);tick(gate, in, 2000);
    check(!rawTick(gate, in, 2000).automaticReady, "duplicate feedback frame revokes held gesture");
    check(tick(gate, in, 9000).prepare, "repeated cached frames cannot authorize AUTO");
  } else if(name == "suspend_retains_withdrawal") {
    enter(gate, in);startWalk(gate, in);gate.suspend();
    check(gate.active() && tick(gate, in, 9000).prepare, "unvisited/fallback frame retains PRE but revokes partial WALK");
  } else if(name == "cached_remote_takeover") {
    check(P::manualTakeoverAllowed(), "initial behavior remains compatible");
    P::requireFreshRemoteNeutral();
    for(unsigned buttons : {576u, 320u, 64u, 32u, 34u}) {
      P::observeRemoteSample(buttons, true);
      check(!P::manualTakeoverAllowed(), "old remote chord cannot steal newly resumed AUTO");
    }
    P::observeRemoteSample(0, false);check(!P::manualTakeoverAllowed(), "non-neutral axes cannot release takeover fence");
    P::observeRemoteSample(0, true);check(P::manualTakeoverAllowed(), "real neutral sample permits later new manual chord");
    P::requireFreshRemoteNeutral();check(!P::manualTakeoverAllowed(), "another withdrawal needs another neutral sample");
  } else if(name == "unsafe_walk_cannot_arm") {
    enter(gate, in);in.executedPrepare = false;in.standOrWalk = in.actualStand = false;
    startWalk(gate, in);tick(gate, in, 1000);
    in.actualStand = in.standOrWalk = true;tick(gate, in, 1030);
    check(!tick(gate, in, 6000).automaticReady && gate.active(), "WALK pressed in Fall/GetUp/PlayDead cannot arm a future AUTO ticket");
  } else if(name == "unsafe_walk_phase_boundary") {
    enter(gate, in);in.executedPrepare = false;in.standOrWalk = in.actualStand = false;
    startWalk(gate, in); // Last actual GetUp/Fall frame, before real Stand.
    in.actualStand = in.standOrWalk = true;tick(gate, in, 230);
    check(!tick(gate, in, 6000).automaticReady && gate.active(), "WALK on final unsafe frame cannot become future legal re-entry");
  } else if(name == "active_imu_fault_retains_lock" || name == "active_emergency_retains_lock" || name == "active_authorization_loss_retains_lock") {
    enter(gate, in);startWalk(gate, in);tick(gate, in, 1800);
    if(name == "active_imu_fault_retains_lock") in.imuFinite = false;
    if(name == "active_emergency_retains_lock") in.emergency = true;
    if(name == "active_authorization_loss_retains_lock") in.authorized = false;
    const auto out = tick(gate, in, 1830);
    check(out.suspended && gate.active() && !out.prepare && !out.stand && !out.automaticReady,
          "body protection must suspend intent, retain human lock and yield native motion");
    in.imuFinite = in.authorized = true;in.emergency = false;
    check(tick(gate, in, 6000).prepare && gate.active(), "recovered health cannot restore old held WALK");
  } else if(name == "automatic_release_epoch") {
    P::offerAutomaticRelease(7);check(P::automaticReleaseEpoch() == 7, "AUTO ticket must preserve epoch");
    in.epoch = 7;validateRelease(in);
    check(P::consumeAutomaticRelease() && !P::consumeAutomaticRelease(), "same legal epoch is one shot");
  } else if(name == "automatic_release_stale_epoch" || name == "automatic_release_illegal_gc"
            || name == "automatic_release_authorization" || name == "automatic_release_emergency" || name == "automatic_release_disabled") {
    P::offerAutomaticRelease(7);in.epoch = 7;
    if(name == "automatic_release_stale_epoch") in.epoch = 8;
    if(name == "automatic_release_illegal_gc") in.gcLegal = false;
    if(name == "automatic_release_authorization") in.authorized = false;
    if(name == "automatic_release_emergency") in.emergency = true;
    if(name == "automatic_release_disabled") in.enabled = false;
    validateRelease(in);check(!P::consumeAutomaticRelease(), "real Physical guard must revoke invalid deferred release");
    in.enabled = in.authorized = in.gcLegal = true;in.emergency = false;in.epoch = 7;
    validateRelease(in);check(!P::consumeAutomaticRelease(), "restored inputs cannot revive cancelled AUTO release");
  } else if(name == "predicate_prepare_pickedup") {
    check(actualUpright({true, true}, {FallDownState::pickedUp}), "native Prepare/pickedUp must use real stable IMU");
  } else if(name == "predicate_stand_pickedup") {
    check(!actualUpright({true, false}, {FallDownState::pickedUp}), "real Stand still requires native upright classification");
    check(!actualUpright({true, false}, {FallDownState::fallen}), "actual Stand cannot bypass fallen classification");
  } else if(name == "predicate_imu_unstable") {
    for(bool prepare : {false, true})
      for(auto state : {FallDownState::pickedUp, FallDownState::upright, FallDownState::fallen})
        check(!actualUpright({false, prepare}, {state}), "Prepare exception cannot bypass real IMU stability");
  } else if(name == "predicate_stand_upright") {
    check(actualUpright({true, false}, {FallDownState::upright}), "real Stand with upright classification admits stable feedback");
  } else if(name == "native_prepare_pickedup_roundtrip") {
    enter(gate, in);
    in.uprightStable = actualUpright({true, true}, {FallDownState::pickedUp});
    startWalk(gate, in);requestStand(gate, in);
    in.uprightStable = actualUpright({true, false}, {FallDownState::pickedUp});
    check(!tick(gate, in, 2230).automaticReady, "initial pickedUp classification in real Stand cannot release");
    in.uprightStable = actualUpright({true, false}, {FallDownState::upright});
    standReady(gate, in, 2260);
  } else if(name == "unsafe_consecutive_frames") {
    enter(gate, in);startWalk(gate, in);
    in.executedPrepare = in.standOrWalk = in.actualStand = false;
    for(unsigned frame = 230; frame <= 1230; frame += 40) {
      const auto out = rawTick(gate, in, frame);
      check(out.suspended && !out.prepare && !out.stand && !out.automaticReady && gate.active(),
            "every real GetUp/Fall/PlayDead frame must yield native control, including after resynchronization");
    }
  } else if(name == "unsafe_frame_gap") {
    enter(gate, in);startWalk(gate, in);
    in.executedPrepare = in.standOrWalk = in.actualStand = false;
    for(unsigned frame : {230u, 1000u, 1030u, 1030u, 900u, 2000u}) {
      const auto out = rawTick(gate, in, frame);
      check(out.suspended && !out.prepare && !out.stand && !out.automaticReady && gate.active(),
            "unsafe phase must precede synchronization, gap, duplicate and reversed frame early returns");
    }
  } else if(name == "unsafe_return_stand_no_old_walk") {
    enter(gate, in);startWalk(gate, in);
    in.executedPrepare = in.standOrWalk = in.actualStand = false;
    rawTick(gate, in, 230);rawTick(gate, in, 270);
    in.standOrWalk = in.actualStand = true;
    for(unsigned frame = 310; frame <= 6310; frame += 40) {
      const auto out = rawTick(gate, in, frame);
      check(out.stand && !out.prepare && !out.automaticReady && gate.active(),
            "return to actual Stand must retain Stand hold, never restore old Prepare or held WALK");
    }
  } else if(name == "native_recovery_stand_requires_new_stand_for_prepare") {
    enter(gate, in);in.executedPrepare = in.standOrWalk = in.actualStand = false;
    for(unsigned frame = 200; frame <= 1200; frame += 40) {
      const auto out = rawTick(gate, in, frame);
      check(out.suspended && !out.prepare && !out.stand && gate.active(), "native recovery always owns the actual unsafe phase");
    }
    in.standOrWalk = in.actualStand = true;
    const auto stood = rawTick(gate, in, 1230);
    check(stood.stand && !stood.prepare && !stood.automaticReady && gate.active(),
          "after >25 unsafe frames, actual Stand cannot restore pre-fall Prepare");
    check(tick(gate, in, 3000).stand, "no new physical STAND means no new Prepare");
    press(1);const auto fresh = tick(gate, in, 3030);
    check(fresh.entered && fresh.prepare && !fresh.stand && !fresh.automaticReady,
          "only a fresh physical STAND re-enters Prepare after native recovery");
  } else if(name == "native_getup_not_interrupted") {
    NativeRecoveryReplay recovery;recovery.theMotionInfo.executedPhase = MotionPhase::getUp;recovery.run();
    check(recovery.request.motion == MotionRequest::stand, "native GetUp receives only normal Stand request");
    GetUpReplay phase;
    check(!phase.isDone(recovery.request), "normal Stand does not interrupt running real GetUp phase");
    phase.state = GetUpReplay::State::done;check(phase.isDone(recovery.request), "completed GetUp may continue normally");
  } else if(name == "native_righted_fall_can_leave") {
    NativeRecoveryReplay recovery;recovery.theMotionInfo.executedPhase = MotionPhase::fall;recovery.run();
    check(recovery.request.motion == MotionRequest::stand, "native Fall must not receive perpetual FallRequest");
    FallReplay phase;check(!phase.isDone(recovery.request), "unsafe Fall cannot leave before real native permission");
    phase.allowLeavingFallPhase = true;check(phase.isDone(recovery.request), "native-approved righted Fall may leave");
    MotionRequest stuck;stuck.motion = MotionRequest::fallRequest;
    check(!phase.isDone(stuck), "regression witness: perpetual FallRequest used to trap righted Fall");
  } else if(name == "native_prepare_playdead_unchanged") {
    NativeRecoveryReplay recovery;
    recovery.theMotionInfo.executedPhase = MotionPhase::prepare;recovery.run();
    check(recovery.request.motion == MotionRequest::prepare, "actual Prepare remains Prepare");
    recovery.theMotionInfo.executedPhase = MotionPhase::playDead;recovery.run();
    check(recovery.request.motion == MotionRequest::playDead, "actual PlayDead must not be replaced by Stand");
  } else if(name == "global_holders") {
    check(!P::held() && !P::consumeAutomaticRelease(), "holders default safe");
    P::setHeld(true);check(P::held(), "motion hold visible independently");
    P::offerAutomaticRelease();check(P::consumeAutomaticRelease(), "AUTO offer consumable once");
    check(!P::consumeAutomaticRelease(), "AUTO cannot repeat");
    P::offerAutomaticRelease();P::cancelAutomaticRelease();check(!P::consumeAutomaticRelease(), "cancel revokes stale AUTO offer");
    P::setHeld(false);check(!P::held(), "explicit hold release");
  } else return 2;
  return 0;
}
'''


class PhysicalPickupGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="bhuman-physical-pickup-")
        folder = Path(cls.temp.name)
        source = folder / "pickup.cpp"
        physical = PHYSICAL.read_text(encoding="utf-8")
        initializer = physical.split("const BoosterPickupControl::Input pickupInput{", 1)[1].split("};", 1)[0]
        upright = re.search(r"\.uprightStable\s*=\s*(.*?),\s*\.gcLegal", initializer, re.S)
        if upright is None:
            raise AssertionError("cannot extract the real pickupInput upright predicate")
        guard = re.search(
            r"if\(!pickupInput\.enabled.*?BoosterPickupControl::cancelAutomaticRelease\(\);",
            physical.split("const auto pickup = physicalPickupGate.update(pickupInput);", 1)[1], re.S,
        )
        if guard is None:
            raise AssertionError("cannot extract the real per-frame deferred AUTO validation")
        native_state = _body(physical, "state(pickupNativeRecovery)")
        native_action = _body(native_state, "action")
        getup_done = _body(GETUP_ENGINE.read_text(encoding="utf-8"), "bool GetUpPhase::isDone(")
        fall_done = _body(FALL_ENGINE.read_text(encoding="utf-8"), "bool RestrictiveFallPhase::isDone(")
        predicates = r'''
struct CombinedForUpright {bool uprightStable, executedPrepare;};
struct FallDownState {enum State {pickedUp, upright, fallen};State state;};
static bool actualUpright(const CombinedForUpright& combinedInput, const FallDownState& theFallDownState) {
  return ''' + upright.group(1) + r''';
}
static void validateRelease(const P::Input& pickupInput) {
''' + guard.group(0) + r'''
}
namespace MotionPhase {enum Type {stand, walk, prepare, playDead, fall, getUp};}
struct MotionRequest {enum Motion {stand, prepare, playDead, fallRequest};Motion motion = stand;};
struct NativeRecoveryReplay {
  struct {MotionPhase::Type executedPhase = MotionPhase::stand;} theMotionInfo;
  MotionRequest request;
  void LookForward() {}
  void Stand() {request.motion = MotionRequest::stand;}
  void Prepare() {request.motion = MotionRequest::prepare;}
  void PlayDead() {request.motion = MotionRequest::playDead;}
  void FallRequest() {request.motion = MotionRequest::fallRequest;}
  void run() ''' + native_action + r'''
};
struct GetUpReplay {
  enum class State {running, done};State state = State::running;
  bool isDone(const MotionRequest& request) const ''' + getup_done + r'''
};
struct FallReplay {
  bool allowLeavingFallPhase = false;
  bool isDone(const MotionRequest& motionRequest) const ''' + fall_done + r'''
};
'''
        source.write_text(HARNESS.replace("/* PRODUCTION_PREDICATES */", predicates), encoding="utf-8")
        cls.binary = folder / "pickup"
        compiler = shutil.which("c++")
        if compiler is None:
            raise RuntimeError("c++ is required to test the real production header")
        subprocess.run(
            [compiler, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-pthread",
             "-I", str(ROOT / "Src/Libs"), str(source), "-o", str(cls.binary)],
            check=True, capture_output=True, text=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def replay(self, name):
        completed = subprocess.run([str(self.binary), name], capture_output=True, text=True, timeout=15)
        self.assertEqual(completed.returncode, 0, f"{name}: {completed.stdout}{completed.stderr}")

    def test_native_prepare_classification_wiring(self):
        options = FALL_OPTIONS.read_text(encoding="utf-8")
        self.assertRegex(options, r"(?s)executedPhase == MotionPhase::prepare\)\s*goto pickedUp;")
        physical = PHYSICAL.read_text(encoding="utf-8")
        initializer = physical.split("const BoosterPickupControl::Input pickupInput{", 1)[1].split("};", 1)[0]
        self.assertIn("combinedInput.executedPrepare || theFallDownState.state == FallDownState::upright", initializer)
        self.assertIn(".executedPrepare = combinedInput.executedPrepare", initializer)
        self.assertIn(".actualStand = theMotionInfo.executedPhase == MotionPhase::stand", initializer)

    def test_epoch_release_producer_wiring(self):
        physical = PHYSICAL.read_text(encoding="utf-8")
        self.assertIn("BoosterPickupControl::offerAutomaticRelease(pickupInput.epoch);", physical)
        self.assertIn("BoosterPickupControl::automaticReleaseEpoch() != pickupInput.epoch", physical)
        self.assertIn("!pickupInput.gcLegal", physical)


CASES = (
    "button_edges", "unknown_buttons", "atomic_button_counters",
    "withdraw_gc_illegal", "withdraw_remote_unrelated", "withdraw_normal_walk",
    "held_before_authorization", "no_authority", "disabled", "emergency", "imu_invalid", "unsafe_phase",
    "legal_walk_to_auto", "walk_duration_boundary", "actual_prepare_time", "prepare_continuity", "upright_continuity",
    "illegal_walk_later_legal", "gc_gap_revokes_ticket", "epoch_revokes_ticket", "remote_generation_revokes_ticket",
    "new_stand_revokes_ticket", "walk_held_before_withdrawal", "simultaneous_stand_walk",
    "walk_release_cancels_hold", "frame_reversal_cancels_hold", "frame_gap_cancels_hold", "duplicate_frame_cancels_hold",
    "suspend_retains_withdrawal", "cached_remote_takeover", "unsafe_walk_cannot_arm", "unsafe_walk_phase_boundary", "global_holders",
    "active_imu_fault_retains_lock", "active_emergency_retains_lock", "active_authorization_loss_retains_lock",
    "automatic_release_epoch", "automatic_release_stale_epoch", "automatic_release_illegal_gc",
    "automatic_release_authorization", "automatic_release_emergency", "automatic_release_disabled",
    "predicate_prepare_pickedup", "predicate_stand_pickedup", "predicate_imu_unstable", "predicate_stand_upright",
    "native_prepare_pickedup_roundtrip",
    "unsafe_consecutive_frames", "unsafe_frame_gap", "unsafe_return_stand_no_old_walk",
    "native_recovery_stand_requires_new_stand_for_prepare",
    "native_getup_not_interrupted", "native_righted_fall_can_leave", "native_prepare_playdead_unchanged",
)


def _case(name):
    def test(self):
        self.replay(name)
    return test


for _name in CASES:
    setattr(PhysicalPickupGateTest, "test_" + _name, _case(_name))


if __name__ == "__main__":
    unittest.main()
