/** Process-wide, one-way fence for the explicitly authorized damping-only app. */
#pragma once

#include <atomic>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>

namespace BoosterAppCommissioning
{
  enum class StopReason : unsigned {none, f1, deadline, signal, inputFault, outputFault, exception, remoteStop};

  namespace detail
  {
    struct State
    {
      std::atomic<bool> active{false};
      std::atomic<unsigned> reason{0};
      std::atomic<std::uint64_t> motion{0}, output{0}, camera{0}, lowState{0}, behavior{0};
      std::atomic<std::int64_t> lastLowStateNs{0};
      std::atomic<std::int64_t> providerInputReadyNs{0};
      std::atomic<bool> remoteStopX{false};
      std::atomic<std::uint64_t> remote{0}, remoteXPressed{0}, remoteXFalse{0};
      std::atomic<std::int64_t> firstRemoteXNs{0};
      std::atomic<std::int64_t> firstRemoteXFalseNs{0};
    };

    inline State& processStateStorage()
    {
      // Platform and B-Human are OBJECT targets linked into the single Booster
      // executable, not independently loaded DSOs. An external-linkage inline
      // function's local static is one ODR entity across translation units.
      // Do not make this function TU-local or copy this state into a DSO.
      static State value;
      return value;
    }
  }

  inline bool enabled() noexcept {return detail::processStateStorage().active.load(std::memory_order_acquire);}
  inline void enable() noexcept {detail::processStateStorage().active.store(true, std::memory_order_release);}
  inline void requestStop(StopReason reason) noexcept
  {
    if(enabled() && reason != StopReason::none)
    {
      unsigned empty = 0;
      detail::processStateStorage().reason.compare_exchange_strong(empty, static_cast<unsigned>(reason));
    }
  }
  inline unsigned stopReason() noexcept {return detail::processStateStorage().reason.load();}
  inline bool stopRequested() noexcept {return stopReason() != 0;}
  inline bool remoteStopXEnabled() noexcept {return enabled() && detail::processStateStorage().remoteStopX.load();}
  inline void enableRemoteStopX() noexcept {if(enabled()) detail::processStateStorage().remoteStopX.store(true);}
  inline std::uint64_t remoteFrames() noexcept {return detail::processStateStorage().remote.load(std::memory_order_relaxed);}
  inline std::uint64_t remoteXPresses() noexcept {return detail::processStateStorage().remoteXPressed.load(std::memory_order_relaxed);}
  inline std::uint64_t remoteXFalseSamples() noexcept {return detail::processStateStorage().remoteXFalse.load(std::memory_order_relaxed);}
  inline std::int64_t firstRemoteXTimeNs() noexcept {return detail::processStateStorage().firstRemoteXNs.load();}
  inline std::int64_t firstRemoteXFalseTimeNs() noexcept {return detail::processStateStorage().firstRemoteXFalseNs.load();}

