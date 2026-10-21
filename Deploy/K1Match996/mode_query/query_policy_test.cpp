#include "query_policy.h"
#include <cassert>
#include <iostream>

int main()
{
  const char* good[] = {"query", "--query-only"};
  const char* bad[] = {"query", "--start"};
  assert(mode_query::approvedArguments(2, good));
  assert(!mode_query::approvedArguments(2, bad));
  assert(!mode_query::approvedArguments(1, good));
  assert(!mode_query::approvedArguments(3, good));
  assert(mode_query::approvedSerial("1424725032007"));
  assert(!mode_query::approvedSerial("1424725032008"));
  static_assert(mode_query::maxAttempts == 3 && mode_query::deadlineSeconds <= 10);
  unsigned successes = 6;
  for(int mode : {0, 1, 2, 3, 77})
  {
    unsigned calls = 0, pauses = 0;
    const auto result = mode_query::queryAtMostThree([&](int& value) { ++calls; value = mode; return 0; }, [&] { ++pauses; });
    assert(result.result == 0 && result.mode == mode && result.attempts == 1 && calls == 1 && pauses == 0);
    ++successes;
  }
  unsigned calls = 0, pauses = 0;
  const auto failure = mode_query::queryAtMostThree([&](int& value) { ++calls; value = 9; return 42; }, [&] { ++pauses; });
  assert(failure.result == 42 && failure.mode == -1 && failure.attempts == 3 && calls == 3 && pauses == 2);
  ++successes;
  calls = pauses = 0;
  const auto recovered = mode_query::queryAtMostThree([&](int& value) { ++calls; value = 0; return calls == 3 ? 0 : 7; }, [&] { ++pauses; });
  assert(recovered.result == 0 && recovered.mode == 0 && recovered.attempts == 3 && calls == 3 && pauses == 2);
  ++successes;
  std::cout << "mode query policy checks passed: " << successes << '\n';
}
