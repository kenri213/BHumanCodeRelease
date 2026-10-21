"""SDK-free tests extract real Main predicates/latch methods; no device process."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "Src/Apps/Booster/Main.cpp"


def block(source, marker):
    start = source.index(marker)
    opening = source.index("{", start)
    depth = 1
    index = opening + 1
    while depth:
        depth += (source[index] == "{") - (source[index] == "}")
        index += 1
    return source[start:index]


class EmergencyCommissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = MAIN.read_text()
        cls.commission = block(cls.source, "[[noreturn]] static void runEmergencyOnlyCommission")

    def test_real_predicates_and_latch_compile_and_run_without_sdk(self):
        functions = "\n".join(block(self.source, name) for name in (
            "static bool parseCommissionSeconds", "struct CommissionInputSnapshot",
            "static bool commissionInputsReady", "static bool validCommissionSerial",
            "static bool commissionF1WindowValid", "static bool commissionZeroWarmupReady",
            "template<typename Writer> static bool commissionLowCommandRouteReady"))
        functions = functions.replace(block(self.source, "struct CommissionInputSnapshot"),
                                      block(self.source, "struct CommissionInputSnapshot") + ";")
        methods = "\n".join(block(self.source, name) for name in (
            "void enableEmergencyOnlyCommissioning", "void latchCommissionEmergency",
            "void setState(const State state"))
        test = r'''
#include <atomic>
#include <cstddef>
#include <cstring>
#include <iostream>
#include <thread>
#include <vector>
#include <cstdlib>
static unsigned checks = 0;
static void require(bool value) { ++checks; if(!value) {std::cerr << "Failed " << checks; std::exit(1);} }
''' + functions + r'''
struct Latch {
  enum State {noEmergency, emergencyPressed, emergencyReleased};
  std::atomic<State> state{noEmergency};
  std::atomic<bool> commissionOnly{false}, commissionLatched{false}, commissionFault{false}, commissionDampingAck{false};
  std::atomic<int> commissionRealtime{0};
  struct Signal {void post() {}} stateChangedSignal;
''' + methods + r'''
};
int main() {
  unsigned seconds = 0;
  for(const char* text : {"1", "30", "45"}) require(parseCommissionSeconds(text, seconds));
  for(const char* text : {"", "0", "46", "-1", "+1", "1.5", "nan", "9999999999999999999", " 1"}) require(!parseCommissionSeconds(text, seconds));
  require(!parseCommissionSeconds(nullptr, seconds));
  require(validCommissionSerial("1234567890123"));
  for(const char* text : {"", "1;id", "abc", " 123", "+123"}) require(!validCommissionSerial(text));
  require(!validCommissionSerial(nullptr));
  CommissionInputSnapshot input;
  require(!commissionInputsReady(input, 1000));
  input = {900, 500, 22, 22, 22, 22, true};
  require(commissionInputsReady(input, 1000));
  input.lowTime = 899; require(!commissionInputsReady(input, 1000)); input.lowTime = 900;
  input.statusTime = 499; require(!commissionInputsReady(input, 1000)); input.statusTime = 500;
  input.lowTime = 1001; require(!commissionInputsReady(input, 1000)); input.lowTime = 900;
  input.connectedJoints = 21; require(!commissionInputsReady(input, 1000)); input.connectedJoints = 22;
  input.reportedJoints = 23; require(!commissionInputsReady(input, 1000)); input.reportedJoints = 22;
  input.serialJoints = 0; require(!commissionInputsReady(input, 1000)); input.serialJoints = 22;
  input.parallelJoints = 23; require(!commissionInputsReady(input, 1000)); input.parallelJoints = 22;
  input.finite = false; require(!commissionInputsReady(input, 1000));
  require(commissionF1WindowValid(true, true, 200, 100, 300));
  require(!commissionF1WindowValid(false, true, 200, 100, 300));
  require(!commissionF1WindowValid(true, false, 200, 100, 300));
  require(!commissionF1WindowValid(true, true, 99, 100, 300));
  require(!commissionF1WindowValid(true, true, 301, 100, 300));
  require(!commissionF1WindowValid(true, true, 200, -1, 300));
  require(commissionZeroWarmupReady(11, 100, 200));
  require(commissionZeroWarmupReady(10, 100, 600));
  require(!commissionZeroWarmupReady(9, 100, 200));
  require(!commissionZeroWarmupReady(11, 100, 199));
  require(!commissionZeroWarmupReady(11, 100, 601));
  require(!commissionZeroWarmupReady(11, -1, 200));
  struct Writer {size_t readers; size_t GetMatchedSubscriptionsCount() {return readers;}};
  struct LegacyWriter {};
  Writer writer{0}; LegacyWriter legacy;
  require(!commissionLowCommandRouteReady(writer));
  writer.readers = 1; require(commissionLowCommandRouteReady(writer));
  writer.readers = 2; require(commissionLowCommandRouteReady(writer));
  require(!commissionLowCommandRouteReady(legacy));
  Latch latch;
  latch.enableEmergencyOnlyCommissioning();
  latch.setState(Latch::noEmergency); require(latch.state.load() == Latch::noEmergency);
  latch.latchCommissionEmergency();
  latch.setState(Latch::noEmergency); require(latch.state.load() == Latch::emergencyPressed);
  latch.setState(Latch::emergencyReleased); require(latch.state.load() == Latch::emergencyPressed);
  latch.commissionDampingAck.store(true); latch.latchCommissionEmergency(); require(latch.commissionDampingAck.load());
  for(unsigned repetition = 0; repetition < 100; ++repetition) {
    latch.enableEmergencyOnlyCommissioning();
    std::thread arm([&] {latch.setState(Latch::noEmergency);});
    std::thread f1([&] {latch.latchCommissionEmergency();});
    arm.join(); f1.join();
    require(latch.commissionLatched.load() && latch.state.load() == Latch::emergencyPressed);
  }
  Latch normal;
  normal.setState(Latch::emergencyReleased); require(normal.state.load() == Latch::emergencyReleased);
  normal.setState(Latch::noEmergency); require(normal.state.load() == Latch::noEmergency);
  std::cout << checks << " pure commissioning checks passed; no SDK loaded\n";
}
'''
        with tempfile.TemporaryDirectory(prefix="k1-emergency-pure-") as temporary:
            source = Path(temporary) / "test.cpp"
            output = Path(temporary) / "test"
            source.write_text(test)
            compiled = subprocess.run(["clang++", "-std=c++20", "-pthread", "-Wall", "-Wextra", "-Werror",
                                       "-pedantic", str(source), "-o", str(output)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            tested = subprocess.run([str(output)], capture_output=True, text=True, timeout=10)
            self.assertEqual(tested.returncode, 0, tested.stderr)
            self.assertIn("no SDK loaded", tested.stdout)

    def test_special_entry_precedes_normal_startup(self):
        main = self.source[self.source.index("int main(int argc"):]
        entry = main.index("runEmergencyOnlyCommission(commissionSeconds, commissionExpectedSerial)")
        for token in ("FunctionList::execute", "fork()", "ledControl.Init()", "bhumanStart(settings)"):
            self.assertLess(entry, main.index(token))

    def test_no_robot_models_skills_or_normal_button_handlers(self):
        for forbidden in ("new Robot", "BoosterProvider", "bhumanStart", "FunctionList::execute", "ledControl",
                          "SystemCall::say", "buttonEventHandler(", "robotStatusHandler(", "kWalking", "GetUp(", "Move(", "VisualKick("):
            self.assertNotIn(forbidden, self.commission)

    def test_serial_is_required_and_checked_before_dds_and_realtime(self):
        self.assertIn("--commission-expected-serial", self.source)
        serial = self.commission.index('"/sys/firmware/devicetree/base/serial-number"')
        self.assertLess(serial, self.commission.index("pthread_setschedparam"))
        self.assertLess(serial, self.commission.index("ChannelFactory::Instance()->Init"))
        self.assertIn("actualSerial != expectedSerial", self.commission)
        self.assertIn('ChannelFactory::Instance()->Init(0, "127.0.0.1")', self.commission)

    def test_f1_is_latched_press_only_and_state_is_atomic(self):
        self.assertIn("std::atomic<State> state", self.source)
        button = self.commission[self.commission.index('buttons("rt/button_even2"'):self.commission.index('low("rt/low_state"')]
        self.assertIn("value.button() == 0", button)
        self.assertIn("value.event() == booster_interface::msg::PRESS_DOWN", button)
        self.assertIn("latchCommissionEmergency()", button)
        self.assertNotIn("LONG_PRESS_START", button)

    def test_only_explicit_zero_pd_and_no_commissioned_marker(self):
        initialize = block(self.source, "void init(const Settings::RobotType robotType)")
        self.assertIn("numOfJoints != 22", initialize)
        self.assertIn("throw std::runtime_error", initialize)
        for field in ("q", "dq", "tau", "kp", "kd", "weight"):
            self.assertIn("motor." + field + "(0.f)", initialize)
        self.assertIn("motor.mode(0)", initialize)
        self.assertNotIn("std::ofstream", self.commission)

    def test_actual_rt_and_final_recovery_order_are_checked(self):
        self.assertGreaterEqual(self.source.count("pthread_getschedparam"), 2)
        self.assertIn("REAL_EMERGENCY_HANDLER_DAMPING_ACK", self.commission)
        self.assertLess(self.commission.index("emergencyHandler.stopCommissioning(2000)"),
                        self.commission.index("FINAL_DAMPING_REQUEST"))
        self.assertLess(self.commission.index("status.CloseChannel()"), self.commission.index("std::_Exit(result)"))
        self.assertIn("DANGER_DAMPING_UNCONFIRMED", self.commission)

    def test_private_thread_cannot_cancel_and_timeout_does_not_close_or_join(self):
        start = block(self.source, "void start()")
        self.assertIn("commissionThread = std::thread", start)
        self.assertIn("pthread_setcancelstate(PTHREAD_CANCEL_DISABLE", start)
        self.assertIn("commissionExited.store(true)", start)
        commission_start = start[:start.index("Thread::start(this")]
        self.assertNotIn("Thread::start(", commission_start)
        stop = block(self.source, "bool stopCommissioning(unsigned timeout)")
        self.assertNotIn("Thread::stop", stop)
        self.assertNotIn("pthread_cancel", stop)
        self.assertLess(stop.index("return false"), stop.index("commissionThread.join()"))
        self.assertLess(stop.index("return false"), stop.index("lowCmdPublisher.CloseChannel()"))
        shutdown = self.commission[self.commission.index("bool handlerExited = false;"):]
        timeout_exit = shutdown.index("std::_Exit(EXIT_FAILURE)")
        self.assertLess(timeout_exit, shutdown.index("FINAL_DAMPING_REQUEST"))

    def test_hardware_discovery_and_zero_warmup_precede_custom(self):
        prepare = self.commission.index("emergencyHandler.init(Settings::k1)")
        discovery = self.commission.index("commissioningLowCommandReaderReady()", prepare)
        warmup = self.commission.index("commissionZeroWarmupReady(zeroWrites", discovery)
        request = self.commission.index("emergencyHandler.setState(EmergencyHandler::noEmergency)")
        self.assertLess(discovery, self.commission.index("emergencyHandler.resetMotorCommands()", prepare))
        self.assertLess(warmup, request)
        self.assertIn("ZERO_PD_WARMUP_INPUT_OR_DISCOVERY_LOST", self.commission)
        self.assertIn("DENIED_LOW_COMMAND_HARDWARE_READER_NOT_DISCOVERED", self.commission)

    def test_real_cooperative_stop_handles_fake_late_rpc_without_forced_cancel(self):
        method = block(self.source, "bool stopCommissioning(unsigned timeout)")
        harness = r'''
#include <atomic>
#include <chrono>
#include <thread>
#include <iostream>
struct Thread {static void sleep(unsigned ms) {std::this_thread::sleep_for(std::chrono::milliseconds(ms));}};
struct Handler {
 std::atomic<bool> commissionRunning{true}, commissionExited{false};
 std::thread commissionThread;
 struct Signal {void post() {}} stateChangedSignal;
 struct Publisher {unsigned closed = 0; void CloseChannel() {++closed;}} lowCmdPublisher;
''' + method + r'''
};
int main() {
 Handler late;
 late.commissionThread = std::thread([&] {Thread::sleep(80); late.commissionExited.store(true);});
 if(late.stopCommissioning(5) || late.lowCmdPublisher.closed || !late.commissionThread.joinable()) return 1;
 if(late.commissionRunning.load()) return 2;
 if(!late.stopCommissioning(500) || late.lowCmdPublisher.closed != 1 || late.commissionThread.joinable()) return 3;
 Handler timely;
 timely.commissionThread = std::thread([&] {while(timely.commissionRunning.load()) Thread::sleep(1); timely.commissionExited.store(true);});
 if(!timely.stopCommissioning(500) || timely.lowCmdPublisher.closed != 1 || timely.commissionThread.joinable()) return 4;
 Handler notStarted;
 notStarted.commissionExited.store(true);
 if(!notStarted.stopCommissioning(5) || notStarted.lowCmdPublisher.closed != 1) return 5;
 std::cout << "late RPC timeout preserved live objects; cooperative join tested\n";
}
'''
        with tempfile.TemporaryDirectory(prefix="k1-emergency-exit-") as temporary:
            source = Path(temporary) / "test.cpp"
            output = Path(temporary) / "test"
            source.write_text(harness)
            compiled = subprocess.run(["clang++", "-std=c++20", "-pthread", "-Wall", "-Wextra", "-Werror",
                                       "-pedantic", str(source), "-o", str(output)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            tested = subprocess.run([str(output)], capture_output=True, text=True, timeout=10)
            self.assertEqual(tested.returncode, 0, tested.stderr)
            self.assertIn("cooperative join tested", tested.stdout)

    def test_diagnostics_identify_transient_callback_causes_without_sdk(self):
        declarations = block(self.source, "enum CommissionCallbackCause") + ";\n"
        declarations += "\n".join(block(self.source, name) for name in (
            "static unsigned commissionLowFaultCauses", "static unsigned commissionStatusFaultCauses",
            "template<typename Endpoint> static size_t commissionLowCommandReaderCount",
            "template<typename Endpoint> static size_t commissionButtonWriterCount"))
        harness = r'''
#include <cstddef>
''' + declarations + r'''
int main() {
 if(commissionLowFaultCauses(true, 22, 22)) return 1;
 if(commissionLowFaultCauses(false, 22, 22) != lowNonfinite) return 2;
 if(commissionLowFaultCauses(true, 0, 22) != lowSerialCount) return 3;
 if(commissionLowFaultCauses(true, 22, 23) != lowParallelCount) return 4;
 if(commissionLowFaultCauses(false, 21, 0) != (lowNonfinite | lowSerialCount | lowParallelCount)) return 5;
 if(commissionStatusFaultCauses(22, 22)) return 6;
 if(commissionStatusFaultCauses(23, 22) != statusJointCount) return 7;
 if(commissionStatusFaultCauses(22, 21) != statusDisconnected) return 8;
 if(commissionStatusFaultCauses(23, 21) != (statusJointCount | statusDisconnected)) return 9;
 struct Endpoint {
   size_t GetMatchedSubscriptionsCount() {return 3;}
   size_t GetMatchedPublicationsCount() {return 2;}
 } endpoint;
 struct Legacy {} legacy;
 if(commissionLowCommandReaderCount(endpoint) != 3 || commissionButtonWriterCount(endpoint) != 2) return 10;
 if(commissionLowCommandReaderCount(legacy) || commissionButtonWriterCount(legacy)) return 11;
 return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="k1-emergency-diagnostic-") as temporary:
            source = Path(temporary) / "test.cpp"
            output = Path(temporary) / "test"
            source.write_text(harness)
            compiled = subprocess.run(["clang++", "-std=c++20", "-Wall", "-Wextra", "-Werror", "-pedantic",
                                       str(source), "-o", str(output)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            tested = subprocess.run([str(output)], capture_output=True, text=True, timeout=10)
            self.assertEqual(tested.returncode, 0, tested.stderr)

    def test_diagnostics_preserve_safety_limits_and_do_not_print_in_callbacks(self):
        ready = block(self.source, "static bool commissionInputsReady")
        self.assertIn("now - input.lowTime <= 100 && now - input.statusTime <= 500", ready)
        self.assertIn("input.serialJoints == 22 && input.parallelJoints == 22", ready)
        self.assertIn("input.reportedJoints == 22 && input.connectedJoints == 22 && input.finite", ready)
        callbacks = self.commission[self.commission.index('buttons("rt/button_even2"'):self.commission.index("const auto execute =")]
        self.assertNotIn("printf(", callbacks)
        self.assertIn("callbackCauseMask.fetch_or", callbacks)
        fault = self.commission[self.commission.index("const bool diagnosticCallbackFault"):self.commission.index("if(Clock::now() >= nextMode)")]
        self.assertEqual(fault.count("readInputs()"), 1)
        self.assertEqual(fault.count("clockMs()"), 1)
        self.assertIn("!commissionInputsReady(diagnosticInput, diagnosticNow)", fault)
        self.assertIn("diagnosticInput.serialJoints, diagnosticInput.parallelJoints", fault)
        self.assertIn("commissionButtonWriterCount(buttons)", fault)
        self.assertIn("commissioningLowCommandReaders()", fault)
        reset = block(self.source, "bool resetMotorCommands()")
        self.assertIn("commissionWriteFalseCount.fetch_add(1)", reset)
        self.assertIn("commissionWriteExceptionCount.fetch_add(1)", reset)

    def test_fault_latch_precedes_diagnostic_io_and_write_mutex_has_no_logs(self):
        reset = block(self.source, "bool resetMotorCommands()")
        self.assertNotIn("printf(", reset)
        self.assertNotIn("fprintf(", reset)
        mode = block(self.source, "int changeMode(const booster::robot::RobotMode mode)")
        self.assertNotIn("printf(", mode)
        self.assertNotIn("fprintf(", mode)
        fault = self.commission[self.commission.index("if(diagnosticCallbackFault"):self.commission.index("if(Clock::now() >= nextMode)")]
        latch = fault.index("emergencyHandler.latchCommissionEmergency()")
        self.assertLess(latch, fault.index("printf("))
        self.assertLess(latch, fault.index("commissioningLowCommandReaders()"))
        self.assertLess(latch, fault.index("printCommissionCallbackCauses("))
        self.assertLess(latch, fault.index("printCommissioningFaultCounters()"))
        start = block(self.source, "void start()")
        self.assertNotIn("printf(", start)
        handler = block(self.source, "void handler()")
        rt_failure = handler[handler.index("if(!realtime)"):handler.index("while(handlerRunning())")]
        self.assertLess(rt_failure.index("latchCommissionEmergency()"), rt_failure.index("printf("))


if __name__ == "__main__":
    unittest.main()
