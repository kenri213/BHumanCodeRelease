/** 647-only opt-in remote-loss Stand latch. No SDK, motion or mode commands. */
#pragma once

#include <atomic>
#include <cstdint>

namespace BoosterRemoteStand
{
  namespace detail
  {
    constexpr std::uint64_t holdBit = 1u, neutralBit = 2u, manualBit = 4u, automaticBit = 8u;
    constexpr unsigned modifierButtons = 64u, manualButtons = 576u, automaticButtons = 320u;
    struct Storage
    {
      std::atomic<bool> active{false};
      std::atomic<bool> awaitingRecoveryStand{false};
      // A single CAS binds recovery to the same disconnect generation.
      std::atomic<std::uint64_t> packed{0};
    };
    inline Storage& remoteStandStorage() noexcept {static Storage value; return value;}
  }

  inline void enable() noexcept {detail::remoteStandStorage().active.store(true);}
  inline bool enabled() noexcept {return detail::remoteStandStorage().active.load();}
  inline bool held() noexcept
  {return enabled() && (detail::remoteStandStorage().packed.load() & detail::holdBit) != 0;}
  inline std::uint64_t generation() noexcept
  {return detail::remoteStandStorage().packed.load() >> 4;}

  inline void latchDisconnect() noexcept
  {
    if(!enabled()) return;
    auto& packed = detail::remoteStandStorage().packed;
    auto previous = packed.load();
    while(!packed.compare_exchange_weak(previous, (((previous >> 4) + 1u) << 4) | detail::holdBit)) {}
  }

  /** A manual in-match get-up must not hand straight to automatic football. */
  inline void beginBodyRecovery() noexcept
  {
    if(!enabled()) return;
    detail::remoteStandStorage().awaitingRecoveryStand.store(true);
    latchDisconnect();
  }

  inline bool bodyRecoveryPending() noexcept
  {return enabled() && detail::remoteStandStorage().awaitingRecoveryStand.load();}

  /** Called by Cognition only after a real upright Stand/Walk feedback frame. */
  inline bool onRecoveryStanding(bool realUprightStand) noexcept
  {
    if(!enabled() || !realUprightStand
       || !detail::remoteStandStorage().awaitingRecoveryStand.exchange(false)) return false;
    // Discard all neutral/LT evidence observed before the robot really stood.
    latchDisconnect();
    return true;
  }

  /** Called only for a real new canonical Main callback, never cached frames. */
  inline void observeSample(unsigned buttons, bool axesNeutral) noexcept
  {
    if(!enabled()) return;
    auto& packed = detail::remoteStandStorage().packed;
    auto previous = packed.load();
    const auto sampleGeneration = previous >> 4;
    while((previous & detail::holdBit) && (previous >> 4) == sampleGeneration)
    {
      auto next = (previous & ~std::uint64_t{15}) | detail::holdBit;
      if(axesNeutral)
      {
        if(buttons == 0)
        {
          // Natural release after a requested recovery must not erase it
          // before the behavior thread has had a chance to consume the CAS.
          const auto ready = previous & (detail::manualBit | detail::automaticBit);
          next |= ready ? ready : detail::neutralBit;
        }
        else if(buttons == detail::modifierButtons)
          // Event-driven controllers publish LT separately. It proves no new
          // neutral and requests no action, but preserves existing evidence.
          next |= previous & (detail::neutralBit | detail::manualBit | detail::automaticBit);
        else if(buttons == detail::manualButtons || buttons == detail::automaticButtons)
        {
          const auto requested = buttons == detail::manualButtons ? detail::manualBit : detail::automaticBit;
          if((previous & detail::neutralBit) || (previous & 15u) == (detail::holdBit | requested))
            next |= requested; // Spend neutral once; an old held chord cannot mint another action.
        }
      }
      if(packed.compare_exchange_weak(previous, next)) return;
      // Never attach an old sample to a concurrently created loss generation.
    }
  }

  /** Snapshot only. tryResume must validate the generation again atomically. */
  inline unsigned resumeChoice() noexcept
  {
    if(!enabled()) return 0;
    const auto value = detail::remoteStandStorage().packed.load() & 15u;
    if(value == (detail::holdBit | detail::manualBit)) return 1;
    if(value == (detail::holdBit | detail::automaticBit)) return 2;
    return 0;
  }

  inline bool tryResume(std::uint64_t expectedGeneration, unsigned choice) noexcept
  {
    if(!enabled() || bodyRecoveryPending() || (choice != 1 && choice != 2)) return false;
    auto& packed = detail::remoteStandStorage().packed;
    auto previous = packed.load();
    const auto required = detail::holdBit |
                          (choice == 1 ? detail::manualBit : detail::automaticBit);
    if((previous >> 4) != expectedGeneration || (previous & 15u) != required) return false;
    return packed.compare_exchange_strong(previous, previous & ~std::uint64_t{15});
  }
}
