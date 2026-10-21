#pragma once

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <map>
#include <set>
#include <string>
#include <vector>

namespace k1commissioning
{
struct FiniteAggregate
{
  std::uint64_t callbacksChecked = 0;
  std::uint64_t callbacksWithNonfinite = 0;
  std::uint64_t valuesChecked = 0;
  std::uint64_t nonfiniteValues = 0;

  void observe(std::size_t checked, std::size_t invalid)
  {
    ++callbacksChecked;
    callbacksWithNonfinite += invalid ? 1 : 0;
    valuesChecked += checked;
    nonfiniteValues += invalid;
  }
};

template<typename Range> std::size_t countNonfinite(const Range& values)
{
  return static_cast<std::size_t>(std::count_if(values.begin(), values.end(),
    [](auto value) { return !std::isfinite(static_cast<double>(value)); }));
}

struct GatewayAggregate
{
  static constexpr std::size_t maximumTrackedNames = 256;
  std::uint64_t callbacks = 0;
  std::uint64_t callbacksWithDisconnection = 0;
  std::uint64_t emptyJointCallbacks = 0;
  std::uint64_t callbacksWithTruncatedNames = 0;
  std::uint64_t untrackedNameOccurrences = 0;
  std::size_t minConnected = 0;
  std::size_t maxDisconnected = 0;
  std::size_t minReported = 0;
  std::size_t maxReported = 0;
  std::map<std::string, std::uint64_t> nameCallbackCounts;

  void observe(std::size_t reported, std::size_t connected,
               const std::vector<std::string>& disconnectedNames, bool namesTruncated)
  {
    connected = std::min(connected, reported);
    const auto disconnected = reported - connected;
    if(!callbacks)
    {
      minConnected = connected;
      minReported = reported;
    }
    ++callbacks;
    minConnected = std::min(minConnected, connected);
    maxDisconnected = std::max(maxDisconnected, disconnected);
    minReported = std::min(minReported, reported);
    maxReported = std::max(maxReported, reported);
    callbacksWithDisconnection += disconnected ? 1 : 0;
    emptyJointCallbacks += reported ? 0 : 1;
    callbacksWithTruncatedNames += namesTruncated ? 1 : 0;
    // A duplicated name in one sample counts as one callback, not two.
    for(const auto& name : std::set<std::string>(disconnectedNames.begin(), disconnectedNames.end()))
    {
      auto found = nameCallbackCounts.find(name);
      if(found != nameCallbackCounts.end())
        ++found->second;
      else if(nameCallbackCounts.size() < maximumTrackedNames)
        nameCallbackCounts.emplace(name, 1);
      else
        ++untrackedNameOccurrences;
    }
  }
};
}
