"""SDK-free diagnostic regression; never opens USB or a robot connection."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PROVIDER = ROOT / "Src/Modules/Infrastructure/CameraProvider/RealSenseProvider.cpp"


class AcquisitionDiagnosticsTests(unittest.TestCase):
    def test_cpu_evidence_and_unchanged_timestamp_policy(self):
        with tempfile.TemporaryDirectory(prefix="k1-camera-diagnostics-") as temporary:
            executable = str(Path(temporary) / "diagnostics")
            result = subprocess.run(["clang++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-pedantic",
                                     str(Path(__file__).with_name("acquisition_diagnostics_test.cpp")), "-o", executable],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([executable], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("no devices loaded", result.stdout)
            self.assertIn("comparisons=553536", result.stdout)

    def test_real_wait_keeps_original_deadline_and_acceptance_gates(self):
        source = PROVIDER.read_text()
        wait = source.split("void RealSenseProvider::waitForFrameData2()", 1)[1].split("void RealSenseProvider::releaseFrames()", 1)[0]
        self.assertIn("elapsed >= streamConfig.maxFrameWaitMs", wait)
        self.assertIn("number > lastFrameNumber", wait)
        self.assertIn("stamp.source != RealSenseCamera::TimestampSource::rejected", wait)
        self.assertIn("throw std::runtime_error(evidence.str());", wait)
        self.assertNotIn("setupCamera();", wait.split("const auto start =", 1)[1])
        self.assertNotIn("startStream();", wait)
        self.assertLess(wait.index("acquisitionDiagnostics.noteColor("), wait.index("if((!hasFrameNumber"))
        self.assertGreater(wait.index("acquisitionDiagnostics.noteAccepted("), wait.index("hasFrameNumber = true;"))
        self.assertIn("(unsigned)(1500) maxFrameWaitMs", PROVIDER.with_suffix(".h").read_text())

    def test_timeout_preserves_classified_evidence(self):
        source = PROVIDER.read_text()
        for field in ["received=", "color=", "duplicate=", "backward=", "stale=", "clock_regression=",
                      "observed_clock_regressions=", "last_received_frame=", "last_accepted_frame=",
                      "received_arrival=", "accepted_arrival=", "received_host_ms=", "accepted_host_ms=",
                      "timeout_host_ms=", "timeout_epoch_ms=", "last_timestamp_rejection="]:
            self.assertIn('" ' + field, source)
        self.assertIn("++acquisitionDiagnostics.received;", source)


if __name__ == "__main__":
    unittest.main()
