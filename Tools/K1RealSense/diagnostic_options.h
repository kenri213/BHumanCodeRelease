#pragma once
#include <stdexcept>
#include <string>
#include <vector>

namespace D455Diagnostic
{
  struct Options
  {
    bool help = false;
    bool capture = false;
    bool colorOnly = false;
    bool allProfiles = false;
    unsigned frames = 30;
    unsigned timeoutMs = 5000;
    std::string serial;
  };

  inline unsigned number(const std::string& text, unsigned maximum, const std::string& name)
  {
    if(text.empty() || text.size() > 6)
      throw std::invalid_argument(name + ": invalid positive integer");
    unsigned value = 0;
    for(const char c : text)
    {
      if(c < '0' || c > '9')
        throw std::invalid_argument(name + ": invalid positive integer");
      value = value * 10 + static_cast<unsigned>(c - '0');
    }
    if(!value || value > maximum)
      throw std::invalid_argument(name + ": value out of bounds");
    return value;
  }

  inline Options parse(const std::vector<std::string>& args)
  {
    Options options;
    bool captureParameter = false;
    for(std::size_t i = 0; i < args.size(); ++i)
    {
      const auto& arg = args[i];
      if(arg == "--help" || arg == "-h") options.help = true;
      else if(arg == "--capture") options.capture = true;
      else if(arg == "--color-only") options.colorOnly = true;
      else if(arg == "--all-profiles") options.allProfiles = true;
      else if(arg == "--serial" || arg == "--frames" || arg == "--timeout-ms")
      {
        if(++i == args.size()) throw std::invalid_argument(arg + ": missing value");
        if(arg == "--serial")
        {
          if(args[i].empty() || args[i].size() > 128 || args[i].front() == '-')
            throw std::invalid_argument("--serial: invalid serial");
          options.serial = args[i];
        }
        else if(arg == "--frames")
        {
          options.frames = number(args[i], 300, arg);
          captureParameter = true;
        }
        else
        {
          options.timeoutMs = number(args[i], 30000, arg);
          if(options.timeoutMs < 250) throw std::invalid_argument("--timeout-ms: minimum 250ms");
        }
      }
      else throw std::invalid_argument("Unknown argument: " + arg);
    }
    if(captureParameter && !options.capture && !options.help)
      throw std::invalid_argument("--frames requires explicit --capture; default mode never streams");
    return options;
  }
}
