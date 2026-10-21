"""Compile the real SDK-free recovery headers; no robot, SDK or services."""
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
HARNESS = r'''
#define state(name) invalid_cabsl_macro(name)
#include "Tools/BehaviorControl/CombinedLiveGetUpGesture.h"
#include "Tools/BehaviorControl/LiveGetUpGesture.h"
#undef state
#include <cstdlib>
#include <string>
namespace G = CombinedLiveGetUpGesture;
namespace L = LiveGetUpGesture;
static void check(bool value) {if(!value) std::abort();}
static G::Input input(unsigned raw, unsigned frame, bool pressed = false, bool prepare = false) {
  G::Input v;
  v.enabled = v.authorized = v.joystickValid = v.sticksNeutral = v.unpenalized = v.imuFinite = v.canPrepare = v.lyingStable = true;
  v.emergency = v.stopped = false;
  v.rawInitial = raw == 0;
  v.rawRecoveryAllowed = raw != 0 && G::recoveryStateAllowed(raw);
  v.neutralButtons = !pressed;v.rbAOnly = pressed;v.executedPrepare = prepare;
  v.frameTime = frame;v.gcNow = 100000 + frame;v.gcPacketTime = v.gcNow - 10;v.epoch = 1;
  return v;
}
static void noMotion(const G::Result& r) {check(!r.requestPrepare && !r.requestGetUp && !r.requestActive);}
static void accepted(G::Gate& gate, unsigned raw, bool upright = false) {
  auto v = input(raw, 100);v.lyingStable = !upright;v.uprightStable = upright;gate.update(v);
  v.frameTime = 130;gate.update(v);v.frameTime = 160;v.neutralButtons = false;v.rbAOnly = true;gate.update(v);
  v.frameTime = 2159;noMotion(gate.update(v));v.frameTime = 2160;
  check(gate.update(v).requestPrepare && gate.pending());
}
static L::Input liveInput(unsigned raw, unsigned frame, bool pressed = false) {
  L::Input v;v.live = v.authorized = v.prepare = v.joystickValid = v.gcActive = v.lyingStable = true;
  v.emergency = v.b = v.stand = v.stopped = v.penalized = false;
  v.initial = v.packetInitial = raw == 0;v.matchRecoveryAllowed = raw != 0 && G::recoveryStateAllowed(raw);
  v.rb = v.a = v.walk = pressed;v.frameTime = frame;v.gcNow = 100000 + frame;v.gcPacketTime = v.gcNow - 10;
  return v;
}
int main(int argc, char** argv) {
  check(argc == 2);const std::string name = argv[1];
  if(name == "raw") {
    for(unsigned raw : {0u, 1u, 3u}) {
      check(G::recoveryStateAllowed(raw));check(G::freshRecovery(raw, false, true, 10000, 8000));
      check(!G::freshRecovery(raw, false, true, 10001, 8000));
      check(!G::freshRecovery(raw, true, true, 10000, 9000));
      check(!G::freshRecovery(raw, false, false, 10000, 9000));
      check(!G::freshRecovery(raw, false, true, 10000, 0));
      check(!G::freshRecovery(raw, false, true, 10000, 10001));
    }
    for(unsigned raw : {2u, 4u, 5u, 255u})check(!G::freshRecovery(raw, false, true, 10000, 9000));
  } else if(name == "states") {
    for(unsigned raw : {0u, 1u, 3u}) {
      G::Gate gate;accepted(gate, raw);
      noMotion(gate.update(input(raw, 2200, false, true)));
      noMotion(gate.update(input(raw, 4199, false, true)));
      const auto r = gate.update(input(raw, 4200, false, true));
      check(r.requestGetUp && !r.requestActive && !gate.pending());
    }
    for(unsigned raw : {2u, 4u, 5u, 255u}) {
      G::Gate gate;gate.update(input(raw, 100));gate.update(input(raw, 130));
      for(unsigned t : {160u, 2160u, 5000u}) {const auto r = gate.update(input(raw, t, true));noMotion(r);check(!gate.pending());}
    }
  } else if(name == "hard") {
    for(unsigned raw : {0u, 1u, 3u})for(unsigned bad = 0;bad < 12;++bad) {
      G::Gate gate;accepted(gate, raw);auto v = input(raw, 2200, false, true);
      switch(bad) {
        case 0:v.stopped = true;break;case 1:v.unpenalized = false;break;case 2:v.authorized = false;break;
        case 3:v.emergency = true;break;case 4:v.joystickValid = false;break;case 5:v.sticksNeutral = false;break;
        case 6:v.imuFinite = false;break;case 7:v.epoch = 2;break;case 8:v.rawInitial = v.rawRecoveryAllowed = false;break;
        case 9:v.gcPacketTime = 0;break;case 10:v.gcPacketTime = v.gcNow + 1;break;case 11:v.canPrepare = false;break;
      }
      const auto r = gate.update(v);noMotion(r);check(r.cancelled && !r.refreshPrepareAdmission && !gate.pending());
    }
  } else if(name == "upright_entry") {
    for(unsigned raw : {1u, 3u}) {
      G::Gate gate;
      for(unsigned t : {100u, 130u, 160u, 2160u, 4200u}) {
        auto v = input(raw, t, t >= 160);v.lyingStable = false;v.uprightStable = true;
        const auto r = gate.update(v);noMotion(r);check(!r.refreshPrepareAdmission && !gate.pending());
      }
      check(gate.reason() == G::Reason::recoveryPoseRequired);
    }
    G::Gate original;accepted(original, 0, true);
    auto v = input(0, 2200, false, true);v.lyingStable = false;v.uprightStable = true;original.update(v);
    v.frameTime = 4200;const auto r = original.update(v);check(r.requestActive && !r.requestGetUp);
  } else if(name == "upright_pending") {
    for(unsigned raw : {1u, 3u}) {
      G::Gate gate;accepted(gate, raw);gate.update(input(raw, 2200, false, true));
      auto v = input(raw, 4200, false, true);v.lyingStable = false;v.uprightStable = true;noMotion(gate.update(v));
      v.frameTime = 4500;noMotion(gate.update(v));check(gate.pending());
      noMotion(gate.update(input(raw, 4600, false, true)));
      noMotion(gate.update(input(raw, 4899, false, true)));
      check(gate.update(input(raw, 4900, false, true)).requestGetUp);
    }
  } else if(name == "stale") {
    for(unsigned raw : {1u, 3u}) {
      G::Gate gate;accepted(gate, raw);auto v = input(raw, 2200, false, true);gate.update(v);
      v = input(raw, 4200, false, true);v.gcPacketTime = v.gcNow - 2001;
      auto r = gate.update(v);noMotion(r);check(gate.pending() && !r.refreshPrepareAdmission);
      check(gate.update(input(raw, 4233, false, true)).requestGetUp);
    }
  } else if(name == "refresh") {
    for(unsigned raw : {0u, 1u, 3u}) {
      G::Gate gate;accepted(gate, raw);auto r = gate.update(input(raw, 2200));
      noMotion(r);check(r.refreshPrepareAdmission && gate.pending());
      r = gate.update(input(raw, 10010));noMotion(r);check(r.refreshPrepareAdmission); // pending age 7850
      G::offerPrepare(10010, 1);check(G::consumePrepare(10160, 1, true)); // unchanged 150ms TTL
      r = gate.update(input(raw, 10011));noMotion(r);check(!r.refreshPrepareAdmission);
      r = gate.update(input(raw, 10160));noMotion(r);check(gate.pending() && !r.refreshPrepareAdmission);
      r = gate.update(input(raw, 10161));noMotion(r);check(r.cancelled && !gate.pending() && !r.refreshPrepareAdmission);
    }
    G::offerPrepare(10010, 1);check(!G::consumePrepare(10161, 1, true));
  } else if(name == "refresh_evidence") {
    for(unsigned raw : {1u, 3u})for(unsigned kind = 0;kind < 3;++kind) {
      G::Gate gate;accepted(gate, raw);auto v = input(raw, 2200);
      if(kind == 0)v.gcPacketTime = v.gcNow - 2001;
      if(kind == 1){v.lyingStable = false;v.uprightStable = true;}
      if(kind == 2)v.executedPrepare = true;
      const auto r = gate.update(v);noMotion(r);check(!r.refreshPrepareAdmission);
    }
    G::Gate gate;accepted(gate, 3);auto v = input(3, 2200, false, true);gate.update(v);
    v = input(3, 4100, false, true);v.lyingStable = false;gate.update(v);
    noMotion(gate.update(input(3, 4200, false, true)));
    noMotion(gate.update(input(3, 4499, false, true)));
    check(gate.update(input(3, 4500, false, true)).requestGetUp);
  } else if(name == "legacy") {
    for(unsigned raw : {0u, 1u, 2u, 3u, 4u, 255u}) {
      bool release = false, holding = false;unsigned started = 0, last = 0;
      L::update(liveInput(raw, 100), release, holding, started, last);
      check(!L::update(liveInput(raw, 160, true), release, holding, started, last));
      const bool accepted = L::update(liveInput(raw, 2161, true), release, holding, started, last);
      check(accepted == G::recoveryStateAllowed(raw));
    }
    for(unsigned bad = 0;bad < 6;++bad) {
      bool release = false, holding = false;unsigned started = 0, last = 0;
      L::update(liveInput(3, 100), release, holding, started, last);
      L::update(liveInput(3, 160, true), release, holding, started, last);
      auto v = liveInput(3, 2161, true);
      if(bad == 0)v.stopped = true;if(bad == 1)v.penalized = true;if(bad == 2)v.gcActive = false;
      if(bad == 3)v.authorized = false;if(bad == 4)v.gcPacketTime = v.gcNow - 2001;if(bad == 5)v.lyingStable = false;
      check(!L::update(v, release, holding, started, last));
    }
  } else return 2;
}
'''


class MatchRecoveryGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="bh996-match-recovery-")
        cls.addClassCleanup(cls.temp.cleanup)
        cpp = Path(cls.temp.name) / "recovery.cpp"
        cpp.write_text(HARNESS)
        cls.binary = Path(cls.temp.name) / "recovery"
        result = subprocess.run([
            "/usr/bin/clang++", "-std=c++20", "-Wall", "-Wextra", "-Werror", "-pedantic",
            "-I" + str(ROOT / "Src"), str(cpp), "-o", str(cls.binary),
        ], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stderr)

    def run_case(self, name):
        subprocess.run([str(self.binary), name], check=True, capture_output=True, text=True, timeout=10)

    def test_raw_0_1_3_only_stop_penalty_and_two_second_boundary(self):self.run_case("raw")
    def test_initial_ready_playing_prepare_then_real_getup(self):self.run_case("states")
    def test_stop_penalty_f1_input_imu_phase_and_epoch_hard_cancel(self):self.run_case("hard")
    def test_match_upright_entry_cannot_arm_initial_upright_is_unchanged(self):self.run_case("upright_entry")
    def test_match_pending_cannot_become_upright_active_authority(self):self.run_case("upright_pending")
    def test_final_getup_still_requires_fresh_allowed_packet(self):self.run_case("stale")
    def test_refresh_token_fits_original_eight_seconds_and_150ms_ttl(self):self.run_case("refresh")
    def test_refresh_needs_new_freshness_stable_pose_and_no_actual_prepare(self):self.run_case("refresh_evidence")
    def test_legacy_match_optin_keeps_other_guards(self):self.run_case("legacy")


if __name__ == "__main__":
    unittest.main()
