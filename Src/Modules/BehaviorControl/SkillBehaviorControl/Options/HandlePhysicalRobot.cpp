#include "SkillBehaviorControl.h"
#include "Platform/Time.h"
#include "Tools/BehaviorControl/LiveGetUpGesture.h"
#include "Tools/BehaviorControl/CombinedLiveGetUpGesture.h"
#include "Platform/BoosterMatchSafety.h"
#include <cstdio>
#if defined(TARGET_ROBOT) && defined(LINUX)
#include "Platform/BoosterOfficialLiveTest.h"
#endif

option((SkillBehaviorControl) HandlePhysicalRobot,
       defs((unsigned int)(2000) pressDuration, /**< Button press duration to switch from a safe state into a less safe one. */
            (Angle)(15_deg) maxTorsoForWalk, /**< Max Torso orientation to switch to active state. */
            (Angle)(15_deg) maxGyroForWalk, /**< Max gyro to switch to active state. */
            (Angle)(5_deg) maxGyroForFall, /**< Max gyro to switch to fall state. */
            (Angle)(60_deg) minFallAngle), /**< Min Torso orientation to switch to fall state. */
       vars((bool)(false) liveGetUpReleaseSeen,
            (bool)(false) liveGetUpHolding,
            (unsigned)(0) liveGetUpStartedAt,
            (unsigned)(0) liveGetUpLastFrame,
            (unsigned)(0) liveGetUpLastLog,
            (unsigned)(0) combinedGetUpLastLog,
            (CombinedLiveGetUpGesture::Gate) combinedGetUpGate))
{
#if defined(TARGET_ROBOT) && defined(LINUX)
  const bool liveMode = BoosterOfficialLiveTest::enabled();
  const bool liveAuthorized = BoosterOfficialLiveTest::bodyAuthorized();
#else
  const bool liveMode = false;
  const bool liveAuthorized = false;
#endif
  const LiveGetUpGesture::Input liveInput{
    .live = liveMode, .authorized = liveAuthorized,
    .prepare = theMotionInfo.executedPhase == MotionPhase::prepare,
    .emergency = theSystemSensorData.emergencyMode,
    .joystickValid = theJoystickState.valid, .rb = theJoystickState.pressed(5),
    .a = theJoystickState.pressed(1), .b = theJoystickState.pressed(2),
    .stand = theEnhancedKeyStates.pressed[KeyStates::stand],
    .walk = theEnhancedKeyStates.pressed[KeyStates::walk],
    .gcActive = theGameState.gameControllerActive, .initial = theGameState.isInitial(),
    .packetInitial = theGameControllerData.state == STATE_INITIAL,
    .stopped = theGameState.stopped || theGameControllerData.stopped,
    .penalized = theGameState.isPenalized() || theGameState.playerState != GameState::active,
    .matchRecoveryAllowed = BoosterMatchSafety::enabled()
        && CombinedLiveGetUpGesture::recoveryStateAllowed(theGameControllerData.state),
    .frameTime = theFrameInfo.time, .gcNow = Time::getCurrentSystemTime(),
    .gcPacketTime = theGameControllerData.timeLastPacketReceived, .holdMs = pressDuration,
    .lyingStable = LiveGetUpGesture::stableLying(theInertialData.angle.x(), theInertialData.angle.y(),
                    theInertialData.gyro.x(), theInertialData.gyro.y(), theInertialData.gyro.z(), minFallAngle, maxGyroForFall)};
  const bool wasHolding = liveGetUpHolding;
  const bool liveGetUpRequested = LiveGetUpGesture::update(liveInput, liveGetUpReleaseSeen,
                                      liveGetUpHolding, liveGetUpStartedAt, liveGetUpLastFrame)
      && (!BoosterMatchSafety::enabled() || theJoystickState.buttons == ((1u << 5) | (1u << 1)));
  bool getUpSticksNeutral = true;
  for(const float axis : theJoystickState.axes)
    getUpSticksNeutral &= std::isfinite(axis) && std::abs(axis) <= .1f;
  const CombinedLiveGetUpGesture::Input combinedInput{
    .enabled = liveMode && BoosterMatchSafety::enabled(), .authorized = liveAuthorized,
    .emergency = theSystemSensorData.emergencyMode, .joystickValid = theJoystickState.valid,
    .sticksNeutral = getUpSticksNeutral, .neutralButtons = theJoystickState.buttons == 0,
    .rbAOnly = theJoystickState.buttons == ((1u << 5) | (1u << 1)),
    .rbOnly = theJoystickState.buttons == (1u << 5), .aOnly = theJoystickState.buttons == (1u << 1),
    .rawInitial = theGameControllerData.state == STATE_INITIAL,
    .rawRecoveryAllowed = BoosterMatchSafety::enabled()
        && CombinedLiveGetUpGesture::recoveryStateAllowed(theGameControllerData.state),
    .stopped = theGameControllerData.stopped != 0,
    .unpenalized = CombinedLiveGetUpGesture::ownPlayerUnpenalized(theGameControllerData,
                      Global::getSettings().teamNumber, Global::getSettings().playerNumber),
    .imuFinite = std::isfinite(theInertialData.angle.x()) && std::isfinite(theInertialData.angle.y())
                 && std::isfinite(theInertialData.gyro.x()) && std::isfinite(theInertialData.gyro.y())
                 && std::isfinite(theInertialData.gyro.z()),
    .canPrepare = theMotionInfo.executedPhase == MotionPhase::playDead || theMotionInfo.executedPhase == MotionPhase::prepare,
    .executedPrepare = theMotionInfo.executedPhase == MotionPhase::prepare,
    .lyingStable = liveInput.lyingStable,
    .uprightStable = std::abs(theInertialData.angle.x()) < maxTorsoForWalk
                     && std::abs(theInertialData.angle.y()) < maxTorsoForWalk
                     && std::abs(theInertialData.gyro.x()) < maxGyroForWalk
                     && std::abs(theInertialData.gyro.y()) < maxGyroForWalk
                     && std::abs(theInertialData.gyro.z()) < maxGyroForWalk,
    .frameTime = theFrameInfo.time, .gcNow = liveInput.gcNow, .gcPacketTime = liveInput.gcPacketTime,
    .epoch = BoosterMatchSafety::epoch()};
  physicalPickupVisited = true;
  const bool pickupPacketFresh = combinedInput.gcPacketTime > 0 && combinedInput.gcNow >= combinedInput.gcPacketTime
      && combinedInput.gcNow - combinedInput.gcPacketTime <= 2000;
  const bool pickupTargetPresent = BoosterPickupControl::ownPlayerPresent(theGameControllerData,
      Global::getSettings().teamNumber, Global::getSettings().playerNumber);
  const BoosterPickupControl::Input pickupInput{
    .enabled = liveMode && BoosterMatchSafety::enabled(), .authorized = liveAuthorized,
    .emergency = combinedInput.emergency, .imuFinite = combinedInput.imuFinite,
    .standOrWalk = theMotionInfo.executedPhase == MotionPhase::stand || theMotionInfo.executedPhase == MotionPhase::walk,
    .executedPrepare = combinedInput.executedPrepare,
    .actualStand = theMotionInfo.executedPhase == MotionPhase::stand,
    // Native FallDownState intentionally reports pickedUp throughout Prepare.
    // Use actual IMU stability there; the later real Stand requires upright.
    .uprightStable = combinedInput.uprightStable
        && (combinedInput.executedPrepare || theFallDownState.state == FallDownState::upright),
    .gcLegal = !theGameState.stopped && theGameState.playerState == GameState::active
        && CombinedLiveGetUpGesture::freshRecovery(theGameControllerData.state, combinedInput.stopped,
             combinedInput.unpenalized, combinedInput.gcNow, combinedInput.gcPacketTime),
    .gcArmContext = pickupPacketFresh && pickupTargetPresent && !combinedInput.stopped
        && (combinedInput.rawInitial || combinedInput.rawRecoveryAllowed),
    .gcPacketFresh = pickupPacketFresh, .gcTargetPresent = pickupTargetPresent,
    .gcSessionEnded = pickupPacketFresh
        && !(combinedInput.rawInitial || combinedInput.rawRecoveryAllowed || theGameControllerData.state == STATE_SET),
    .gcTimestampInvalid = combinedInput.gcPacketTime == 0 || combinedInput.gcNow < combinedInput.gcPacketTime,
    .frame = theFrameInfo.time, .gcPacketTime = combinedInput.gcPacketTime, .epoch = combinedInput.epoch,
    .remoteGeneration = BoosterRemoteStand::generation(), .buttons = BoosterPickupControl::physicalButtons()};
  const auto pickupReasonBefore = physicalPickupGate.reason();
  const auto pickup = physicalPickupGate.update(pickupInput);
  int pickupRawPenalty = -1;
  if(pickupTargetPresent)
    for(const auto& team : theGameControllerData.teams)
      if(team.teamNumber == Global::getSettings().teamNumber)
        pickupRawPenalty = team.players[Global::getSettings().playerNumber - 1].penalty;
  if(pickupInput.enabled && (pickupReasonBefore != physicalPickupGate.reason() || pickup.requested || pickup.cancelled))
    std::printf("PHYSICAL_PICKUP transition reason=%s requested=%d pending=%d waiting_gc=%d released=0 cancelled=%d walk_press=%u buttons=%u raw_gc=%u penalty=%d unpenalized=%d active=%d arm_context=%d gc_age_ms=%u phase=%u epoch=%llu remote_generation=%llu\n",
        BoosterPickupControl::reasonName(physicalPickupGate.reason()), pickup.requested, pickup.pending,
        pickup.waitingGc, pickup.cancelled, pickupInput.buttons.walkPress(),
        static_cast<unsigned>(pickupInput.buttons.value & 3u), static_cast<unsigned>(theGameControllerData.state),
        pickupRawPenalty, combinedInput.unpenalized, theGameState.playerState == GameState::active,
        pickupInput.gcArmContext, combinedInput.gcNow >= combinedInput.gcPacketTime
          ? combinedInput.gcNow - combinedInput.gcPacketTime : ~0u,
        static_cast<unsigned>(theMotionInfo.executedPhase), static_cast<unsigned long long>(pickupInput.epoch),
        static_cast<unsigned long long>(pickupInput.remoteGeneration));
  BoosterPickupControl::setHeld(physicalPickupGate.active());
  if(!pickupInput.enabled || !pickupInput.authorized || pickupInput.emergency || !pickupInput.gcLegal
     || BoosterPickupControl::automaticReleaseEpoch() != pickupInput.epoch)
    BoosterPickupControl::cancelAutomaticRelease();
  const bool pickupLyingAttempt = physicalPickupGate.active() && combinedInput.lyingStable && combinedInput.canPrepare;
  if(physicalPickupGate.active())
  {
    if(pickup.entered || !pickupLyingAttempt)
    {
      combinedGetUpGate = CombinedLiveGetUpGesture::Gate{};
      CombinedLiveGetUpGesture::revokePrepare();
    }
    BoosterMatchSafety::cancelJoystickAfterGetUp();
    BoosterMatchSafety::initialHandoff().disarm();
    (void)BoosterMatchSafety::consumeJoystickRelease();
    if(pickup.entered)
    {
      BoosterPickupControl::cancelAutomaticRelease();
      BoosterPickupControl::requireFreshRemoteNeutral();
      liveGetUpReleaseSeen = liveGetUpHolding = false;
      std::printf("PHYSICAL_PICKUP entered_PRE physical_STAND=1 automatic_locked=1\n");
    }
  }
  const bool combinedWasPending = combinedGetUpGate.pending();
  // All physical Prepare exits in match mode share the same rule gate, not
  // just the new RB+A path. This permits self-recovery, not walking or kicking.
  const bool matchRecoveryPermitted = !BoosterMatchSafety::enabled()
      || (liveAuthorized && !combinedInput.emergency && combinedInput.joystickValid
          && combinedInput.sticksNeutral && combinedInput.imuFinite
          && CombinedLiveGetUpGesture::freshRecovery(theGameControllerData.state, combinedInput.stopped,
               combinedInput.unpenalized, combinedInput.gcNow, combinedInput.gcPacketTime));
  const auto combinedPreviousReason = combinedGetUpGate.reason();
  const auto combinedGetUp = physicalPickupGate.active() && !pickupLyingAttempt
      ? CombinedLiveGetUpGesture::Result{} : combinedGetUpGate.update(combinedInput);
  bool pickupLyingHandover = false;
  if(physicalPickupGate.active() && pickupLyingAttempt && combinedGetUp.requestPrepare)
  {
    // Only the ORIGINAL fresh, lawful RB+A gate may leave the manual lock
    // while lying. Rejoin its existing Prepare/GetUp path, never grant AUTO.
    physicalPickupGate.yieldToLyingRecovery();
    BoosterPickupControl::setHeld(false);
    BoosterPickupControl::cancelAutomaticRelease();
    BoosterPickupControl::requireFreshRemoteNeutral();
    BoosterRemoteStand::beginBodyRecovery();
    pickupLyingHandover = true;
    std::printf("PHYSICAL_PICKUP LYING_RBA_TO_NATIVE fresh_original_gate=1 stand_handover_required=1\n");
  }
  if(combinedGetUp.cancelled) CombinedLiveGetUpGesture::revokePrepare();
  if(combinedGetUp.requestPrepare || combinedGetUp.refreshPrepareAdmission)
  {
    CombinedLiveGetUpGesture::offerPrepare(theFrameInfo.time, combinedInput.epoch);
    if(combinedGetUp.requestPrepare)
    {
      if(BoosterMatchSafety::enabled() && !combinedInput.rawInitial && !pickupLyingHandover)
        BoosterRemoteStand::beginBodyRecovery();
      std::printf("LIVE_COMBINED_GETUP_PREPARE fresh_RB_A_hold=2000 raw_gc=%u stable=1 recovery_hold=%d\n",
                  static_cast<unsigned>(theGameControllerData.state), BoosterRemoteStand::bodyRecoveryPending());
    }
  }
  const bool combinedReasonChanged = combinedPreviousReason != combinedGetUpGate.reason();
  if(liveMode && (combinedGetUp.requestPrepare || combinedGetUp.requestGetUp || combinedGetUp.requestActive || combinedGetUp.cancelled
      || ((theJoystickState.buttons || combinedWasPending || combinedGetUpGate.pending() || combinedReasonChanged)
          && (theFrameInfo.time - combinedGetUpLastLog >= 2000
              || (combinedReasonChanged && theFrameInfo.time - combinedGetUpLastLog >= 250)))))
  {
    combinedGetUpLastLog = theFrameInfo.time;
    std::printf("LIVE_COMBINED_GETUP reason=%s raw_gc=%u gc_age_ms=%u gc_stop=%d raw_unpenalized=%d team=%u pid=%u players=%u valid=%d buttons=%u axes_neutral=%d auth=%d emergency=%d imu_finite=%d phase=%u released=%d holding=%d hold_ms=%u rb_prefix_ms=%u pending=%d pending_age_ms=%u epoch=%llu\n",
                CombinedLiveGetUpGesture::reasonName(combinedGetUpGate.reason()),
                static_cast<unsigned>(theGameControllerData.state),
                combinedInput.gcNow >= combinedInput.gcPacketTime ? combinedInput.gcNow - combinedInput.gcPacketTime : ~0u,
                combinedInput.stopped, combinedInput.unpenalized,
                static_cast<unsigned>(Global::getSettings().teamNumber), static_cast<unsigned>(Global::getSettings().playerNumber),
                static_cast<unsigned>(theGameControllerData.playersPerTeam), combinedInput.joystickValid, theJoystickState.buttons,
                combinedInput.sticksNeutral, combinedInput.authorized, combinedInput.emergency, combinedInput.imuFinite,
                static_cast<unsigned>(theMotionInfo.executedPhase), combinedGetUpGate.released(), combinedGetUpGate.chordHolding(),
                combinedGetUpGate.holdMs(theFrameInfo.time), combinedGetUpGate.modifierMs(theFrameInfo.time),
                combinedGetUpGate.pending(), combinedGetUpGate.pendingMs(theFrameInfo.time), static_cast<unsigned long long>(combinedInput.epoch));
  }
  if(liveMode && (wasHolding != liveGetUpHolding ||
      (liveInput.rb && liveInput.a && theFrameInfo.time - liveGetUpLastLog >= 2000)))
  {
    liveGetUpLastLog = theFrameInfo.time;
    std::printf("LIVE_GETUP_GESTURE holding=%d released=%d hold_ms=%u walk_ms=%u auth=%d phase=%u gc_active=%d gc_age_ms=%u initial=%d/%d stopped=%d player=%u rb_a=%d b=%d stand=%d angles_rad=%.4f,%.4f gyro_rad_s=%.4f,%.4f,%.4f stable=%d\n",
                liveGetUpHolding, liveGetUpReleaseSeen,
                liveGetUpHolding ? theFrameInfo.time - liveGetUpStartedAt : 0,
                theEnhancedKeyStates.pressedDuration[KeyStates::walk], liveAuthorized,
                static_cast<unsigned>(theMotionInfo.executedPhase), liveInput.gcActive,
                liveInput.gcNow >= liveInput.gcPacketTime ? liveInput.gcNow - liveInput.gcPacketTime : ~0u,
                liveInput.initial, liveInput.packetInitial, liveInput.stopped,
                static_cast<unsigned>(theGameState.playerState), liveInput.rb && liveInput.a,
                liveInput.b, liveInput.stand, static_cast<double>(theInertialData.angle.x()),
                static_cast<double>(theInertialData.angle.y()), static_cast<double>(theInertialData.gyro.x()),
                static_cast<double>(theInertialData.gyro.y()), static_cast<double>(theInertialData.gyro.z()), liveInput.lyingStable);
  }

  common_transition
  {
    if(theSystemSensorData.emergencyMode || theGameState.playerState == GameState::unstiff)
      goto inactive;
    else if(physicalPickupGate.active())
    {
      if(pickup.suspended) goto pickupNativeRecovery;
      if(pickup.automaticReady && BoosterRemoteStand::resumeFromPhysicalPickup(pickupInput.remoteGeneration))
      {
        physicalPickupGate.finishAutomatic();
        BoosterPickupControl::requireFreshRemoteNeutral();
        BoosterPickupControl::setHeld(false);
        BoosterPickupControl::offerAutomaticRelease(pickupInput.epoch);
        BoosterMatchSafety::cancelJoystickAfterGetUp();
        std::printf("PHYSICAL_PICKUP AUTO_RELEASE released=1 pending=0 walk_press=%u buttons=%u penalty=%d unpenalized=%d real_Stand_ms>=300 legal_GC=1 phase=%u epoch=%llu remote_generation=%llu\n",
            pickupInput.buttons.walkPress(), static_cast<unsigned>(pickupInput.buttons.value & 3u),
            pickupRawPenalty, combinedInput.unpenalized, static_cast<unsigned>(theMotionInfo.executedPhase),
            static_cast<unsigned long long>(pickupInput.epoch), static_cast<unsigned long long>(pickupInput.remoteGeneration));
        goto active;
      }
      if(pickup.prepare) goto pickupPrepare;
      goto pickupStand;
    }
    else if(SystemCall::getMode() == SystemCall::simulatedRobot && theGameState.playerState != GameState::unstiff && theExtendedGameState.playerStateLastFrame == GameState::unstiff)
      goto active;
    else if((combinedGetUp.requestPrepare || combinedGetUp.refreshPrepareAdmission)
            && theGameState.playerState != GameState::unstiff)
      goto prepare;
    else if(matchRecoveryPermitted && theMotionInfo.executedPhase != MotionPhase::prepare &&
            (!BoosterMatchSafety::enabled() || combinedInput.rawInitial || combinedInput.lyingStable) &&
            ((theEnhancedKeyStates.pressed[KeyStates::stand] && theMotionInfo.executedPhase != MotionPhase::playDead && theMotionInfo.executedPhase != MotionPhase::fall)
             || (theGameState.playerState != GameState::unstiff && theExtendedGameState.playerStateLastFrame == GameState::unstiff)))
      goto prepare;
  }

  // The remaining behavior is executed.
  initial_state(active)
  {}

  state(pickupPrepare)
  {
    action
    {
      LookAtAngles({.pan = JointAngles::off, .tilt = JointAngles::off});
      Prepare();
    }
  }

  state(pickupStand)
  {
    action
    {
      LookForward();
      Stand();
    }
  }

  state(pickupNativeRecovery)
  {
    action
    {
      LookForward();
      // Stand is only a normal request: native Fall/GetUp isDone keeps control
      // until safe. Repeating FallRequest would trap a fall that was righted.
      if(theMotionInfo.executedPhase == MotionPhase::prepare)
        Prepare();
      else if(theMotionInfo.executedPhase == MotionPhase::playDead)
        PlayDead();
      else
        Stand();
    }
  }

  // The robot is inactive. This state can only be reached on Booster
  // robots and can only be left by pressing "stand".
  state(inactive)
  {
    action
    {
      LookAtAngles({.pan = JointAngles::off,
                    .tilt = JointAngles::off});

      // Robot is still in damping, but the other buttons are pressed -> let LED blink fast
      if(theKeyStates.pressed[KeyStates::walk])
        BehaviorStateTransitionFailed();

      PlayDead();
    }
  }

  // Prepare for switching to the active state. This state can only be
  // reached by pressing the "stand" button, which only exists on Booster
  // robots.
  state(prepare)
  {
    transition
    {
      if(combinedGetUp.requestActive)
      {
        std::printf("LIVE_COMBINED_PREPARE_ACTIVE actual_Prepare_ms>=2000 raw_gc=%u upright_stable=1\n",
                    static_cast<unsigned>(theGameControllerData.state));
        BoosterMatchSafety::requestJoystickAfterGetUp();
        goto active;
      }
      if(!combinedGetUpGate.pending() && matchRecoveryPermitted
         && (!BoosterMatchSafety::enabled() || combinedInput.rawInitial)
         && theEnhancedKeyStates.pressedDuration[KeyStates::walk] > pressDuration
         && theMotionInfo.executedPhase == MotionPhase::prepare
         && std::abs(theInertialData.angle.x()) < maxTorsoForWalk
         && std::abs(theInertialData.angle.y()) < maxTorsoForWalk
         && std::abs(theInertialData.gyro.x()) < maxGyroForWalk
         && std::abs(theInertialData.gyro.y()) < maxGyroForWalk
         && std::abs(theInertialData.gyro.z()) < maxGyroForWalk)
      {
        if(BoosterMatchSafety::enabled() && !combinedInput.rawInitial)
          BoosterRemoteStand::beginBodyRecovery();
        goto active;
      }

      if(matchRecoveryPermitted
         && ((theEnhancedKeyStates.pressedDuration[KeyStates::stand] > pressDuration
           && theEnhancedKeyStates.hitStreak[KeyStates::walk] == 3) || liveGetUpRequested || combinedGetUp.requestGetUp)
         && theMotionInfo.executedPhase == MotionPhase::prepare
         && (std::abs(theInertialData.angle.x()) > minFallAngle || std::abs(theInertialData.angle.y()) > minFallAngle)
         && std::abs(theInertialData.gyro.x()) < maxGyroForFall
         && std::abs(theInertialData.gyro.y()) < maxGyroForFall
         && std::abs(theInertialData.gyro.z()) < maxGyroForFall)
      {
        if(liveGetUpRequested)
        {
          std::printf("LIVE_GETUP_ACCEPTED fresh_RB_A_hold_ms=%u gc_age_ms=%u raw_gc=%u stable=1\n",
                      theFrameInfo.time - liveGetUpStartedAt, liveInput.gcNow - liveInput.gcPacketTime,
                      static_cast<unsigned>(theGameControllerData.state));
          BoosterMatchSafety::requestJoystickAfterGetUp();
        }
        if(combinedGetUp.requestGetUp)
        {
          std::printf("LIVE_COMBINED_GETUP_ACCEPTED actual_Prepare_ms>=2000 raw_gc=%u stable=1\n",
                      static_cast<unsigned>(theGameControllerData.state));
          BoosterMatchSafety::requestJoystickAfterGetUp();
        }
        if(BoosterMatchSafety::enabled() && !combinedInput.rawInitial
           && !BoosterRemoteStand::bodyRecoveryPending())
          BoosterRemoteStand::beginBodyRecovery();
        goto fall;
      }
    }
    action
    {
      LookAtAngles({.pan = JointAngles::off,
                    .tilt = JointAngles::off});

      // Conditions are not met -> let LED blink fast
      if(theKeyStates.pressed[KeyStates::walk]
         && theMotionInfo.executedPhase == MotionPhase::prepare
         && (std::abs(theInertialData.angle.x()) > maxTorsoForWalk
             || std::abs(theInertialData.angle.y()) > maxTorsoForWalk
             || std::abs(theInertialData.gyro.x()) > maxGyroForWalk
             || std::abs(theInertialData.gyro.y()) > maxGyroForWalk
             || std::abs(theInertialData.gyro.z()) > maxGyroForWalk))
        BehaviorStateTransitionFailed();

      Prepare();
    }
  }

  state(fall)
  {
    transition
    {
      if(theMotionInfo.executedPhase == MotionPhase::fall)
        goto active;
    }
    action
    {
      LookAtAngles({.pan = JointAngles::off,
                    .tilt = JointAngles::off});
      FallRequest();
    }
  }
}
