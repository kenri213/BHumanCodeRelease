"""Single-hold Prepare/GetUp admission; pure C++, no SDK, services or robot."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PHYSICAL = ROOT / "Src/Modules/BehaviorControl/SkillBehaviorControl/Options/HandlePhysicalRobot.cpp"
PROVIDER = ROOT / "Src/Modules/Infrastructure/GameStateProvider/GameStateProvider.cpp"
HARNESS = r'''
#define state(name) static_assert(false, "CABSL macro collision")
#include "Tools/BehaviorControl/CombinedLiveGetUpGesture.h"
#include "Platform/BoosterMatchSafety.h"
#undef state
#include <array>
#include <cstdlib>
#include <string>
namespace G = CombinedLiveGetUpGesture;
static void check(bool value) {if(!value) std::abort();}
static G::Input good(unsigned frame = 100) {
  G::Input in;
  in.enabled = in.authorized = in.joystickValid = in.sticksNeutral = in.neutralButtons = true;
  in.rawInitial = in.unpenalized = in.imuFinite = in.canPrepare = in.lyingStable = true;
  in.emergency = in.stopped = false;
  in.frameTime = frame;in.gcNow = frame + 100;in.gcPacketTime = frame + 90;in.epoch = 1;
  return in;
}
static G::Result tick(G::Gate& gate, unsigned frame, bool pressed = true, bool prepare = false) {
  auto in = good(frame);in.neutralButtons = !pressed;in.rbAOnly = pressed;in.executedPrepare = prepare;
  return gate.update(in);
}
static void neutral(G::Gate& gate) {tick(gate, 100, false);tick(gate, 130, false);}
static void pending(G::Gate& gate) {
  neutral(gate);check(!tick(gate, 160).requestPrepare);
  check(!tick(gate, 2159).requestPrepare);check(tick(gate, 2160).requestPrepare);
}
int main(int argc, char** argv) {
  check(argc == 2);const std::string name = argv[1];
  if(name == "single") {
    G::Gate gate;pending(gate);
    check(!tick(gate, 2200, true, true).requestGetUp);
    check(!tick(gate, 4199, true, true).requestGetUp);
    check(tick(gate, 4200, true, true).requestGetUp);
    check(!tick(gate, 9000, true, true).requestPrepare); // Continued old hold cannot retry.
    check(!tick(gate, 9001, true, true).requestGetUp);
    tick(gate, 9030, false, true);tick(gate, 9060, true, true);
    check(tick(gate, 11060, true, true).requestPrepare); // Explicit new neutral/hold may retry.
  } else if(name == "release") {
    G::Gate gate;pending(gate); // No second press or explicit release needed.
    check(!tick(gate, 2200, false, true).requestGetUp);
    check(tick(gate, 4200, false, true).requestGetUp);
  } else if(name == "upright") {
    G::Gate gate;auto in = good(100);in.lyingStable = false;in.uprightStable = true;
    gate.update(in);in.frameTime = 130;gate.update(in);
    in.frameTime = 160;in.neutralButtons = false;in.rbAOnly = true;gate.update(in);
    in.frameTime = 2160;check(gate.update(in).requestPrepare);check(gate.pending());
    in.frameTime = 2200;in.executedPrepare = true;check(!gate.update(in).requestActive);
    in.frameTime = 4199;check(!gate.update(in).requestActive);check(gate.pending());
    in.frameTime = 4200;const auto result = gate.update(in);
    check(result.requestActive && !result.requestGetUp && !gate.pending());
    G::Gate changing;pending(changing);tick(changing, 2200, true, true);
    auto changed = good(4200);changed.rbAOnly = true;changed.neutralButtons = false;
    changed.executedPrepare = true;changed.lyingStable = false;changed.uprightStable = true;
    check(!changing.update(changed).requestActive); // Cannot reuse old lying stability.
    changed.frameTime = 4499;check(!changing.update(changed).requestActive);
    changed.frameTime = 4500;check(changing.update(changed).requestActive);
  } else if(name == "held") {
    G::Gate gate;check(!tick(gate, 100).requestPrepare);check(!tick(gate, 9000).requestPrepare);
    tick(gate, 9010, false);tick(gate, 9040);check(tick(gate, 11040).requestPrepare);
    G::Gate alone;neutral(alone);auto in = good(160);in.neutralButtons = false;
    check(!alone.update(in).requestPrepare); // A-first/other combo consumes neutral.
    check(!tick(alone, 200).requestPrepare);check(!tick(alone, 6000).requestPrepare);
  } else if(name == "gates") {
    for(unsigned bad = 0;bad < 14;++bad) {
      G::Gate gate;pending(gate);auto in = good(2200);in.rbAOnly = true;in.neutralButtons = false;
      if(bad == 0)in.enabled = false;if(bad == 1)in.authorized = false;
      if(bad == 2)in.emergency = true;if(bad == 3)in.joystickValid = false;
      if(bad == 4)in.sticksNeutral = false;if(bad == 5)in.rawInitial = false;
      if(bad == 6)in.stopped = true;if(bad == 7)in.unpenalized = false;
      if(bad == 8)in.imuFinite = false;if(bad == 9)in.gcPacketTime = 0;
      if(bad == 10)in.gcPacketTime = in.gcNow - 2001;
      if(bad == 11)in.gcPacketTime = in.gcNow + 1;
      if(bad == 12)in.canPrepare = false;
      if(bad == 13){in.rbAOnly = false;in.neutralButtons = false;}
      const auto result = gate.update(in);
      if(bad == 10) { // Ordinary packet-age gap pauses an accepted intent only.
        check(!result.cancelled && gate.pending() && !result.requestPrepare && !result.requestGetUp && !result.requestActive);
        in.frameTime = 2233;in.stopped = true;check(gate.update(in).cancelled);continue;
      }
      check(result.cancelled && !result.requestGetUp);
      check(!tick(gate, 6000, true, true).requestGetUp);
      check(!tick(gate, 9000, true, true).requestPrepare);
    }
  } else if(name == "bounds") {
    G::Gate gate;pending(gate);check(tick(gate, 10161).cancelled);
    check(!tick(gate, 13000, true, true).requestGetUp);
    for(unsigned kind = 0;kind < 3;++kind) {
      G::Gate item;pending(item);auto in = good(kind == 0 ? 2160 : kind == 1 ? 80 : 2200);
      in.rbAOnly = true;in.neutralButtons = false;if(kind == 2)in.epoch = 2;
      check(item.update(in).cancelled);check(!tick(item, 9000).requestPrepare);
    }
  } else if(name == "stable") {
    G::Gate gate;pending(gate);tick(gate, 2200, true, true);
    auto in = good(4100);in.executedPrepare = true;in.lyingStable = false;
    check(!gate.update(in).requestGetUp);
    check(!tick(gate, 4200, true, true).requestGetUp);
    check(!tick(gate, 4499, true, true).requestGetUp);
    check(tick(gate, 4500, true, true).requestGetUp);
    G::Gate phases;pending(phases);tick(phases, 2200, true, true);
    tick(phases, 4100, true, false);tick(phases, 4200, true, true);
    check(!tick(phases, 6100, true, true).requestGetUp);
    check(tick(phases, 6200, true, true).requestGetUp);
  } else if(name == "admission") {
    for(unsigned bad = 0;bad < 4;++bad) {
      G::offerPrepare(2000, 4);
      check(!G::consumePrepare(bad == 0 ? 1999 : bad == 1 ? 2151 : 2033,
                              bad == 2 ? 5 : 4, bad != 3));
      check(!G::consumePrepare(2033, 4, true)); // Invalid consume also revokes.
    }
    G::offerPrepare(2000, 4);check(G::consumePrepare(2033, 4, true));
    check(!G::consumePrepare(2034, 4, true));
    G::offerPrepare(2000, 4);G::revokePrepare();check(!G::consumePrepare(2033, 4, true));
  } else if(name == "raw") {
    struct Player {unsigned penalty = 0;};
    struct Team {unsigned teamNumber = 0;std::array<Player, 3> players;};
    struct Packet {unsigned playersPerTeam = 3;std::array<Team, 2> teams;};
    Packet packet;packet.teams[0].teamNumber = 115;packet.teams[1].teamNumber = 116;
    check(G::ownPlayerUnpenalized(packet, 115, 1));
    check(!G::ownPlayerUnpenalized(packet, 115, 0));check(!G::ownPlayerUnpenalized(packet, 115, 4));
    check(!G::ownPlayerUnpenalized(packet, 117, 1));
    packet.teams[0].players[0].penalty = 1;check(!G::ownPlayerUnpenalized(packet, 115, 1));
    packet.teams[0].players[0].penalty = 0;packet.teams[1].teamNumber = 115;
    check(!G::ownPlayerUnpenalized(packet, 115, 1));
    check(G::freshInitial(true, false, true, 3000, 1000));
    check(!G::freshInitial(true, false, true, 3001, 1000));
  } else if(name == "initial_epoch") {
    BoosterMatchSafety::enable();BoosterMatchSafety::noteGcInitial(true);
    const auto epoch = BoosterMatchSafety::epoch();G::Gate gate;
    auto in = good(100);in.epoch = epoch;gate.update(in);in.frameTime = 130;gate.update(in);
    in.frameTime = 160;in.neutralButtons = false;in.rbAOnly = true;gate.update(in);
    in.frameTime = 2160;check(gate.update(in).requestPrepare);
    G::offerPrepare(2160, epoch);BoosterMatchSafety::noteGcInitial(true);
    check(BoosterMatchSafety::epoch() == epoch);check(G::consumePrepare(2193, epoch, true));
    in.frameTime = 2200;in.executedPrepare = true;gate.update(in);
    in.frameTime = 4200;check(gate.update(in).requestGetUp);
  } else return 2;
}
'''


class CombinedGetUpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="combined-getup-pure-")
        cls.addClassCleanup(cls.temp.cleanup)
        source = Path(cls.temp.name) / "combined.cpp"
        cls.binary = Path(cls.temp.name) / "combined"
        source.write_text(HARNESS)
        subprocess.run(["/usr/bin/clang++", "-std=c++20", "-Wall", "-Wextra", "-Werror", "-DNDEBUG",
                        "-I" + str(ROOT / "Src"), "-I" + str(ROOT / "Src/Libs"),
                        str(source), "-o", str(cls.binary)],
                       check=True, capture_output=True, text=True)

    def run_case(self, name):
        subprocess.run([str(self.binary), name], check=True, capture_output=True, text=True)

    def test_single_hold_prepares_then_preserves_two_second_interpolation(self):self.run_case("single")
    def test_release_after_admission_needs_no_second_command(self):self.run_case("release")
    def test_upright_single_hold_waits_actual_prepare_then_stand_not_getup(self):self.run_case("upright")
    def test_old_held_combo_and_a_first_cannot_reuse_neutral(self):self.run_case("held")
    def test_stop_gc_f1_authorization_bad_input_and_imu_revoke_without_retry(self):self.run_case("gates")
    def test_timeout_frame_epoch_changes_are_bounded(self):self.run_case("bounds")
    def test_actual_prepare_and_recent_stability_both_required(self):self.run_case("stable")
    def test_prepare_admission_is_fresh_epoch_bound_and_single_use(self):self.run_case("admission")
    def test_raw_own_team_player_penalty_and_gc_freshness(self):self.run_case("raw")
    def test_first_raw_initial_epoch_does_not_drop_accepted_first_command(self):self.run_case("initial_epoch")

    def test_module_ordering_preserves_legacy_paths_and_does_not_fake_keys(self):
        physical = PHYSICAL.read_text();provider = PROVIDER.read_text()
        self.assertIn("combinedGetUpGate.update(combinedInput)", physical)
        self.assertIn("CombinedLiveGetUpGesture::offerPrepare", physical)
        self.assertIn("combinedGetUp.requestGetUp", physical)
        self.assertIn("if(combinedGetUp.requestActive)", physical)
        self.assertIn("!combinedGetUpGate.pending() && matchRecoveryPermitted", physical)
        self.assertIn("&& (!BoosterMatchSafety::enabled() || combinedInput.rawInitial)", physical)
        self.assertIn("&& theEnhancedKeyStates.pressedDuration[KeyStates::walk] > pressDuration", physical)
        self.assertIn("theEnhancedKeyStates.hitStreak[KeyStates::walk] == 3", physical)
        self.assertIn("LiveGetUpGesture::update", physical)
        self.assertLess(provider.index("CombinedLiveGetUpGesture::consumePrepare"),
                        provider.index("switch(gameState.playerState)"))
        self.assertIn("!BoosterMatchSafety::enabled() || (combinedPreparePermitted && theGameControllerData.state == STATE_INITIAL)", provider)
        self.assertIn("&& theEnhancedKeyStates.pressedDuration[KeyStates::stand] > pressDurationUnstiffToPrepare", provider)
        self.assertRegex(provider, r"pressDurationUnstiffToPrepare\)\s*\|\| combinedPrepareAdmission")
        self.assertNotIn("theEnhancedKeyStates.pressed[KeyStates::stand] =", physical + provider)
        raw = (ROOT / "Src/Modules/Communication/GameControllerDataProvider/GameControllerDataProvider.cpp").read_text()
        self.assertLess(raw.index("theGameControllerData) = buffer"), raw.index("noteGcInitial"))


WIRING_PREFIX = r'''
#include "Tools/BehaviorControl/CombinedLiveGetUpGesture.h"
#include "Tools/BehaviorControl/LiveGetUpGesture.h"
#include "Platform/BoosterMatchSafety.h"
#include <array>
#include <cstdint>
#include <cstdlib>
#include <string>
namespace G = CombinedLiveGetUpGesture;
static void check(bool value) {if(!value) std::abort();}
constexpr std::uint8_t STATE_INITIAL = 0;
namespace MotionPhase {enum Type {playDead, stand, walk, fall, getUp, prepare};}
static_assert(MotionPhase::playDead == 0 && MotionPhase::prepare == 5);
struct Settings {int teamNumber = 115, playerNumber = 1;};
namespace Global {static Settings settings;Settings& getSettings() {return settings;}}
struct Player {std::uint8_t penalty = 0;};
struct Team {std::uint8_t teamNumber = 0;std::array<Player, 20> players;};
struct Packet {
  std::uint8_t state = 0, stopped = 0, playersPerTeam = 5;
  std::array<Team, 2> teams;
  Packet() {teams[0].teamNumber = 115;teams[1].teamNumber = 116;}
} theGameControllerData;
struct Vector {float x() const {return xx;}float y() const {return yy;}float z() const {return zz;}
               float xx = 0, yy = 0, zz = 0;};
struct Imu {Vector angle{1.2f, 0.f, 0.f}, gyro;} theInertialData;
struct Remote {bool valid = true;std::array<float, 8> axes{};unsigned buttons = 0;} theJoystickState;
struct Sensors {bool emergencyMode = false;} theSystemSensorData;
struct Motion {MotionPhase::Type executedPhase = MotionPhase::playDead;} theMotionInfo;
struct Frame {unsigned time = 0;} theFrameInfo;
struct LiveInput {bool lyingStable = false;unsigned gcNow = 0, gcPacketTime = 0;};
bool liveMode = true, liveAuthorized = true;
constexpr float maxTorsoForWalk = .261799388f, maxGyroForWalk = .261799388f;
G::Input wired(unsigned frame, unsigned buttons, bool prepare = false) {
  theFrameInfo.time = frame;theJoystickState.buttons = buttons;
  theMotionInfo.executedPhase = prepare ? MotionPhase::prepare : MotionPhase::playDead;
  const LiveInput liveInput{LiveGetUpGesture::stableLying(theInertialData.angle.x(), theInertialData.angle.y(),
    theInertialData.gyro.x(), theInertialData.gyro.y(), theInertialData.gyro.z(), 1.04719755f, .087266463f),
    frame + 100, frame + 90};
'''
WIRING_SUFFIX = r'''
  return combinedInput;
}
static G::Result tick(G::Gate& gate, unsigned frame, unsigned buttons, bool prepare = false) {
  return gate.update(wired(frame, buttons, prepare));
}
static void neutral(G::Gate& gate) {tick(gate, 123967, 0);tick(gate, 124000, 0);}
static void journalPending(G::Gate& gate) {
  neutral(gate);check(!tick(gate, 134000, 32).requestPrepare);
  check(gate.released() && !gate.chordHolding() && gate.reason() == G::Reason::waitingA);
  check(!tick(gate, 136000, 34).requestPrepare);
  check(gate.chordHolding() && gate.holdMs(136000) == 0);
  check(!tick(gate, 137999, 34).requestPrepare);check(tick(gate, 138000, 34).requestPrepare);
}
int main(int argc, char** argv) {
  check(argc == 2);const std::string name = argv[1];
  BoosterMatchSafety::enable();BoosterMatchSafety::noteGcInitial(true);
  if(name == "journal") {
    G::Gate gate;journalPending(gate);tick(gate, 138033, 34, true);
    check(tick(gate, 140033, 34, true).requestGetUp);
  } else if(name == "first_rb") {
    G::Gate gate;
    tick(gate, 134000, 32);tick(gate, 134033, 32); // No SDK neutral event exists.
    check(gate.released() && gate.reason() == G::Reason::waitingA);
    tick(gate, 136000, 34);check(tick(gate, 138000, 34).requestPrepare);
  } else if(name == "first_rba") {
    G::Gate gate;
    for(unsigned frame = 134000;frame <= 144000;frame += 100)
      check(!tick(gate, frame, 34).requestPrepare);
    check(!gate.pending() && !gate.released());
  } else if(name == "first_rb_expired") {
    G::Gate gate;tick(gate, 134000, 32);tick(gate, 134033, 32);
    for(unsigned frame = 134066;frame <= 146000;frame += 33)
      check(!tick(gate, frame, 32).requestPrepare);
    tick(gate, 146033, 34);check(!tick(gate, 148100, 34).requestPrepare);
    tick(gate, 148200, 0);tick(gate, 148233, 32);tick(gate, 148266, 34);
    check(tick(gate, 150266, 34).requestPrepare);
  } else if(name == "rb_only") {
    G::Gate gate;neutral(gate);
    for(unsigned frame = 134000;frame <= 140000;frame += 100)
      check(!tick(gate, frame, 32).requestPrepare);
    check(!gate.pending() && !gate.released());
    check(!tick(gate, 140033, 34).requestPrepare);check(!tick(gate, 144000, 34).requestPrepare);
  } else if(name == "prefix_window") {
    G::Gate late;neutral(late);tick(late, 134000, 32);tick(late, 138900, 32);
    tick(late, 138999, 34);check(tick(late, 140999, 34).requestPrepare);
    G::Gate boundary;neutral(boundary);tick(boundary, 134000, 32);
    tick(boundary, 139000, 34);check(tick(boundary, 141000, 34).requestPrepare);
    G::Gate expired;neutral(expired);tick(expired, 134000, 32);tick(expired, 138999, 32);
    check(!tick(expired, 139001, 34).requestPrepare);
    check(expired.reason() == G::Reason::modifierExpired && !expired.released());
    check(!tick(expired, 143000, 34).requestPrepare); // Must observe neutral again.
  } else if(name == "continuous_hold") {
    G::Gate gate;neutral(gate);tick(gate, 134000, 32);tick(gate, 136000, 34);
    check(!tick(gate, 137000, 32).requestPrepare); // Releasing A breaks pre-acceptance hold.
    check(!gate.released());tick(gate, 137100, 34);
    check(!tick(gate, 141000, 34).requestPrepare);
  } else if(name == "a_first_wrong_keys") {
    for(unsigned wrong : {2u, 4u, 36u, 35u, 42u}) {
      G::Gate gate;neutral(gate);tick(gate, 134000, wrong);
      check(!gate.released());tick(gate, 136000, 34);
      check(!tick(gate, 140000, 34).requestPrepare);
    }
  } else if(name == "release_a_first" || name == "release_rb_first") {
    G::Gate gate;journalPending(gate);tick(gate, 138033, 34, true);
    check(!tick(gate, 138066, name == "release_a_first" ? 32 : 2, true).cancelled);
    check(!tick(gate, 138100, 0, true).cancelled);check(gate.pending());
    check(tick(gate, 140033, 0, true).requestGetUp);
    check(!tick(gate, 142100, 0, true).requestGetUp);
  } else if(name == "pending_other_keys") {
    for(unsigned wrong : {1u, 4u, 8u, 36u, 35u, 42u}) {
      G::Gate gate;journalPending(gate);tick(gate, 138033, 34, true);
      check(tick(gate, 138066, wrong, true).cancelled);
      check(!tick(gate, 141000, 34, true).requestGetUp);
    }
  } else if(name == "wired_reason") {
    G::Gate gate;neutral(gate);
    theGameControllerData.state = 2;tick(gate, 134000, 32);
    check(gate.reason() == G::Reason::rawNotInitial);
    theGameControllerData.state = 4;tick(gate, 134010, 32);
    check(gate.reason() == G::Reason::rawNotInitial);
    theGameControllerData.state = 1;
    G::Gate ready;neutral(ready);tick(ready, 134000, 32);tick(ready, 136000, 34);
    check(tick(ready, 138000, 34).requestPrepare);
    theGameControllerData.state = 0;theGameControllerData.stopped = 1;tick(gate, 134033, 32);
    check(gate.reason() == G::Reason::gcStopped);
    theGameControllerData.stopped = 0;theGameControllerData.teams[0].players[0].penalty = 1;
    tick(gate, 134066, 32);check(gate.reason() == G::Reason::rawPlayerUnavailable);
    theGameControllerData.teams[0].players[0].penalty = 0;theJoystickState.axes[0] = .2f;
    tick(gate, 134100, 32);check(gate.reason() == G::Reason::sticksNotNeutral);
    theJoystickState.axes[0] = 0;theJoystickState.valid = false;tick(gate, 134133, 32);
    check(gate.reason() == G::Reason::invalidController);
  } else return 2;
}
'''


class ProductionCombinedInputTests(unittest.TestCase):
    """Compile the EXACT production initializer, not independently hand-wired booleans."""
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="combined-getup-wiring-")
        cls.addClassCleanup(cls.temp.cleanup)
        physical = PHYSICAL.read_text()
        begin = physical.index("  bool getUpSticksNeutral = true;")
        end = physical.index("  physicalPickupVisited = true;", begin)
        production_initializer = physical[begin:end]
        source = Path(cls.temp.name) / "wired.cpp"
        cls.binary = Path(cls.temp.name) / "wired"
        source.write_text(WIRING_PREFIX + production_initializer + WIRING_SUFFIX)
        subprocess.run(["/usr/bin/clang++", "-std=c++20", "-Wall", "-Wextra", "-Werror", "-DNDEBUG",
                        "-I" + str(ROOT / "Src"), "-I" + str(ROOT / "Src/Libs"),
                        str(source), "-o", str(cls.binary)], check=True, capture_output=True, text=True)

    def run_case(self, name):
        subprocess.run([str(self.binary), name], check=True, capture_output=True, text=True)

    def test_actual_journal_neutral_rb_then_a_enters_prepare(self):self.run_case("journal")
    def test_first_sdk_event_rb_proves_a_up_without_inventing_idle_packet(self):self.run_case("first_rb")
    def test_first_sdk_event_preheld_rba_cannot_arm(self):self.run_case("first_rba")
    def test_first_rb_prefix_cannot_auto_renew_after_expiry_then_neutral_allows_retry(self):self.run_case("first_rb_expired")
    def test_rb_alone_never_starts_and_expiry_requires_new_neutral(self):self.run_case("rb_only")
    def test_prefix_window_five_seconds_not_renewed_then_full_chord_gets_two_seconds(self):self.run_case("prefix_window")
    def test_two_second_hold_requires_continuous_both_buttons(self):self.run_case("continuous_hold")
    def test_a_first_and_wrong_keys_still_consume_neutral(self):self.run_case("a_first_wrong_keys")
    def test_accepted_intent_survives_a_released_first(self):self.run_case("release_a_first")
    def test_accepted_intent_survives_rb_released_first(self):self.run_case("release_rb_first")
    def test_other_keys_cancel_pending_without_retry(self):self.run_case("pending_other_keys")
    def test_actual_raw_uint8_packet_enum_and_controller_fields_produce_specific_reasons(self):self.run_case("wired_reason")

    def test_production_reason_log_is_explicit_and_rate_limited(self):
        physical = PHYSICAL.read_text()
        for token in ("LIVE_COMBINED_GETUP reason=%s", "raw_gc=%u", "raw_unpenalized=%d",
                      "valid=%d", "axes_neutral=%d", "released=%d", "hold_ms=%u", "rb_prefix_ms=%u", "pending=%d"):
            self.assertIn(token, physical)
        self.assertIn("combinedGetUpLastLog >= 2000", physical)
        self.assertIn("combinedReasonChanged && theFrameInfo.time - combinedGetUpLastLog >= 250", physical)


if __name__ == "__main__":unittest.main()
