#!/usr/bin/env python3
"""Static profile contracts only: no camera, build, simulator, or robot access."""

from pathlib import Path
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
BASE = "2e3374ba896da8288607f9a1881b876642fe18b8"
PROFILE = ROOT / "Config/Scenarios/K1RealSense/K1"
CAMERA_REPRESENTATIONS = ("CameraImage", "CameraInfo", "CameraIntrinsics", "FrameInfo")


def read(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def color_providers(text):
    """Inspect only the Color thread, not Cognition's FrameInfo provider."""
    color = text.split("name = Color;", 1)[1].split("name = Cognition;", 1)[0]
    return re.findall(r"\{representation\s*=\s*(\w+);\s*provider\s*=\s*(\w+);\}", color)


class K1RealSenseProfileTests(unittest.TestCase):
    def setUp(self):
        self.default = read("Config/Scenarios/Default/K1/threads.cfg")
        self.profile = (PROFILE / "threads.cfg").read_text(encoding="utf-8")

    def test_profile_changes_exactly_four_provider_assignments(self):
        expected = self.default
        for representation in CAMERA_REPRESENTATIONS:
            old = f"{{representation = {representation}; provider = X5CameraProvider;}}"
            self.assertEqual(expected.count(old), 1)
            expected = expected.replace(
                old, f"{{representation = {representation}; provider = RealSenseProvider;}}"
            )
        self.assertEqual(self.profile, expected)

    def test_color_has_no_duplicate_representation(self):
        names = [name for name, _ in color_providers(self.profile)]
        self.assertEqual(len(names), len(set(names)))

    def test_real_sense_is_the_only_camera_provider(self):
        providers = dict(color_providers(self.profile))
        for representation in CAMERA_REPRESENTATIONS:
            self.assertEqual(providers[representation], "RealSenseProvider")
        self.assertNotIn("X5CameraProvider", providers.values())

    def test_parallel_jpeg_is_preserved(self):
        providers = dict(color_providers(self.profile))
        self.assertEqual(providers["JPEGImage"], "ParallelJPEGImageProvider")
        self.assertEqual(providers["JPEGImageRequest"], "ParallelJPEGImageProvider")

    def test_default_k1_still_uses_x5(self):
        providers = dict(color_providers(self.default))
        for representation in CAMERA_REPRESENTATIONS:
            self.assertEqual(providers[representation], "X5CameraProvider")

    def test_default_t1_keeps_real_sense_and_original_jpeg(self):
        providers = dict(color_providers(read("Config/Scenarios/Default/T1/threads.cfg")))
        for representation in CAMERA_REPRESENTATIONS:
            self.assertEqual(providers[representation], "RealSenseProvider")
        self.assertEqual(providers["JPEGImage"], "RealSenseProvider")

    def test_resolution_is_a_real_sense_640_profile(self):
        resolution = (PROFILE / "cameraResolution.cfg").read_text(encoding="utf-8")
        assignments = re.findall(r"^\s*resolution\s*=\s*(\w+)\s*;", resolution, re.MULTILINE)
        self.assertEqual(assignments, ["w640h480f30"])
        enum = read("Src/Representations/Configuration/CameraResolutionRequest.h")
        self.assertRegex(enum, r"\bw640h480f30\b")

    def test_detector_width_has_a_bundled_model(self):
        model = ROOT / "Config/NeuralNets/RobotBallPerceptor/ball_robot_640.onnx"
        self.assertTrue(model.is_file())
        self.assertGreater(model.stat().st_size, 1000)
        detector = read("Src/Modules/Perception/MultiPerceptors/RobotBallPerceptor.cpp")
        self.assertIn("std::to_string(theCameraInfo.width)", detector)

    def test_optional_acquisition_config_is_complete_and_separate(self):
        config = (PROFILE / "realSenseCamera.cfg").read_text(encoding="utf-8")
        values = dict(re.findall(r"^\s*(\w+)\s*=\s*([^;]+);", config, re.MULTILINE))
        self.assertEqual(values, {
            "serialNumber": '""',
            "enableDepthKeepAlive": "true",
            "depthWidth": "424",
            "depthHeight": "240",
            "depthFrameRate": "5",
            "rectifyBrownConrady": "true",
            "maxMetadataAgeMs": "500",
            "maxFrameWaitMs": "1500",
        })
        self.assertFalse((PROFILE / "realSenseProvider.cfg").exists())

    def test_acquisition_time_budgets_and_depth_are_positive(self):
        config = (PROFILE / "realSenseCamera.cfg").read_text(encoding="utf-8")
        values = dict(re.findall(r"^\s*(\w+)\s*=\s*(\d+)\s*;", config, re.MULTILINE))
        for key in ("depthWidth", "depthHeight", "depthFrameRate",
                    "maxMetadataAgeMs", "maxFrameWaitMs"):
            self.assertGreater(int(values[key]), 0)

    def test_no_generic_intrinsics_or_t1_body_override_is_installed(self):
        # Factory RGB calibration and the physical K1 extrinsics need separate checks.
        names = {path.name for path in PROFILE.iterdir() if path.is_file()}
        self.assertNotIn("cameraIntrinsics.cfg", names)
        self.assertNotIn("cameraCalibration.cfg", names)
        self.assertNotIn("robotDimensions.cfg", names)
        self.assertNotIn("walkingEngine.cfg", names)
        self.assertNotIn("motorParameters.cfg", names)
        self.assertNotIn("getUpEngine.cfg", names)

    def test_specific_scenario_precedes_default_location(self):
        # Named locations can shadow a scenario; Locations/Default cannot.
        settings = read("Src/Libs/Framework/Settings.cpp")
        robot = settings.index('"Robots/" + robotName')
        named_location = settings.index('"Locations/" + location')
        named_scenario = settings.index('"Scenarios/" + scenario')
        default_robot = settings.index('"Robots/Default/"')
        default_location = settings.index('"Locations/Default/"')
        self.assertLess(robot, named_location)
        self.assertLess(named_location, named_scenario)
        self.assertLess(named_scenario, default_robot)
        self.assertLess(default_robot, default_location)

    def test_original_camera_and_motion_configs_are_unchanged(self):
        protected = (
            "Config/Scenarios/Default/K1/threads.cfg",
            "Config/Scenarios/Default/T1/threads.cfg",
            "Config/Scenarios/AnyPlaceDemo/K1/threads.cfg",
            "Config/Scenarios/AnyPlaceDemo/T1/threads.cfg",
            "Config/Robots/Default/K1/cameraResolution.cfg",
            "Config/Robots/Default/T1/cameraResolution.cfg",
            "Config/Robots/Default/K1/cameraIntrinsics.cfg",
            "Config/Robots/Default/T1/cameraIntrinsics.cfg",
            "Config/Robots/Default/K1/robotDimensions.cfg",
            "Config/Robots/Default/K1/walkingEngine.cfg",
            "Config/Robots/Default/K1/motorParameters.cfg",
            "Config/Robots/Default/K1/getUpEngine.cfg",
        )
        for relative in protected:
            with self.subTest(path=relative):
                baseline = subprocess.run(
                    ["git", "show", f"{BASE}:{relative}"], cwd=ROOT,
                    check=True, capture_output=True,
                ).stdout
                self.assertEqual((ROOT / relative).read_bytes(), baseline)


if __name__ == "__main__":
    unittest.main(verbosity=2)
