"""Static guard checks; never starts the DDS diagnostic or connects a robot."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "Tools/K1Commissioning/dds_observer.cpp"


class ObserverSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text()

    def test_no_application_actuation_api(self):
        for forbidden in (r"\bChannelPublisher\b", r"\bCreateSendChannel\b", r"\bB1LocoClient\b",
                          r"\bChangeMode\s*\(", r"\bLowCmd\b", r"\bWrite\s*\(",
                          r"\bsystem\s*\(", r"\bexecv?p?\s*\(", r"rt/joint_ctrl", r"LocoApiTopic"):
            self.assertIsNone(re.search(forbidden, self.source), forbidden)

    def test_only_the_six_expected_topics(self):
        self.assertEqual(set(re.findall(r'"(rt/[a-z_0-9]+)"', self.source)), {
            "rt/remote_controller_state", "rt/remote_controller_stat2", "rt/button_event",
            "rt/button_even2", "rt/low_state", "rt/device_gateway"})
        self.assertEqual(self.source.count("add(new ChannelSubscriber<"), 6)

    def test_zero_duration_is_before_dds_initialization(self):
        self.assertLess(self.source.index("if(!options.durationSeconds)"),
                        self.source.index("ChannelFactory::Instance()->Init"))

    def test_no_sdk_global_teardown_and_bounded_exit(self):
        self.assertIn("std::_Exit(124)", self.source)
        self.assertIn("std::_Exit(exitCode)", self.source)
        self.assertIn("reader->CloseChannel()", self.source)
        self.assertLess(self.source.index("state->accepting.store(false"),
                        self.source.index("for(auto iterator = closeReaders.rbegin()"))

    def test_callbacks_do_not_print(self):
        callbacks = self.source[self.source.index("auto remoteHandler"):self.source.index("template<typename Sample> Json status")]
        self.assertNotIn("std::cout", callbacks)
        self.assertNotIn("std::cerr", callbacks)
        self.assertNotIn("emit(", callbacks)

    def test_disconnected_names_and_head_feedback_are_not_omitted(self):
        self.assertIn('"disconnected_joint_names"', self.source)
        self.assertIn('"disconnected_joint_count"', self.source)
        self.assertIn('"head_q_radians"', self.source)
        self.assertIn('"parallel_joint_count"', self.source)

    def test_all_callback_aggregates_are_reported(self):
        for key in ("min_connected_joint_count", "max_disconnected_joint_count", "any_disconnected_names",
                    "disconnected_name_callback_counts", "callbacks_with_disconnection", "callbacks_with_nonfinite",
                    "nonfinite_values", "all_serial_q_dq", "all_parallel_q_dq_tau_est"):
            self.assertIn('"' + key + '"', self.source)
        self.assertIn("data.gatewayAggregate.observe", self.source)
        self.assertIn("data.remoteFinite[index].observe", self.source)
        self.assertIn("data.imuFinite.observe", self.source)


if __name__ == "__main__":
    unittest.main()
