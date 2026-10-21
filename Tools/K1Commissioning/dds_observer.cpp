// Independent receive-only diagnostic. Do not link the B-Human application.
#include "ObserverOptions.h"
#include "ObserverAggregate.h"

#include <booster/idl/b1/ButtonEvent.h>
#include <booster/idl/b1/LowState.h>
#include <booster/idl/b1/RemoteControllerState.h>
#include <booster/idl/b1/RobotStatusDdsMsg.h>
#include <booster/robot/channel/channel_subscriber.hpp>
#include <booster/third_party/nlohmann_json/json.hpp>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <functional>
#include <iostream>
#include <memory>
#include <mutex>
#include <thread>
#include <vector>

namespace
{
using Clock = std::chrono::steady_clock;
using Json = nlohmann::json;
using namespace std::chrono_literals;
volatile std::sig_atomic_t interrupted = 0;
constexpr std::size_t maximumJointSamples = 64;
constexpr std::array<const char*, 6> topics = {
  "rt/remote_controller_state", "rt/remote_controller_stat2",
  "rt/button_event", "rt/button_even2", "rt/low_state", "rt/device_gateway"};

std::int64_t nowNanoseconds()
{
  return std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch()).count();
}

void handleSignal(int)
{
  interrupted = 1;
}

struct RemoteSample
{
  std::uint32_t event = 0;
  std::array<float, 4> axes{};
  std::array<bool, 12> buttons{};
  std::array<bool, 9> hat{};
};

struct ButtonSample
{
  std::int32_t button = 0;
  std::uint32_t event = 0;
};

struct LowSample
{
  std::size_t serialJoints = 0;
  std::size_t parallelJoints = 0;
  std::size_t sampledJoints = 0;
  std::array<float, 3> rpy{}, gyro{}, acceleration{};
  std::array<float, maximumJointSamples> q{}, dq{};
  std::size_t serialValuesChecked = 0;
  std::size_t serialNonfinite = 0;
  std::size_t parallelValuesChecked = 0;
  std::size_t parallelNonfinite = 0;
};

struct GatewaySample
{
  std::size_t joints = 0;
  std::size_t connectedJoints = 0;
  std::size_t sampledJoints = 0;
  std::array<bool, maximumJointSamples> connected{};
  std::vector<std::string> disconnectedNames;
  std::size_t batteries = 0;
};

template<typename Sample> struct Slot
{
  std::uint64_t count = 0;
  std::int64_t lastArrival = 0;
  Sample latest{};
};

struct Data
{
  std::array<Slot<RemoteSample>, 2> remote;
  std::array<Slot<ButtonSample>, 2> buttons;
  std::array<std::uint64_t, 2> f1PressDownCounts{}, f1PressUpCounts{};
  Slot<LowSample> low;
  Slot<GatewaySample> gateway;
  std::array<k1commissioning::FiniteAggregate, 2> remoteFinite;
  k1commissioning::FiniteAggregate imuFinite, serialFinite, parallelFinite, headFinite;
  std::uint64_t missingHeadCallbacks = 0;
  k1commissioning::GatewayAggregate gatewayAggregate;
};

struct Shared
{
  std::mutex mutex;
  std::atomic<bool> accepting{true};
  std::atomic<unsigned> callbackErrors{0};
  Data data;
};

template<typename Sample, typename Select, typename Aggregate>
void save(const std::shared_ptr<Shared>& state, Sample sample, Select select, Aggregate aggregate)
{
  const auto arrival = nowNanoseconds();
  std::lock_guard<std::mutex> guard(state->mutex);
  if(!state->accepting.load(std::memory_order_acquire))
    return;
  auto& slot = select(state->data);
  ++slot.count;
  slot.lastArrival = arrival;
  aggregate(state->data, sample);
  slot.latest = std::move(sample);
}

template<typename Sample, typename Select>
void save(const std::shared_ptr<Shared>& state, Sample sample, Select select)
{
  save(state, std::move(sample), select, [](Data&, const Sample&) {});
}

template<typename Function>
void callback(const std::shared_ptr<Shared>& state, const void* message, Function function) noexcept
{
  if(!message || !state->accepting.load(std::memory_order_acquire))
    return;
  try { function(); }
  catch(...) { state->callbackErrors.fetch_add(1, std::memory_order_relaxed); }
}

