"""Ensure the minimal v4 candidate retains exact v2 native football/reentry."""
import hashlib
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
BASE = "48fd18a05834b6f2165ef28ad47256655c50c7e9"
MAIN = "Src/Apps/Booster/Main.cpp"
NATIVE = (
    "Src/Modules/Modeling/SelfLocator/SelfLocator.cpp",
    "Src/Modules/BehaviorControl/SkillBehaviorControl/Options/HandlePhysicalRobot.cpp",
    "Src/Modules/BehaviorControl/SkillBehaviorControl/Options/HandleReturnFromTouchline.cpp",
    "Src/Modules/BehaviorControl/SkillBehaviorControl/Options/HandleJoystick.cpp",
    "Src/Modules/BehaviorControl/SkillBehaviorControl/SkillBehaviorControl.cpp",
    "Src/Modules/BehaviorControl/SkillBehaviorControl/SkillBehaviorControl.h",
    "Src/Modules/MotionControl/MotionEngine/MotionEngine.cpp",
    "Src/Libs/Platform/BoosterRemoteStand.h",
    "Src/Libs/Platform/BoosterMatchSafety.h",
)


def original(path):
    return subprocess.check_output(["git", "show", BASE + ":" + path], cwd=ROOT)


class NativeRetainedV4Tests(unittest.TestCase):
    def test_native_reentry_motion_and_control_files_are_exact_v2(self):
        for path in NATIVE:
            with self.subTest(path=path):
                self.assertEqual((ROOT / path).read_bytes(), original(path))

    def test_main_only_adds_the_fixed_log_directory_helper(self):
        source = (ROOT / MAIN).read_text()
        additions = (
            '#include "Platform/BoosterMatchLogDirectory.h"\n',
            '      BoosterMatchLogDirectory::prepare987();\n',
            '      printf("K1_987_MATCH LOG_DIRECTORY_READY path=/home/booster/logs logger_recording_not_yet_verified=1\\n");\n',
        )
        for addition in additions:
            self.assertEqual(source.count(addition), 1)
            source = source.replace(addition, "")
        self.assertEqual(source.encode(), original(MAIN))

    def test_no_v3_session_geometry_or_custom_reentry_markers_exist(self):
        self.assertFalse((ROOT / "Src/Libs/Platform/BoosterPickupReentry.h").exists())
        self.assertFalse((ROOT / "Tests/K1Commissioning/test_pickup_reentry_localization.py").exists())
        for path in (ROOT / "Src").rglob("*"):
            if path.is_file() and path.suffix in (".h", ".cpp"):
                source = path.read_bytes()
                for marker in (b"BoosterPickupReentry", b"PICKUP_REENTRY", b"manual_prior=1"):
                    with self.subTest(path=str(path.relative_to(ROOT)), marker=marker):
                        self.assertNotIn(marker, source)

    def test_only_two_existing_production_files_changed(self):
        changed = subprocess.check_output(
            ["git", "diff", "--name-only", BASE, "--", "Src", "Config", "Make"], cwd=ROOT, text=True
        ).splitlines()
        # Before staging git omits the new helper; after staging/committing it
        # includes it. Keep the same production boundary in both cases.
        self.assertEqual(set(changed) - {"Src/Libs/Platform/BoosterMatchLogDirectory.h"},
                         {MAIN, "Src/Libs/Platform/BoosterPickupControl.h"})

    def test_verified_minimal_helpers_are_exact(self):
        expected = {
            "Src/Libs/Platform/BoosterPickupControl.h": "01ca7563776a3a2fbaae73425af19f74974f2d00edd28ef49a7bc66146e7439c",
            "Src/Libs/Platform/BoosterMatchLogDirectory.h": "883364c83f9d9470e502a94e40dffffd0b18377e6a88d0d7857dddc62b93b49c",
        }
        for path, digest in expected.items():
            with self.subTest(path=path):
                self.assertEqual(hashlib.sha256((ROOT / path).read_bytes()).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
