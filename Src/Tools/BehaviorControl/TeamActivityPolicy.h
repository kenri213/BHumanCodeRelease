/** Optional policy for incomplete real teams. No inference of a received packet
 * from a GameController roster or a synthetic SET pose is permitted here. */
#pragma once

#include <cstdint>

namespace TeamActivityPolicy
{
  inline bool fresh(unsigned now, unsigned timestamp, unsigned maxAge) noexcept
  {
    if(timestamp == 0)
      return false;
    const std::int32_t age = static_cast<std::int32_t>(now - timestamp);
    return age >= 0 && static_cast<unsigned>(age) <= maxAge;
  }

  inline unsigned boundedTimeout(unsigned configured) noexcept
  {
    return configured < 5000u ? 5000u : configured > 60000u ? 60000u : configured;
  }

  inline bool retainAgent(bool enabled, bool playing, bool self,
                          unsigned now, unsigned messageTime, unsigned maxAge) noexcept
  {
    return !enabled || !playing || self || fresh(now, messageTime, boundedTimeout(maxAge));
  }

  inline bool ballAvailable(bool enabled, unsigned now, unsigned messageTime,
                            unsigned seenTime, unsigned threshold) noexcept
  {
    return enabled ? fresh(now, seenTime, threshold) : messageTime <= seenTime + threshold;
  }
}
