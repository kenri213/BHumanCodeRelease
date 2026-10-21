"""First-frame scheduling regression; no RealSense SDK, USB, or robot access."""
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[2]
PROVIDER = ROOT / "Src/Modules/Infrastructure/CameraProvider/RealSenseProvider.cpp"


class FirstFrameBootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = PROVIDER.read_text()
        cls.update = cls.source.split("void RealSenseProvider::update(CameraImage& theCameraImage)", 1)[1].split("void RealSenseProvider::update(JPEGImage&", 1)[0]
        cls.physical = cls.update.split("#ifdef TARGET_ROBOT", 1)[1].split("#else", 1)[0]

    def test_missing_first_frame_acquires_before_fail_closed_publish_guard(self):
        self.assertIn("if(!yuvFrameData)\n    waitForFrameData2();", self.physical)
        self.assertLess(self.physical.index("waitForFrameData2();"), self.physical.index("throw std::runtime_error"))
        self.assertLess(self.physical.index("throw std::runtime_error"), self.physical.index("theCameraImage.timestamp = imageTimestamp;"))
        self.assertNotIn("Time::getCurrentSystemTime()", self.physical)

    def test_frame_info_depends_on_delivered_camera_image(self):
        header = PROVIDER.with_suffix(".h").read_text()
        self.assertIn("REQUIRES(CameraImage)", header)
        self.assertIn("theFrameInfo.time = theCameraImage.timestamp;", header)

    def test_existing_wait_keeps_deadline_and_rejects_duplicate_frames(self):
        wait = self.source.split("void RealSenseProvider::waitForFrameData2()", 1)[1].split("void RealSenseProvider::releaseFrames()", 1)[0]
        self.assertIn("elapsed >= streamConfig.maxFrameWaitMs", wait)
        self.assertIn("throw std::runtime_error", wait)
        self.assertIn("number > lastFrameNumber", wait)
        self.assertIn("stamp.source != RealSenseCamera::TimestampSource::rejected", wait)

    def test_framework_order_explains_bootstrap_not_a_frameinfo_workaround(self):
        perception = (ROOT / "Src/Threads/Perception.cpp").read_text()
        self.assertIn("RealSenseProvider::waitForFrameData();", perception.split("bool Perception::afterFrame()", 1)[1])
        container = (ROOT / "Src/Libs/Framework/ModuleContainer.cpp").read_text()
        self.assertLess(container.index("moduleGraphRunner.execute()"), container.index("executionUnit->afterFrame()"))

    def test_real_bootstrap_guards_with_success_timeout_shutdown_and_existing_frame(self):
        guards = self.physical.split("  if(!rectificationMap.empty())", 1)[0]
        # Align the extracted body with the synthetic function's trailing
        # statement without changing production first-frame behavior.
        guards = "\n" + textwrap.indent(textwrap.dedent(guards), "    ")
        cpp = r'''
#include <stdexcept>
#include <iostream>
struct Fake {
  const unsigned char* yuvFrameData = nullptr;
  unsigned waits = 0, timestamp = 77, imageTimestamp = 0;
  unsigned char pixel = 1;
  enum Result {frame, timeout, stopping} result = frame;
  void waitForFrameData2() {
    ++waits;
    if(result == timeout) throw std::runtime_error("bounded real acquisition timeout");
    if(result == frame) {yuvFrameData = &pixel; imageTimestamp = 123;}
  }
  void publish() {''' + guards + r'''
    timestamp = imageTimestamp;
  }
};
void require(bool value) {if(!value) throw std::runtime_error("failed assertion");}
int main() {
  Fake first; first.publish();
  require(first.waits == 1 && first.timestamp == 123 && first.yuvFrameData);
  first.publish(); require(first.waits == 1 && first.timestamp == 123);
  for(auto result : {Fake::timeout, Fake::stopping}) {
    Fake missing; missing.result = result;
    bool rejected = false;
    try {missing.publish();} catch(const std::runtime_error&) {rejected = true;}
    require(rejected && missing.waits == 1 && missing.timestamp == 77 && !missing.yuvFrameData);
  }
  std::cout << "first-frame guards passed; no devices loaded\n";
}
'''
        with tempfile.TemporaryDirectory(prefix="k1-camera-bootstrap-") as temporary:
            source = Path(temporary) / "test.cpp"
            executable = Path(temporary) / "test"
            source.write_text(cpp)
            compiled = subprocess.run(["clang++", "-std=c++20", "-Wall", "-Wextra", "-Werror", "-pedantic",
                                       str(source), "-o", str(executable)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("no devices loaded", result.stdout)


if __name__ == "__main__":
    unittest.main()
