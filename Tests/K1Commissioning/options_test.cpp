#include "ObserverOptions.h"

#include <cstdlib>
#include <iostream>
#include <string>
#include <vector>

namespace
{
unsigned checks = 0;
void check(bool condition)
{
  ++checks;
  if(!condition)
  {
    std::cerr << "Option parser check failed: " << checks << '\n';
    std::exit(1);
  }
}

bool accepted(const std::vector<std::string>& arguments)
{
  k1commissioning::ObserverOptions options;
  std::string error;
  return k1commissioning::parseObserverOptions(arguments, options, error);
}
}

int main()
{
  k1commissioning::ObserverOptions defaults;
  std::string error;
  check(k1commissioning::parseObserverOptions({}, defaults, error));
  check(defaults.durationSeconds == 10 && defaults.domainId == 0 && defaults.interfaceIp == "127.0.0.1");
  for(const auto* duration : {"0", "1", "10", "15", "60"})
    check(accepted({"--duration-seconds", duration}));
  for(const auto* duration : {"-1", "61", "1.5", "nan", "inf", "+1", "", "9999999999999999999999999"})
    check(!accepted({"--duration-seconds", duration}));
  for(const auto* domain : {"0", "1", "232"})
    check(accepted({"--domain-id", domain}));
  for(const auto* domain : {"-1", "233", "232a", "1.1", "99999999999"})
    check(!accepted({"--domain-id", domain}));
  for(const auto* address : {"127.0.0.1", "192.168.10.102", "192.168.50.68", "0.0.0.0", "255.255.255.255"})
    check(accepted({"--interface-ip", address}));
  for(const auto* address : {"localhost", "127.1", "127.0.0.1.", "127.0.0.", ".0.0.1", "256.0.0.1",
                             "127.00.0.1", "127.0.0.+1", "127.0.0.-1", "127.0.0.1;id", ""})
    check(!accepted({"--interface-ip", address}));
  for(const auto* option : {"--duration-seconds", "--domain-id", "--interface-ip"})
  {
    check(!accepted({option}));
    const std::string value = std::string(option) == "--interface-ip" ? "127.0.0.1" : "1";
    check(!accepted({option, value, option, value}));
  }
  check(!accepted({"--control", "on"}));
  check(!accepted({"15"}));
  check(accepted({"--help"}));
  check(accepted({"-h"}));
  check(accepted({"--stop-after-f1"}));
  check(!accepted({"--stop-after-f1", "--stop-after-f1"}));
  k1commissioning::ObserverOptions changed;
  check(k1commissioning::parseObserverOptions({"--duration-seconds", "15", "--domain-id", "2",
                                             "--interface-ip", "192.168.10.102"}, changed, error));
  check(changed.durationSeconds == 15 && changed.domainId == 2 && changed.interfaceIp == "192.168.10.102");
  std::cout << checks << " option checks passed; no SDK initialized\n";
}
