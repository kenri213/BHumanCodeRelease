#include "diagnostic_options.h"
#include <cstdlib>
#include <iostream>
#include <stdexcept>

int main()
{
  unsigned count = 0;
  const auto check = [&](bool condition)
  {
    ++count;
    if(!condition) throw std::runtime_error("diagnostic options assertion failed");
  };
  try
  {
    auto options = D455Diagnostic::parse({});
    check(!options.capture && options.frames == 30 && options.timeoutMs == 5000);
    options = D455Diagnostic::parse({"--serial", "123456", "--capture", "--frames", "15", "--timeout-ms", "2000", "--color-only"});
    check(options.serial == "123456" && options.capture && options.frames == 15 && options.timeoutMs == 2000 && options.colorOnly);
    check(D455Diagnostic::parse({"--all-profiles"}).allProfiles);
    check(D455Diagnostic::parse({"--help"}).help);
    check(D455Diagnostic::parse({"--timeout-ms", "250"}).timeoutMs == 250);
    const std::vector<std::vector<std::string>> invalid{
      {"--frames", "10"}, {"--capture", "--frames", "0"}, {"--capture", "--frames", "301"},
      {"--capture", "--frames", "-1"}, {"--capture", "--frames", "1.5"}, {"--capture", "--frames", "100000000000"},
      {"--timeout-ms", "249"}, {"--timeout-ms", "30001"}, {"--serial"}, {"--serial", ""},
      {"--serial", "--capture"}, {"--unknown"}, {"--capture", "--frames"}};
    for(const auto& args : invalid)
    {
      bool rejected = false;
      try { static_cast<void>(D455Diagnostic::parse(args)); }
      catch(const std::invalid_argument&) { rejected = true; }
      check(rejected);
    }
    std::cout << "D455 diagnostic options: " << count << " checks passed\n";
    return EXIT_SUCCESS;
  }
  catch(const std::exception& error)
  {
    std::cerr << error.what() << '\n';
    return EXIT_FAILURE;
  }
}
