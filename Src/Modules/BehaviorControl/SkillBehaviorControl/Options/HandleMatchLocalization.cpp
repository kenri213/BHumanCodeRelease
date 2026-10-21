/** Explicit opt-in match entry: image evidence AND human direction confirmation. */
#include "SkillBehaviorControl.h"
#include "Platform/BoosterMatchSafety.h"
#include "Platform/BoosterOfficialLiveTest.h"
#include "Platform/Time.h"
#include <cstdio>

option((SkillBehaviorControl) HandleMatchLocalization,
       vars((BoosterMatchSafety::ConfirmationGesture) confirmationGesture,
            (std::uint64_t)(0) observedEpoch,
            (unsigned)(0) scanStartedAt,
            (bool)(false) scanReachedLeft,
            (bool)(false) scanFinished,
            (unsigned)(0) lastDiagnosticAt,
            (bool)(false) candidateWasReady))
{
  const auto currentEpoch = BoosterMatchSafety::epoch();
  if(observedEpoch != currentEpoch)
  {
    observedEpoch = currentEpoch;
    scanStartedAt = 0;
    scanReachedLeft = scanFinished = candidateWasReady = false;
  }
  const bool match = BoosterMatchSafety::enabled();
  const bool physicalBusy = theSystemSensorData.emergencyMode || theGameState.playerState == GameState::unstiff
                            || (theMotionInfo.executedPhase != MotionPhase::stand && theMotionInfo.executedPhase != MotionPhase::walk)
                            || (theFallDownState.state != FallDownState::upright && theFallDownState.state != FallDownState::staggering);
  const bool needsGate = match && !BoosterMatchSafety::confirmed() && !physicalBusy
                         && theGameState.playerState == GameState::active && !theGameState.isInitial()
                         && (theGameState.isReady() || theGameState.isSet() || theGameState.isPlaying());
  const bool stableStand = theFallDownState.state == FallDownState::upright && theMotionInfo.executedPhase == MotionPhase::stand
                           && std::abs(theInertialData.angle.x()) < 15_deg && std::abs(theInertialData.angle.y()) < 15_deg
                           && std::abs(theInertialData.gyro.x()) < 5_deg && std::abs(theInertialData.gyro.y()) < 5_deg
                           && std::abs(theInertialData.gyro.z()) < 5_deg;
  const bool finiteCovariance = theRobotPose.covariance.allFinite() && theRobotPose.covariance(0, 0) >= 0.f
                                && theRobotPose.covariance(1, 1) >= 0.f && theRobotPose.covariance(2, 2) >= 0.f;
  const auto visual = BoosterMatchSafety::visualEvidence();
  const BoosterMatchSafety::EligibilityInput candidate{
    .gcActive = theGameState.gameControllerActive,
    .readyOrSet = theGameState.isReady() || theGameState.isSet(),
    .rawReadyOrSet = theGameControllerData.state == STATE_READY || theGameControllerData.state == STATE_SET,
    .stopped = theGameState.stopped || theGameControllerData.stopped,
    .penalized = theGameState.playerState != GameState::active,
    .authorized = BoosterOfficialLiveTest::bodyAuthorized(), .stableStand = stableStand,
    .poseQuality = theRobotPose.quality == RobotPose::superb || theRobotPose.quality == RobotPose::okay,
    .poseFinite = finiteCovariance && std::isfinite(theRobotPose.translation.x()) && std::isfinite(theRobotPose.translation.y())
                  && std::isfinite(static_cast<float>(theRobotPose.rotation)),
    .now = Time::getCurrentSystemTime(), .packetTime = theGameControllerData.timeLastPacketReceived,
    .frameTime = theFrameInfo.time,
    .positionStdMm = finiteCovariance ? theRobotPose.getTranslationalStandardDeviation() : -1.f,
    .headingStdRad = finiteCovariance ? std::sqrt(theRobotPose.covariance(2, 2)) : -1.f};
  const bool candidateReady = needsGate && BoosterMatchSafety::eligible(candidate, visual, currentEpoch);
  const bool neutralSticks = std::all_of(theJoystickState.axes.begin(), theJoystickState.axes.end(),
      [](float value) {return std::isfinite(value) && std::abs(value) <= 0.12f;});
  const bool inputNeutral = theJoystickState.valid && neutralSticks && theJoystickState.buttons == 0;
  const unsigned rbYMask = (1u << 5) | (1u << 3);
  const bool rbYOnly = theJoystickState.valid && neutralSticks && theJoystickState.buttons == rbYMask;
  const bool confirmRequested = confirmationGesture.update(currentEpoch, theFrameInfo.time,
                                                            candidateReady, inputNeutral, rbYOnly);

  common_transition
  {
    if(!needsGate)
      goto passThrough;
    if(confirmRequested && BoosterMatchSafety::confirm(currentEpoch))
    {
      std::printf("MATCH_LOCALIZATION_CONFIRMED epoch=%llu pose_mm_rad=%.1f,%.1f,%.5f visual_frames=%u AUTO_READY_RELEASED=1\n",
                  static_cast<unsigned long long>(currentEpoch), static_cast<double>(theRobotPose.translation.x()),
                  static_cast<double>(theRobotPose.translation.y()), static_cast<double>(theRobotPose.rotation), visual.consecutiveFrames);
      Say({.text = "Localization confirmed. Automatic ready movement enabled."});
      goto passThrough;
    }
    goto waitingForConfirmation;
  }

  initial_state(passThrough)
  {}

  state(waitingForConfirmation)
  {
    action
    {
      Stand();
      // One gentle native sweep per entry epoch. Expiry never grants walking.
      if(!scanStartedAt) scanStartedAt = theFrameInfo.time;
      if(!theHeadMotionInfo.moving && std::abs(theJointAngles.angles[Joints::headYaw] - 45_deg) < 5_deg)
        scanReachedLeft = true;
      if((scanReachedLeft && !theHeadMotionInfo.moving && std::abs(theJointAngles.angles[Joints::headYaw] + 45_deg) < 5_deg)
         || theFrameInfo.getTimeSince(scanStartedAt) >= 8000)
        scanFinished = true;
      if(scanFinished)
        LookAtAngles({.pan = 0_deg, .tilt = 20_deg, .speed = 30_deg});
      else
        LookLeftAndRight({.startLeft = true, .maxPan = 45_deg, .tilt = 20_deg, .speed = 30_deg});

      if(candidateReady && !candidateWasReady)
        Say({.text = "Check my position and direction. Release buttons, then hold R B and Y to enable automatic ready movement."});
      candidateWasReady = candidateReady;
      if(!lastDiagnosticAt || theFrameInfo.getTimeSince(lastDiagnosticAt) >= 2000)
      {
        lastDiagnosticAt = theFrameInfo.time;
        const char* reason = !candidate.gcActive ? "GC_INACTIVE" : !candidate.readyOrSet || !candidate.rawReadyOrSet ? "NEED_READY_OR_SET" :
          candidate.stopped ? "STOPPED" : candidate.penalized ? "PENALIZED" :
          !candidate.authorized ? "NEED_FRESH_PHYSICAL_F1" : !stableStand ? "NEED_STABLE_STAND" :
          visual.epoch != currentEpoch || visual.consecutiveFrames < 6 ? "NEED_REAL_VISUAL_REGISTRATIONS" :
          !candidate.poseQuality || !candidate.poseFinite || candidate.positionStdMm > 500.f || candidate.headingStdRad > 15_deg ? "POSE_UNCERTAIN" :
          !candidateReady ? "STALE_EVIDENCE" : "VERIFY_POSE_DIRECTION_THEN_FRESH_RB_Y_2S";
        std::printf("MATCH_LOCALIZATION_WAIT epoch=%llu reason=%s gc_age_ms=%u visual_frames=%u visual_age_ms=%u registrations=%u,%u,%u pose_mm_rad=%.1f,%.1f,%.5f std_mm_rad=%.1f,%.5f scan_done=%d AUTO_MOVE=0\n",
                    static_cast<unsigned long long>(currentEpoch), reason,
                    candidate.now >= candidate.packetTime ? candidate.now - candidate.packetTime : ~0u,
                    visual.consecutiveFrames, theFrameInfo.time >= visual.frameTime ? theFrameInfo.time - visual.frameTime : ~0u,
                    visual.poses, visual.landmarks, visual.lines, static_cast<double>(theRobotPose.translation.x()),
                    static_cast<double>(theRobotPose.translation.y()), static_cast<double>(theRobotPose.rotation),
                    static_cast<double>(candidate.positionStdMm), static_cast<double>(candidate.headingStdRad), scanFinished);
      }
    }
  }
}