  /** Event-driven input only; no axis or key is forwarded to an actuator. */
  template<typename Remote, typename RemoveEvent> void handleRemoteStopX(const Remote& input, RemoveEvent removeEvent)
  {
    if(!remoteStopXEnabled())
      return;
    if(input.event() == removeEvent || !std::isfinite(input.lx()) || !std::isfinite(input.ly())
       || !std::isfinite(input.rx()) || !std::isfinite(input.ry()))
    {
      requestStop(StopReason::inputFault);
      return;
    }
    detail::processStateStorage().remote.fetch_add(1, std::memory_order_relaxed);
    if(input.x())
    {
      detail::processStateStorage().remoteXPressed.fetch_add(1, std::memory_order_relaxed);
      const auto now = std::chrono::steady_clock::now().time_since_epoch();
      std::int64_t empty = 0;
      detail::processStateStorage().firstRemoteXNs.compare_exchange_strong(
          empty, std::chrono::duration_cast<std::chrono::nanoseconds>(now).count());
      requestStop(StopReason::remoteStop);
    }
    else
    {
      detail::processStateStorage().remoteXFalse.fetch_add(1, std::memory_order_relaxed);
      const auto now = std::chrono::steady_clock::now().time_since_epoch();
      std::int64_t empty = 0;
      detail::processStateStorage().firstRemoteXFalseNs.compare_exchange_strong(
          empty, std::chrono::duration_cast<std::chrono::nanoseconds>(now).count());
    }
  }
  inline void noteMotionFrame() noexcept {if(enabled()) detail::processStateStorage().motion.fetch_add(1, std::memory_order_relaxed);}
  inline void noteOutputFrame() noexcept {if(enabled()) detail::processStateStorage().output.fetch_add(1, std::memory_order_relaxed);}
  inline void noteCameraFrame() noexcept {if(enabled()) detail::processStateStorage().camera.fetch_add(1, std::memory_order_relaxed);}
  inline void noteBehaviorFrame() noexcept {if(enabled()) detail::processStateStorage().behavior.fetch_add(1, std::memory_order_relaxed);}
  inline std::uint64_t motionFrames() noexcept {return detail::processStateStorage().motion.load(std::memory_order_relaxed);}
  inline std::uint64_t outputFrames() noexcept {return detail::processStateStorage().output.load(std::memory_order_relaxed);}
  inline std::uint64_t cameraFrames() noexcept {return detail::processStateStorage().camera.load(std::memory_order_relaxed);}
  inline std::uint64_t behaviorFrames() noexcept {return detail::processStateStorage().behavior.load(std::memory_order_relaxed);}
  inline void noteLowStateFrame() noexcept
  {
    if(enabled())
    {
      const auto now = std::chrono::steady_clock::now().time_since_epoch();
      detail::processStateStorage().lastLowStateNs.store(std::chrono::duration_cast<std::chrono::nanoseconds>(now).count(), std::memory_order_release);
      detail::processStateStorage().lowState.fetch_add(1, std::memory_order_relaxed);
    }
  }
  inline std::uint64_t lowStateFrames() noexcept {return detail::processStateStorage().lowState.load(std::memory_order_relaxed);}
  inline std::int64_t lastLowStateTimeNs() noexcept {return detail::processStateStorage().lastLowStateNs.load(std::memory_order_acquire);}
  inline void noteProviderInputReady() noexcept
  {
    if(enabled())
    {
      const auto now = std::chrono::steady_clock::now().time_since_epoch();
      std::int64_t empty = 0;
      detail::processStateStorage().providerInputReadyNs.compare_exchange_strong(
          empty, std::chrono::duration_cast<std::chrono::nanoseconds>(now).count());
    }
  }
  inline std::int64_t providerInputReadyTimeNs() noexcept {return detail::processStateStorage().providerInputReadyNs.load();}

  /** SDK-free: pass the real SDK SERIAL enum, never a guessed numeric value. */
  template<typename Command, typename Serial> bool zeroLowCommand(Command& command, Serial serial)
  {
    if(command.motor_cmd().size() != 22)
    {
      requestStop(StopReason::outputFault);
      return false;
    }
    command.cmd_type(serial);
    for(auto& motor : command.motor_cmd())
    {
      motor.mode(0);
      motor.q(0.f);
      motor.dq(0.f);
      motor.tau(0.f);
      motor.kp(0.f);
      motor.kd(0.f);
      motor.weight(0.f);
    }
    return true;
  }

  /** Preserve forced-unwind cancellation; latch on any failed/unwound output. */
  template<typename Publisher, typename Command, typename Serial>
  bool sendZeroLowCommand(Publisher& publisher, Command& command, Serial serial)
  {
    if(!enabled())
      return false;
    struct OutputAttempt
    {
      bool completed = false;
      ~OutputAttempt() {if(!completed) requestStop(StopReason::outputFault);}
    } attempt;
    if(!zeroLowCommand(command, serial) || !publisher.Write(&command))
      return false;
    attempt.completed = true;
    noteOutputFrame();
    return true;
  }

  template<typename LowState> bool validLowState(const LowState& value)
  {
    if(value.motor_state_serial().size() != 22 || value.motor_state_parallel().size() != 22)
      return false;
    for(std::size_t axis = 0; axis < 3; ++axis)
      if(!std::isfinite(value.imu_state().rpy()[axis]) || !std::isfinite(value.imu_state().gyro()[axis])
         || !std::isfinite(value.imu_state().acc()[axis]))
        return false;
    for(const auto& motor : value.motor_state_serial())
      if(!std::isfinite(motor.q()) || !std::isfinite(motor.dq()) || !std::isfinite(motor.tau_est()))
        return false;
    for(const auto& motor : value.motor_state_parallel())
      if(!std::isfinite(motor.q()) || !std::isfinite(motor.dq()) || !std::isfinite(motor.tau_est()))
        return false;
    return true;
  }
}