auto remoteHandler(const std::shared_ptr<Shared>& state, std::size_t index)
{
  return [state, index](const void* message)
  {
    callback(state, message, [&]
    {
      const auto& value = *static_cast<const booster_interface::msg::RemoteControllerState*>(message);
      RemoteSample sample;
      sample.event = value.event();
      sample.axes = {value.lx(), value.ly(), value.rx(), value.ry()};
      sample.buttons = {value.a(), value.b(), value.x(), value.y(), value.lb(), value.rb(),
                        value.lt(), value.rt(), value.ls(), value.rs(), value.back(), value.start()};
      sample.hat = {value.hat_c(), value.hat_u(), value.hat_d(), value.hat_l(), value.hat_r(),
                    value.hat_lu(), value.hat_ld(), value.hat_ru(), value.hat_rd()};
      save(state, sample, [index](Data& data) -> auto& { return data.remote[index]; },
        [index](Data& data, const RemoteSample& value)
        { data.remoteFinite[index].observe(value.axes.size(), k1commissioning::countNonfinite(value.axes)); });
    });
  };
}

auto buttonHandler(const std::shared_ptr<Shared>& state, std::size_t index)
{
  return [state, index](const void* message)
  {
    callback(state, message, [&]
    {
      const auto& value = *static_cast<const booster_interface::msg::ButtonEventMsg*>(message);
      const ButtonSample sample{value.button(), static_cast<std::uint32_t>(value.event())};
      save(state, sample, [index](Data& data) -> auto& { return data.buttons[index]; },
        [index](Data& data, const ButtonSample& value)
        {
          if(value.button == 0 && value.event == booster_interface::msg::PRESS_DOWN)
            ++data.f1PressDownCounts[index];
          if(value.button == 0 && value.event == booster_interface::msg::PRESS_UP)
            ++data.f1PressUpCounts[index];
        });
    });
  };
}

auto lowHandler(const std::shared_ptr<Shared>& state)
{
  return [state](const void* message)
  {
    callback(state, message, [&]
    {
      const auto& value = *static_cast<const booster_interface::msg::LowState*>(message);
      LowSample sample;
      sample.serialJoints = value.motor_state_serial().size();
      sample.parallelJoints = value.motor_state_parallel().size();
      sample.sampledJoints = std::min(sample.serialJoints, maximumJointSamples);
      for(std::size_t joint = 0; joint < sample.serialJoints; ++joint)
      {
        const auto& motor = value.motor_state_serial()[joint];
        sample.serialValuesChecked += 2;
        sample.serialNonfinite += std::isfinite(motor.q()) ? 0 : 1;
        sample.serialNonfinite += std::isfinite(motor.dq()) ? 0 : 1;
        if(joint < sample.sampledJoints)
        {
          sample.q[joint] = motor.q();
          sample.dq[joint] = motor.dq();
        }
      }
      for(const auto& motor : value.motor_state_parallel())
      {
        sample.parallelValuesChecked += 3;
        sample.parallelNonfinite += std::isfinite(motor.q()) ? 0 : 1;
        sample.parallelNonfinite += std::isfinite(motor.dq()) ? 0 : 1;
        sample.parallelNonfinite += std::isfinite(motor.tau_est()) ? 0 : 1;
      }
      for(std::size_t axis = 0; axis < 3; ++axis)
      {
        sample.rpy[axis] = value.imu_state().rpy()[axis];
        sample.gyro[axis] = value.imu_state().gyro()[axis];
        sample.acceleration[axis] = value.imu_state().acc()[axis];
      }
      save(state, sample, [](Data& data) -> auto& { return data.low; },
        [](Data& data, const LowSample& value)
        {
          data.imuFinite.observe(9, k1commissioning::countNonfinite(value.rpy)
                                   + k1commissioning::countNonfinite(value.gyro)
                                   + k1commissioning::countNonfinite(value.acceleration));
          data.serialFinite.observe(value.serialValuesChecked, value.serialNonfinite);
          data.parallelFinite.observe(value.parallelValuesChecked, value.parallelNonfinite);
          if(value.sampledJoints >= 2)
            data.headFinite.observe(4, k1commissioning::countNonfinite(
              std::array<float, 4>{value.q[0], value.dq[0], value.q[1], value.dq[1]}));
          else
            ++data.missingHeadCallbacks;
        });
    });
  };
}

