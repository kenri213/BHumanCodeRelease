/**
 * @file Main.cpp
 * Implementation of the main() function for starting and stopping the module framework.
 * @author Colin Graf
 * @author Thomas Röfer
 */

#include <booster/idl/b1/ButtonEvent.h>
#include <booster/idl/b1/LowCmd.h>
#include <booster/idl/b1/LowState.h>
#include <booster/idl/b1/RobotStatusDdsMsg.h>
#include <booster/idl/b1/RemoteControllerState.h>
#include <booster/robot/b1/b1_api_const.hpp>
#include <booster/robot/b1/b1_loco_client.hpp>
#include <booster/robot/channel/channel_publisher.hpp>
#include <booster/robot/channel/channel_subscriber.hpp>
#include <booster/robot/device/light/light_control_client.hpp>
#include <csignal>
#include <atomic>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <sys/file.h> // flock
#include <fcntl.h>
#include <pthread.h>
#include <sys/wait.h>
#include <unistd.h>

#include "Framework/Robot.h"
#include "Framework/Robots.h"
#include "Framework/Settings.h"
#include "Math/Constants.h"
#include "Platform/File.h"
#include "Platform/BoosterAppCommissioning.h"
#include "Platform/BoosterOfficialLiveTest.h"
#include "Platform/BoosterMatchSafety.h"
#include "Platform/Semaphore.h"
#include "Platform/SystemCall.h"
#include "Platform/Thread.h"
#include "Platform/Time.h"
#include "Streaming/FunctionList.h"
#include "Tools/Motion/MotionPhase.h"

static pid_t bhumanPid = 0;
static Robot* robot = nullptr;
static volatile sig_atomic_t run = 1;
static pthread_t mainThread;

// Used by BoosterProvider
namespace Main
{
  booster::robot::b1::B1LocoClient client;
  booster::robot::light::LightControlClient ledControl;
  void stopOfficialLiveTest();
}
using namespace Main;

static bool ledControlAvailable = false;
static unsigned timeWhenAskedToTerminate = 0;

SystemCall::Mode SystemCall::getMode()
{
  return physicalRobot;
}

static void bhumanStart(const Settings& settings)
{
  fprintf(stderr, "B-Human: Start.\n");

  robot = new Robot(settings, std::string());
  robot->start();
}

static void bhumanStop()
{
  fprintf(stderr, "B-Human: Stop.\n");
  robot->announceStop();
  robot->stop();
  delete robot;
  robot = nullptr;
}

static void sigHandlerShutdown(int sig)
{
  if(run)
    fprintf(stderr, "Caught signal %i\nShutting down...\n", sig);
  run = false;
}

static void sigHandlerRedirect(int)
{
  timeWhenAskedToTerminate = Time::getCurrentSystemTime();
  run = false;
}

static void sigHandlerIgnore(int)
{
}

// No stdio, SDK RPC or semaphore operations from the live-test signal handler.
static void sigHandlerOfficialLiveShutdown(int)
{
  // enable() constructs the state before this handler is installed. Only
  // lock-free bool atomics are used here: no SDK, stdio, allocation or locks.
  static_assert(std::atomic<bool>::is_always_lock_free);
  BoosterOfficialLiveTest::requestStop();
  run = 0;
}

template<typename Writer> static bool commissionLowCommandRouteReady(Writer& writer)
{
  if constexpr(requires {writer.GetMatchedSubscriptionsCount();})
    return writer.GetMatchedSubscriptionsCount() >= 1;
  else
    return false; // Never treat a local Write return as proof of hardware discovery.
}

template<typename Endpoint> static size_t commissionLowCommandReaderCount(Endpoint& endpoint)
{
  if constexpr(requires {endpoint.GetMatchedSubscriptionsCount();})
    return endpoint.GetMatchedSubscriptionsCount();
  else
    return 0;
}

template<typename Endpoint> static size_t commissionButtonWriterCount(Endpoint& endpoint)
{
  if constexpr(requires {endpoint.GetMatchedPublicationsCount();})
    return endpoint.GetMatchedPublicationsCount();
  else
    return 0;
}

class EmergencyHandler : public Thread
{
  Semaphore stateChangedSignal; /**< Semaphore is triggered when the state has been changed. */
  Semaphore lowStateSignal; /**< Semaphore is triggered when a low state was received. */
  booster_interface::msg::LowCmd lowCmd; /**< Command to deactivate all joints. */
  booster::robot::ChannelPublisher<booster_interface::msg::LowCmd> lowCmdPublisher{booster::robot::b1::kTopicJointCtrl}; /**< Publisher for it. */
  Settings::RobotType robotType = Settings::k1; /**< Type of this robot. */
  std::atomic<bool> commissionOnly{false};
  std::atomic<bool> commissionLatched{false};
  std::atomic<bool> commissionFault{false};
  std::atomic<int> commissionRealtime{0};
  std::atomic<bool> commissionDampingAck{false};
  std::atomic<bool> commissionRunning{false};
  std::atomic<bool> commissionExited{true};
  std::atomic<unsigned> commissionWriteFalseCount{0};
  std::atomic<unsigned> commissionWriteExceptionCount{0};
  std::atomic<unsigned> commissionModeFailureCount{0};
  std::atomic<unsigned> commissionModeExceptionCount{0};
  std::atomic<unsigned> commissionHandlerExceptionCount{0};
  std::atomic<int> commissionLastModeRequested{-1};
  std::atomic<int> commissionLastModeResult{0};
  std::thread commissionThread;
  std::mutex zeroCommandMutex;

  bool handlerRunning() const
  {
    return commissionOnly.load() ? commissionRunning.load() : isRunning();
  }

  int changeMode(const booster::robot::RobotMode mode)
  {
    if(BoosterOfficialLiveTest::enabled() && BoosterOfficialLiveTest::stopRequested()
       && mode != booster::robot::RobotMode::kDamping)
      return -1;
    if(BoosterAppCommissioning::enabled() && mode != booster::robot::RobotMode::kDamping)
    {
      BoosterAppCommissioning::requestStop(BoosterAppCommissioning::StopReason::outputFault);
      return -1;
    }
    if(!commissionOnly.load())
    {
      const int result = client.ChangeMode(mode);
      // A Custom RPC already in flight when X was pressed must not re-arm the
      // body after a concurrent Damping request completed.
      if(BoosterOfficialLiveTest::enabled() && BoosterOfficialLiveTest::stopRequested()
         && mode != booster::robot::RobotMode::kDamping)
      {
        client.ChangeMode(booster::robot::RobotMode::kDamping);
        return -1;
      }
      return result;
    }
    if(mode == booster::robot::RobotMode::kCustom && commissionLatched.load())
      return -1;
    try
    {
      const int result = client.ChangeMode(mode);
      if(!result && mode == booster::robot::RobotMode::kDamping)
        commissionDampingAck.store(true);
      if(result)
      {
        commissionModeFailureCount.fetch_add(1);
        commissionLastModeRequested.store(static_cast<int>(mode));
        commissionLastModeResult.store(result);
        commissionFault.store(true);
        latchCommissionEmergency();
      }
      return result;
    }
    catch(...)
    {
      commissionModeExceptionCount.fetch_add(1);
      commissionLastModeRequested.store(static_cast<int>(mode));
      commissionLastModeResult.store(-1);
      commissionFault.store(true);
      latchCommissionEmergency();
      return -1;
    }
  }

  /** The handler that switches between client modes for the K1. */
  void handler()
  {
    booster::robot::b1::GetModeResponse modeResponse;
    if(commissionOnly.load())
    {
      sched_param requested{};
      requested.sched_priority = 21;
      int policy = 0;
      sched_param actual{};
      const bool realtime = !pthread_setschedparam(pthread_self(), SCHED_FIFO, &requested)
                            && !pthread_getschedparam(pthread_self(), &policy, &actual)
                            && policy == SCHED_FIFO && actual.sched_priority == 21;
      commissionRealtime.store(realtime ? 1 : -1);
      if(!realtime)
      {
        commissionFault.store(true);
        latchCommissionEmergency();
        printf("COMMISSION HANDLER_RT policy=%d priority=%d verified=0\n", policy, actual.sched_priority);
        return;
      }
      printf("COMMISSION HANDLER_RT policy=%d priority=%d verified=1\n", policy, actual.sched_priority);
    }
    else
      setPriority(21);
    while(handlerRunning())
    {
      if(commissionOnly.load())
      {
        if(commissionLatched.load())
          state.store(emergencyPressed);
        if(!resetMotorCommands())
        {
          commissionFault.store(true);
          latchCommissionEmergency();
        }
      }
      // If state was changed or no low state was received recently.
      if(stateChangedSignal.wait(commissionOnly.load() ? 10 : 100) || !lowStateSignal.wait(10))
        while(handlerRunning())
        {
          switch(state.load())
          {
            case noEmergency:
            {
              if(lowStateSignal.wait(10))
              {
                if(!changeMode(booster::robot::RobotMode::kCustom))
                  goto success; // Leave switch and inner loop
                break;
              }
              else
                continue;
            }
            case emergencyPressed:
              // On the T1, wait for the Motion PC to switch to damping mode, then switch to emergencyReleased.
              if(robotType == Settings::t1 && !client.GetMode(modeResponse) && modeResponse.mode_ == booster::robot::RobotMode::kDamping)
              {
                resetMotorCommands();
                state = emergencyReleased;
                goto success;
              }
              [[fallthrough]];
            case emergencyReleased:
              // On the K1, switch to damping mode, both in emergencyPressed and emergencyReleased.
              // Nothing happens here for the T1, but the robotStatusHandler below waits for the joints
              // to reconnect.
              if(robotType == Settings::k1 && !changeMode(booster::robot::RobotMode::kDamping))
              {
                resetMotorCommands();
                goto success; // Leave switch and inner loop
              }
          }
          Thread::sleep(10);
        }
      success: ;
      }
  }

public:
  enum State {noEmergency, emergencyPressed, emergencyReleased};
  std::atomic<State> state{noEmergency};

  void enableEmergencyOnlyCommissioning()
  {
    commissionOnly.store(true);
    commissionLatched.store(false);
    commissionFault.store(false);
    commissionRealtime.store(0);
    commissionDampingAck.store(false);
    state.store(emergencyReleased);
  }

  void latchCommissionEmergency()
  {
    if(!commissionLatched.exchange(true))
      commissionDampingAck.store(false);
    state.store(emergencyPressed);
    stateChangedSignal.post();
  }

