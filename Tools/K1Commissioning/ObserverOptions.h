#pragma once

#include <string>
#include <vector>

namespace k1commissioning
{
struct ObserverOptions
{
  unsigned durationSeconds = 10;
  unsigned domainId = 0;
  std::string interfaceIp = "127.0.0.1";
  bool help = false;
  bool stopAfterF1 = false;
};

inline bool boundedUnsigned(const std::string& text, unsigned maximum, unsigned& value)
{
  if(text.empty())
    return false;
  unsigned parsed = 0;
  for(char character : text)
  {
    if(character < '0' || character > '9')
      return false;
    const unsigned digit = static_cast<unsigned>(character - '0');
    if(parsed > maximum / 10 || (parsed == maximum / 10 && digit > maximum % 10))
      return false;
    parsed = parsed * 10 + digit;
  }
  value = parsed;
  return true;
}

inline bool ipv4Literal(const std::string& text)
{
  std::size_t start = 0;
  for(unsigned part = 0; part < 4; ++part)
  {
    const auto end = text.find('.', start);
    if((part < 3 && end == std::string::npos) || (part == 3 && end != std::string::npos))
      return false;
    const auto component = text.substr(start, end == std::string::npos ? end : end - start);
    unsigned value = 0;
    if(component.empty() || (component.size() > 1 && component.front() == '0')
       || !boundedUnsigned(component, 255, value))
      return false;
    start = end == std::string::npos ? text.size() : end + 1;
  }
  return true;
}

inline bool parseObserverOptions(const std::vector<std::string>& arguments,
                                 ObserverOptions& options, std::string& error)
{
  bool durationSeen = false;
  bool domainSeen = false;
  bool interfaceSeen = false;
  bool stopAfterF1Seen = false;
  for(std::size_t index = 0; index < arguments.size(); ++index)
  {
    const auto& argument = arguments[index];
    if(argument == "--help" || argument == "-h")
    {
      options.help = true;
      continue;
    }
    if(argument == "--stop-after-f1")
    {
      if(stopAfterF1Seen) {error = "--stop-after-f1 must occur once"; return false;}
      stopAfterF1Seen = true;
      options.stopAfterF1 = true;
      continue;
    }
    if(argument != "--duration-seconds" && argument != "--domain-id" && argument != "--interface-ip")
    {
      error = "Unknown option: " + argument;
      return false;
    }
    if(++index == arguments.size())
    {
      error = "Missing value for " + argument;
      return false;
    }
    const auto& value = arguments[index];
    if(argument == "--duration-seconds")
    {
      if(durationSeen || !boundedUnsigned(value, 60, options.durationSeconds))
      {
        error = "--duration-seconds must occur once and be an integer from 0 to 60";
        return false;
      }
      durationSeen = true;
    }
    else if(argument == "--domain-id")
    {
      if(domainSeen || !boundedUnsigned(value, 232, options.domainId))
      {
        error = "--domain-id must occur once and be an integer from 0 to 232";
        return false;
      }
      domainSeen = true;
    }
    else
    {
      if(interfaceSeen || !ipv4Literal(value))
      {
        error = "--interface-ip must occur once and be an unambiguous numeric IPv4 address";
        return false;
      }
      interfaceSeen = true;
      options.interfaceIp = value;
    }
  }
  return true;
}
}
