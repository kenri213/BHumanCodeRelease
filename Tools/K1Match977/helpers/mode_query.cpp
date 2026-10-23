// 977 SDK query-only process. No actuator or mode-setting operation is present.
#include "query_policy.h"
#include <booster/robot/b1/b1_loco_client.hpp>
#include <booster/robot/channel/channel_factory.hpp>
#include <chrono>
#include <csignal>
#include <cstdio>
#include <fstream>
#include <thread>
#include <unistd.h>

namespace
{
void deadline(int)
{
  constexpr char json[] = "{\"kind\":\"k1_mode_query_only\",\"result\":-124,\"mode\":null,\"error\":\"deadline_8_seconds\"}\n";
  const auto ignored = write(STDOUT_FILENO, json, sizeof(json) - 1);
  (void)ignored;
  _exit(124);
}

[[noreturn]] void finish(int result, int mode, unsigned attempts, const char* error, int status)
{
  // Keep the watchdog armed through output flushing and process exit.
  if(result == 0)
    std::printf("{\"kind\":\"k1_mode_query_only\",\"serial\":\"1424225066425\",\"result\":0,\"mode\":%d,\"damping\":%s,\"attempts\":%u}\n",
                mode, mode == 0 ? "true" : "false", attempts);
  else
    std::printf("{\"kind\":\"k1_mode_query_only\",\"result\":%d,\"mode\":null,\"attempts\":%u,\"error\":\"%s\"}\n",
                result, attempts, error);
  std::fflush(stdout);
  // Do not wait for SDK/destructor shutdown beyond the query deadline.
  _exit(status);
}
}

int main(int argc, char** argv)
{
  struct sigaction action {};
  action.sa_handler = deadline;
  if(sigemptyset(&action.sa_mask) != 0 || sigaction(SIGALRM, &action, nullptr) != 0)
    finish(-70, -1, 0, "deadline_setup_failed", 70);
  alarm(mode_query::deadlineSeconds);
  if(!mode_query::approvedArguments(argc, argv))
    finish(-2, -1, 0, "usage_query_only", 2);

  std::ifstream file("/sys/firmware/devicetree/base/serial-number", std::ios::binary);
  char raw[64] = {};
  file.getline(raw, sizeof(raw), '\0');
  if(file.fail() || !mode_query::approvedSerial(raw))
    finish(-3, -1, 0, "wrong_hardware_serial", 3);

  try
  {
    booster::robot::ChannelFactory::Instance()->Init(0, "127.0.0.1");
    booster::robot::b1::B1LocoClient client;
    client.Init();
    const auto result = mode_query::queryAtMostThree(
      [&client](int& value)
      {
        booster::robot::b1::GetModeResponse response;
        const int code = client.GetMode(response);
        if(code == 0)
          value = static_cast<int>(response.mode_);
        return code;
      },
      [] { std::this_thread::sleep_for(std::chrono::milliseconds(75)); });
    finish(result.result, result.mode, result.attempts, "query_failed", result.result == 0 ? 0 : 4);
  }
  catch(...)
  {
    finish(-5, -1, 0, "sdk_exception", 5);
  }
}
