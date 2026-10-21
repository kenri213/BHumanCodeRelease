#include "ObserverAggregate.h"

#include <array>
#include <cstdlib>
#include <iostream>
#include <limits>
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
    std::cerr << "Aggregate check failed: " << checks << '\n';
    std::exit(1);
  }
}
}

int main()
{
  using namespace k1commissioning;
  GatewayAggregate gateway;
  check(gateway.callbacks == 0 && gateway.nameCallbackCounts.empty());
  gateway.observe(22, 22, {}, false);
  check(gateway.callbacks == 1 && gateway.minConnected == 22 && gateway.maxDisconnected == 0);
  gateway.observe(22, 21, {"AAHead_Yaw"}, false);
  // The latest sample is healthy again; the transient must still be visible.
  gateway.observe(22, 22, {}, false);
  check(gateway.callbacks == 3 && gateway.minConnected == 21 && gateway.maxDisconnected == 1);
  check(gateway.callbacksWithDisconnection == 1);
  check(gateway.nameCallbackCounts.at("AAHead_Yaw") == 1);
  gateway.observe(22, 20, {"AAHead_Yaw", "AAHead_Pitch", "AAHead_Yaw"}, false);
  check(gateway.callbacksWithDisconnection == 2 && gateway.maxDisconnected == 2);
  check(gateway.nameCallbackCounts.at("AAHead_Yaw") == 2);
  check(gateway.nameCallbackCounts.at("AAHead_Pitch") == 1);
  check(gateway.minReported == 22 && gateway.maxReported == 22);
  gateway.observe(21, 21, {}, false);
  check(gateway.minReported == 21 && gateway.maxReported == 22);
  gateway.observe(0, 0, {}, false);
  check(gateway.emptyJointCallbacks == 1 && gateway.minConnected == 0 && gateway.minReported == 0);
  gateway.observe(70, 0, {"bounded_sample"}, true);
  check(gateway.maxDisconnected == 70 && gateway.maxReported == 70);
  check(gateway.callbacksWithTruncatedNames == 1);

  GatewayAggregate bounded;
  for(std::size_t index = 0; index < GatewayAggregate::maximumTrackedNames + 3; ++index)
    bounded.observe(1, 0, {"joint_" + std::to_string(index)}, false);
  check(bounded.nameCallbackCounts.size() == GatewayAggregate::maximumTrackedNames);
  check(bounded.untrackedNameOccurrences == 3);
  check(bounded.callbacksWithDisconnection == GatewayAggregate::maximumTrackedNames + 3);

  const std::array<double, 4> healthy{0., -1., 1., 123.};
  const std::array<double, 4> broken{std::numeric_limits<double>::quiet_NaN(),
                                    std::numeric_limits<double>::infinity(),
                                    -std::numeric_limits<double>::infinity(), 0.};
  check(countNonfinite(healthy) == 0);
  check(countNonfinite(broken) == 3);
  FiniteAggregate finite;
  finite.observe(healthy.size(), countNonfinite(healthy));
  finite.observe(broken.size(), countNonfinite(broken));
  finite.observe(healthy.size(), countNonfinite(healthy));
  check(finite.callbacksChecked == 3 && finite.callbacksWithNonfinite == 1);
  check(finite.valuesChecked == 12 && finite.nonfiniteValues == 3);
  finite.observe(0, 0);
  check(finite.callbacksChecked == 4 && finite.callbacksWithNonfinite == 1);
  check(finite.valuesChecked == 12 && finite.nonfiniteValues == 3);
  std::cout << checks << " aggregate checks passed; no SDK initialized\n";
}
