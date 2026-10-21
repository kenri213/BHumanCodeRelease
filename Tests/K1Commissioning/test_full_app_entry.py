"""SDK-free tests of actual full-app Main predicates and entry ordering."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "Src/Apps/Booster/Main.cpp"


def block(source, marker):
    start = source.index(marker)
    opening = source.index("{", start)
    index, depth = opening + 1, 1
    while depth:
        depth += (source[index] == "{") - (source[index] == "}")
        index += 1
    return source[start:index]


def condition(source, marker):
    opening = source.index("(", source.index(marker))
    index, depth = opening + 1, 1
    while depth:
        depth += (source[index] == "(") - (source[index] == ")")
        index += 1
    return source[opening + 1:index - 1]


class FullAppEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = MAIN.read_text()
        cls.app = block(cls.source, "[[noreturn]] static void runAppDampingOnly(")
        cls.main = cls.source[cls.source.index("int main(int argc") :]

    def compile_run(self, source):
        with tempfile.TemporaryDirectory(prefix="k1-full-app-pure-") as temporary:
            cpp, executable = Path(temporary) / "test.cpp", Path(temporary) / "test"
            cpp.write_text(source)
            compiled = subprocess.run(["clang++", "-std=c++20", "-Wall", "-Wextra", "-Werror", "-pedantic",
                                       str(cpp), "-o", str(executable)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_actual_parse_seconds_boundaries_and_no_unsigned_overflow(self):
        parser = block(self.source, "static bool parseAppSeconds(")
        self.compile_run(r'''
#include <string>
''' + parser + r'''
int main() {
  unsigned seconds = 123;
  for(unsigned value = 1; value <= 900; ++value) {
    const std::string text = std::to_string(value);
    if(!parseAppSeconds(text.c_str(), seconds) || seconds != value) return 1;
  }
  for(const char* text : {"", "0", "000", "901", "9000", "-1", "+1", "1.0", "1e2", "nan", " 10", "10 ",
                         "4294967295", "4294967296", "18446744073709551615", "9999999999999999999999999999999999"}) {
    seconds = 123;
    if(parseAppSeconds(text, seconds) || seconds != 123) return 2;
  }
  if(parseAppSeconds(nullptr, seconds)) return 3;
  if(!parseAppSeconds("000900", seconds) || seconds != 900) return 4;
  const std::string manyDigits(100000, '9');
  if(parseAppSeconds(manyDigits.c_str(), seconds)) return 5;
  return 0;
}
''')

    def test_actual_cli_combination_predicate_exhaustively(self):
        predicate = condition(self.main, "if(((commissionSecondsSet || commissionExpectedSerial)")
        self.compile_run(r'''
static bool rejected(bool commissionSecondsSet, const char* commissionExpectedSerial,
                     bool commissionEmergencyOnly, bool commissionAppDampingOnly,
                     bool watchdog, bool appSecondsSet) {
  return ''' + predicate + r''';
}
int main() {
  for(unsigned bits = 0; bits < 64; ++bits) {
    const bool oldSeconds = bits & 1, serial = bits & 2, emergency = bits & 4,
               app = bits & 8, watchdog = bits & 16, appSeconds = bits & 32;
    const bool normalValid = !emergency && !app && !oldSeconds && !serial && !appSeconds;
    const bool emergencyValid = emergency && !app && !watchdog && serial && !appSeconds;
    const bool appValid = app && !emergency && !watchdog && serial && !oldSeconds;
    const bool expectReject = !(normalValid || emergencyValid || appValid);
    if(rejected(oldSeconds, serial ? "1424725032007" : nullptr, emergency, app, watchdog, appSeconds) != expectReject) return 1;
  }
  return 0;
}
''')
        self.assertIn("if(appSecondsSet || !parseAppSeconds", self.main)

    def test_entry_precedes_normal_custom_robot_and_fork_startup(self):
        entry = self.main.index("runAppDampingOnly(bhDir, commissionExpectedSerial, appSeconds)")
        for token in ("FunctionList::execute()", "fork()", "emergencyHandler.init(settings.robotType)",
                      "emergencyHandler.start()", "RobotMode::kCustom", "bhumanStart(settings)"):
            self.assertLess(entry, self.main.index(token))
        self.assertIn("[[noreturn]] static void runAppDampingOnly", self.app)

    def test_physical_serial_checked_before_enabling_and_all_framework_setup(self):
        physical = self.app.index('"/sys/firmware/devicetree/base/serial-number"')
        hard_pin = self.app.index('actualSerial != "1424725032007"')
        expected = self.app.index("actualSerial != expectedSerial")
        enable = self.app.index("enable();")
        self.assertLess(physical, hard_pin)
        self.assertLess(hard_pin, enable)
        self.assertLess(expected, enable)
        for token in ("std::thread watchdog", "ChannelFactory::Instance()->Init", "FunctionList::execute()",
                      "Settings settings(", "bhumanStart(settings)"):
            self.assertLess(enable, self.app.index(token))

    def test_full_app_never_uses_custom_or_normal_emergency_startup(self):
        for forbidden in ("kCustom", "emergencyHandler.start(", "emergencyHandler.init(", "setState(",
                          "runEmergencyOnlyCommission(", "fork(", "Move(", "GetUp(", "Walk(", "Kick("):
            self.assertNotIn(forbidden, self.app)
        calls = self.app.split("client.ChangeMode(")[1:]
        self.assertTrue(calls)
        for call in calls:
            self.assertEqual(call.split(")", 1)[0].strip(), "booster::robot::RobotMode::kDamping")
        self.assertIn('ChannelFactory::Instance()->Init(0, "127.0.0.1")', self.app)

    def test_damping_confirmation_before_framework_stop_and_final_recheck(self):
        confirmation = self.app.index("dampingBeforeStop = !client.ChangeMode(")
        stop = self.app.index("if(robot) bhumanStop();")
        self.assertLess(confirmation, stop)
        before_stop = self.app[confirmation:stop]
        self.assertIn("!client.GetMode(mode)", before_stop)
        self.assertIn("mode.mode_ == booster::robot::RobotMode::kDamping", before_stop)
        self.assertLess(stop, self.app.index("finalDamping = !client.ChangeMode("))
        result = self.app[self.app.index("const bool pass =") :]
        for token in ("frameworkReady", "StopReason::f1", "dampingBeforeStop", "finalDamping", "robotStopped", "!modeFailures.load()"):
            self.assertIn(token, result)


if __name__ == "__main__":
    unittest.main()
