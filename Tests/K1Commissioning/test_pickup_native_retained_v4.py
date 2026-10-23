"""Retain v4 native football/reentry while explicitly migrating resident F1."""
import hashlib
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
BASE = "48fd18a05834b6f2165ef28ad47256655c50c7e9"
V4 = "48ce90279f27f8a51680661f448184fb2f2ecb9b"
F1_SOURCE = "1cf8bf14a6cc079999b807a946d6df31c528ed16"
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


def original(path, commit=BASE):
    return subprocess.check_output(["git", "show", commit + ":" + path], cwd=ROOT)


def block(source, marker):
    start = source.index(marker)
    cursor = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[cursor] == "{") - (source[cursor] == "}")
        cursor += 1
    return source[start:cursor]


class NativeRetainedV4Tests(unittest.TestCase):
    def test_native_reentry_motion_and_control_files_are_exact_v2(self):
        for path in NATIVE:
            with self.subTest(path=path):
                current = (ROOT / path).read_bytes()
                if path.endswith("Options/HandlePhysicalRobot.cpp"):
                    # F1 no longer destroys the process. Only replace that old
                    # transaction lifetime; every normal Pickup branch stays.
                    start = current.index(b"  // BEGIN_OFFICIAL_F1_PICKUP_LIFECYCLE\n")
                    end_marker = b"  // END_OFFICIAL_F1_PICKUP_LIFECYCLE\n"
                    end = current.index(end_marker, start) + len(end_marker)
                    hook = current[start:end]
                    self.assertIn(b"officialButtonMode()", hook)
                    self.assertIn(b"!liveAuthorized || theGameState.playerState == GameState::unstiff", hook)
                    self.assertIn(b"physicalPickupGate = BoosterPickupControl::Gate{};", hook)
                    current = current[:start] + current[end:]
                self.assertEqual(current, original(path))

    def test_main_keeps_exact_987_identity_log_preparation_and_996_scope(self):
        source = (ROOT / MAIN).read_text()
        base = original(MAIN, V4).decode()
        additions = (
            '#include "Platform/BoosterMatchLogDirectory.h"\n',
            '      BoosterMatchLogDirectory::prepare987();\n',
            '      printf("K1_987_MATCH LOG_DIRECTORY_READY path=/home/booster/logs logger_recording_not_yet_verified=1\\n");\n',
        )
        for addition in additions:
            self.assertEqual(source.count(addition), 1)
        for marker in ("static bool match987IdentityAllowed(", "if(officialLiveTest)\n    {"):
            self.assertEqual(block(source, marker), block(base, marker))
        scope = block(source, "if(officialMatch987)\n    {")
        self.assertEqual(scope.count("BoosterOfficialLiveTest::enableOfficialButtonMode();"), 1)
        self.assertLess(scope.index("match987IdentityAllowed"), scope.index("enableOfficialButtonMode();"))
        self.assertNotIn("--k1-977-match", source)

    def test_real_resident_f1_main_methods_match_the_pinned_source(self):
        source = (ROOT / MAIN).read_text()
        official = original(MAIN, F1_SOURCE).decode()
        for marker in ("int changeMode(const booster::robot::RobotMode mode)",
                       "static void buttonEventHandler(const void* msg)"):
            with self.subTest(marker=marker):
                self.assertEqual(block(source, marker), block(official, marker))

    def test_no_v3_session_geometry_or_custom_reentry_markers_exist(self):
        self.assertFalse((ROOT / "Src/Libs/Platform/BoosterPickupReentry.h").exists())
        self.assertFalse((ROOT / "Tests/K1Commissioning/test_pickup_reentry_localization.py").exists())
        for path in (ROOT / "Src").rglob("*"):
            if path.is_file() and path.suffix in (".h", ".cpp"):
                source = path.read_bytes()
                for marker in (b"BoosterPickupReentry", b"PICKUP_REENTRY", b"manual_prior=1"):
                    with self.subTest(path=str(path.relative_to(ROOT)), marker=marker):
                        self.assertNotIn(marker, source)

    def test_only_explicit_official_f1_production_paths_change_from_v4(self):
        changed = subprocess.check_output(
            ["git", "diff", "--name-only", V4, "--", "Src", "Config", "Make"], cwd=ROOT, text=True
        ).splitlines()
        self.assertEqual(set(changed), {
            MAIN, "Src/Libs/Platform/BoosterOfficialLiveTest.h",
            "Src/Modules/Infrastructure/BoosterProvider/BoosterProvider.cpp",
            "Src/Modules/Infrastructure/GameStateProvider/GameStateProvider.cpp",
            "Src/Modules/Infrastructure/GameStateProvider/GameStateProvider.h",
            "Src/Modules/BehaviorControl/SkillBehaviorControl/Options/HandlePhysicalRobot.cpp",
        })

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