  bool commissioningLatched() const {return commissionLatched.load();}
  bool commissioningFault() const {return commissionFault.load();}
  int commissioningRealtime() const {return commissionRealtime.load();}
  bool commissioningDampingAcknowledged() const {return commissionDampingAck.load();}
  bool commissioningLowCommandReaderReady() {return commissionLowCommandRouteReady(lowCmdPublisher);}
  size_t commissioningLowCommandReaders() {return commissionLowCommandReaderCount(lowCmdPublisher);}
  void printCommissioningFaultCounters() const
  {
    printf("COMMISSION CONTROL_FAULT_COUNTS zero_write_false=%u zero_write_exception=%u mode_rpc_failure=%u mode_exception=%u handler_exception=%u last_mode_requested=%d last_mode_result=%d\n",
           commissionWriteFalseCount.load(), commissionWriteExceptionCount.load(), commissionModeFailureCount.load(),
           commissionModeExceptionCount.load(), commissionHandlerExceptionCount.load(),
           commissionLastModeRequested.load(), commissionLastModeResult.load());
  }

  /**
   * Initialize the instance for the current robot type.
   * @param robotType The robot type.
   */
  void init(const Settings::RobotType robotType)
  {
    this->robotType = robotType;

    // Prepare motor commands with zero p and d values, i.e. motors off.
    lowCmd.cmd_type(booster_interface::msg::SERIAL);
    const size_t numOfJoints = booster::robot::b1::kJointCnt - (robotType == Settings::k1 ? 1 : 0);
    lowCmd.motor_cmd(std::vector<booster_interface::msg::MotorCmd>(numOfJoints));
    if(commissionOnly.load())
    {
      if(robotType != Settings::k1 || numOfJoints != 22)
      {
        commissionFault.store(true);
        throw std::runtime_error("COMMISSION DENIED non-K1 or non-22 command layout");
      }
      for(auto& motor : lowCmd.motor_cmd())
      {
        motor.mode(0);
        motor.q(0.f);
        motor.dq(0.f);
        motor.tau(0.f);
        motor.kp(0.f);
        motor.kd(0.f);
        motor.weight(0.f);
      }
    }

    lowCmdPublisher.InitChannel();
  }

  /** Reset motor commands so that the robot does not move when activating the custom mode. */
  bool resetMotorCommands()
  {
    std::lock_guard<std::mutex> guard(zeroCommandMutex);
    if(BoosterAppCommissioning::enabled())
      return BoosterAppCommissioning::sendZeroLowCommand(lowCmdPublisher, lowCmd, booster_interface::msg::SERIAL);
    if(!commissionOnly.load())
      return lowCmdPublisher.Write(&lowCmd);
    try
    {
      const bool written = lowCmdPublisher.Write(&lowCmd);
      if(!written)
        commissionWriteFalseCount.fetch_add(1);
      return written;
    }
    catch(...)
    {
      commissionWriteExceptionCount.fetch_add(1);
      commissionFault.store(true);
      return false;
    }
  }

  /**
   * Start handler.
   * @param robotType The type of robot the handler is required for.
   */
  void start()
  {
    if(commissionOnly.load())
    {
      if(commissionThread.joinable())
        throw std::runtime_error("COMMISSION handler already started");
      commissionRunning.store(true);
      commissionExited.store(false);
      try
      {
        commissionThread = std::thread([this]
        {
          // Thread::threadStart enables ASYNCHRONOUS cancellation. Do not use
          // it here: cancellation inside an SDK call/mutex cannot be recovered.
          pthread_setcancelstate(PTHREAD_CANCEL_DISABLE, nullptr);
          try {handler();}
          catch(...)
          {
            commissionHandlerExceptionCount.fetch_add(1);
            commissionFault.store(true);
            latchCommissionEmergency();
          }
          commissionRunning.store(false);
          commissionExited.store(true);
        });
      }
      catch(...)
      {
        commissionRunning.store(false);
        commissionExited.store(true);
        throw;
      }
      return;
    }
    Thread::start(this, &EmergencyHandler::handler);
  }

  /** Cooperative-only exit. A late SDK RPC must never trigger pthread_cancel. */
  bool stopCommissioning(unsigned timeout)
  {
    commissionRunning.store(false);
    stateChangedSignal.post();
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout);
    while(!commissionExited.load() && std::chrono::steady_clock::now() < deadline)
      Thread::sleep(5);
    if(!commissionExited.load())
      return false; // Do not join, close channels or destroy a still-live SDK callback/lock.
    if(commissionThread.joinable())
      commissionThread.join();
    lowCmdPublisher.CloseChannel();
    return true;
  }

  /** Stop handler. */
  void stop(unsigned timeout = 10000)
  {
    if(commissionOnly.load())
    {
      if(!stopCommissioning(timeout))
      {
        printf("COMMISSION HANDLER_EXIT_TIMEOUT RECOVERY_REQUIRED EXTERNAL_DAMPING_WATCHDOG_REQUIRED\n");
        fflush(stdout);
        std::_Exit(EXIT_FAILURE);
      }
      return;
    }
    announceStop();
    stateChangedSignal.post();
    Thread::stop(timeout);
    lowCmdPublisher.CloseChannel();
  }

  /**
   * Set state of handler.
   * @param state The new state.
   * @param trigger Will the handler be triggered to process the new state?
   */
  void setState(const State state, const bool trigger = true)
  {
    if(commissionOnly.load() && commissionLatched.load() && state == noEmergency)
      return;
    if(commissionOnly.load() && state == noEmergency)
      commissionDampingAck.store(false);
    this->state.store(state);
    if(commissionOnly.load() && commissionLatched.load())
      this->state.store(emergencyPressed);
    if(trigger)
      stateChangedSignal.post();
  }

  /** A low state was received. */
  void lowStateReceived()
  {
    while(lowStateSignal.tryWait());
    lowStateSignal.post();
  }
};

static EmergencyHandler emergencyHandler;

void Main::stopOfficialLiveTest()
{
  if(!BoosterOfficialLiveTest::enabled() || !BoosterOfficialLiveTest::stopRequested())
    return;
  emergencyHandler.setState(EmergencyHandler::emergencyPressed);
  pthread_kill(mainThread, SIGINT);
}

static void buttonEventHandler(const void* msg)
{
  const booster_interface::msg::ButtonEventMsg& buttonEvent = *reinterpret_cast<const booster_interface::msg::ButtonEventMsg*>(msg);
  if(buttonEvent.button() == 0) // F1
  {
    if(BoosterOfficialLiveTest::enabled())
    {
      if(buttonEvent.event() == booster_interface::msg::PRESS_DOWN)
      {
        const bool stopMatch = BoosterMatchSafety::enabled() && BoosterOfficialLiveTest::bodyAuthorized();
        if(BoosterMatchSafety::enabled())
          BoosterMatchSafety::invalidateConfirmation();
        BoosterOfficialLiveTest::observePhysicalF1Down(BoosterOfficialLiveTest::steadyTimeNs());
        if(stopMatch)
        {
          // Revoke final-output authority before the SDK/termination path.
          // The first F1 DOWN is still the original fresh-long-press unlock.
          BoosterOfficialLiveTest::requestStop();
          Main::stopOfficialLiveTest();
          return;
        }
      }
      else if(buttonEvent.event() == booster_interface::msg::PRESS_UP)
        BoosterOfficialLiveTest::observePhysicalF1Release();
    }
    switch(emergencyHandler.state)
    {
      case EmergencyHandler::noEmergency:
        if(buttonEvent.event() == booster_interface::msg::PRESS_DOWN)
          emergencyHandler.setState(EmergencyHandler::emergencyPressed);
        break;
      case EmergencyHandler::emergencyPressed:
        if(buttonEvent.event() == booster_interface::msg::PRESS_UP)
          emergencyHandler.setState(EmergencyHandler::emergencyReleased, false);
        break;
      case EmergencyHandler::emergencyReleased:
        if(buttonEvent.event() == booster_interface::msg::LONG_PRESS_START)
        {
          if(!BoosterOfficialLiveTest::enabled()
             || BoosterOfficialLiveTest::authorizePhysicalF1Long(BoosterOfficialLiveTest::steadyTimeNs()))
            emergencyHandler.setState(EmergencyHandler::noEmergency);
        }
    }
  }
}

static void lowStateHandler(const void*)
{
  emergencyHandler.lowStateReceived();
}

static void robotStatusHandler(const void* msg)
{
  const booster_interface::msg::RobotStatusDdsMsg& robotStatus = *reinterpret_cast<const booster_interface::msg::RobotStatusDdsMsg*>(msg);
  int connectedJoints = 0;
  for(const booster_interface::msg::RobotDdsJointStatus& joint : robotStatus.joint_vec())
    if(joint.is_connected())
      ++connectedJoints;

  switch(emergencyHandler.state)
  {
    case EmergencyHandler::noEmergency:
      if(!connectedJoints)
        emergencyHandler.setState(EmergencyHandler::emergencyPressed);
      break;
    case EmergencyHandler::emergencyReleased:
      if(connectedJoints == booster::robot::b1::kJointCnt)
        emergencyHandler.setState(EmergencyHandler::noEmergency);
  }
}

static Settings::RobotType getRobotType(const std::string& robotName)
{
  const std::string bhdir = File::getBHDir();
  InMapFile robotsStream(bhdir + "/Config/Robots/robots.cfg");
  if(!robotsStream.exists())
    fprintf(stderr, "Could not load robots.cfg\n");
  else
  {
    Robots robots;
    robotsStream >> robots;
    for(const Robots::RobotId& robot : robots.robotsIds)
      if(robot.name == robotName)
        return robot.robotType;
    fprintf(stderr, "Could not find robot name \"%s\" in robots.cfg!\n", robotName.c_str());
  }
  return Settings::k1;
}

static bool initInstance()
{
  // Initialize connection to robot at realtime priority.
  sched_param param;
  param.sched_priority = 21; // One more than Motion
  if(pthread_setschedparam(pthread_self(), SCHED_FIFO, &param))
  {
    fprintf(stderr, "Raising priority failed!\n");
    return false;
  }
  if(BoosterMatchSafety::enabled())
    booster::robot::ChannelFactory::Instance()->Init(0, "127.0.0.1");
  else
    booster::robot::ChannelFactory::Instance()->Init(0);
  param.sched_priority = 0;
  if(pthread_setschedparam(pthread_self(), SCHED_OTHER, &param))
  {
    fprintf(stderr, "Lowering priority failed!\n");
    return false;
  }
  return true;
}