auto gatewayHandler(const std::shared_ptr<Shared>& state)
{
  return [state](const void* message)
  {
    callback(state, message, [&]
    {
      const auto& value = *static_cast<const booster_interface::msg::RobotStatusDdsMsg*>(message);
      GatewaySample sample;
      sample.joints = value.joint_vec().size();
      sample.sampledJoints = std::min(sample.joints, maximumJointSamples);
      sample.batteries = value.battery_vec().size();
      sample.disconnectedNames.reserve(sample.sampledJoints);
      for(std::size_t joint = 0; joint < sample.joints; ++joint)
      {
        const bool connected = value.joint_vec()[joint].is_connected();
        sample.connectedJoints += connected ? 1 : 0;
        if(!connected && sample.disconnectedNames.size() < maximumJointSamples)
          sample.disconnectedNames.push_back(value.joint_vec()[joint].name());
        if(joint < sample.sampledJoints)
          sample.connected[joint] = connected;
      }
      save(state, sample, [](Data& data) -> auto& { return data.gateway; },
        [](Data& data, const GatewaySample& value)
        {
          data.gatewayAggregate.observe(value.joints, value.connectedJoints, value.disconnectedNames,
            value.joints - value.connectedJoints > value.disconnectedNames.size());
        });
    });
  };
}

template<typename Sample> Json status(const Slot<Sample>& slot, std::int64_t now)
{
  return {{"samples_total", slot.count},
          {"arrival_age_ms", slot.count ? Json(std::max<std::int64_t>(0, now - slot.lastArrival) / 1e6) : Json(nullptr)}};
}

template<std::size_t N> bool finite(const std::array<float, N>& values)
{
  return std::all_of(values.begin(), values.end(), [](float value) { return std::isfinite(value); });
}

Json finiteSummary(const k1commissioning::FiniteAggregate& value)
{
  return {{"callbacks_checked", value.callbacksChecked},
          {"callbacks_with_nonfinite", value.callbacksWithNonfinite},
          {"values_checked", value.valuesChecked}, {"nonfinite_values", value.nonfiniteValues}};
}

Json gatewaySummary(const k1commissioning::GatewayAggregate& value)
{
  std::vector<std::string> names;
  for(const auto& entry : value.nameCallbackCounts)
    names.push_back(entry.first);
  return {{"callbacks_total", value.callbacks},
          {"callbacks_with_disconnection", value.callbacksWithDisconnection},
          {"empty_joint_callbacks", value.emptyJointCallbacks},
          {"min_connected_joint_count", value.callbacks ? Json(value.minConnected) : Json(nullptr)},
          {"max_disconnected_joint_count", value.callbacks ? Json(value.maxDisconnected) : Json(nullptr)},
          {"min_reported_joint_count", value.callbacks ? Json(value.minReported) : Json(nullptr)},
          {"max_reported_joint_count", value.callbacks ? Json(value.maxReported) : Json(nullptr)},
          {"any_disconnected_names", names}, {"disconnected_name_callback_counts", value.nameCallbackCounts},
          {"callbacks_with_truncated_names", value.callbacksWithTruncatedNames},
          {"untracked_name_occurrences", value.untrackedNameOccurrences},
          {"name_history_complete", !value.callbacksWithTruncatedNames && !value.untrackedNameOccurrences}};
}

