#pragma once

#include <cmath>

/** SDK-free timing for the optional Live-only RB+A get-up gesture. */
namespace LiveGetUpGesture
{
  struct Input
  {
    bool live = false, authorized = false, prepare = false, emergency = false;
    bool joystickValid = false, rb = false, a = false, b = false, stand = false, walk = false;
    bool gcActive = false, initial = false, packetInitial = false, stopped = false, penalized = true;
    bool matchRecoveryAllowed = false;
    unsigned frameTime = 0, gcNow = 0, gcPacketTime = 0, holdMs = 2000;
    bool lyingStable = false;
  };

  inline bool stableLying(float roll, float pitch, float gx, float gy, float gz,
                         float minFallAngle, float maxGyro) noexcept
  {
    return std::isfinite(roll) && std::isfinite(pitch) && std::isfinite(gx)
           && std::isfinite(gy) && std::isfinite(gz)
           && (std::abs(roll) > minFallAngle || std::abs(pitch) > minFallAngle)
           && std::abs(gx) < maxGyro && std::abs(gy) < maxGyro && std::abs(gz) < maxGyro;
  }

  inline bool update(const Input& input, bool& releaseSeen, bool& holding,
                     unsigned& startedAt, unsigned& lastFrame) noexcept
  {
    const bool permitted = input.live && input.authorized && input.prepare && !input.emergency
                           && input.joystickValid && !input.b && !input.stand
                           && input.gcActive && ((input.initial && input.packetInitial) || input.matchRecoveryAllowed)
                           && !input.stopped && !input.penalized
                           && input.gcPacketTime > 0 && input.gcNow >= input.gcPacketTime
                           && input.gcNow - input.gcPacketTime <= 2000;
    if(!permitted || input.frameTime < lastFrame)
    {
      releaseSeen = holding = false;
      startedAt = 0;
      lastFrame = input.frameTime;
      return false;
    }
    lastFrame = input.frameTime;
    if(!input.a)
    {
      releaseSeen = true;
      holding = false;
      startedAt = 0;
      return false;
    }
    // Neutral A is valid with RB released too. But A pressed BEFORE RB must
    // consume that neutral evidence, not turn into a valid hold by adding RB.
    if(!input.rb)
    {
      releaseSeen = holding = false;
      startedAt = 0;
      return false;
    }
    // A must have been observed released in THIS eligible Prepare episode.
    // Enhanced.walk may lag raw A by a frame: do not consume the release then.
    if(!releaseSeen || !input.walk)
    {
      holding = false;
      startedAt = 0;
      return false;
    }
    if(!holding)
    {
      holding = true;
      startedAt = input.frameTime;
      return false;
    }
    if(input.frameTime - startedAt > input.holdMs && input.lyingStable)
    {
      releaseSeen = holding = false; // Exactly one request until A is released again.
      return true;
    }
    return false;
  }
}