// SDK-free predicates are also compiled independently by the commissioning tests.
static bool parseCommissionSeconds(const char* text, unsigned& seconds)
{
  if(!text || !*text)
    return false;
  unsigned value = 0;
  for(const char* cursor = text; *cursor; ++cursor)
  {
    if(*cursor < '0' || *cursor > '9')
      return false;
    value = value * 10 + static_cast<unsigned>(*cursor - '0');
    if(value > 45)
      return false;
  }
  if(!value)
    return false;
  seconds = value;
  return true;
}

// 987 receives the 996 match behavior in its own hardware-bound entry.
// Validate before any mode/SDK/configuration initialization. Do not broaden
// the older 996/live/commissioning scopes or accept another project's Config.
static bool match987IdentityAllowed(const std::string& actualSerial, const char* bhDir)
{
  return actualSerial == "1424725033885" && bhDir
         && !strcmp(bhDir, "/home/booster/Workspace/BHuman_987_Match_20261023");
}

struct CommissionInputSnapshot
{
  long long lowTime = -1;
  long long statusTime = -1;
  size_t serialJoints = 0;
  size_t parallelJoints = 0;
  size_t reportedJoints = 0;
  size_t connectedJoints = 0;
  bool finite = false;
};

static bool commissionInputsReady(const CommissionInputSnapshot& input, long long now)
{
  return input.lowTime >= 0 && input.statusTime >= 0 && now >= input.lowTime && now >= input.statusTime
         && now - input.lowTime <= 100 && now - input.statusTime <= 500
         && input.serialJoints == 22 && input.parallelJoints == 22
         && input.reportedJoints == 22 && input.connectedJoints == 22 && input.finite;
}

static bool validCommissionSerial(const char* serial)
{
  if(!serial || !*serial || strlen(serial) > 64)
    return false;
  for(const char* cursor = serial; *cursor; ++cursor)
    if(*cursor < '0' || *cursor > '9')
      return false;
  return true;
}

static bool commissionF1WindowValid(bool customObserved, bool f1TriggeredExit,
                                    long long f1Time, long long customTime, long long deadline)
{
  return customObserved && f1TriggeredExit && customTime >= 0 && f1Time >= customTime && f1Time <= deadline;
}

static bool commissionZeroWarmupReady(unsigned writes, long long firstWrite, long long lastWrite)
{
  return writes >= 10 && firstWrite >= 0 && lastWrite >= firstWrite
         && lastWrite - firstWrite >= 100 && lastWrite - firstWrite <= 500;
}

// Diagnostics only: callbacks record bits atomically; only the main error path prints them.
enum CommissionCallbackCause : unsigned
{
  buttonCallbackException = 1u << 0,
  lowCallbackException = 1u << 1,
  statusCallbackException = 1u << 2,
  lowNonfinite = 1u << 3,
  lowSerialCount = 1u << 4,
  lowParallelCount = 1u << 5,
  statusJointCount = 1u << 6,
  statusDisconnected = 1u << 7
};

static unsigned commissionLowFaultCauses(bool finite, size_t serial, size_t parallel)
{
  return (finite ? 0u : lowNonfinite) | (serial == 22 ? 0u : lowSerialCount)
         | (parallel == 22 ? 0u : lowParallelCount);
}

static unsigned commissionStatusFaultCauses(size_t reported, size_t connected)
{
  return (reported == 22 ? 0u : statusJointCount) | (connected == 22 ? 0u : statusDisconnected);
}

static void printCommissionCallbackCauses(unsigned causes)
{
  printf("COMMISSION CALLBACK_FAULT_CAUSES mask=0x%x", causes);
  if(causes & buttonCallbackException) printf(" BUTTON_CALLBACK_EXCEPTION");
  if(causes & lowCallbackException) printf(" LOW_CALLBACK_EXCEPTION");
  if(causes & statusCallbackException) printf(" STATUS_CALLBACK_EXCEPTION");
  if(causes & lowNonfinite) printf(" LOW_NONFINITE");
  if(causes & lowSerialCount) printf(" LOW_SERIAL_JOINT_COUNT_NOT_22");
  if(causes & lowParallelCount) printf(" LOW_PARALLEL_JOINT_COUNT_NOT_22");
  if(causes & statusJointCount) printf(" STATUS_JOINT_COUNT_NOT_22");
  if(causes & statusDisconnected) printf(" STATUS_CONNECTED_NOT_22");
  printf("\n");
}

template<typename Reader> static bool commissionButtonRouteReady(Reader& reader)
{
  if constexpr(requires {reader.GetMatchedPublicationsCount();})
    return reader.GetMatchedPublicationsCount() == 1;
  else
    return false; // An SDK without discovery proof cannot arm this entry.
}

static volatile sig_atomic_t commissionSignal = 0;
static void commissionSignalHandler(int signal) {commissionSignal = signal;}

