"""Actual Main predicates/callback branches, compiled without SDK or devices."""
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


class OfficialLiveMainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = MAIN.read_text()
        cls.main = cls.source[cls.source.index("int main(int argc") :]
        cls.scope = block(cls.main, "if(officialLiveTest)\n    {")

    def compile_run(self, source, args=()):
        with tempfile.TemporaryDirectory(prefix="k1-official-main-pure-") as temporary:
            cpp, executable = Path(temporary) / "test.cpp", Path(temporary) / "test"
            cpp.write_text(source)
            compiled = subprocess.run(["clang++", "-std=c++20", "-Wall", "-Wextra", "-Werror", "-pedantic",
                                       "-I", str(ROOT / "Src/Libs"), str(cpp), "-o", str(executable)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            for argv in args or ((),):
                tested = subprocess.run([str(executable), *argv], capture_output=True, text=True, timeout=10)
                self.assertEqual(tested.returncode, 0, tested.stderr)

    def test_actual_cli_rejects_all_mixed_live_scopes(self):
        expression = condition(self.scope, "if(watchdog || commissionEmergencyOnly")
        self.compile_run(r'''
static bool reject(bool officialLiveTest, bool watchdog, bool commissionEmergencyOnly,
                   bool commissionAppDampingOnly, bool remoteStopX, bool commissionSecondsSet,
                   bool appSecondsSet, const char* commissionExpectedSerial) {
 return officialLiveTest && (''' + expression + r''');
}
int main() {
 for(unsigned bits = 0; bits < 256; ++bits) {
   const bool live = bits & 1;
   const bool result = reject(live, bits & 2, bits & 4, bits & 8, bits & 16,
                              bits & 32, bits & 64, bits & 128 ? "1424725032007" : nullptr);
   if(result != (live && (bits & 254))) return 1;
 }
 return 0;
}
''')
        self.assertIn('"--k1-996-live-test"', self.main)
        self.assertIn("if(officialLiveTest) return EXIT_FAILURE;", self.main)

    def test_serial_before_enable_and_all_sdk_initialization(self):
        serial = self.scope.index('"/sys/firmware/devicetree/base/serial-number"')
        pinned = self.scope.index('actualSerial != "1424725032007"')
        enable = self.scope.index("BoosterOfficialLiveTest::enable();")
        self.assertLess(serial, pinned)
        self.assertLess(pinned, enable)
        self.assertIn("return EXIT_FAILURE", self.scope[pinned:enable])
        self.assertNotIn("BoosterAppCommissioning::enable", self.scope)
        self.assertLess(self.main.index(self.scope), self.main.index("if(!initInstance())"))
        self.assertLess(self.main.index(self.scope), self.main.index("client.Init();"))

    def test_early_x_subscription_precedes_emergency_handler_start(self):
        subscribe = self.main.index("liveRemoteSubscriber.InitChannel();")
        self.assertLess(subscribe, self.main.index("emergencyHandler.start();"))
        self.assertLess(subscribe, self.main.index("RobotMode::kCustom"))
        self.assertLess(subscribe, self.main.index("bhumanStart(settings);"))
        callback = self.main[:self.main.index("bool watchdog = false;")]
        self.assertIn("BoosterOfficialLiveTest::observeRemoteStopX", callback)
        self.assertIn("Main::stopOfficialLiveTest()", callback)

    def test_real_post_rpc_branch_reasserts_damping_after_late_stop(self):
        change = block(self.source, "int changeMode(const booster::robot::RobotMode mode)")
        normal_branch = block(change, "if(!commissionOnly.load())")
        self.compile_run(r'''
#include "Platform/BoosterOfficialLiveTest.h"
#include <atomic>
#include <string>
#include <vector>
namespace booster::robot {enum class RobotMode {kDamping, kCustom};}
struct Client {
 std::vector<booster::robot::RobotMode> requests;
 int ChangeMode(booster::robot::RobotMode mode) {
   requests.push_back(mode);
   if(mode == booster::robot::RobotMode::kCustom) BoosterOfficialLiveTest::requestStop();
   return 0;
 }
};
struct Handler {
 std::atomic<bool> commissionOnly{false}; Client client;
 int changeMode(booster::robot::RobotMode mode) {
''' + normal_branch + r'''
 return 99;
 }
};
int main(int argc, char** argv) {
 if(argc != 2) return 1;
 const bool enabled = std::string(argv[1]) == "enabled";
 if(enabled) BoosterOfficialLiveTest::enable();
 Handler handler;
 const int result = handler.changeMode(booster::robot::RobotMode::kCustom);
 if(enabled) {
   if(result != -1 || handler.client.requests.size() != 2
      || handler.client.requests.back() != booster::robot::RobotMode::kDamping) return 2;
 } else if(result != 0 || handler.client.requests.size() != 1) return 3;
 return 0;
}
''', (("enabled",), ("default",)))
        first_stop_check = change.index("BoosterOfficialLiveTest::stopRequested()")
        rpc = change.index("const int result = client.ChangeMode(mode);")
        post_check = change.index("BoosterOfficialLiveTest::stopRequested()", rpc)
        self.assertLess(first_stop_check, rpc)
        self.assertLess(rpc, post_check)

    def test_signal_handler_lockfree_stop_precedes_sig_atomic_exit(self):
        handler = block(self.source, "static void sigHandlerOfficialLiveShutdown(int)")
        self.assertIn("static volatile sig_atomic_t run = 1;", self.source)
        self.assertIn("static_assert(std::atomic<bool>::is_always_lock_free)", handler)
        self.assertLess(handler.index("BoosterOfficialLiveTest::requestStop()"), handler.index("run = 0;"))
        for forbidden in ("printf(", "fprintf(", "client.", "ChangeMode(", "post(", "new ", "malloc("):
            self.assertNotIn(forbidden, handler)
        self.compile_run('#include <csignal>\n#include "Platform/BoosterOfficialLiveTest.h"\n'
                         + 'static volatile sig_atomic_t run = 1;\n' + handler + r'''
int main() {
 BoosterOfficialLiveTest::enable();
 if(!BoosterOfficialLiveTest::enabled() || BoosterOfficialLiveTest::stopRequested()) return 1;
 sigHandlerOfficialLiveShutdown(SIGINT);
 if(run != 0 || !BoosterOfficialLiveTest::stopRequested()) return 2;
 BoosterOfficialLiveTest::enable();
 return !BoosterOfficialLiveTest::stopRequested();
}
''')
        self.assertLess(self.scope.index("BoosterOfficialLiveTest::enable();"),
                        self.scope.index("signal(SIGINT, sigHandlerOfficialLiveShutdown)"))
        callback = block(self.source, "void Main::stopOfficialLiveTest()")
        self.assertLess(callback.index("stopRequested()"), callback.index("pthread_kill(mainThread, SIGINT)"))
        for token in ("printf(", "fprintf(", "client.", "ChangeMode("):
            self.assertNotIn(token, callback)

    def test_shutdown_damping_precedes_framework_stop_and_checks_exit_mode(self):
        loop = self.main.index("\n  while(run)\n")
        tail = self.main[loop:]
        before = tail.index("DAMPING_BEFORE_FRAMEWORK_STOP")
        stop = tail.index("bhumanStop();")
        request = tail.index("client.ChangeMode(booster::robot::RobotMode::kDamping)")
        verify = tail.index("client.GetMode(finalMode)")
        self.assertLess(request, verify)
        self.assertLess(verify, before)
        self.assertLess(before, stop)
        self.assertIn("finalMode.mode_ == booster::robot::RobotMode::kDamping && !robot", tail[stop:])
        self.assertIn("liveRemoteSubscriber.CloseChannel()", tail[stop:])


if __name__ == "__main__":
    unittest.main()
