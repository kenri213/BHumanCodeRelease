/** Explicit Live-test stop and fresh-physical-F1 authorization overlay. */
#pragma once

#include <atomic>
#pragma push_macro("state")
#undef state
#include <chrono>
#pragma pop_macro("state")
#include <cmath>
#include <cstdint>
#include "BoosterRemoteStand.h"

namespace BoosterOfficialLiveTest
{
  namespace detail
  {
    constexpr unsigned f1Pending = 1u, f1Armed = 2u, f1Stopped = 4u;
    struct LiveState
    {
      std::atomic<bool> active{false}, stopped{false}, officialButtons{false}, startupConnection{false};
      std::atomic<unsigned> f1Authority{0}, f1CognitionFrame{0};
      std::atomic<std::uint64_t> f1CognitionFrames{0};
      std::atomic<std::int64_t> f1DownNs{0}, f1CognitionNs{0};
      std::atomic<std::uint64_t> frames{0};
      std::atomic<unsigned> phase{0};
      std::atomic<float> peakKp{0.f};
      std::atomic<std::uint64_t> remoteCallbacks{0}, keyFrames{0}, cognitionFrames{0}, motionDiagnosticFrames{0};
      std::atomic<unsigned> remoteEvent{0}, remoteButtons{0}, keyBits{0};
      std::atomic<bool> systemEmergency{false};
      std::atomic<unsigned> previousPlayerState{0};
      std::atomic<unsigned> cognitionTime{0}, playerState{0}, standHeldMs{0}, walkHitStreak{0};
      std::atomic<bool> damping{false};
      std::atomic<unsigned> motionTime{0}, requestedMotion{0}, effectiveMotion{0}, olderPerceptionTime{0}, motionFlags{0};
    };
    inline LiveState& liveStateStorage()
    {
      // The current Platform/B-Human OBJECT libraries link into one Booster ELF.
      static LiveState value;
      return value;
    }
  }

  inline bool enabled() noexcept {return detail::liveStateStorage().active.load();}
  inline void enable() noexcept {detail::liveStateStorage().active.store(true);}
  /** Only the explicitly selected 987 entry restores resident official F1
   * mode switching. Older deployment/commission scopes are not broadened. */
  inline void enableOfficialButtonMode() noexcept
  {enable(); detail::liveStateStorage().officialButtons.store(true);}
  inline bool officialButtonMode() noexcept
  {return enabled() && detail::liveStateStorage().officialButtons.load();}
  inline void requestStop() noexcept
  {
    // Called by the signal handler too: never lock, allocate or do I/O here.
    static_assert(std::atomic<unsigned>::is_always_lock_free);
    if(enabled())
    {
      detail::liveStateStorage().f1Authority.fetch_or(detail::f1Stopped);
      detail::liveStateStorage().startupConnection.store(false);
      detail::liveStateStorage().stopped.store(true);
    }
  }
  inline bool stopRequested() noexcept {return enabled() && detail::liveStateStorage().stopped.load();}
  inline unsigned modeToken() noexcept {return detail::liveStateStorage().f1Authority.load();}
  /** One zero-command-only startup handshake, before Framework creation.
   * Main calls begin only AFTER the official resetMotorCommands succeeded. */
  inline void beginStartupConnection() noexcept
  {
    if(officialButtonMode() && !stopRequested() && modeToken() == 0)
      detail::liveStateStorage().startupConnection.store(true);
  }
  inline void endStartupConnection() noexcept {detail::liveStateStorage().startupConnection.store(false);}
  inline bool startupConnectionAllowed() noexcept
  {return officialButtonMode() && !stopRequested() && modeToken() == 0 && detail::liveStateStorage().startupConnection.load();}
  inline bool customModeAllowed(unsigned token) noexcept
  {
    if(!enabled()) return true;
    if(stopRequested()) return false;
    return !officialButtonMode() || (modeToken() == token
        && ((token & 7u) == detail::f1Armed || (token == 0 && startupConnectionAllowed())));
  }