[[noreturn]] static void runEmergencyOnlyCommission(unsigned durationSeconds, const char* expectedSerial)
{
  using Clock = std::chrono::steady_clock;
  using namespace std::chrono_literals;
  const auto applicationStart = Clock::now();
  const auto clockMs = [] {return std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now().time_since_epoch()).count();};
  struct Inputs
  {
    std::mutex mutex;
    CommissionInputSnapshot sample;
    std::atomic<bool> accepting{true};
    std::atomic<bool> armed{false};
    std::atomic<bool> f1Pressed{false};
    std::atomic<long long> firstF1Time{-1};
    std::atomic<bool> callbackFault{false};
    std::atomic<unsigned> callbackCauseMask{0};
  };
  const auto inputs = std::make_shared<Inputs>();
  const auto readInputs = [&] {std::lock_guard<std::mutex> guard(inputs->mutex); return inputs->sample;};
  bool publisherInitialized = false;
  bool handlerStarted = false;
  bool actuatorTouched = false;
  bool customObserved = false;
  bool handlerDampingAck = false;
  bool finalDampingConfirmed = false;
  bool f1TriggeredExit = false;
  bool shutdownFault = false;
  long long customConfirmationTime = -1;
  long long activeDeadlineTime = -1;
  const char* reason = "STARTUP_DENIED";
  int result = EXIT_FAILURE;
  printf("COMMISSION EMERGENCY_ONLY duration=%u ZERO_PD_ONLY NO_ROBOT NO_MODELS NO_WALK_NO_KICK\n", durationSeconds);
  signal(SIGINT, commissionSignalHandler);
  signal(SIGTERM, commissionSignalHandler);
  emergencyHandler.enableEmergencyOnlyCommissioning();

  booster::robot::ChannelSubscriber<booster_interface::msg::ButtonEventMsg> buttons("rt/button_even2", [inputs, clockMs](const void* message)
  {
    if(!inputs->accepting.load() || !message)
      return;
    try
    {
      const auto& value = *static_cast<const booster_interface::msg::ButtonEventMsg*>(message);
      // All other buttons, PRESS_UP and LONG_PRESS are deliberately ignored.
      if(value.button() == 0 && value.event() == booster_interface::msg::PRESS_DOWN)
      {
        long long unset = -1;
        inputs->firstF1Time.compare_exchange_strong(unset, clockMs());
        inputs->f1Pressed.store(true);
        emergencyHandler.latchCommissionEmergency();
      }
    }
    catch(...)
    {
      inputs->callbackCauseMask.fetch_or(buttonCallbackException);
      inputs->callbackFault.store(true);
      emergencyHandler.latchCommissionEmergency();
    }
  });
  booster::robot::ChannelSubscriber<booster_interface::msg::LowState> low("rt/low_state", [inputs, clockMs](const void* message)
  {
    if(!inputs->accepting.load() || !message)
      return;
    try
    {
      const auto& value = *static_cast<const booster_interface::msg::LowState*>(message);
      bool finite = true;
      for(size_t axis = 0; axis < 3; ++axis)
        finite &= std::isfinite(value.imu_state().rpy()[axis]) && std::isfinite(value.imu_state().gyro()[axis]) && std::isfinite(value.imu_state().acc()[axis]);
      for(const auto& motor : value.motor_state_serial())
        finite &= std::isfinite(motor.q()) && std::isfinite(motor.dq());
      for(const auto& motor : value.motor_state_parallel())
        finite &= std::isfinite(motor.q()) && std::isfinite(motor.dq()) && std::isfinite(motor.tau_est());
      const bool healthy = finite && value.motor_state_serial().size() == 22 && value.motor_state_parallel().size() == 22;
      {
        std::lock_guard<std::mutex> guard(inputs->mutex);
        inputs->sample.lowTime = clockMs();
        inputs->sample.serialJoints = value.motor_state_serial().size();
        inputs->sample.parallelJoints = value.motor_state_parallel().size();
        inputs->sample.finite = finite;
      }
      if(inputs->armed.load() && !healthy)
      {
        inputs->callbackCauseMask.fetch_or(commissionLowFaultCauses(finite, value.motor_state_serial().size(), value.motor_state_parallel().size()));
        inputs->callbackFault.store(true);
        emergencyHandler.latchCommissionEmergency();
      }
      if(healthy)
        emergencyHandler.lowStateReceived();
    }
    catch(...)
    {
      inputs->callbackCauseMask.fetch_or(lowCallbackException);
      inputs->callbackFault.store(true);
      emergencyHandler.latchCommissionEmergency();
    }
  });
  booster::robot::ChannelSubscriber<booster_interface::msg::RobotStatusDdsMsg> status("rt/device_gateway", [inputs, clockMs](const void* message)
  {
    if(!inputs->accepting.load() || !message)
      return;
    try
    {
      const auto& value = *static_cast<const booster_interface::msg::RobotStatusDdsMsg*>(message);
      size_t connected = 0;
      for(const auto& joint : value.joint_vec())
        connected += joint.is_connected() ? 1 : 0;
      {
        std::lock_guard<std::mutex> guard(inputs->mutex);
        inputs->sample.statusTime = clockMs();
        inputs->sample.reportedJoints = value.joint_vec().size();
        inputs->sample.connectedJoints = connected;
      }
      if(inputs->armed.load() && (connected != 22 || value.joint_vec().size() != 22))
      {
        inputs->callbackCauseMask.fetch_or(commissionStatusFaultCauses(value.joint_vec().size(), connected));
        inputs->callbackFault.store(true);
        emergencyHandler.latchCommissionEmergency();
      }
    }
    catch(...)
    {
      inputs->callbackCauseMask.fetch_or(statusCallbackException);
      inputs->callbackFault.store(true);
      emergencyHandler.latchCommissionEmergency();
    }
  });

  try
  {
    const auto execute = [&]
    {
      std::ifstream serialFile("/sys/firmware/devicetree/base/serial-number", std::ios::binary);
      std::string actualSerial;
      std::getline(serialFile, actualSerial, '\0');
      while(!actualSerial.empty() && (actualSerial.back() == '\n' || actualSerial.back() == '\r'))
        actualSerial.pop_back();
      if(!validCommissionSerial(expectedSerial) || actualSerial != expectedSerial)
      {reason = "DENIED_PHYSICAL_SERIAL_MISMATCH_OR_MISSING"; return;}
      printf("COMMISSION PHYSICAL_SERIAL_MATCH_CONFIRMED\n");
      std::ifstream identity("/opt/booster/robot_info.txt");
      std::string line;
      bool k1 = false;
      while(std::getline(identity, line))
        if(line.compare(0, 6, "Model:") == 0)
        {
          const auto first = line.find_first_not_of(" \t", 6);
          const auto last = line.find_last_not_of(" \t\r");
          k1 = first != std::string::npos && line.substr(first, last - first + 1) == "Booster K1";
        }
      if(!k1) {reason = "DENIED_NOT_VERIFIED_K1"; return;}
      sched_param requested{};
      requested.sched_priority = 21;
      int policy = 0;
      sched_param actual{};
      const bool realtime = !pthread_setschedparam(pthread_self(), SCHED_FIFO, &requested)
                            && !pthread_getschedparam(pthread_self(), &policy, &actual)
                            && policy == SCHED_FIFO && actual.sched_priority == 21;
      printf("COMMISSION MAIN_RT policy=%d priority=%d verified=%d\n", policy, actual.sched_priority, realtime);
      if(!realtime) {reason = "DENIED_REALTIME_PERMISSION"; return;}
      booster::robot::ChannelFactory::Instance()->Init(0, "127.0.0.1");
      client.Init();
      if(!client.WaitForService(5000, true))
      {reason = "DENIED_READONLY_SERVICE_DISCOVERY_TIMEOUT"; return;}
      booster::robot::b1::GetModeResponse mode;
      if(client.GetMode(mode) || mode.mode_ != booster::robot::RobotMode::kDamping)
      {reason = "DENIED_INITIAL_MODE_NOT_CONFIRMED_DAMPING"; return;}
      printf("COMMISSION INITIAL_DAMPING_CONFIRMED READONLY_PREFLIGHT\n");
      buttons.InitChannel();
      low.InitChannel();
      status.InitChannel();
      const auto preflightDeadline = Clock::now() + 8s;
      while(Clock::now() < preflightDeadline && !commissionSignal && !inputs->f1Pressed.load())
      {
        if(commissionInputsReady(readInputs(), clockMs()) && commissionButtonRouteReady(buttons))
          break;
        Thread::sleep(10);
      }
      if(commissionSignal || inputs->f1Pressed.load() || inputs->callbackFault.load()
         || !commissionInputsReady(readInputs(), clockMs()) || !commissionButtonRouteReady(buttons))
      {reason = "DENIED_FRESH_22_JOINTS_OR_BUTTON_ROUTE_OR_EARLY_F1"; return;}
      if(client.GetMode(mode) || mode.mode_ != booster::robot::RobotMode::kDamping
         || !commissionInputsReady(readInputs(), clockMs()) || inputs->f1Pressed.load()
         || inputs->callbackFault.load() || commissionSignal || emergencyHandler.commissioningLatched()
         || Clock::now() >= applicationStart + 45s)
      {reason = "DENIED_MODE_OR_INPUT_CHANGED_BEFORE_ARM"; return;}
      emergencyHandler.init(Settings::k1);
      publisherInitialized = true;
      const auto discoveryDeadline = Clock::now() + 3s;
      while(Clock::now() < discoveryDeadline && !emergencyHandler.commissioningLowCommandReaderReady()
            && !commissionSignal && !inputs->f1Pressed.load())
        Thread::sleep(10);
      if(!emergencyHandler.commissioningLowCommandReaderReady())
      {reason = "DENIED_LOW_COMMAND_HARDWARE_READER_NOT_DISCOVERED"; return;}
      if(client.GetMode(mode) || mode.mode_ != booster::robot::RobotMode::kDamping
         || !commissionInputsReady(readInputs(), clockMs()) || inputs->f1Pressed.load()
         || inputs->callbackFault.load() || commissionSignal || emergencyHandler.commissioningLatched()
         || Clock::now() >= applicationStart + 45s)
      {reason = "DENIED_MODE_OR_INPUT_CHANGED_BEFORE_ZERO_PD"; return;}
      printf("COMMISSION LOW_COMMAND_HARDWARE_READER_DISCOVERED ZERO_PD_WARMUP_IN_DAMPING\n");
      const auto warmupDeadline = Clock::now() + 500ms;
      unsigned zeroWrites = 0;
      long long firstZeroTime = -1;
      long long lastZeroTime = -1;
      actuatorTouched = true;
      while(Clock::now() < warmupDeadline)
      {
        if(!emergencyHandler.commissioningLowCommandReaderReady()
           || !commissionInputsReady(readInputs(), clockMs()) || inputs->f1Pressed.load()
           || inputs->callbackFault.load() || commissionSignal || emergencyHandler.commissioningLatched())
        {reason = "ZERO_PD_WARMUP_INPUT_OR_DISCOVERY_LOST"; return;}
        if(!emergencyHandler.resetMotorCommands()) {reason = "ZERO_PD_INITIAL_WRITE_FAILED"; return;}
        lastZeroTime = clockMs();
        if(!zeroWrites++)
          firstZeroTime = lastZeroTime;
        if(commissionZeroWarmupReady(zeroWrites, firstZeroTime, lastZeroTime))
          break;
        Thread::sleep(10);
      }
      if(!commissionZeroWarmupReady(zeroWrites, firstZeroTime, lastZeroTime))
      {reason = "ZERO_PD_WARMUP_TIMEOUT"; return;}
      printf("COMMISSION ZERO_PD_INITIALIZED joints=22 mode_q_dq_tau_kp_kd_weight=0 writes=%u elapsed_ms=%lld\n",
             zeroWrites, lastZeroTime - firstZeroTime);
      emergencyHandler.start();
      handlerStarted = true;
      const auto rtDeadline = Clock::now() + 1s;
      while(!emergencyHandler.commissioningRealtime() && Clock::now() < rtDeadline)
        Thread::sleep(5);
      if(emergencyHandler.commissioningRealtime() != 1 || client.GetMode(mode)
         || mode.mode_ != booster::robot::RobotMode::kDamping
         || !emergencyHandler.commissioningLowCommandReaderReady() || !commissionButtonRouteReady(buttons)
         || inputs->f1Pressed.load() || commissionSignal
         || inputs->callbackFault.load() || emergencyHandler.commissioningFault() || emergencyHandler.commissioningLatched()
         || !commissionInputsReady(readInputs(), clockMs()) || Clock::now() >= applicationStart + 45s)
      {reason = "DENIED_HANDLER_RT_OR_CHANGED_INPUT"; return;}
      inputs->armed.store(true);
      emergencyHandler.setState(EmergencyHandler::noEmergency);
      printf("COMMISSION REQUEST_CUSTOM ZERO_PD_HEARTBEAT_ACTIVE F1_LATCH_ONLY\n");
      const auto activeStart = Clock::now();
      // The 45s actuation budget includes read-only startup, leaving separate
      // bounded recovery time before the external service watchdog deadline.
      const auto deadline = std::min(activeStart + std::chrono::seconds(durationSeconds), applicationStart + 45s);
      activeDeadlineTime = std::chrono::duration_cast<std::chrono::milliseconds>(deadline.time_since_epoch()).count();
      auto nextMode = activeStart;
      reason = "TIMEOUT_NO_F1";
      while(Clock::now() < deadline)
      {
        if(inputs->f1Pressed.load()) {reason = "F1_PRESS_DOWN_LATCHED"; f1TriggeredExit = true; break;}
        if(commissionSignal) {reason = "SIGNAL_LATCHED"; break;}
        const bool diagnosticCallbackFault = inputs->callbackFault.load();
        const bool diagnosticEmergencyFault = emergencyHandler.commissioningFault();
        const auto diagnosticInput = readInputs();
        const long long diagnosticNow = clockMs();
        if(diagnosticCallbackFault || diagnosticEmergencyFault
           || !commissionInputsReady(diagnosticInput, diagnosticNow))
        {
          emergencyHandler.latchCommissionEmergency();
          reason = "INPUT_OR_CONTROL_FAULT_LATCHED";
          // The identical fault predicate and log share a single input snapshot,
          // so a newer healthy callback cannot hide the sample that caused exit.
          printf("COMMISSION FAULT_INPUT_SNAPSHOT now_ms=%lld low_age_ms=%lld status_age_ms=%lld serial=%zu parallel=%zu reported=%zu connected=%zu finite=%d callback_fault=%d emergency_fault=%d\n",
                 diagnosticNow, diagnosticInput.lowTime < 0 ? -1 : diagnosticNow - diagnosticInput.lowTime,
                 diagnosticInput.statusTime < 0 ? -1 : diagnosticNow - diagnosticInput.statusTime,
                 diagnosticInput.serialJoints, diagnosticInput.parallelJoints, diagnosticInput.reportedJoints,
                 diagnosticInput.connectedJoints, diagnosticInput.finite, diagnosticCallbackFault, diagnosticEmergencyFault);
          try
          {
            printf("COMMISSION FAULT_ROUTE_SNAPSHOT lowcmd_readers=%zu button_writers=%zu\n",
                   emergencyHandler.commissioningLowCommandReaders(), commissionButtonWriterCount(buttons));
          }
          catch(...) {printf("COMMISSION FAULT_ROUTE_SNAPSHOT query_exception=1\n");}
          printCommissionCallbackCauses(inputs->callbackCauseMask.load());
          emergencyHandler.printCommissioningFaultCounters();
          break;
        }
        if(Clock::now() >= nextMode)
        {
          if(client.GetMode(mode)) {reason = "MODE_QUERY_FAILED_LATCHED"; break;}
          if(mode.mode_ == booster::robot::RobotMode::kCustom)
          {
            if(!customObserved)
            {
              customConfirmationTime = clockMs();
              printf("COMMISSION CUSTOM_CONFIRMED ZERO_PD_ONLY PRESS_F1_ONCE_NO_OTHER_ACTION\n");
            }
            customObserved = true;
          }
          else if(inputs->f1Pressed.load()) {reason = "F1_PRESS_DOWN_LATCHED"; f1TriggeredExit = true; break;}
          else if(customObserved || mode.mode_ != booster::robot::RobotMode::kDamping)
          {reason = "UNEXPECTED_MODE_LATCHED"; break;}
          nextMode = Clock::now() + 100ms;
        }
        if(!customObserved && Clock::now() - activeStart > 3s)
        {reason = "CUSTOM_NOT_CONFIRMED_LATCHED"; break;}
        Thread::sleep(10);
      }
    };
    execute();
  }
  catch(...) {reason = "EXCEPTION_LATCHED";}

  printf("COMMISSION EXIT_REQUEST reason=%s custom_observed=%d f1=%d\n", reason, customObserved, inputs->f1Pressed.load());
  if(actuatorTouched)
  {
    emergencyHandler.latchCommissionEmergency();
    const auto recoverDeadline = Clock::now() + 4s;
    while(handlerStarted && Clock::now() < recoverDeadline)
    {
      try
      {
        booster::robot::b1::GetModeResponse mode;
        if(!client.GetMode(mode) && mode.mode_ == booster::robot::RobotMode::kDamping
           && emergencyHandler.commissioningDampingAcknowledged())
        {handlerDampingAck = true; break;}
      }
      catch(...) {}
      Thread::sleep(20);
    }
    printf("COMMISSION REAL_EMERGENCY_HANDLER_DAMPING_ACK=%d\n", handlerDampingAck);
  }
  if(publisherInitialized)
  {
    bool handlerExited = false;
    try {handlerExited = emergencyHandler.stopCommissioning(2000);}
    catch(...) {}
    if(!handlerExited)
    {
      // A late/blocked RPC may still own the SDK and may complete Custom late.
      // Never race it with CloseChannel/final RPCs or asynchronous cancellation.
      // The independently supervised recovery process must kill this unit,
      // reassert Damping, confirm GetMode=0, then restore the original routing.
      printf("COMMISSION HANDLER_EXIT_UNCONFIRMED RECOVERY_REQUIRED EXTERNAL_DAMPING_WATCHDOG_REQUIRED\n");
      fflush(stdout);
      fflush(stderr);
      std::_Exit(EXIT_FAILURE);
    }
  }
  // The handler can no longer issue an in-flight Custom request. Reassert and
  // confirm Damping last, so an early F1 cannot race a pending Custom reply.
  if(actuatorTouched)
  {
    printf("COMMISSION FINAL_DAMPING_REQUEST ONLY_RECOVERY_MODE_CHANGE\n");
    for(unsigned attempt = 0; attempt < 3 && !finalDampingConfirmed; ++attempt)
      try
      {
        booster::robot::b1::GetModeResponse mode;
        finalDampingConfirmed = !client.ChangeMode(booster::robot::RobotMode::kDamping)
                                 && !client.GetMode(mode) && mode.mode_ == booster::robot::RobotMode::kDamping;
      }
      catch(...) {}
    const auto f1Time = inputs->firstF1Time.load();
    result = commissionF1WindowValid(customObserved, f1TriggeredExit, f1Time, customConfirmationTime, activeDeadlineTime)
             && handlerDampingAck && finalDampingConfirmed && !shutdownFault
             && !inputs->callbackFault.load() && !emergencyHandler.commissioningFault() && !commissionSignal
             ? EXIT_SUCCESS : EXIT_FAILURE;
  }
  inputs->accepting.store(false);
  try {buttons.CloseChannel();} catch(...) {shutdownFault = true;}
  try {low.CloseChannel();} catch(...) {shutdownFault = true;}
  try {status.CloseChannel();} catch(...) {shutdownFault = true;}
  if(shutdownFault)
    result = EXIT_FAILURE;
  emergencyHandler.printCommissioningFaultCounters();
  printf("COMMISSION RESULT=%s final_damping_confirmed=%d NO_COMMISSIONED_MARKER_CREATED\n",
         !result ? "PASS_F1_ZERO_PD_ONLY" : "FAIL_OR_DENIED", finalDampingConfirmed);
  if(actuatorTouched && (!finalDampingConfirmed || shutdownFault))
    printf("COMMISSION DANGER_DAMPING_UNCONFIRMED EXTERNAL_RECOVERY_AND_ONSITE_CHECK_REQUIRED\n");
  fflush(stdout);
  fflush(stderr);
  std::_Exit(result);
}

