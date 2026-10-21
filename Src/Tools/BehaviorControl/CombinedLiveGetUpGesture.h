#pragma once

#include <atomic>
#include <cmath>
#include <cstdint>
#include <iterator>

/** SDK-free one-shot Prepare/GetUp admission. Never synthesizes keys or poses. */
namespace CombinedLiveGetUpGesture
{
  template<typename Packet>
  bool ownPlayerUnpenalized(const Packet& packet, unsigned teamNumber, unsigned playerNumber) noexcept
  {
    if(!playerNumber || playerNumber > packet.playersPerTeam) return false;
    unsigned matches = 0;
    bool unpenalized = false;
    for(const auto& team : packet.teams)
      if(team.teamNumber == teamNumber)
      {
        ++matches;
        unpenalized = playerNumber <= std::size(team.players) && team.players[playerNumber - 1].penalty == 0;
      }
    return matches == 1 && unpenalized;
  }

  inline bool freshInitial(bool initial, bool stopped, bool unpenalized, unsigned now, unsigned packet) noexcept
  {return initial && !stopped && unpenalized && packet > 0 && now >= packet && now - packet <= 2000;}

  /** Raw GC states: INITIAL=0, READY=1, PLAYING=3. SET and FINISHED stay blocked. */
  inline bool recoveryStateAllowed(unsigned raw) noexcept
  {return raw == 0 || raw == 1 || raw == 3;}

  inline bool freshRecovery(unsigned raw, bool stopped, bool unpenalized, unsigned now, unsigned packet) noexcept
  {return freshInitial(recoveryStateAllowed(raw), stopped, unpenalized, now, packet);}

  struct Input
  {
    bool enabled = false, authorized = false, emergency = true, joystickValid = false;
    bool sticksNeutral = false, neutralButtons = false, rbAOnly = false, rbOnly = false, aOnly = false;
    bool rawInitial = false, rawRecoveryAllowed = false, stopped = true, unpenalized = false, imuFinite = false;
    bool canPrepare = false, executedPrepare = false, lyingStable = false, uprightStable = false;
    unsigned frameTime = 0, gcNow = 0, gcPacketTime = 0;
    std::uint64_t epoch = 0;
  };
  struct Result
  {
    bool requestPrepare = false, requestGetUp = false, requestActive = false, cancelled = false;
    bool refreshPrepareAdmission = false;
  };

  enum class Reason
  {
    waitingNeutral, waitingChord, waitingA, holdingChord, prepareWaiting, prepareInterpolating,
    stabilityWaiting, acceptedPrepare, acceptedGetUp, acceptedUpright,
    disabled, unauthorized, emergency, invalidController, sticksNotNeutral, invalidImu,
    rawNotInitial, gcStopped, rawPlayerUnavailable, staleGc, awaitingFreshGc, epochChanged, frameNotAdvancing,
    wrongKeys, brokenChord, modifierExpired, pendingTimeout, wrongMotionPhase, unstableStartingPose, recoveryPoseRequired,
  };
  inline const char* reasonName(Reason reason) noexcept
  {
    switch(reason)
    {
#define COMBINED_REASON(name) case Reason::name: return #name;
      COMBINED_REASON(waitingNeutral) COMBINED_REASON(waitingChord) COMBINED_REASON(waitingA)
      COMBINED_REASON(holdingChord) COMBINED_REASON(prepareWaiting) COMBINED_REASON(prepareInterpolating)
      COMBINED_REASON(stabilityWaiting) COMBINED_REASON(acceptedPrepare) COMBINED_REASON(acceptedGetUp)
      COMBINED_REASON(acceptedUpright) COMBINED_REASON(disabled) COMBINED_REASON(unauthorized)
      COMBINED_REASON(emergency) COMBINED_REASON(invalidController) COMBINED_REASON(sticksNotNeutral)
      COMBINED_REASON(invalidImu) COMBINED_REASON(rawNotInitial) COMBINED_REASON(gcStopped)
      COMBINED_REASON(rawPlayerUnavailable) COMBINED_REASON(staleGc) COMBINED_REASON(epochChanged)
      COMBINED_REASON(awaitingFreshGc)
      COMBINED_REASON(frameNotAdvancing) COMBINED_REASON(wrongKeys) COMBINED_REASON(brokenChord)
      COMBINED_REASON(modifierExpired) COMBINED_REASON(pendingTimeout) COMBINED_REASON(wrongMotionPhase)
      COMBINED_REASON(unstableStartingPose) COMBINED_REASON(recoveryPoseRequired)
#undef COMBINED_REASON
    }
    return "unknown";
  }