Json snapshot(const std::shared_ptr<Shared>& state, std::int64_t begin)
{
  Data data;
  {
    std::lock_guard<std::mutex> guard(state->mutex);
    data = state->data;
  }
  const auto now = nowNanoseconds();
  Json result{{"type", "snapshot"}, {"elapsed_ms", (now - begin) / 1e6},
              {"callback_errors", state->callbackErrors.load(std::memory_order_relaxed)}};
  for(std::size_t index = 0; index < 2; ++index)
  {
    auto remote = status(data.remote[index], now);
    remote["finite_checks"] = finiteSummary(data.remoteFinite[index]);
    if(data.remote[index].count)
    {
      const auto& sample = data.remote[index].latest;
      remote["latest"] = {{"event", sample.event}, {"axes_lx_ly_rx_ry", sample.axes},
                          {"axes_finite", finite(sample.axes)},
                          {"buttons_a_b_x_y_lb_rb_lt_rt_ls_rs_back_start", sample.buttons},
                          {"hat_c_u_d_l_r_lu_ld_ru_rd", sample.hat}};
    }
    result[topics[index]] = std::move(remote);
    auto button = status(data.buttons[index], now);
    button["f1_press_down_total"] = data.f1PressDownCounts[index];
    button["f1_press_up_total"] = data.f1PressUpCounts[index];
    if(data.buttons[index].count)
      button["latest"] = {{"button", data.buttons[index].latest.button}, {"event", data.buttons[index].latest.event}};
    result[topics[index + 2]] = std::move(button);
  }
  auto low = status(data.low, now);
  low["finite_checks"] = {{"imu_rpy_gyro_acceleration", finiteSummary(data.imuFinite)},
                          {"all_serial_q_dq", finiteSummary(data.serialFinite)},
                          {"all_parallel_q_dq_tau_est", finiteSummary(data.parallelFinite)},
                          {"head_serial_0_1_q_dq", finiteSummary(data.headFinite)},
                          {"missing_head_callbacks", data.missingHeadCallbacks}};
  if(data.low.count)
  {
    const auto& sample = data.low.latest;
    low["latest"] = {{"serial_joint_count", sample.serialJoints}, {"parallel_joint_count", sample.parallelJoints},
                     {"rpy", sample.rpy}, {"gyro", sample.gyro}, {"acceleration", sample.acceleration},
                     {"imu_finite", finite(sample.rpy) && finite(sample.gyro) && finite(sample.acceleration)},
                     {"sampled_joint_count", sample.sampledJoints},
                     {"serial_q", std::vector<float>(sample.q.begin(), sample.q.begin() + sample.sampledJoints)},
                     {"serial_dq", std::vector<float>(sample.dq.begin(), sample.dq.begin() + sample.sampledJoints)}};
    if(sample.sampledJoints >= 2)
      low["latest"]["head_q_radians"] = {{"serial_index_0_yaw", sample.q[0]},
                                         {"serial_index_1_pitch", sample.q[1]}};
  }
  result[topics[4]] = std::move(low);
  auto gateway = status(data.gateway, now);
  gateway["aggregate"] = gatewaySummary(data.gatewayAggregate);
  if(data.gateway.count)
  {
    const auto& sample = data.gateway.latest;
    gateway["latest"] = {{"joint_count", sample.joints}, {"connected_joint_count", sample.connectedJoints},
                         {"disconnected_joint_count", sample.joints - sample.connectedJoints},
                         {"disconnected_joint_names", sample.disconnectedNames},
                         {"disconnected_names_truncated", sample.joints - sample.connectedJoints > sample.disconnectedNames.size()},
                         {"battery_count", sample.batteries}, {"sampled_joint_count", sample.sampledJoints},
                         {"joint_connected", std::vector<bool>(sample.connected.begin(), sample.connected.begin() + sample.sampledJoints)}};
  }
  result[topics[5]] = std::move(gateway);
  return result;
}

void emit(const Json& value)
{
  std::cout << value.dump() << '\n' << std::flush;
}

template<typename Reader> Json matchedWriters(Reader* reader)
{
  // Older SDK headers do not expose this optional discovery diagnostic.
  if constexpr(requires { reader->GetMatchedPublicationsCount(); })
    return reader->GetMatchedPublicationsCount();
  else
    return nullptr;
}

Json discoveryCounts(const std::vector<std::function<Json()>>& queries)
{
  Json result = Json::object();
  for(std::size_t index = 0; index < queries.size(); ++index)
    try { result[topics[index]] = queries[index](); }
    catch(...) { result[topics[index]] = nullptr; }
  return result;
}
}

