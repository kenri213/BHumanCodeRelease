#pragma once
#include <cstring>
#include <string>

namespace mode_query
{
constexpr const char* serial977 = "1424225066425";
constexpr unsigned maxAttempts = 3;
constexpr unsigned deadlineSeconds = 8;

inline bool approvedArguments(int argc, const char* const* argv)
{
  return argc == 2 && argv && argv[1] && std::strcmp(argv[1], "--query-only") == 0;
}

inline bool approvedSerial(const std::string& serial) { return serial == serial977; }

struct Result
{
  int result = -1;
  int mode = -1;
  unsigned attempts = 0;
};

// Query is injected in CPU tests. Success reports every mode, never changes it.
template<typename Query, typename Pause>
Result queryAtMostThree(Query query, Pause pause)
{
  Result out;
  for(unsigned attempt = 0; attempt < maxAttempts; ++attempt)
  {
    int mode = -1;
    out.result = query(mode);
    ++out.attempts;
    if(out.result == 0)
    {
      out.mode = mode;
      return out;
    }
    if(attempt + 1 < maxAttempts)
      pause();
  }
  return out;
}
}
