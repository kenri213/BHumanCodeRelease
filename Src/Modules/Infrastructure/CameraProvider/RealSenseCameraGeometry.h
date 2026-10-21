/** Pure, SDK-independent validation, timestamps and YUYV rectification. */
#pragma once

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <vector>

namespace RealSenseCamera
{
  struct Intrinsics
  {
    int width = 0;
    int height = 0;
    float fx = 0.f;
    float fy = 0.f;
    float cx = 0.f;
    float cy = 0.f;

    bool valid() const
    {
      return width > 0 && width <= 2560 && !(width % 2) && height > 0 && height <= 1920 &&
             std::isfinite(fx) && std::isfinite(fy) && fx > 0.f && fy > 0.f &&
             std::isfinite(cx) && std::isfinite(cy) && cx >= 0.f && cx < width && cy >= 0.f && cy < height;
    }

    float openingAngleWidth() const { return 2.f * std::atan(static_cast<float>(width) / (2.f * fx)); }
    float openingAngleHeight() const { return 2.f * std::atan(static_cast<float>(height) / (2.f * fy)); }
  };

  enum class TimestampSource { metadata, hostArrival, rejected };
  // Diagnostic labels only: these do not change the timestamp acceptance policy.
  enum class TimestampRejection { none, hostClockRegression, hostNotAdvancing, staleMetadata, metadataNotAdvancing };
  inline const char* rejectionName(TimestampRejection reason)
  {
    switch(reason)
    {
      case TimestampRejection::none: return "none";
      case TimestampRejection::hostClockRegression: return "host_clock_regression";
      case TimestampRejection::hostNotAdvancing: return "host_not_advancing";
      case TimestampRejection::staleMetadata: return "stale_metadata";
      case TimestampRejection::metadataNotAdvancing: return "metadata_not_advancing";
    }
    return "unknown";
  }
  struct Timestamp
  {
    unsigned value = 0;
    TimestampSource source = TimestampSource::rejected;
    TimestampRejection rejection = TimestampRejection::none;
  };

  // Only TIME_OF_ARRIVAL (host epoch ms) is accepted, never the device hardware clock.
  // Old/duplicate host-domain frames are rejected, not relabeled as fresh by last + 1.
  inline Timestamp timestamp(bool hasMetadata, std::uint64_t arrivalEpochMs, std::uint64_t baseMs,
                             unsigned now, unsigned last, unsigned maxAgeMs)
  {
    if(now <= last)
      return {0, TimestampSource::rejected, now < last ? TimestampRejection::hostClockRegression : TimestampRejection::hostNotAdvancing};
    const std::uint64_t epochNow = baseMs + now;
    if(hasMetadata && arrivalEpochMs >= baseMs && arrivalEpochMs <= epochNow)
    {
      if(epochNow - arrivalEpochMs > maxAgeMs)
        return {0, TimestampSource::rejected, TimestampRejection::staleMetadata};
      if(arrivalEpochMs - baseMs <= last)
        return {0, TimestampSource::rejected, TimestampRejection::metadataNotAdvancing};
      return {static_cast<unsigned>(arrivalEpochMs - baseMs), TimestampSource::metadata};
    }
    return {now, TimestampSource::hostArrival};
  }

  // Per-wait evidence plus the last accepted frame. No SDK, image publication,
  // stream restart, clock adjustment or frame-counter resynchronization here.
  struct AcquisitionDiagnostics
  {
    std::uint64_t received = 0, color = 0, duplicate = 0, backward = 0, stale = 0;
    std::uint64_t metadataNotAdvancing = 0, clockRegression = 0, hostNotAdvancing = 0, observedClockRegressions = 0;
    bool hasReceived = false, hasAccepted = false, receivedMetadata = false, acceptedMetadata = false;
    std::uint64_t receivedNumber = 0, acceptedNumber = 0, receivedArrival = 0, acceptedArrival = 0;
    std::uint64_t receivedEpoch = 0, acceptedEpoch = 0, observedEpoch = 0;
    unsigned receivedHost = 0, acceptedHost = 0, observedHost = 0, acceptedTimestamp = 0;
    TimestampRejection lastRejection = TimestampRejection::none;

