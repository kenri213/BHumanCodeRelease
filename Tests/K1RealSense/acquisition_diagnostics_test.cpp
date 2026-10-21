// Pure CPU evidence tests: no RealSense SDK, USB, robot or motion access.
#include "../../Src/Modules/Infrastructure/CameraProvider/RealSenseCameraGeometry.h"
#include <iostream>
#include <stdexcept>
#include <string>

namespace
{
  void require(bool condition, const char* message)
  {
    if(!condition) throw std::runtime_error(message);
  }

  // Preserve the exact pre-diagnostics acceptance policy, including precedence.
  RealSenseCamera::Timestamp originalTimestamp(bool metadata, std::uint64_t arrival,
                                              std::uint64_t base, unsigned now, unsigned last, unsigned maxAge)
  {
    using namespace RealSenseCamera;
    if(now <= last) return {};
    const auto epochNow = base + now;
    if(metadata && arrival >= base && arrival <= epochNow)
    {
      if(epochNow - arrival > maxAge || arrival - base <= last) return {};
      return {static_cast<unsigned>(arrival - base), TimestampSource::metadata};
    }
    return {now, TimestampSource::hostArrival};
  }
}

int main()
{
  using namespace RealSenseCamera;
  constexpr std::uint64_t base = 1780000000000ULL;
  // Exhaustive small-window cross product plus epoch/hardware/future samples.
  std::uint64_t comparisons = 0;
  for(unsigned now = 0; now <= 30; ++now)
    for(unsigned last = 0; last <= 30; ++last)
      for(unsigned arrival = 0; arrival <= 35; ++arrival)
        for(unsigned maxAge : {0u, 1u, 10u, 500u})
          for(bool metadata : {false, true})
            for(auto epoch : {static_cast<std::uint64_t>(arrival), base + arrival})
            {
              const auto before = originalTimestamp(metadata, epoch, base, now, last, maxAge);
              const auto after = timestamp(metadata, epoch, base, now, last, maxAge);
              require(before.value == after.value && before.source == after.source, "timestamp acceptance changed");
              ++comparisons;
            }
  require(timestamp(true, base + 80, base, 90, 100, 500).rejection == TimestampRejection::hostClockRegression,
          "host regression not classified");
  require(timestamp(false, 0, base, 100, 100, 500).rejection == TimestampRejection::hostNotAdvancing,
          "equal host time not classified");
  require(timestamp(true, base + 100, base, 800, 50, 500).rejection == TimestampRejection::staleMetadata,
          "stale metadata not classified");
  require(timestamp(true, base + 90, base, 120, 100, 500).rejection == TimestampRejection::metadataNotAdvancing,
          "backward metadata not classified");
  require(std::string(rejectionName(TimestampRejection::staleMetadata)) == "stale_metadata", "human-readable reason missing");

  AcquisitionDiagnostics d;
  d.beginWait(base, 100);
  d.observeHost(base, 110);
  require(d.received == 0 && d.color == 0 && !d.hasReceived && !d.hasAccepted, "no-frame wait misreported");
  ++d.received;
  auto stamp = timestamp(true, base + 105, base, 110, 100, 500);
  d.noteColor(50, true, base + 105, base, 110, false, 0, stamp);
  d.noteAccepted(stamp.value);
  require(d.hasAccepted && d.acceptedNumber == 50 && d.acceptedTimestamp == 105 && d.acceptedHost == 110 &&
          d.acceptedArrival == base + 105 && d.acceptedEpoch == base + 110 && d.acceptedMetadata,
          "accepted snapshot incorrect");

  d.beginWait(base, 111);
  require(d.received == 0 && d.color == 0 && !d.hasReceived && d.hasAccepted && d.acceptedNumber == 50,
          "new wait must clear counters but retain accepted frame");
  // A frameset without COLOR is distinct from no SDK frames.
  ++d.received;
  require(d.received == 1 && d.color == 0, "frameset-only evidence lost");
  ++d.received;
  stamp = timestamp(true, base + 106, base, 112, 105, 500);
  d.noteColor(50, true, base + 106, base, 112, true, 50, stamp);
  require(d.duplicate == 1 && d.backward == 0 && d.color == 1 && d.hasReceived && d.acceptedTimestamp == 105,
          "duplicate must not change accepted snapshot");
  ++d.received;
  stamp = timestamp(true, base + 100, base, 800, 105, 500);
  d.noteColor(1, true, base + 100, base, 800, true, 50, stamp);
  require(d.backward == 1 && d.stale == 1 && d.lastRejection == TimestampRejection::staleMetadata,
          "frame reset plus stale metadata not separately counted");
  ++d.received;
  stamp = timestamp(true, base + 104, base, 120, 105, 500);
  d.noteColor(51, true, base + 104, base, 120, true, 50, stamp);
  require(d.metadataNotAdvancing == 1 && d.observedClockRegressions == 1,
          "backward metadata/host observation not counted");
  ++d.received;
  stamp = timestamp(false, 0, base, 90, 105, 500);
  d.noteColor(52, false, 0, base, 90, true, 50, stamp);
  require(d.clockRegression == 1 && d.observedClockRegressions == 2 && !d.receivedMetadata,
          "clock regression not separated from missing metadata");
  ++d.received;
  stamp = timestamp(false, 0, base, 105, 105, 500);
  d.noteColor(53, false, 0, base, 105, true, 50, stamp);
  require(d.hostNotAdvancing == 1 && d.clockRegression == 1 && d.acceptedNumber == 50,
          "equal host timestamp must not advance accepted frame");
  d.beginWait(base, 1000);
  d.observeHost(base, 900);
  require(d.observedClockRegressions == 1 && d.received == 0 && d.acceptedNumber == 50,
          "clock regression without any SDK frame must be visible");
  std::cout << "acquisition diagnostic evidence passed; timestamp equivalence comparisons=" << comparisons
            << "; no devices loaded\n";
}
