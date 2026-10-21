"""Real Main match-entry branches with fake emergency/input types; no SDK."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "Src/Apps/Booster/Main.cpp"


def block(source, marker):
    start = source.index(marker)
    index = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[index] == "{") - (source[index] == "}")
        index += 1
    return source[start:index]


class MatchMainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = MAIN.read_text()
        cls.main = cls.source[cls.source.index("int main(int argc"):]

    def compile_run(self, code, cases):
        with tempfile.TemporaryDirectory(prefix="k1-match-main-cpu-") as folder:
            cpp, binary = Path(folder) / "test.cpp", Path(folder) / "test"
            cpp.write_text(code)
            built = subprocess.run(["clang++", "-std=c++20", "-DNDEBUG", "-Wall", "-Wextra", "-Werror",
                                    "-pedantic", "-I", str(ROOT / "Src/Libs"), str(cpp), "-o", str(binary)],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stderr)
            for arguments, expected in cases:
                with self.subTest(arguments=arguments):
                    result = subprocess.run([str(binary), *arguments], capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, expected, result.stderr)

    def test_actual_flags_reject_repeat_and_mixed_live_match_in_both_orders(self):
        start = self.main.index('else if(!strcmp(argv[i], "--k1-996-live-test"))')
        end = self.main.index('else if(!strcmp(argv[i], "--commission-emergency-only"))', start)
        flags = self.main[start:end].replace("else if", "if", 1)
        self.compile_run(r'''
#include <cstring>
#include <cstdlib>
int main(int argc, char** argv) {
  bool officialLiveTest = false, officialMatchMode = false;
  for(int i = 1; i < argc; ++i) {
''' + flags + r'''
    else return 9;
  }
  return !officialLiveTest ? 2 : officialMatchMode ? 4 : 3;
}
''', [(("--k1-996-live-test",), 3), (("--k1-996-match",), 4), ((), 2),
      (("--k1-996-match", "--k1-996-match"), 1),
      (("--k1-996-live-test", "--k1-996-live-test"), 1),
      (("--k1-996-match", "--k1-996-live-test"), 1),
      (("--k1-996-live-test", "--k1-996-match"), 1)])

    def test_serial_and_scope_checks_precede_both_guards_and_sdk(self):
        scope = block(self.main, "if(officialLiveTest)\n    {")
        pinned = scope.index('actualSerial != "1424725032007"')
        live_enable = scope.index("BoosterOfficialLiveTest::enable();")
        match_enable = scope.index("BoosterMatchSafety::enable();")
        self.assertLess(scope.index("DENIED_MIXED_SCOPE"), pinned)
        self.assertLess(pinned, live_enable)
        self.assertLess(live_enable, match_enable)
        self.assertIn("return EXIT_FAILURE", scope[pinned:live_enable])
        self.assertIn("BoosterMatchSafety::enable();", block(scope, "if(officialMatchMode)"))
        self.assertNotIn("BoosterAppCommissioning::enable", scope)
        self.assertLess(self.main.index(scope), self.main.index("if(!initInstance())"))
        self.assertLess(self.main.index(scope), self.main.index("client.Init();"))
        self.assertIn("NO_FIXED_RUNTIME_LIMIT", scope)

    def test_actual_entry_enable_block_keeps_live_guard_and_opt_in_match_layer(self):
        scope = block(self.main, "if(officialLiveTest)\n    {")
        start = scope.index("BoosterOfficialLiveTest::enable();")
        end = scope.index("signal(SIGINT,", start)
        self.compile_run(r'''
#include "Platform/BoosterOfficialLiveTest.h"
#include "Platform/BoosterMatchSafety.h"
#include <cstdio>
#include <cstdlib>
#include <string>
static void check(bool value) {if(!value) std::abort();}
int main(int argc, char** argv) {
  check(argc == 2); const bool officialMatchMode = std::string(argv[1]) == "match";
''' + scope[start:end] + r'''
  check(BoosterOfficialLiveTest::enabled());
  check(BoosterMatchSafety::enabled() == officialMatchMode);
  check(!BoosterOfficialLiveTest::bodyAuthorized() && !BoosterOfficialLiveTest::stopRequested());
  check(!BoosterMatchSafety::confirmed());
  return 0;
}
''', [(("live",), 0), (("match",), 0)])

    def test_actual_f1_first_unlock_then_short_press_stop_and_x_remove_remain_latched(self):
        callback = block(self.source, "static void buttonEventHandler(const void* msg)")
        self.compile_run(r'''
#include "Platform/BoosterOfficialLiveTest.h"
#include "Platform/BoosterMatchSafety.h"
#include <atomic>
#include <cstdlib>
#include <string>
namespace C = BoosterOfficialLiveTest; namespace M = BoosterMatchSafety;
static void check(bool value) {if(!value) std::abort();}
namespace Main {unsigned stops = 0; void stopOfficialLiveTest() {++stops;}}
namespace booster_interface::msg {
  enum ButtonEventType {PRESS_DOWN, PRESS_UP, LONG_PRESS_START};
  struct ButtonEventMsg {
    unsigned value = 0; ButtonEventType type = PRESS_DOWN;
    unsigned button() const {return value;} ButtonEventType event() const {return type;}
  };
}
struct EmergencyHandler {
  enum State {noEmergency, emergencyPressed, emergencyReleased};
  std::atomic<State> state{emergencyReleased};
  void setState(State target, bool = true) {state.store(target);}
};
static EmergencyHandler emergencyHandler;
''' + callback + r'''
static void send(booster_interface::msg::ButtonEventType type, unsigned index = 0) {
  booster_interface::msg::ButtonEventMsg message; message.value = index; message.type = type;
  buttonEventHandler(&message);
}
struct Remote {
  bool pressed = false; unsigned code = 1;
  unsigned event() const {return code;} bool x() const {return pressed;}
  float lx() const {return 0.f;} float ly() const {return 0.f;}
  float rx() const {return 0.f;} float ry() const {return 0.f;}
};
int main(int argc, char** argv) {
  check(argc == 2); const std::string test = argv[1];
  using namespace booster_interface::msg;
  if(test == "normal") {
    send(LONG_PRESS_START); check(emergencyHandler.state == EmergencyHandler::noEmergency);
    check(!C::enabled() && !M::enabled() && !Main::stops); return 0;
  }
  C::enable(); if(test != "live") M::enable();
  const auto initialEpoch = M::epoch();
  send(LONG_PRESS_START); check(!C::bodyAuthorized() && !C::stopRequested());
  send(PRESS_DOWN, 1); check(M::epoch() == initialEpoch);
  send(PRESS_DOWN);
  check(!C::bodyAuthorized() && !C::stopRequested() && !Main::stops);
  check(M::epoch() == initialEpoch + (M::enabled() ? 1 : 0));
  for(unsigned frame = 1; frame <= 5; ++frame)
    C::noteCognitionForF1Authorization(frame, C::steadyTimeNs());
  send(LONG_PRESS_START);
  check(C::bodyAuthorized() && !C::stopRequested() && emergencyHandler.state == EmergencyHandler::noEmergency);
  send(PRESS_UP); check(C::bodyAuthorized());
  if(M::enabled()) check(M::confirm(M::epoch()) && M::confirmed());
  if(test == "first_unlock") return 0;
  if(test == "f1_stop" || test == "live") send(PRESS_DOWN);
  else {
    Remote remote;
    if(test == "x") remote.pressed = true;
    else if(test == "remove") remote.code = 2;
    else return 2;
    C::observeRemoteStopX(remote, 2u);
  }
  check(!C::bodyAuthorized());
  if(test == "live") {check(!C::stopRequested() && !Main::stops); return 0;}
  check(C::stopRequested() && Main::stops == (test == "f1_stop" ? 1u : 0u));
  if(test == "f1_stop") check(!M::confirmed());
  C::enable(); send(PRESS_DOWN); send(PRESS_UP); send(LONG_PRESS_START);
  check(!C::bodyAuthorized() && C::stopRequested()); return 0;
}
''', [((name,), 0) for name in ("normal", "live", "first_unlock", "f1_stop", "x", "remove")])


if __name__ == "__main__":
    unittest.main()