  class Gate
  {
  public:
    bool pending() const noexcept {return preparing;}
    bool released() const noexcept {return releaseSeen;}
    bool chordHolding() const noexcept {return holding;}
    unsigned holdMs(unsigned now) const noexcept {return holding && now >= holdStarted ? now - holdStarted : 0;}
    unsigned modifierMs(unsigned now) const noexcept {return modifierPending && now >= modifierStarted ? now - modifierStarted : 0;}
    unsigned pendingMs(unsigned now) const noexcept {return preparing && now >= pendingStarted ? now - pendingStarted : 0;}
    Reason reason() const noexcept {return lastReason;}
    Result update(const Input& input) noexcept
    {
      Result result;
      const bool newAuthorization = input.authorized && !lastAuthorized;
      lastAuthorized = input.authorized;
      const Reason eligibility = baseReason(input);
      // An already accepted one-shot may wait through a packet gap. This does
      // not authorize a new Prepare/GetUp: every request still needs a fresh
      // allowed GC packet. Zero/future timestamps are invalid, not ordinary gaps.
      const bool waitingForGc = preparing && eligibility == Reason::staleGc &&
                                input.gcPacketTime > 0 && input.gcNow >= input.gcPacketTime;
      if((eligibility != Reason::waitingNeutral && !waitingForGc) || newAuthorization || input.epoch != lastEpoch || input.frameTime <= lastFrame)
      {
        result.cancelled = preparing;
        lastReason = eligibility != Reason::waitingNeutral ? eligibility
                     : input.epoch != lastEpoch ? Reason::epochChanged
                     : newAuthorization ? Reason::waitingNeutral : Reason::frameNotAdvancing;
        clearEntry(); preparing = false; pendingStarted = prepareStarted = stableStarted = 0;
        if(input.epoch != lastEpoch || newAuthorization) prefixBlocked = false;
        else if(result.cancelled) prefixBlocked = true;
        lastEpoch = input.epoch; lastFrame = input.frameTime;
        return result;
      }
      lastEpoch = input.epoch; lastFrame = input.frameTime;
      if(preparing)
      {
        // One accepted intent can bridge the module ordering and interpolation,
        // but cannot survive a GC/authorization epoch, clock, or eight seconds
        // from the original acceptance. Packet recovery never resets this bound.
        if(input.frameTime - pendingStarted > 8000 || !input.canPrepare
           || (!input.neutralButtons && !input.rbAOnly && !input.rbOnly && !input.aOnly))
        {
          lastReason = input.frameTime - pendingStarted > 8000 ? Reason::pendingTimeout
                       : !input.canPrepare ? Reason::wrongMotionPhase : Reason::wrongKeys;
          preparing = false; clearEntry(); prefixBlocked = true;
          result.cancelled = true;
          return result;
        }
        if(!input.executedPrepare)
        {
          prepareStarted = stableStarted = 0;
          // Refresh only the same accepted one-shot, not another key gesture.
          // A token's unchanged 150ms TTL must fit inside the original 8s limit.
          result.refreshPrepareAdmission = !waitingForGc && input.frameTime - pendingStarted <= 7850 &&
                                            (input.lyingStable || (input.rawInitial && input.uprightStable));
          lastReason = waitingForGc ? Reason::awaitingFreshGc : Reason::prepareWaiting;
          return result;
        }
        if(!prepareStarted) prepareStarted = input.frameTime;
        const bool upright = input.uprightStable && !input.lyingStable;
        if(!input.lyingStable && !input.uprightStable) stableStarted = 0;
        else if(!stableStarted || stableIsUpright != upright)
        {stableStarted = input.frameTime; stableIsUpright = upright;}
        // Observe the real Prepare and IMU even while waiting; never infer
        // interpolation/stability from a missing packet.
        if(waitingForGc)
        {lastReason = Reason::awaitingFreshGc; return result;}
        // Preserve PrepareEngine's full 2000ms interpolation before GetUp.
        if(input.frameTime - prepareStarted >= 2000 && stableStarted
           && input.frameTime - stableStarted >= 300
           && (input.lyingStable || (input.rawInitial && input.uprightStable)))
        {
          preparing = false; clearEntry(); prefixBlocked = true;
          result.requestGetUp = input.lyingStable;
          result.requestActive = input.rawInitial && input.uprightStable && !input.lyingStable;
          lastReason = result.requestGetUp ? Reason::acceptedGetUp : Reason::acceptedUpright;
        }
        else lastReason = input.frameTime - prepareStarted < 2000 ? Reason::prepareInterpolating
                          : !input.rawInitial && input.uprightStable ? Reason::recoveryPoseRequired : Reason::stabilityWaiting;
        return result;
      }
      // During a match RB+A only recovers an actually lying robot. It must not
      // become another way to arm standing/walking from READY or PLAYING.
      if(!input.rawInitial && !input.lyingStable)
      {
        clearEntry(); prefixBlocked = true; lastReason = Reason::recoveryPoseRequired;
        return result;
      }
      if(input.neutralButtons)
      {
        releaseSeen = input.canPrepare;
        holding = modifierPending = prefixBlocked = false; holdStarted = modifierStarted = 0;
        lastReason = releaseSeen ? Reason::waitingChord : Reason::wrongMotionPhase;
        return result;
      }
      // Real handsets publish RB and A as separate events. Eligible EXACT RB
      // also proves A-up/other-keys-up if the event-driven SDK never published
      // idle buttons=0 after startup. It arms ONE five-second prefix, never a
      // body request. Expiry/wrong keys lock it until neutral or a new epoch.
      if(input.rbOnly && !prefixBlocked && !holding && input.canPrepare
         && (input.lyingStable || input.uprightStable))
      {
        releaseSeen = true;
        if(!modifierPending) {modifierPending = true; modifierStarted = input.frameTime;}
        if(input.frameTime - modifierStarted > 5000)
        {clearEntry(); prefixBlocked = true; lastReason = Reason::modifierExpired;}
        else lastReason = Reason::waitingA;
        return result;
      }
      if(!releaseSeen || !input.rbAOnly || !input.canPrepare || (!input.lyingStable && !input.uprightStable))
      {
        lastReason = !releaseSeen ? Reason::waitingNeutral : !input.canPrepare ? Reason::wrongMotionPhase
                     : (!input.lyingStable && !input.uprightStable) ? Reason::unstableStartingPose
                     : holding ? Reason::brokenChord : Reason::wrongKeys;
        clearEntry(); prefixBlocked = true;
        return result;
      }
      if(!holding)
      {
        if(modifierPending && input.frameTime - modifierStarted > 5000)
        {clearEntry(); prefixBlocked = true; lastReason = Reason::modifierExpired; return result;}
        holding = true; holdStarted = input.frameTime; modifierPending = false; modifierStarted = 0;
        lastReason = Reason::holdingChord;
        return result;
      }
      lastReason = Reason::holdingChord;
      if(input.frameTime - holdStarted >= 2000)
      {
        preparing = true; clearEntry(); prefixBlocked = true; pendingStarted = input.frameTime;
        prepareStarted = stableStarted = 0;
        result.requestPrepare = true;
        lastReason = Reason::acceptedPrepare;
      }
      return result;
    }
  private:
    static Reason baseReason(const Input& input) noexcept
    {
      if(!input.enabled) return Reason::disabled;
      if(!input.authorized) return Reason::unauthorized;
      if(input.emergency) return Reason::emergency;
      if(!input.joystickValid) return Reason::invalidController;
      if(!input.sticksNeutral) return Reason::sticksNotNeutral;
      if(!input.imuFinite) return Reason::invalidImu;
      if(!input.rawInitial && !input.rawRecoveryAllowed) return Reason::rawNotInitial;
      if(input.stopped) return Reason::gcStopped;
      if(!input.unpenalized) return Reason::rawPlayerUnavailable;
      if(!freshInitial(input.rawInitial || input.rawRecoveryAllowed, input.stopped, input.unpenalized, input.gcNow, input.gcPacketTime))
        return Reason::staleGc;
      return Reason::waitingNeutral;
    }
    void clearEntry() noexcept
    {releaseSeen = holding = modifierPending = false; holdStarted = modifierStarted = 0;}
    std::uint64_t lastEpoch = 0;
    unsigned lastFrame = 0, holdStarted = 0, pendingStarted = 0, prepareStarted = 0, stableStarted = 0, modifierStarted = 0;
    bool releaseSeen = false, holding = false, preparing = false, stableIsUpright = false, modifierPending = false;
    bool prefixBlocked = false, lastAuthorized = false;
    Reason lastReason = Reason::waitingNeutral;
  };

  // Physical runs after GameStateProvider in Cognition. This single-use intent
  // lets the NEXT provider frame admit Prepare without faking the stand key.
  namespace detail
  {
    struct Admission
    {
      std::atomic<bool> pending{false};
      std::atomic<unsigned> frame{0};
      std::atomic<std::uint64_t> epoch{0};
    };
    inline Admission& admissionStorage() {static Admission value; return value;}
  }
  inline void offerPrepare(unsigned frame, std::uint64_t epoch) noexcept
  {
    auto& value = detail::admissionStorage(); value.frame.store(frame); value.epoch.store(epoch);
    value.pending.store(true, std::memory_order_release);
  }
  inline void revokePrepare() noexcept {detail::admissionStorage().pending.store(false);}
  inline bool consumePrepare(unsigned frame, std::uint64_t epoch, bool permitted) noexcept
  {
    auto& value = detail::admissionStorage();
    if(!value.pending.exchange(false, std::memory_order_acquire)) return false;
    const unsigned offered = value.frame.load();
    return permitted && value.epoch.load() == epoch && frame >= offered && frame - offered <= 150;
  }
}