int main(int argc, char** argv)
{
  k1commissioning::ObserverOptions options;
  std::string error;
  const std::vector<std::string> arguments(argv + 1, argv + argc);
  if(!k1commissioning::parseObserverOptions(arguments, options, error))
  {
    std::cerr << error << '\n';
    return 2;
  }
  if(options.help)
  {
    std::cout << "Receive-only K1 DDS observer. No application control output.\n"
                 "--duration-seconds <0..60> (default 10; 0 does not initialize DDS)\n"
                 "--stop-after-f1 (finish after new-topic F1 down/up, minimum 3s observation)\n"
                 "--interface-ip <IPv4> (default 127.0.0.1)\n"
                 "--domain-id <0..232> (default 0)\n";
    return 0;
  }
  emit({{"type", "observer_start"}, {"receive_only", true}, {"interface_ip", options.interfaceIp},
        {"domain_id", options.domainId}, {"duration_seconds", options.durationSeconds}, {"topics", topics},
        {"note", "Arrival ages are not device timestamps; idle event-driven controllers need not send heartbeats"}});
  if(!options.durationSeconds)
  {
    emit({{"type", "observer_done"}, {"dds_initialized", false}, {"reason", "zero_duration"}});
    return 0;
  }
  std::signal(SIGINT, handleSignal);
  std::signal(SIGTERM, handleSignal);
  const auto begin = Clock::now();
  const auto beginNs = nowNanoseconds();
  const auto deadline = begin + std::chrono::seconds(options.durationSeconds);
  // Covers blocked SDK initialization, output, or reader shutdown as well.
  // This thread never logs and never changes robot state.
  std::thread([deadline] { std::this_thread::sleep_until(deadline); std::_Exit(124); }).detach();
  const auto state = std::make_shared<Shared>();
  std::vector<std::function<void()>> closeReaders;
  std::vector<std::function<Json()>> writerQueries;
  int exitCode = 0;
  try
  {
    booster::robot::ChannelFactory::Instance()->Init(static_cast<std::int32_t>(options.domainId), options.interfaceIp);
    auto add = [&](auto* reader)
    {
      // Keep callback storage alive until process exit. Closing is explicit;
      // SDK global/static destructors must not run while callbacks drain.
      closeReaders.emplace_back([reader] { reader->CloseChannel(); });
      writerQueries.emplace_back([reader] { return matchedWriters(reader); });
      reader->InitChannel();
    };
    using namespace booster_interface::msg;
    using booster::robot::ChannelSubscriber;
    add(new ChannelSubscriber<RemoteControllerState>(topics[0], remoteHandler(state, 0)));
    add(new ChannelSubscriber<RemoteControllerState>(topics[1], remoteHandler(state, 1)));
    add(new ChannelSubscriber<ButtonEventMsg>(topics[2], buttonHandler(state, 0)));
    add(new ChannelSubscriber<ButtonEventMsg>(topics[3], buttonHandler(state, 1)));
    add(new ChannelSubscriber<LowState>(topics[4], lowHandler(state)));
    add(new ChannelSubscriber<RobotStatusDdsMsg>(topics[5], gatewayHandler(state)));
    auto nextReport = begin;
    const auto receiveUntil = deadline - 250ms;
    while(!interrupted && Clock::now() < receiveUntil)
    {
      const auto now = Clock::now();
      if(options.stopAfterF1 && now - begin >= 3s)
      {
        std::lock_guard<std::mutex> guard(state->mutex);
        if(state->data.f1PressDownCounts[1] && state->data.f1PressUpCounts[1])
          break;
      }
      if(now >= nextReport)
      {
        auto report = snapshot(state, beginNs);
        report["matched_writers"] = discoveryCounts(writerQueries);
        emit(report);
        nextReport = now + 1s;
      }
      std::this_thread::sleep_until(std::min(receiveUntil, Clock::now() + 20ms));
    }
  }
  catch(...)
  {
    emit({{"type", "observer_error"}, {"reason", "DDS initialization or subscription exception"}});
    exitCode = 2;
  }
  state->accepting.store(false, std::memory_order_release);
  const auto writersBeforeClose = discoveryCounts(writerQueries);
  unsigned closed = 0;
  for(auto iterator = closeReaders.rbegin(); iterator != closeReaders.rend(); ++iterator)
    try { (*iterator)(); ++closed; }
    catch(...) { exitCode = 2; }
  auto final = snapshot(state, beginNs);
  final["type"] = "observer_done";
  final["closed_readers"] = closed;
  final["requested_readers"] = closeReaders.size();
  final["matched_writers_before_close"] = writersBeforeClose;
  final["interrupted"] = interrupted != 0;
  final["exit_code"] = exitCode;
  emit(final);
  // Deliberate: the SDK owns worker threads beyond the application readers.
  // All readers were closed above; skip static teardown after final flush.
  std::_Exit(exitCode);
}
