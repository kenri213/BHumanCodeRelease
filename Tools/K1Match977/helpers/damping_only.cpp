// Explicitly ACTUATING emergency fallback: mode 0 only, never starts a policy.
// For the approved 977 lying-down commissioning session, not a read-only probe.
#include <booster/robot/b1/b1_loco_client.hpp>
#include <booster/robot/channel/channel_factory.hpp>
#include <chrono>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <string>
#include <thread>

int main(int argc, char** argv)
{
  if(argc != 2 || std::string(argv[1]) != "--force-damping-only")
  {
    std::cerr << "Usage: k1_damping_only --force-damping-only\n"
                 "WARNING: switches 977 to damping; support may be lost. No walking or joint targets.\n";
    return 2;
  }
  std::ifstream serialFile("/sys/firmware/devicetree/base/serial-number", std::ios::binary);
  std::string serial;
  std::getline(serialFile, serial, '\0');
  if(serial != "1424225066425")
  {
    std::cerr << "BLOCKED: not the approved 977 hardware serial\n";
    return 3;
  }
  std::thread([]
  {
    std::this_thread::sleep_for(std::chrono::seconds(8));
    std::_Exit(124);
  }).detach();
  booster::robot::ChannelFactory::Instance()->Init(0, "127.0.0.1");
  booster::robot::b1::B1LocoClient client;
  client.Init();
  const int requested = client.ChangeMode(booster::robot::RobotMode::kDamping);
  std::cout << "DAMPING_ONLY request_result=" << requested << std::endl;
  for(unsigned attempt = 0; attempt < 3; ++attempt)
  {
    booster::robot::b1::GetModeResponse response;
    const int result = client.GetMode(response);
    std::cout << "DAMPING_ONLY verify_result=" << result;
    if(result == 0)
      std::cout << " mode=" << static_cast<int>(response.mode_);
    std::cout << std::endl;
    if(result == 0 && response.mode_ == booster::robot::RobotMode::kDamping)
      std::_Exit(0);
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
  }
  std::_Exit(4);
}