static bool parseAppSeconds(const char* text, unsigned& result)
{
  if(!text || !*text) return false;
  unsigned value = 0;
  for(const char* p = text; *p; ++p)
  {
    if(*p < '0' || *p > '9') return false;
    value = value * 10 + static_cast<unsigned>(*p - '0');
    if(value > 900) return false;
  }
  if(!value) return false;
  result = value;
  return true;
}

// Full framework, but never the normal Custom/stand/walk startup path.
// The immutable process flag is enforced again in MotionEngine and at the
// final hardware writer, independent of configuration, input and debug state.
[[noreturn]] static void runAppDampingOnly(const char* directory, const char* expectedSerial, unsigned seconds,
                                        bool remoteStopX = false)
{
  using namespace BoosterAppCommissioning;
  using Clock = std::chrono::steady_clock;
  using namespace std::chrono_literals;
  const auto begin = Clock::now();
  const auto nowNs = [] {return std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch()).count();};
  std::ifstream serialFile("/sys/firmware/devicetree/base/serial-number", std::ios::binary);
  std::string actualSerial;
  std::getline(serialFile, actualSerial, '\0');
  if(!expectedSerial || actualSerial != "1424725032007" || actualSerial != expectedSerial)
  {fprintf(stderr, "APP_COMMISSION DENIED_SERIAL\n"); std::_Exit(EXIT_FAILURE);}
  if(!directory || !*directory || chdir(directory))
  {fprintf(stderr, "APP_COMMISSION DENIED_DIRECTORY\n"); std::_Exit(EXIT_FAILURE);}
  // Arm before FunctionList, settings, Robot or any module can be constructed.
  enable();
  if(remoteStopX) enableRemoteStopX();
  commissionSignal = 0;
  signal(SIGINT, commissionSignalHandler);
  signal(SIGTERM, commissionSignalHandler);
  std::atomic<bool> finished{false};
  std::atomic<unsigned> modeFailures{0};
  std::atomic<bool> sdkReady{false};
  bool frameworkReady = false;
  long long frameworkReadyAtNs = 0;
  bool dampingBeforeStop = false;
  bool finalDamping = false;
  bool robotStopped = false;
  bool buttonsOpened = false;
  bool lowOpened = false;
  struct Inputs {std::atomic<long long> lastNs{0}; std::atomic<bool> invalid{false};};
  const auto input = std::make_shared<Inputs>();
  booster::robot::ChannelSubscriber<booster_interface::msg::ButtonEventMsg> buttons("rt/button_even2", [](const void* data)
  {
    if(!data) return;
    const auto& event = *static_cast<const booster_interface::msg::ButtonEventMsg*>(data);
    if(event.button() == 0 && event.event() == booster_interface::msg::PRESS_DOWN)
      requestStop(StopReason::f1); // release/long press and other keys cannot unlock.
  });
  booster::robot::ChannelSubscriber<booster_interface::msg::LowState> low("rt/low_state", [input, nowNs](const void* data)
  {
    if(!data || !validLowState(*static_cast<const booster_interface::msg::LowState*>(data)))
    {input->invalid.store(true); requestStop(StopReason::inputFault); return;}
    input->lastNs.store(nowNs());
  });
  // Independent from Robot construction/model loading and Robot::stop. No log
  // I/O on this deadline path; the service's independent recovery is final.
  std::thread watchdog([&]
  {
    auto stopAt = Clock::time_point::max();
    auto nextMode = Clock::now();
    while(!finished.load())
    {
      if(commissionSignal) requestStop(StopReason::signal);
      if(Clock::now() - begin >= std::chrono::seconds(seconds)) requestStop(StopReason::deadline);
      if(sdkReady.load() && Clock::now() >= nextMode)
      {
        try
        {
          booster::robot::b1::GetModeResponse mode;
          if(client.GetMode(mode) || mode.mode_ != booster::robot::RobotMode::kDamping)
          {
            modeFailures.fetch_add(1);
            requestStop(StopReason::inputFault);
            client.ChangeMode(booster::robot::RobotMode::kDamping);
          }
        }
        catch(...) {modeFailures.fetch_add(1); requestStop(StopReason::exception);}
        nextMode = Clock::now() + 250ms;
      }
      if((lowStateFrames() && nowNs() - lastLowStateTimeNs() > 500000000LL)
         || (providerInputReadyTimeNs() && !lowStateFrames()
             && nowNs() - providerInputReadyTimeNs() > 2000000000LL))
        requestStop(StopReason::inputFault);
      if(stopRequested())
      {
        if(stopAt == Clock::time_point::max()) stopAt = Clock::now();
        // Do not asynchronously cancel C++ threads or block on their SDK locks.
        if(Clock::now() - stopAt > 8s) std::_Exit(EXIT_FAILURE);
      }
      std::this_thread::sleep_for(20ms);
    }
  });
  try
  {
    printf("APP_COMMISSION DAMPING_ONLY_LOCKED SERIAL22_ALL_FIELDS_ZERO NO_STAND_GETUP_WALK_KICK seconds=%u remote_stop_x=%d\n", seconds, remoteStopX);
    sched_param priority{};
    priority.sched_priority = 21;
    if(pthread_setschedparam(pthread_self(), SCHED_FIFO, &priority))
      throw std::runtime_error("APP_REALTIME_PERMISSION");
    booster::robot::ChannelFactory::Instance()->Init(0, "127.0.0.1");
    priority.sched_priority = 0;
    if(pthread_setschedparam(pthread_self(), SCHED_OTHER, &priority))
      throw std::runtime_error("APP_REALTIME_RESET");
    client.Init();
    if(!client.WaitForService(5000, true)) throw std::runtime_error("APP_SERVICE_DISCOVERY");
    booster::robot::b1::GetModeResponse mode;
    if(client.GetMode(mode) || mode.mode_ != booster::robot::RobotMode::kDamping)
      throw std::runtime_error("APP_INITIAL_MODE_NOT_DAMPING");
    if(client.ChangeMode(booster::robot::RobotMode::kDamping) || client.GetMode(mode)
       || mode.mode_ != booster::robot::RobotMode::kDamping)
      throw std::runtime_error("APP_INITIAL_DAMPING_UNCONFIRMED");
    buttons.InitChannel(); buttonsOpened = true;
    low.InitChannel(); lowOpened = true;
    const auto inputDeadline = Clock::now() + 5s;
    while(Clock::now() < inputDeadline && !stopRequested())
    {
      if(input->lastNs.load() && nowNs() - input->lastNs.load() < 100000000LL
         && commissionButtonRouteReady(buttons)) break;
      std::this_thread::sleep_for(10ms);
    }
    if(stopRequested() || input->invalid.load() || !input->lastNs.load()
       || nowNs() - input->lastNs.load() > 100000000LL || !commissionButtonRouteReady(buttons))
      throw std::runtime_error("APP_INPUT_OR_F1_ROUTE");
    low.CloseChannel(); lowOpened = false;
    sdkReady.store(true);
    BH_TRACE_INIT("appDampingOnly");
    FunctionList::execute();
    const std::string name = SystemCall::getHostName();
    Settings settings(name, Constants::motionCycleTime, getRobotType(name));
    if(settings.robotType != Settings::k1 || settings.playerNumber < 0 || settings.robotName.empty())
      throw std::runtime_error("APP_INVALID_K1_SETTINGS");
    printf("APP_COMMISSION STARTING_FRAMEWORK scenario=%s location=%s team=%d player=%d\n",
           settings.scenario.c_str(), settings.location.c_str(), settings.teamNumber, settings.playerNumber);
    if(!stopRequested())
    {
      bhumanStart(settings);
    }
    printf("APP_COMMISSION FRAMEWORK_STARTED_DAMPING_ONLY\n");
    auto nextReport = Clock::now();
    while(!stopRequested())
    {
      if(!frameworkReady && motionFrames() >= 5 && outputFrames() >= 5
         && cameraFrames() >= 5 && behaviorFrames() >= 5)
      {
        frameworkReady = true;
        frameworkReadyAtNs = nowNs();
        printf("APP_COMMISSION FRAMEWORK_READY_DAMPING_ONLY %s\n",
               remoteStopX ? "REMOTE_X_TEST_NEUTRAL_THEN_PRESS_X" : "PRESS_F1_ONCE");
      }
      if(Clock::now() >= nextReport)
      {
        printf("APP_COMMISSION FRAMES motion=%llu output=%llu camera=%llu behavior=%llu low=%llu mode_failures=%u remote_samples=%llu x_samples=%llu\n",
               static_cast<unsigned long long>(motionFrames()), static_cast<unsigned long long>(outputFrames()),
               static_cast<unsigned long long>(cameraFrames()), static_cast<unsigned long long>(behaviorFrames()),
               static_cast<unsigned long long>(lowStateFrames()), modeFailures.load(),
               static_cast<unsigned long long>(remoteFrames()), static_cast<unsigned long long>(remoteXPresses()));
        nextReport = Clock::now() + 2s;
      }
      std::this_thread::sleep_for(20ms);
    }
  }
  catch(const std::exception& error)
  {requestStop(StopReason::exception); fprintf(stderr, "APP_COMMISSION ERROR %s\n", error.what());}
  catch(...) {requestStop(StopReason::exception); fprintf(stderr, "APP_COMMISSION ERROR unknown\n");}
  printf("APP_COMMISSION STOP_REQUEST reason=%u ready=%d\n", stopReason(), frameworkReady);
  // Reassert Damping before potentially blocking framework shutdown.
  try
  {
    booster::robot::b1::GetModeResponse mode;
    dampingBeforeStop = !client.ChangeMode(booster::robot::RobotMode::kDamping)
                        && !client.GetMode(mode) && mode.mode_ == booster::robot::RobotMode::kDamping;
  }
  catch(...) {}
  printf("APP_COMMISSION DAMPING_BEFORE_FRAMEWORK_STOP=%d\n", dampingBeforeStop);
  if(robot) bhumanStop();
  robotStopped = robot == nullptr;
  finished.store(true);
  watchdog.join();
  try {if(lowOpened) low.CloseChannel(); if(buttonsOpened) buttons.CloseChannel();} catch(...) {robotStopped = false;}
  try
  {
    booster::robot::b1::GetModeResponse mode;
    finalDamping = !client.ChangeMode(booster::robot::RobotMode::kDamping)
                   && !client.GetMode(mode) && mode.mode_ == booster::robot::RobotMode::kDamping;
  }
  catch(...) {}
  const bool remoteEvidence = remoteFrames() > 0 && remoteXFalseSamples() > 0
                              && firstRemoteXFalseTimeNs() > 0
                              && firstRemoteXFalseTimeNs() < firstRemoteXTimeNs()
                              && firstRemoteXTimeNs() >= frameworkReadyAtNs;
  const bool pass = frameworkReady
                    && stopReason() == static_cast<unsigned>(remoteStopX ? StopReason::remoteStop : StopReason::f1)
                    && (!remoteStopX || remoteEvidence)
                    && dampingBeforeStop && finalDamping && robotStopped && !modeFailures.load();
  if(remoteStopX)
    printf("APP_COMMISSION REMOTE_X_PROOF valid=%d neutral_ns=%lld press_ns=%lld ready_ns=%lld\n", remoteEvidence,
           static_cast<long long>(firstRemoteXFalseTimeNs()), static_cast<long long>(firstRemoteXTimeNs()), frameworkReadyAtNs);
  printf("APP_COMMISSION RESULT=%s final_damping=%d robot_stopped=%d NO_COMMISSIONED_MARKER_CREATED\n",
         pass ? (remoteStopX ? "PASS_FULL_FRAMEWORK_REMOTE_X_DAMPING_ONLY" : "PASS_FULL_FRAMEWORK_DAMPING_ONLY")
              : "INCOMPLETE_OR_FAILED", finalDamping, robotStopped);
  fflush(stdout); fflush(stderr);
  std::_Exit(pass ? EXIT_SUCCESS : EXIT_FAILURE);
}