  inline std::int64_t steadyTimeNs() noexcept
  {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
      std::chrono::steady_clock::now().time_since_epoch()).count();
  }

  /** Only Main's physical F1 callback records these events, never a joystick. */
  inline void observePhysicalF1Down(std::int64_t nowNs) noexcept
  {
    if(!enabled()) return;
    auto& value = detail::liveStateStorage();
    value.startupConnection.store(false);
    value.f1DownNs.store(nowNs);
    unsigned previous = value.f1Authority.load();
    while(!(previous & detail::f1Stopped))
    {
      // The generation prevents a concurrent new DOWN/UP from granting an old
      // LONG. A repeated physical DOWN always revokes existing body authority.
      const unsigned next = ((previous + 8u) & ~7u) | detail::f1Pending;
      if(value.f1Authority.compare_exchange_weak(previous, next)) return;
    }
  }

  inline void observePhysicalF1Release() noexcept
  {
    if(enabled())
      detail::liveStateStorage().f1Authority.fetch_and(~detail::f1Pending);
    // Releasing an already accepted long press must not require holding F1
    // while using the official Stand/Walk gestures.
  }

  inline void noteCognitionForF1Authorization(unsigned frameTime, std::int64_t nowNs) noexcept
  {
    if(!enabled() || nowNs <= 0) return;
    auto& value = detail::liveStateStorage();
    unsigned previous = value.f1CognitionFrame.load();
    while(frameTime > previous)
      if(value.f1CognitionFrame.compare_exchange_weak(previous, frameTime))
      {
        value.f1CognitionNs.store(nowNs);
        value.f1CognitionFrames.fetch_add(1);
        return;
      }
  }

  /** Called exclusively inside Main's official emergencyReleased LONG branch. */
  inline bool authorizePhysicalF1Long(std::int64_t nowNs) noexcept
  {
    if(!enabled()) return false;
    auto& value = detail::liveStateStorage();
    unsigned expected = value.f1Authority.load();
    if((expected & 7u) != detail::f1Pending) return false;
    // Official mode uses the real physical SDK DOWN -> LONG event sequence,
    // not an invented perception/ten-second authorization condition. The
    // existing process stop latch and concurrent DOWN/UP generation still win.
    if(officialButtonMode())
      return value.f1Authority.compare_exchange_strong(expected, (expected & ~7u) | detail::f1Armed);
    const auto down = value.f1DownNs.load();
    const auto cognition = value.f1CognitionNs.load();
    if(down <= 0 || nowNs <= down || nowNs - down > 10000000000LL
       || value.f1CognitionFrames.load() < 5 || cognition <= 0 || nowNs < cognition
       || nowNs - cognition > 500000000LL)
      return false;
    return value.f1Authority.compare_exchange_strong(expected, (expected & ~7u) | detail::f1Armed);
  }

  inline bool bodyAuthorized() noexcept
  {
    return enabled() && !stopRequested()
           && (detail::liveStateStorage().f1Authority.load() & 7u) == detail::f1Armed;
  }

  template<typename Remote, typename RemoveEvent> void observeRemoteStopX(const Remote& input, RemoveEvent removeEvent)
  {
    if(!enabled()) return;
    // In the official-button entry, X is ordinary native input, including
    // RB+X -> the original KeyStates f1/unstiff mapping. It is not APP exit,
    // and no X->Damping RPC is invented here. Corrupt axes remain a fault.
    if(!std::isfinite(input.lx()) || !std::isfinite(input.ly())
       || !std::isfinite(input.rx()) || !std::isfinite(input.ry()) || (!officialButtonMode() && input.x()))
      requestStop();
    else if(input.event() == removeEvent)
    {
      if(BoosterRemoteStand::enabled()) BoosterRemoteStand::latchDisconnect();
      else requestStop(); // Preserve plain Live/commissioning behavior.
    }
  }

  /** Diagnostic-only: count normal submitted frames and retain their positive KP peak. */
  inline void noteOutput(unsigned phase, float frameMaxKp) noexcept
  {
    if(!enabled()) return;
    detail::liveStateStorage().phase.store(phase, std::memory_order_relaxed);
    float previous = detail::liveStateStorage().peakKp.load(std::memory_order_relaxed);
    while(std::isfinite(frameMaxKp) && frameMaxKp > previous
          && !detail::liveStateStorage().peakKp.compare_exchange_weak(previous, frameMaxKp, std::memory_order_relaxed)) {}
    detail::liveStateStorage().frames.fetch_add(1, std::memory_order_relaxed);
  }
  inline std::uint64_t outputFrames() noexcept {return detail::liveStateStorage().frames.load(std::memory_order_relaxed);}
  inline unsigned lastPhase() noexcept {return detail::liveStateStorage().phase.load(std::memory_order_relaxed);}
  inline float maxKp() noexcept {return detail::liveStateStorage().peakKp.load(std::memory_order_relaxed);}

  struct Diagnostics
  {
    std::uint64_t remoteCallbacks, keyFrames, cognitionFrames, motionDiagnosticFrames;
    unsigned remoteEvent, remoteButtons, keyBits;
    unsigned cognitionTime, playerState, standHeldMs, walkHitStreak;
    bool damping;
    unsigned motionTime, requestedMotion, effectiveMotion, olderPerceptionTime;
    bool offsetFinished, imuBad, forceSitDown;
    bool systemEmergency;
    unsigned previousPlayerState;
  };

  inline void noteRemoteDiagnostic(unsigned event, unsigned buttons) noexcept
  {
    if(!enabled()) return;
    auto& value = detail::liveStateStorage();
    value.remoteEvent.store(event, std::memory_order_relaxed);
    value.remoteButtons.store(buttons, std::memory_order_relaxed);
    value.remoteCallbacks.fetch_add(1, std::memory_order_relaxed);
  }
  inline void noteKeyDiagnostic(bool f1, bool walk, bool stand, bool systemEmergency = false) noexcept
  {
    if(!enabled()) return;
    auto& value = detail::liveStateStorage();
    value.keyBits.store((f1 ? 1u : 0u) | (walk ? 2u : 0u) | (stand ? 4u : 0u), std::memory_order_relaxed);
    value.systemEmergency.store(systemEmergency, std::memory_order_relaxed);
    value.keyFrames.fetch_add(1, std::memory_order_relaxed);
  }
  inline void notePreviousPlayerState(unsigned player) noexcept
  {
    if(enabled()) detail::liveStateStorage().previousPlayerState.store(player, std::memory_order_relaxed);
  }
  inline void noteGameDiagnostic(unsigned time, unsigned player, unsigned standMs, unsigned walkHits, bool isDamping) noexcept
  {
    if(!enabled()) return;
    auto& value = detail::liveStateStorage();
    value.cognitionTime.store(time, std::memory_order_relaxed);
    value.playerState.store(player, std::memory_order_relaxed);
    value.standHeldMs.store(standMs, std::memory_order_relaxed);
    value.walkHitStreak.store(walkHits, std::memory_order_relaxed);
    value.damping.store(isDamping, std::memory_order_relaxed);
    value.cognitionFrames.fetch_add(1, std::memory_order_relaxed);
    // Freshness is checked only when accepting F1, not as another ongoing
    // perception watchdog. The official MotionEngine sit-down logic is intact.
    noteCognitionForF1Authorization(time, steadyTimeNs());
  }
  inline void noteMotionDiagnostic(unsigned time, unsigned requested, unsigned effective, unsigned perceptionTime,
                                   bool offsetFinished, bool imuBad, bool forceSitDown) noexcept
  {
    if(!enabled()) return;
    auto& value = detail::liveStateStorage();
    value.motionTime.store(time, std::memory_order_relaxed);
    value.requestedMotion.store(requested, std::memory_order_relaxed);
    value.effectiveMotion.store(effective, std::memory_order_relaxed);
    value.olderPerceptionTime.store(perceptionTime, std::memory_order_relaxed);
    value.motionFlags.store((offsetFinished ? 1u : 0u) | (imuBad ? 2u : 0u) | (forceSitDown ? 4u : 0u), std::memory_order_relaxed);
    value.motionDiagnosticFrames.fetch_add(1, std::memory_order_relaxed);
  }
  /** Best-effort atomic observations from distinct threads, not a control snapshot or a new gate. */
  inline Diagnostics diagnostics() noexcept
  {
    const auto& value = detail::liveStateStorage();
    const unsigned flags = value.motionFlags.load(std::memory_order_relaxed);
    return {value.remoteCallbacks.load(std::memory_order_relaxed), value.keyFrames.load(std::memory_order_relaxed),
            value.cognitionFrames.load(std::memory_order_relaxed), value.motionDiagnosticFrames.load(std::memory_order_relaxed),
            value.remoteEvent.load(std::memory_order_relaxed), value.remoteButtons.load(std::memory_order_relaxed),
            value.keyBits.load(std::memory_order_relaxed), value.cognitionTime.load(std::memory_order_relaxed),
            value.playerState.load(std::memory_order_relaxed), value.standHeldMs.load(std::memory_order_relaxed),
            value.walkHitStreak.load(std::memory_order_relaxed), value.damping.load(std::memory_order_relaxed),
            value.motionTime.load(std::memory_order_relaxed), value.requestedMotion.load(std::memory_order_relaxed),
            value.effectiveMotion.load(std::memory_order_relaxed), value.olderPerceptionTime.load(std::memory_order_relaxed),
            bool(flags & 1u), bool(flags & 2u), bool(flags & 4u), value.systemEmergency.load(std::memory_order_relaxed),
            value.previousPlayerState.load(std::memory_order_relaxed)};
  }

  template<typename Function> struct DiagnosticExit
  {
    Function function;
    ~DiagnosticExit() noexcept {function();}
  };
  template<typename Function> DiagnosticExit<Function> onDiagnosticExit(Function function) {return {function};}
}
