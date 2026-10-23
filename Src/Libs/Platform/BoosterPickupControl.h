/** Physical STAND withdrawal and explicit WALK re-entry. No SDK or motor commands. */
#pragma once

#include <atomic>
#include <cstdint>
#include <iterator>

namespace BoosterPickupControl
{
  template<typename Packet>
  bool ownPlayerPresent(const Packet& packet, unsigned teamNumber, unsigned playerNumber) noexcept
  {
    if(!playerNumber || playerNumber > packet.playersPerTeam) return false;
    unsigned matches = 0;
    bool present = false;
    for(const auto& team : packet.teams)
      if(team.teamNumber == teamNumber)
      {++matches; present = playerNumber <= std::size(team.players);}
    return matches == 1 && present;
  }

  enum class Reason
  {
    inactive, prepareLocked, holdingWalk, unarmedWalk, standLocked, standConfirm,
    waitingGc, readyAutomatic, cancelledContext, cancelledIdentity, cancelledEpoch,
    cancelledRemote, cancelledFault, cancelledClock, cancelledObservation, nativeRecovery,
  };
  inline const char* reasonName(Reason reason) noexcept
  {
    switch(reason)
    {
#define PICKUP_REASON(name) case Reason::name: return #name;
      PICKUP_REASON(inactive) PICKUP_REASON(prepareLocked) PICKUP_REASON(holdingWalk)
      PICKUP_REASON(unarmedWalk) PICKUP_REASON(standLocked) PICKUP_REASON(standConfirm)
      PICKUP_REASON(waitingGc) PICKUP_REASON(readyAutomatic) PICKUP_REASON(cancelledContext)
      PICKUP_REASON(cancelledIdentity) PICKUP_REASON(cancelledEpoch) PICKUP_REASON(cancelledRemote)
      PICKUP_REASON(cancelledFault) PICKUP_REASON(cancelledClock) PICKUP_REASON(cancelledObservation)
      PICKUP_REASON(nativeRecovery)
#undef PICKUP_REASON
    }
    return "unknown";
  }
  struct Buttons
  {
    std::uint64_t value = 0;
    bool standDown() const noexcept {return value & 1u;}
    bool walkDown() const noexcept {return value & 2u;}
    unsigned standPress() const noexcept {return static_cast<unsigned>((value >> 2) & 0x7fffffffu);}
    unsigned walkPress() const noexcept {return static_cast<unsigned>(value >> 33);}
  };
  namespace detail
  {
    struct Storage
    {
      std::atomic<std::uint64_t> buttons{0};
      std::atomic<std::uint64_t> releaseEpoch{0};
      std::atomic<bool> holding{false}, releaseAutomatic{false}, remoteNeutralRequired{false};
    };
    inline Storage& storage() noexcept {static Storage value; return value;}
  }
  /** Only the actual body ButtonEvent callback calls this, never remote keys. */
  inline void observePhysicalButton(unsigned button, bool down) noexcept
  {
    if(button != 1 && button != 2) return;
    auto& value = detail::storage().buttons;
    auto previous = value.load();
    const unsigned shift = button == 1 ? 2u : 33u;
    const std::uint64_t bit = button == 1 ? 1u : 2u;
    for(;;)
    {
      if(((previous & bit) != 0) == down) return;
      auto next = down ? previous | bit : previous & ~bit;
      if(down)
      {
        constexpr std::uint64_t counterMask = 0x7fffffffu;
        const auto count = ((previous >> shift) + 1u) & counterMask;
        next = (next & ~(counterMask << shift)) | (count << shift);
      }
      if(value.compare_exchange_weak(previous, next)) return;
    }
  }
  inline Buttons physicalButtons() noexcept {return {detail::storage().buttons.load()};}
  inline bool held() noexcept {return detail::storage().holding.load();}
  inline void setHeld(bool hold) noexcept {detail::storage().holding.store(hold);}
  inline void cancelAutomaticRelease() noexcept {detail::storage().releaseAutomatic.store(false);}
  inline void offerAutomaticRelease(std::uint64_t epoch = 0) noexcept
  {detail::storage().releaseEpoch.store(epoch); detail::storage().releaseAutomatic.store(true);}
  inline std::uint64_t automaticReleaseEpoch() noexcept {return detail::storage().releaseEpoch.load();}
  inline bool consumeAutomaticRelease() noexcept {return detail::storage().releaseAutomatic.exchange(false);}
  inline void requireFreshRemoteNeutral() noexcept {detail::storage().remoteNeutralRequired.store(true);}
  inline bool manualTakeoverAllowed() noexcept {return !detail::storage().remoteNeutralRequired.load();}
  inline void observeRemoteSample(unsigned buttons, bool neutralAxes) noexcept
  {if(buttons == 0 && neutralAxes) detail::storage().remoteNeutralRequired.store(false);}