    void beginWait(std::uint64_t base, unsigned now)
    {
      received = color = duplicate = backward = stale = metadataNotAdvancing = 0;
      clockRegression = hostNotAdvancing = observedClockRegressions = 0;
      hasReceived = receivedMetadata = false;
      receivedNumber = receivedArrival = receivedEpoch = receivedHost = 0;
      lastRejection = TimestampRejection::none;
      observedHost = now;
      observedEpoch = base + now;
    }

    void observeHost(std::uint64_t base, unsigned now)
    {
      if(now < observedHost)
        ++observedClockRegressions;
      observedHost = now;
      observedEpoch = base + now;
    }

    void noteColor(std::uint64_t number, bool metadata, std::uint64_t arrival,
                   std::uint64_t base, unsigned now, bool haveNumber, std::uint64_t lastNumber,
                   const Timestamp& stamp)
    {
      ++color;
      if(haveNumber && number == lastNumber) ++duplicate;
      if(haveNumber && number < lastNumber) ++backward;
      if(stamp.rejection == TimestampRejection::staleMetadata) ++stale;
      if(stamp.rejection == TimestampRejection::metadataNotAdvancing) ++metadataNotAdvancing;
      if(stamp.rejection == TimestampRejection::hostClockRegression) ++clockRegression;
      if(stamp.rejection == TimestampRejection::hostNotAdvancing) ++hostNotAdvancing;
      hasReceived = true;
      receivedNumber = number;
      receivedMetadata = metadata;
      receivedArrival = arrival;
      receivedHost = now;
      receivedEpoch = base + now;
      lastRejection = stamp.rejection;
      observeHost(base, now);
    }

    void noteAccepted(unsigned stamp)
    {
      hasAccepted = true;
      acceptedNumber = receivedNumber;
      acceptedMetadata = receivedMetadata;
      acceptedArrival = receivedArrival;
      acceptedHost = receivedHost;
      acceptedEpoch = receivedEpoch;
      acceptedTimestamp = stamp;
    }
  };

  struct MapPoint { float x; float y; };

  // Standard Brown-Conrady only. Inverse Brown uses the separate SDK-compatible map below.
  inline std::vector<MapPoint> brownMap(const Intrinsics& i, const std::array<float, 5>& c)
  {
    if(!i.valid() || !std::all_of(c.begin(), c.end(), [](float v) { return std::isfinite(v); }))
      throw std::invalid_argument("Invalid RealSense color intrinsics/distortion");
    std::vector<MapPoint> map;
    map.reserve(static_cast<std::size_t>(i.width) * i.height);
    if(std::all_of(c.begin(), c.end(), [](float coefficient) { return coefficient == 0.f; }))
    {
      for(int v = 0; v < i.height; ++v)
        for(int u = 0; u < i.width; ++u)
          map.push_back({static_cast<float>(u), static_cast<float>(v)});
      return map;
    }
    for(int v = 0; v < i.height; ++v)
      for(int u = 0; u < i.width; ++u)
      {
        const float x = (u - i.cx) / i.fx;
        const float y = (v - i.cy) / i.fy;
        const float r2 = x * x + y * y;
        const float radial = 1.f + r2 * (c[0] + r2 * (c[1] + r2 * c[4]));
        const float xd = x * radial + 2.f * c[2] * x * y + c[3] * (r2 + 2.f * x * x);
        const float yd = y * radial + 2.f * c[3] * x * y + c[2] * (r2 + 2.f * y * y);
        map.push_back({xd * i.fx + i.cx, yd * i.fy + i.cy});
      }
    return map;
  }