int main(int argc, char* argv[])
{
  booster::robot::ChannelSubscriber<booster_interface::msg::ButtonEventMsg> buttonEventSubscriber("rt/button_even2", &buttonEventHandler);
  booster::robot::ChannelSubscriber<booster_interface::msg::LowState> lowStateSubscriber(booster::robot::b1::kTopicLowState, &lowStateHandler);
  booster::robot::ChannelSubscriber<booster_interface::msg::RobotStatusDdsMsg> robotStatusSubscriber("rt/device_gateway", &robotStatusHandler);
  booster::robot::ChannelSubscriber<booster_interface::msg::RemoteControllerState> liveRemoteSubscriber("rt/remote_controller_stat2", [](const void* message)
  {
    if(!BoosterOfficialLiveTest::enabled()) return;
    if(message)
    {
      const auto& input = *static_cast<const booster_interface::msg::RemoteControllerState*>(message);
      BoosterOfficialLiveTest::observeRemoteStopX(
          input, booster::robot::b1::REMOVE);
      if(BoosterRemoteStand::enabled() && !BoosterOfficialLiveTest::stopRequested())
      {
        if(input.event() == booster::robot::b1::REMOVE)
          printf("REMOTE_LINK_LOST policy=STAND_NO_AUTO_RESUME generation=%llu\n",
                 static_cast<unsigned long long>(BoosterRemoteStand::generation()));
        else
        {
          const unsigned buttons = (input.x() ? 1u : 0u) | (input.a() ? 2u : 0u)
              | (input.b() ? 4u : 0u) | (input.y() ? 8u : 0u) | (input.lb() ? 16u : 0u)
              | (input.rb() ? 32u : 0u) | (input.lt() ? 64u : 0u) | (input.rt() ? 128u : 0u)
              | (input.back() ? 256u : 0u) | (input.start() ? 512u : 0u)
              | (input.ls() ? 1024u : 0u) | (input.rs() ? 2048u : 0u);
          const bool neutralAxes = std::abs(input.lx()) <= .1f && std::abs(input.ly()) <= .1f
              && std::abs(input.rx()) <= .1f && std::abs(input.ry()) <= .1f
              && !(input.hat_l() || input.hat_lu() || input.hat_ld()
                   || input.hat_r() || input.hat_ru() || input.hat_rd() || input.hat_u() || input.hat_d());
          BoosterRemoteStand::observeSample(buttons, neutralAxes);
        }
      }
    }
    else
      BoosterOfficialLiveTest::requestStop();
    if(BoosterOfficialLiveTest::stopRequested()) Main::stopOfficialLiveTest();
  });
  bool watchdog = false;
  bool commissionEmergencyOnly = false;
  bool commissionSecondsSet = false;
  bool commissionAppDampingOnly = false;
  bool remoteStopX = false;
  bool officialLiveTest = false;
  bool officialMatchMode = false;
  bool officialMatch987 = false;
  bool appSecondsSet = false;
  unsigned appSeconds = 600;
  unsigned commissionSeconds = 30;
  const char* commissionExpectedSerial = nullptr;
  {
    // Set stdout to be unbuffered.
    // This has previously been done using stdbuf, but this does not work for a 64-bit program on a 32-bit system.
    setvbuf(stdout, nullptr, _IONBF, 0);
    setvbuf(stderr, nullptr, _IONBF, 0);

    mainThread = pthread_self();

    // parse command-line arguments
    const char* bhDir = "/home/booster";

    for(int i = 1; i < argc; ++i)
      if(!strcmp(argv[i], "-c") && i + 1 < argc)
        bhDir = argv[++i];
      else if(!strcmp(argv[i], "-w"))
        watchdog = true;
      else if(!strcmp(argv[i], "--k1-987-match"))
      {
        if(officialLiveTest) return EXIT_FAILURE;
        officialLiveTest = true;
        officialMatchMode = true;
        officialMatch987 = true;
      }
      else if(!strcmp(argv[i], "--k1-996-live-test"))
      {
        if(officialLiveTest) return EXIT_FAILURE;
        officialLiveTest = true;
      }
      else if(!strcmp(argv[i], "--k1-996-match"))
      {
        if(officialLiveTest) return EXIT_FAILURE;
        officialLiveTest = true;
        officialMatchMode = true;
      }
      else if(!strcmp(argv[i], "--commission-emergency-only"))
        commissionEmergencyOnly = true;
      else if(!strcmp(argv[i], "--commission-app-damping-only"))
        commissionAppDampingOnly = true;
      else if(!strcmp(argv[i], "--commission-remote-stop-x"))
      {
        if(remoteStopX)
        {fprintf(stderr, "APP_COMMISSION DENIED_DUPLICATE_REMOTE_STOP\n"); return EXIT_FAILURE;}
        remoteStopX = true;
      }
      else if(!strcmp(argv[i], "--app-seconds") && i + 1 < argc)
      {
        if(appSecondsSet || !parseAppSeconds(argv[++i], appSeconds))
        {fprintf(stderr, "APP_COMMISSION DENIED_DURATION\n"); return EXIT_FAILURE;}
        appSecondsSet = true;
      }
      else if(!strcmp(argv[i], "--commission-expected-serial") && i + 1 < argc)
      {
        if(commissionExpectedSerial || !validCommissionSerial(argv[++i]))
        {
          fprintf(stderr, "COMMISSION DENIED expected physical serial must be explicit numeric text, once\n");
          return EXIT_FAILURE;
        }
        commissionExpectedSerial = argv[i];
      }
      else if(!strcmp(argv[i], "--commission-seconds") && i + 1 < argc)
      {
        if(commissionSecondsSet || !parseCommissionSeconds(argv[++i], commissionSeconds))
        {
          fprintf(stderr, "COMMISSION DENIED duration must be an integer from 1 to 45 seconds, once\n");
          return EXIT_FAILURE;
        }
        commissionSecondsSet = true;
      }
      else
      {
        fprintf(stderr, "Usage: %s [-c <dir>] [-w] | [--commission-emergency-only --commission-expected-serial <serial> [--commission-seconds 1..45]]\n\
    -c <dir>      used bh directory (default is /home/booster)\n\
    -w            use a watchdog for crash recovery and creating trace dumps\n", argv[0]);
        exit(EXIT_FAILURE);
      }

    // avoid duplicated instances
    int fd = open("/tmp/bhuman", O_CREAT, 0600);
    if(fd == -1 || flock(fd, LOCK_EX | LOCK_NB) == -1)
    {
      fprintf(stderr, "There is already an instance of this process!\n");
      exit(EXIT_FAILURE);
    }

    if(((commissionSecondsSet || commissionExpectedSerial) && !commissionEmergencyOnly && !commissionAppDampingOnly)
       || (commissionEmergencyOnly && (watchdog || !commissionExpectedSerial || commissionAppDampingOnly || appSecondsSet))
       || (commissionAppDampingOnly && (watchdog || !commissionExpectedSerial || commissionSecondsSet))
       || (appSecondsSet && !commissionAppDampingOnly))
    {
      fprintf(stderr, "COMMISSION DENIED emergency-only requires explicit physical serial; normal watchdog/fork is not permitted\n");
      return EXIT_FAILURE;
    }
    if(remoteStopX && (!commissionAppDampingOnly || !appSecondsSet || appSeconds > 180))
    {
      fprintf(stderr, "APP_COMMISSION DENIED_REMOTE_STOP_SCOPE requires damping-only and explicit 1..180 seconds\n");
      return EXIT_FAILURE;
    }
    if(officialMatch987)
    {
      if(watchdog || commissionEmergencyOnly || commissionAppDampingOnly || remoteStopX
         || commissionSecondsSet || appSecondsSet || commissionExpectedSerial)
      {fprintf(stderr, "K1_987_MATCH DENIED_MIXED_SCOPE\n"); return EXIT_FAILURE;}
      std::ifstream serialFile("/sys/firmware/devicetree/base/serial-number", std::ios::binary);
      std::string actualSerial;
      std::getline(serialFile, actualSerial, '\0');
      if(!match987IdentityAllowed(actualSerial, bhDir))
      {fprintf(stderr, "K1_987_MATCH DENIED_IDENTITY_OR_DIRECTORY\n"); return EXIT_FAILURE;}
      BoosterOfficialLiveTest::enable();
      BoosterMatchSafety::enable();
      BoosterRemoteStand::enable();
      signal(SIGINT, sigHandlerOfficialLiveShutdown);
      signal(SIGTERM, sigHandlerOfficialLiveShutdown);
      printf("K1_987_MATCH ENTRY GC_SOURCE=192.168.50.37 FRESH_F1_X_STOP NATIVE_GC_ENTRY READY_AUTO_HANDOFF MANUAL_OVERRIDE_ALLOWED NO_FIXED_RUNTIME_LIMIT\n");
      printf("K1_987_MATCH FROM_996_37A2C4BA REMOTE_REMOVE_STAND_HOLD MANUAL_RECOVERY_INITIAL_READY_PLAY\n");
    }
    else if(officialLiveTest)
    {
      if(watchdog || commissionEmergencyOnly || commissionAppDampingOnly || remoteStopX
         || commissionSecondsSet || appSecondsSet || commissionExpectedSerial)
      {fprintf(stderr, "K1_OFFICIAL_LIVE DENIED_MIXED_SCOPE\n"); return EXIT_FAILURE;}
      std::ifstream serialFile("/sys/firmware/devicetree/base/serial-number", std::ios::binary);
      std::string actualSerial;
      std::getline(serialFile, actualSerial, '\0');
      if(actualSerial != "1424725032007")
      {fprintf(stderr, "K1_OFFICIAL_LIVE DENIED_SERIAL\n"); return EXIT_FAILURE;}
      BoosterOfficialLiveTest::enable();
      if(officialMatchMode)
      {
        BoosterMatchSafety::enable();
        BoosterRemoteStand::enable();
        printf("K1_996_MATCH ENTRY GC_SOURCE=192.168.50.37 FRESH_F1_X_STOP NATIVE_GC_ENTRY READY_AUTO_HANDOFF MANUAL_OVERRIDE_ALLOWED NO_FIXED_RUNTIME_LIMIT\n");
        printf("K1_996_MATCH REMOTE_REMOVE_STAND_HOLD MANUAL_RECOVERY_INITIAL_READY_PLAY\n");
      }
      signal(SIGINT, sigHandlerOfficialLiveShutdown);
      signal(SIGTERM, sigHandlerOfficialLiveShutdown);
      printf("K1_OFFICIAL_LIVE NORMAL_ENTRY OFFICIAL_MOTION_MODELS X_STOP_NO_REARM NO_COMMISSION_DAMPING_FENCE\n");
    }
    // Before configuration/module loading, watchdog fork, LED or Robot startup.
    if(commissionEmergencyOnly)
      runEmergencyOnlyCommission(commissionSeconds, commissionExpectedSerial);
    if(commissionAppDampingOnly)
    {
      if(remoteStopX)
        runAppDampingOnly(bhDir, commissionExpectedSerial, appSeconds, true);
      runAppDampingOnly(bhDir, commissionExpectedSerial, appSeconds);
    }

    // change working directory
    if(*bhDir && chdir(bhDir) != 0)
    {
      fprintf(stderr, "chdir to config directory failed!\n");
      exit(EXIT_FAILURE);
    }

    BH_TRACE_INIT("main");

    // Acquire static data, e.g. about types
    FunctionList::execute();

    const std::string name = SystemCall::getHostName();
    Settings settings(name, Constants::motionCycleTime, getRobotType(name));
    if(settings.playerNumber < 0 || settings.robotName.empty())
      return EXIT_FAILURE;

    // print status information
    printf("Hi, I am %s.\n", settings.robotName.c_str());
    printf("teamNumber %d\n", settings.teamNumber);
    printf("fieldPlayerColor %s\n", TypeRegistry::getEnumName(settings.fieldPlayerColor));
    printf("goalkeeperColor %s\n", TypeRegistry::getEnumName(settings.goalkeeperColor));
    printf("playerNumber %d\n", settings.playerNumber);
    printf("location %s\n", settings.location.c_str());
    printf("scenario %s\n", settings.scenario.c_str());
    printf("magicNumber %d\n", settings.magicNumber);

    struct Signal
    {
      const char* signalName = "/bhuman_body_connected";
      sem_t* signal;
      Signal() : signal(sem_open(signalName, O_CREAT, 0600, 0)) {}
      ~Signal() {if(signal) sem_unlink(signalName);}
    } connected;

    if(connected.signal == SEM_FAILED)
    {
      fprintf(stderr, "Creating semaphore failed!\n");
      return EXIT_FAILURE;
    }

    // the watchdog
    if(watchdog)
    {
      bhumanPid = fork();
      if(bhumanPid == -1)
        exit(EXIT_FAILURE);
    }

    if(bhumanPid)
    {
      signal(SIGTERM, sigHandlerRedirect);
      signal(SIGINT, sigHandlerRedirect);
      signal(SIGCHLD, sigHandlerIgnore);
    }

    if(!initInstance())
    {
      if(bhumanPid)
      {
        kill(bhumanPid, SIGTERM);
        sem_post(connected.signal);
      }
      return EXIT_FAILURE;
    }

    // Initialize the only instances in this process
    client.Init();
    Thread::sleep(100);
    if(BoosterOfficialLiveTest::enabled())
    {
      // X is subscribed before the official startup's Custom handshake, not
      // only when BoosterProvider is eventually constructed in Robot.
      liveRemoteSubscriber.InitChannel();
      const unsigned waitStart = Time::getCurrentSystemTime();
      while(run && !commissionButtonWriterCount(liveRemoteSubscriber)
            && Time::getTimeSince(waitStart) < 5000)
        Thread::sleep(10);
      if(!run || !commissionButtonWriterCount(liveRemoteSubscriber))
      {
        BoosterOfficialLiveTest::requestStop();
        client.ChangeMode(booster::robot::RobotMode::kDamping);
        fprintf(stderr, "K1_OFFICIAL_LIVE EARLY_X_ABORT_OR_REMOTE_MISSING\n");
        std::_Exit(EXIT_FAILURE);
      }
      printf("K1_OFFICIAL_LIVE EARLY_X_SUBSCRIBED_BEFORE_CUSTOM\n");
    }
    if(settings.robotType == Settings::k1)
    {
      ledControl.Init();
      ledControlAvailable = true;
    }

    if(!watchdog || bhumanPid)
    {
      emergencyHandler.init(settings.robotType);

      // Deactivate all motors before switching to custom mode, because robot would otherwise apply previous commands.
      if(!emergencyHandler.resetMotorCommands())
      {
        fprintf(stderr, "Resetting motor commands failed!\n");
        if(bhumanPid)
        {
          kill(bhumanPid, SIGTERM);
          sem_post(connected.signal);
        }
        return EXIT_FAILURE;
      }

      // Listen to the F1 button on the K1 or to disconnected joints on the T1.
      if(settings.robotType == Settings::k1)
      {
        buttonEventSubscriber.InitChannel();
        emergencyHandler.setState(EmergencyHandler::noEmergency);
      }
      else
      {
        emergencyHandler.state = EmergencyHandler::emergencyReleased;
        robotStatusSubscriber.InitChannel();
      }
      lowStateSubscriber.InitChannel();

      // Start handler for handling emergency states.
      emergencyHandler.start();

      // Wait until the handler switched to custom mode.
      const unsigned startTime = Time::getCurrentSystemTime();
      booster::robot::b1::GetModeResponse modeResponse;
      bool waitingForBody = false;
      while(run && Time::getTimeSince(startTime) < 60000)
      {
        Thread::sleep(100);
        if(!client.GetMode(modeResponse) && modeResponse.mode_ == booster::robot::RobotMode::kCustom)
        {
          // When booting up, custom mode might be disabled again after a while. Wait a bit.
          if(waitingForBody)
            Thread::sleep(5000);
          goto success;
        }
        else if(!waitingForBody)
        {
          SystemCall::say("Waiting for robot body");
          waitingForBody = true;
        }
      }

      SystemCall::say("Failed to connect to robot body. Terminating.");
      if(BoosterOfficialLiveTest::enabled())
      {
        BoosterOfficialLiveTest::requestStop();
        emergencyHandler.setState(EmergencyHandler::emergencyPressed);
        const int requestResult = client.ChangeMode(booster::robot::RobotMode::kDamping);
        booster::robot::b1::GetModeResponse lastMode;
        const int verifyResult = client.GetMode(lastMode);
        fprintf(stderr, "K1_OFFICIAL_LIVE STARTUP_ABORT request=%d verify=%d mode=%d EXTERNAL_FINAL_MODE_CHECK_REQUIRED\n",
                requestResult, verifyResult, verifyResult ? -1 : static_cast<int>(lastMode.mode_));
        std::_Exit(EXIT_FAILURE);
      }
      if(bhumanPid)
      {
        kill(bhumanPid, SIGTERM);
        sem_post(connected.signal);
      }
      return EXIT_FAILURE;

    success:
      // Switch K1 back into damping mode
      if(settings.robotType == Settings::k1)
      {
        Thread::sleep(100);
        emergencyHandler.setState(EmergencyHandler::emergencyReleased);
      }
    }

    if(bhumanPid)
    {
      // Signal successful connection
      sem_post(connected.signal);

      sigset_t sigSet = {0};
      sigemptyset(&sigSet);
      sigaddset(&sigSet, SIGCHLD);
      siginfo_t sigInfo;
      const timespec timeout =
      {
        .tv_sec = 1,
        .tv_nsec = 0,
      };

      // Wait for main bhuman process to terminate
      while(!timeWhenAskedToTerminate || Time::getTimeSince(timeWhenAskedToTerminate) < 5000)
      {
        std::memset(&sigInfo, 0, sizeof(sigInfo));
        if(sigtimedwait(&sigSet, &sigInfo, &timeout) != -1 || errno == EINTR)
          break;
        else if(errno != EAGAIN)
        {
          kill(bhumanPid, SIGTERM);
          return EXIT_FAILURE;
        }
      }

      signal(SIGTERM, SIG_DFL);
      signal(SIGINT, SIG_DFL);
      signal(SIGCHLD, SIG_DFL);

      // detect requested or normal exit
      bool normalExit = !run || (WIFEXITED(sigInfo.si_status) && WEXITSTATUS(sigInfo.si_status) == EXIT_SUCCESS);

      // dump trace and assert trace
      if(!normalExit)
      {
        // Dump to file first, because writing to stderr may fail for various reasons.
        Assert::logDump(false, WIFSIGNALED(sigInfo.si_status) ? int(WTERMSIG(sigInfo.si_status)) : 0);
        Assert::logDump(true, WIFSIGNALED(sigInfo.si_status) ? int(WTERMSIG(sigInfo.si_status)) : 0);
      }

      // These calls are allowed even if the handlers were not started.
      buttonEventSubscriber.CloseChannel();
      lowStateSubscriber.CloseChannel();
      robotStatusSubscriber.CloseChannel();
      emergencyHandler.stop();

      // Switch Booster back to damping mode.
      const int result = client.ChangeMode(booster::robot::RobotMode::kDamping);
      if(result)
      {
        fprintf(stderr, "Switching robot back to damping mode failed with error %d!\n", result);
        return EXIT_FAILURE;
      }

      // Deactivate custom LED control.
      if(ledControlAvailable)
      {
        const int result = ledControl.StopLEDLightControl();
        if(result)
        {
          fprintf(stderr, "Deactivating custom LED control failed with error %d!\n", result);
          return EXIT_FAILURE;
        }
      }

      // quit
      exit(WIFEXITED(sigInfo.si_status) && normalExit ? WEXITSTATUS(sigInfo.si_status) : EXIT_FAILURE);
    }

    // This part is only reachable in the main bhuman process

    // register signal handler for ctrl+c and termination signal
    signal(SIGTERM, BoosterOfficialLiveTest::enabled() ? sigHandlerOfficialLiveShutdown : sigHandlerShutdown);
    signal(SIGINT, BoosterOfficialLiveTest::enabled() ? sigHandlerOfficialLiveShutdown : sigHandlerShutdown);

    // Wait for successful connection if established by the watchdog
    if(watchdog && sem_wait(connected.signal) < 0)
      return EXIT_FAILURE;

    if(run)
      bhumanStart(settings);

    // settings go out of scope here, but everything that needs it later makes a copy.
  }

  while(run)
    if(BoosterOfficialLiveTest::enabled())
    {
      booster::robot::b1::GetModeResponse current;
      const int result = client.GetMode(current);
      const auto phase = static_cast<MotionPhase::Type>(BoosterOfficialLiveTest::lastPhase());
      const char* phaseName = BoosterOfficialLiveTest::outputFrames()
                               ? TypeRegistry::getEnumName(phase) : "not_ready";
      printf("K1_OFFICIAL_LIVE STATUS mode_result=%d mode=%d phase=%s frames=%llu peak_kp=%.2f stop=%d armed=%d\n",
             result, result ? -1 : static_cast<int>(current.mode_), phaseName ? phaseName : "unknown",
             static_cast<unsigned long long>(BoosterOfficialLiveTest::outputFrames()),
             BoosterOfficialLiveTest::maxKp(), BoosterOfficialLiveTest::stopRequested(),
             BoosterOfficialLiveTest::bodyAuthorized());
      const auto diagnostic = BoosterOfficialLiveTest::diagnostics();
      printf("K1_OFFICIAL_LIVE INPUT remote_callbacks=%llu event=%u buttons=%u key_frames=%llu keys=%u "
             "cognition_frames=%llu cognition_time=%u player_state=%u previous_player=%u stand_ms=%u walk_hits=%u damping=%d emergency=%d\n",
             static_cast<unsigned long long>(diagnostic.remoteCallbacks), diagnostic.remoteEvent, diagnostic.remoteButtons,
             static_cast<unsigned long long>(diagnostic.keyFrames), diagnostic.keyBits,
             static_cast<unsigned long long>(diagnostic.cognitionFrames), diagnostic.cognitionTime,
             diagnostic.playerState, diagnostic.previousPlayerState, diagnostic.standHeldMs, diagnostic.walkHitStreak,
             diagnostic.damping, diagnostic.systemEmergency);
      printf("K1_OFFICIAL_LIVE MOTION diagnostic_frames=%llu time=%u requested=%u effective=%u perception_time=%u "
             "gyro_finished=%d imu_bad=%d force_sit_down=%d\n",
             static_cast<unsigned long long>(diagnostic.motionDiagnosticFrames), diagnostic.motionTime,
             diagnostic.requestedMotion, diagnostic.effectiveMotion, diagnostic.olderPerceptionTime,
             diagnostic.offsetFinished, diagnostic.imuBad, diagnostic.forceSitDown);
      if(BoosterRemoteStand::enabled())
        printf("K1_REMOTE_LINK stand_hold=%d generation=%llu resume_choice=%u recovery_pending=%d\n",
               BoosterRemoteStand::held(), static_cast<unsigned long long>(BoosterRemoteStand::generation()),
               BoosterRemoteStand::resumeChoice(), BoosterRemoteStand::bodyRecoveryPending());
      for(unsigned n = 0; n < 20 && run; ++n) Thread::sleep(100);
    }
    else
      pause();

  if(BoosterOfficialLiveTest::enabled())
  {
    BoosterOfficialLiveTest::requestStop();
    emergencyHandler.setState(EmergencyHandler::emergencyPressed);
    const int requestResult = client.ChangeMode(booster::robot::RobotMode::kDamping);
    booster::robot::b1::GetModeResponse finalMode;
    const int verifyResult = client.GetMode(finalMode);
    printf("K1_OFFICIAL_LIVE DAMPING_BEFORE_FRAMEWORK_STOP request=%d verify=%d mode=%d\n",
           requestResult, verifyResult, verifyResult ? -1 : static_cast<int>(finalMode.mode_));
  }

  if(robot)
    bhumanStop();

  if(!watchdog)
  {
    // These calls are allowed even if the handlers were not started.
    buttonEventSubscriber.CloseChannel();
    lowStateSubscriber.CloseChannel();
    robotStatusSubscriber.CloseChannel();
    if(BoosterOfficialLiveTest::enabled()) liveRemoteSubscriber.CloseChannel();
    emergencyHandler.stop();

    // Switch Booster back to damping mode.
    const int result = client.ChangeMode(booster::robot::RobotMode::kDamping);
    if(result)
    {
      fprintf(stderr, "Switching robot back to damping mode failed with error %d!\n", result);
      return EXIT_FAILURE;
    }

    // Deactivate custom LED control.
    if(ledControlAvailable)
    {
      const int result = ledControl.StopLEDLightControl();
      if(result)
      {
        fprintf(stderr, "Deactivating custom LED control failed with error %d!\n", result);
        return EXIT_FAILURE;
      }
    }
  }

  if(BoosterOfficialLiveTest::enabled())
  {
    booster::robot::b1::GetModeResponse finalMode;
    const int result = client.GetMode(finalMode);
    printf("K1_OFFICIAL_LIVE EXIT robot_stopped=%d mode_result=%d mode=%d\n",
           robot == nullptr, result, result ? -1 : static_cast<int>(finalMode.mode_));
    fflush(stdout); fflush(stderr);
    std::_Exit(!result && finalMode.mode_ == booster::robot::RobotMode::kDamping && !robot ? EXIT_SUCCESS : EXIT_FAILURE);
  }

  return EXIT_SUCCESS;
}