  struct Input
  {
    bool enabled = false, authorized = false, emergency = true, imuFinite = false;
    bool standOrWalk = false, executedPrepare = false, actualStand = false, uprightStable = false;
    bool gcLegal = false, gcArmContext = false, gcPacketFresh = false, gcTargetPresent = false;
    bool gcSessionEnded = false, gcTimestampInvalid = false;
    unsigned frame = 0, gcPacketTime = 0;
    std::uint64_t epoch = 0, remoteGeneration = 0;
    Buttons buttons;
  };
  struct Result
  {
    bool entered = false, prepare = false, stand = false, automaticReady = false, suspended = false;
    bool requested = false, pending = false, waitingGc = false, cancelled = false;
  };
  class Gate
  {
  public:
    bool active() const noexcept {return stage != Stage::inactive;}
    bool pending() const noexcept {return resumeRequested && intentLegal;}
    Reason reason() const noexcept {return lastReason;}
    void finishAutomatic() noexcept {stage = Stage::inactive; clearWalk();}
    void yieldToLyingRecovery() noexcept {stage = Stage::inactive; suspend();}
    /** A skipped behavior frame cannot retain a partially-held re-entry gesture. */
    void suspend() noexcept
    {clearWalk(); prepareAt = uprightAt = standAt = 0; synchronized = false; lastReason = Reason::cancelledObservation;}
    Result update(const Input& in) noexcept
    {
      Result out;
      const bool hadIntent = intentLegal && (walkHolding || resumeRequested);
      if(!in.enabled)
      {
        stage = Stage::inactive; clearWalk(); prepareAt = uprightAt = standAt = 0;
        synchronized = false;
        lastReason = Reason::inactive;
        return finish(out, in, hadIntent);
      }
      // GC ordering can revoke re-entry, but must never block physical STAND.
      // Adopt a valid new baseline after revocation rather than poison all
      // subsequent genuine WALK attempts with an unreachable old high-water.
      const bool gcPacketReversed = !in.gcTimestampInvalid && in.gcPacketTime
          && lastGcPacketTime && in.gcPacketTime < lastGcPacketTime;
      if(!in.gcTimestampInvalid && in.gcPacketTime) lastGcPacketTime = in.gcPacketTime;
      if(!in.authorized || in.emergency || !in.imuFinite)
      {
        // A hard body/input protection revokes re-entry, never the human lock.
        suspend(); out.suspended = active();
        lastReason = Reason::cancelledFault;
        return finish(out, in, hadIntent);
      }
      // Check unsafe native phases BEFORE synchronization/timing early exits.
      // Otherwise a suspended Prepare could reappear on the next GetUp frame.
      if(active() && !in.standOrWalk && !in.executedPrepare)
      {
        stage = Stage::stand;
        suspend(); out.suspended = true;
        lastReason = Reason::nativeRecovery;
        return finish(out, in, hadIntent);
      }
      if(!synchronized)
      {
        // A button held before fresh F1 authorization is not a new request.
        previous = in.buttons; lastFrame = in.frame; synchronized = true;
        out.prepare = stage == Stage::prepare;
        out.stand = stage == Stage::stand;
        return finish(out, in, hadIntent);
      }
      const bool freshStand = in.buttons.standPress() != previous.standPress();
      const bool freshWalk = in.buttons.walkPress() != previous.walkPress();
      previous = in.buttons;
      if(in.frame <= lastFrame || in.frame - lastFrame > 250)
      {
        suspend(); lastFrame = in.frame;
        out.prepare = stage == Stage::prepare;
        out.stand = stage == Stage::stand;
        lastReason = Reason::cancelledClock;
        return finish(out, in, hadIntent);
      }
      lastFrame = in.frame;
      if(freshStand && (in.standOrWalk || (active() && in.executedPrepare)))
      {
        stage = Stage::prepare; clearWalk(); prepareAt = uprightAt = standAt = 0;
        out.entered = true;
        lastReason = Reason::prepareLocked;
      }
      if(!active()) return finish(out, in, hadIntent);
      // Do not force a new Prepare phase over a genuine Fall/GetUp/PlayDead.
      if(!in.standOrWalk && !in.executedPrepare)
      {
        suspend(); out.suspended = true;
        lastReason = Reason::nativeRecovery;
        return finish(out, in, hadIntent);
      }
      if(in.executedPrepare)
      {if(!prepareAt) prepareAt = in.frame;}
      else prepareAt = 0;
      if(in.uprightStable)
      {if(!uprightAt) uprightAt = in.frame;}
      else uprightAt = 0;
      if(in.actualStand && in.uprightStable)
      {if(!standAt) standAt = in.frame;}
      else standAt = 0;

      // Snapshot counters cannot order two edges between behavior frames.
      // Any fresh STAND wins; that WALK must be released and pressed again.
      if(!freshStand && freshWalk && in.buttons.walkDown() && !in.buttons.standDown())
      {
        clearWalk();
        walkHolding = true; walkAt = in.frame;
        // A fresh allowed match context may authorize WAITING for this player's
        // penalty to end. It never authorizes football while still penalized.
        intentLegal = in.gcArmContext; intentEpoch = in.epoch; intentRemoteGeneration = in.remoteGeneration;
        lastReason = intentLegal ? Reason::holdingWalk : Reason::unarmedWalk;
      }
      if(walkHolding && (!in.buttons.walkDown() || in.buttons.standDown()))
        clearWalk();
      if(intentLegal && in.epoch != intentEpoch)
      {intentLegal = false; lastReason = Reason::cancelledEpoch;}
      if(intentLegal && in.remoteGeneration != intentRemoteGeneration)
      {intentLegal = false; lastReason = Reason::cancelledRemote;}
      if(intentLegal && in.gcPacketFresh && !in.gcTargetPresent)
      {intentLegal = false; lastReason = Reason::cancelledIdentity;}
      if(intentLegal && in.gcSessionEnded)
      {intentLegal = false; lastReason = Reason::cancelledContext;}
      if(intentLegal && (in.gcTimestampInvalid || gcPacketReversed))
      {intentLegal = false; lastReason = Reason::cancelledClock;}
      // Before the deliberate long press completes, STOP/SET/old packets revoke
      // admission. After completion they only pause the explicit waiting ticket.
      if(intentLegal && walkHolding && !in.gcArmContext)
      {intentLegal = false; lastReason = Reason::cancelledContext;}
      if(walkHolding && in.frame - walkAt > 2000 && uprightAt && in.frame - uprightAt >= 300
         && ((in.executedPrepare && prepareAt && in.frame - prepareAt >= 2000)
             || (stage == Stage::stand && in.actualStand)))
      {
        stage = Stage::stand; walkHolding = false;
        resumeRequested = true;
        out.requested = intentLegal;
        if(!intentLegal && lastReason == Reason::unarmedWalk) lastReason = Reason::standLocked;
        // Confirmation is always from real subsequent Stand feedback, not Prepare time.
        standAt = in.actualStand ? in.frame : 0;
      }
      out.prepare = stage == Stage::prepare;
      out.stand = stage == Stage::stand;
      out.automaticReady = out.stand && resumeRequested && intentLegal && in.gcLegal
                           && in.epoch == intentEpoch && in.actualStand && in.uprightStable
                           && standAt && in.frame - standAt >= 300;
      return finish(out, in, hadIntent);
    }
  private:
    enum class Stage {inactive, prepare, stand};
    Result finish(Result out, const Input& in, bool hadIntent) noexcept
    {
      out.pending = pending();
      out.waitingGc = out.pending && !in.gcLegal;
      out.cancelled = hadIntent && !(intentLegal && (walkHolding || resumeRequested));
      if(out.automaticReady) lastReason = Reason::readyAutomatic;
      else if(out.waitingGc) lastReason = Reason::waitingGc;
      else if(out.pending) lastReason = Reason::standConfirm;
      return out;
    }
    void clearWalk() noexcept {walkHolding = resumeRequested = intentLegal = false; walkAt = 0;}
    Stage stage = Stage::inactive;
    Buttons previous;
    bool synchronized = false, walkHolding = false, resumeRequested = false, intentLegal = false;
    unsigned lastFrame = 0, prepareAt = 0, uprightAt = 0, standAt = 0, walkAt = 0;
    unsigned lastGcPacketTime = 0;
    std::uint64_t intentEpoch = 0, intentRemoteGeneration = 0;
    Reason lastReason = Reason::inactive;
  };
}