  // Equivalent to rs2_project_point_to_pixel()'s INVERSE_BROWN_CONRADY
  // branch in librealsense v2.55.1 src/rs.cpp:3927-3950.
  // Direction: rectified output pixel -> distorted source pixel, NOT deprojection.
  // Crucially: r2 uses the original normalized ray, tangential terms use a,b
  // after radial scaling. This is NOT the standard Brown formula above.
  inline std::vector<MapPoint> inverseBrownMap(const Intrinsics& i, const std::array<float, 5>& c)
  {
    if(!i.valid() || !std::all_of(c.begin(), c.end(), [](float value) { return std::isfinite(value); }))
      throw std::invalid_argument("Invalid inverse Brown color intrinsics/distortion");
    if(std::all_of(c.begin(), c.end(), [](float coefficient) { return coefficient == 0.f; }))
      return brownMap(i, c); // Exact integer identity, including the last row/column.
    std::vector<MapPoint> map;
    map.reserve(static_cast<std::size_t>(i.width) * i.height);
    for(int v = 0; v < i.height; ++v)
      for(int u = 0; u < i.width; ++u)
      {
        const float x = (u - i.cx) / i.fx;
        const float y = (v - i.cy) / i.fy;
        const float r2 = x * x + y * y;
        const float radial = 1.f + c[0] * r2 + c[1] * r2 * r2 + c[4] * r2 * r2 * r2;
        if(!std::isfinite(radial) || radial <= 0.f)
          throw std::invalid_argument("Nonfinite/singular inverse Brown radial mapping");
        const float a = x * radial;
        const float b = y * radial;
        const float xd = a + 2.f * c[2] * a * b + c[3] * (r2 + 2.f * a * a);
        const float yd = b + 2.f * c[3] * a * b + c[2] * (r2 + 2.f * b * b);
        const MapPoint point{xd * i.fx + i.cx, yd * i.fy + i.cy};
        if(!std::isfinite(point.x) || !std::isfinite(point.y))
          throw std::invalid_argument("Nonfinite inverse Brown source pixel");
        map.push_back(point);
      }
    return map;
  }

  inline bool inside(const MapPoint& p, int width, int height)
  {
    return std::isfinite(p.x) && std::isfinite(p.y) && p.x >= 0.f && p.x <= width - 1 && p.y >= 0.f && p.y <= height - 1;
  }

  inline float sample(const std::uint8_t* src, int width, int height, int stride,
                      float x, float y, int pixelStride, int offset)
  {
    x = std::clamp(x, 0.f, static_cast<float>(width - 1));
    y = std::clamp(y, 0.f, static_cast<float>(height - 1));
    const int x0 = static_cast<int>(x), y0 = static_cast<int>(y);
    const int x1 = std::min(x0 + 1, width - 1), y1 = std::min(y0 + 1, height - 1);
    const float dx = x - x0, dy = y - y0;
    const auto at = [&](int px, int py) { return static_cast<float>(src[static_cast<std::size_t>(py) * stride + px * pixelStride + offset]); };
    return (1.f - dy) * ((1.f - dx) * at(x0, y0) + dx * at(x1, y0)) +
           dy * ((1.f - dx) * at(x0, y1) + dx * at(x1, y1));
  }

  inline void rectifyYuyv(const std::uint8_t* src, int width, int height, int stride,
                          const std::vector<MapPoint>& map, std::uint8_t* dst)
  {
    if(!src || !dst || width <= 0 || width > 2560 || width % 2 || height <= 0 || height > 1920 || stride < width * 2 ||
       map.size() != static_cast<std::size_t>(width) * height)
      throw std::invalid_argument("Invalid RealSense YUYV frame/map");
    const auto byte = [](float v) { return static_cast<std::uint8_t>(std::clamp(std::lround(v), 0l, 255l)); };
    for(int y = 0; y < height; ++y)
      for(int x = 0; x < width; x += 2)
      {
        const auto& a = map[static_cast<std::size_t>(y) * width + x];
        const auto& b = map[static_cast<std::size_t>(y) * width + x + 1];
        const bool va = inside(a, width, height), vb = inside(b, width, height);
        auto* out = dst + (static_cast<std::size_t>(y) * width + x) * 2;
        out[0] = va ? byte(sample(src, width, height, stride, a.x, a.y, 2, 0)) : 0;
        out[2] = vb ? byte(sample(src, width, height, stride, b.x, b.y, 2, 0)) : 0;
        // Chroma is shared by each 4:2:2 pair: sample at the mapped pair centre.
        // Do not interpolate the packed YUYV bytes as four independent color planes.
        const MapPoint center{(a.x + b.x) * .5f, (a.y + b.y) * .5f};
        out[1] = va && vb ? byte(sample(src, width / 2, height, stride, (center.x - .5f) * .5f, center.y, 4, 1)) : 128;
        out[3] = va && vb ? byte(sample(src, width / 2, height, stride, (center.x - .5f) * .5f, center.y, 4, 3)) : 128;
      }
  }
}
