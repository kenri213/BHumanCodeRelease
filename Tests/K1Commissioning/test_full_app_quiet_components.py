"""Pure extracted-code/source checks; no SDK, audio device, camera, or robot."""
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "Src/Modules/BehaviorControl/SkillBehaviorControl"
INFRA = ROOT / "Src/Modules/Infrastructure"


class QuietFullApplicationComponents(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.skill = (SKILL / "SkillBehaviorControl.cpp").read_text()
        cls.option = (SKILL / "Options/CommissionPlayDead.cpp").read_text()
        cls.audio = (INFRA / "AudioProvider/AudioProvider.cpp").read_text()
        cls.camera = (INFRA / "CameraProvider/RealSenseProvider.cpp").read_text()

    def test_root_is_forced_at_runtime_not_only_in_configuration(self):
        branch = self.skill.split("beginFrame(theFrameInfo.time);", 1)[1].split("endFrame();", 1)[0]
        self.assertIn("const bool commissioning = BoosterAppCommissioning::enabled();", branch)
        self.assertIn('if(commissioning)\n  {\n    execute("CommissionPlayDead");\n    BoosterAppCommissioning::noteBehaviorFrame();\n  }\n  else\n    execute(root);', branch)
        self.assertIn("option(CommissionPlayDead);", (SKILL / "Options/Options.h").read_text())

    def test_option_only_requests_head_off_and_play_dead(self):
        self.assertIn(".pan = JointAngles::off", self.option)
        self.assertIn(".tilt = JointAngles::off", self.option)
        self.assertIn("PlayDead();", self.option)
        self.assertNotIn("transition", self.option)
        for forbidden in ("Prepare();", "Stand();", "GetUp(", "Walk(", "HandleJoystick", "executeRequest"):
            self.assertNotIn(forbidden, self.option)

    def test_ball_interventions_are_disabled_only_in_commissioning(self):
        for field in ("shouldInterceptBall", "shouldWalkOutOfBallLine"):
            self.assertIn("theMotionRequest." + field + " = !commissioning &&", self.skill)

    def test_audio_guard_covers_call_and_setter_before_any_mixer_access(self):
        self.assertIn("if(!BoosterAppCommissioning::enabled() && captureVolume != currentCaptureVolume)", self.audio)
        setter = self.audio.split("bool AudioProvider::setCaptureVolume()", 1)[1]
        self.assertLess(setter.index("if(BoosterAppCommissioning::enabled())"), setter.index("snd_mixer_t*"))
        self.assertIn("return true;", setter[:setter.index("snd_mixer_t*")])

    def test_camera_counts_only_first_delivered_target_frame(self):
        update = self.camera.split("void RealSenseProvider::update(CameraImage& theCameraImage)", 1)[1].split("void RealSenseProvider::update(JPEGImage&", 1)[0]
        physical, desktop = update.split("#else", 1)
        self.assertEqual(self.camera.count("BoosterAppCommissioning::noteCameraFrame();"), 1)
        self.assertIn("imageTimestamp != lastImageTimestamp", physical)
        self.assertLess(physical.index("theCameraImage.timestamp = imageTimestamp;"), physical.index("noteCameraFrame();"))
        self.assertLess(physical.index("noteCameraFrame();"), physical.index("lastImageTimestamp = imageTimestamp;"))
        self.assertNotIn("noteCameraFrame", desktop)
        self.assertNotIn("noteCameraFrame", self.camera.split("RealSenseProvider::RealSenseProvider()", 1)[1].split("RealSenseProvider::~RealSenseProvider()", 1)[0])

    def test_extracted_root_audio_and_frame_guards_execute_without_devices(self):
        root = self.skill.split("beginFrame(theFrameInfo.time);", 1)[1].split("endFrame();", 1)[0]
        action = self.option.split("    action\n    {", 1)[1].split("\n    }", 1)[0]
        camera = self.camera.split("  theCameraImage.timestamp = imageTimestamp;", 1)[1].split("#else", 1)[0]
        setter = self.audio.split("bool AudioProvider::setCaptureVolume()\n{", 1)[1].split("  snd_mixer_t*", 1)[0]
        # Keep extracted statements and the fake mixer tail at the same scope
        # indentation; strict host Clang otherwise flags the generated fixture.
        setter = "\n" + textwrap.indent(textwrap.dedent(setter), "    ")
        program = r'''
#include <cstring>
#include <cstdlib>
#include <iostream>
namespace BoosterAppCommissioning {
  bool active = false;
  unsigned frames = 0;
  unsigned behaviors = 0;
  bool enabled() { return active; }
  void noteCameraFrame() { ++frames; }
  void noteBehaviorFrame() { ++behaviors; }
}
struct JointAngles { static constexpr int off = -1000; };
struct Head { int pan, tilt; };
struct Fake {
  int root = 7;
  unsigned normal = 0, quiet = 0, heads = 0, dead = 0, mixer = 0;
  unsigned imageTimestamp = 0, lastImageTimestamp = 0;
  Head head{0, 0};
  void execute(int value) { if(value != 7) std::abort(); ++normal; }
  void execute(const char* value) {
    if(std::strcmp(value, "CommissionPlayDead")) std::abort();
    ++quiet; quietAction();
  }
  void LookAtAngles(Head value) { head = value; ++heads; }
  void PlayDead() { ++dead; }
  void quietAction() {''' + action + r'''
  }
  void run() {''' + root + r'''
  }
  void deliver() {''' + camera + r'''
  }
  bool setCaptureVolume() {''' + setter + r'''
    ++mixer;
    return true;
  }
};
static void require(bool value) { if(!value) std::abort(); }
int main() {
  Fake value;
  value.run(); require(value.normal == 1 && value.quiet == 0);
  require(BoosterAppCommissioning::behaviors == 0);
  BoosterAppCommissioning::active = true;
  for(unsigned i = 0; i < 20; ++i) value.run();
  require(value.normal == 1 && value.quiet == 20 && value.heads == 20 && value.dead == 20);
  require(BoosterAppCommissioning::behaviors == 20);
  require(value.head.pan == JointAngles::off && value.head.tilt == JointAngles::off);
  require(value.setCaptureVolume() && value.mixer == 0);
  value.imageTimestamp = 123; value.deliver(); value.deliver();
  require(BoosterAppCommissioning::frames == 1);
  value.imageTimestamp = 124; value.deliver();
  require(BoosterAppCommissioning::frames == 2);
  BoosterAppCommissioning::active = false;
  value.run(); require(value.normal == 2 && value.quiet == 20);
  require(BoosterAppCommissioning::behaviors == 20);
  require(value.setCaptureVolume() && value.mixer == 1);
  value.imageTimestamp = 125; value.deliver();
  require(BoosterAppCommissioning::frames == 2);
  std::cout << "quiet component guards passed; no devices loaded\n";
}
'''
        with tempfile.TemporaryDirectory(prefix="k1-quiet-components-") as temporary:
            cpp = Path(temporary) / "test.cpp"
            executable = Path(temporary) / "test"
            cpp.write_text(program)
            compile_result = subprocess.run(["clang++", "-std=c++20", "-Wall", "-Wextra", "-Werror", "-pedantic",
                                             str(cpp), "-o", str(executable)], capture_output=True, text=True)
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("no devices loaded", result.stdout)


if __name__ == "__main__":
    unittest.main()
