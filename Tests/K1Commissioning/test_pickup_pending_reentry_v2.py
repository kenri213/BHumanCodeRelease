"""Replay the production Gate and real producer predicates; no SDK or robot."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import test_physical_pickup_gate as original

ROOT = Path(__file__).resolve().parents[2]
PHYSICAL = ROOT / "Src/Modules/BehaviorControl/SkillBehaviorControl/Options/HandlePhysicalRobot.cpp"

HELPERS = r'''
static void v2Pending(P::Gate& gate,P::Input& in){
  enter(gate,in);in.gcLegal=false;startWalk(gate,in);
  const auto requested=tick(gate,in,2201);
  check(requested.requested&&requested.pending&&requested.waitingGc&&!requested.automaticReady,"lawful long WALK may wait while penalized");
  in.executedPrepare=false;in.standOrWalk=in.actualStand=true;
  check(!tick(gate,in,2230).automaticReady,"real Stand first frame");
  check(tick(gate,in,2530).pending&&gate.pending(),"standing does not remove the penalty");
}
static void v2NewWalk(P::Gate& gate,P::Input& in,unsigned frame){
  release(2);tick(gate,in,frame);startWalk(gate,in,frame+40);
  check(!tick(gate,in,frame+2040).automaticReady,"new WALK still needs more than 2s");
  check(tick(gate,in,frame+2041).requested,"new valid WALK completes");
  in.executedPrepare=false;in.standOrWalk=in.actualStand=true;
  check(!tick(gate,in,frame+2081).automaticReady,"new real Stand cannot be inferred from the request");
  check(tick(gate,in,frame+2381).automaticReady,"new stable Stand confirms");
}
struct V2Combined {bool rawInitial=false,rawRecoveryAllowed=true,stopped=false,unpenalized=false;unsigned gcNow=10000,gcPacketTime=9990;};
struct V2GameState {enum PlayerState{active,penalized};bool stopped=false;PlayerState playerState=penalized;};
struct V2Raw {unsigned state=3;};
static std::pair<bool,bool> productionContext(const V2Combined& combinedInput,const V2GameState& theGameState,
                                             const V2Raw& theGameControllerData,bool pickupPacketFresh,bool pickupTargetPresent){
  return {/* REAL_ARM */,/* REAL_LEGAL */};
}
'''

CASES_CPP = r'''
  } else if(name=="v2_penalty_to_none"){
    v2Pending(gate,in);const auto pressCount=P::physicalButtons().walkPress();
    release(2);tick(gate,in,2600);in.gcLegal=true;
    check(tick(gate,in,2640).automaticReady,"fresh NONE/active releases the same WALK");
    check(P::physicalButtons().walkPress()==pressCount,"no second physical WALK occurred");
    gate.finishAutomatic();check(!tick(gate,in,2680).automaticReady&&!gate.pending(),"single-use release");
  } else if(name=="v2_no_wait_timeout"){
    v2Pending(gate,in);const auto count=P::physicalButtons().walkPress();
    const auto out=tick(gate,in,600000);
    check(out.pending&&out.waitingGc&&!out.automaticReady&&P::physicalButtons().walkPress()==count,"ten minutes is not an arbitrary request expiry");
    in.gcLegal=true;check(tick(gate,in,600040).automaticReady,"fresh legal packet completes long wait");
  } else if(name=="v2_penalty_not_cleared"){
    v2Pending(gate,in);in.gcLegal=false;in.gcArmContext=in.gcPacketFresh=in.gcTargetPresent=true;
    check(!tick(gate,in,90000).automaticReady&&gate.pending(),"canceling a GC UI selection is not proof penalty became NONE");
  } else if(name=="v2_pause_stop"||name=="v2_pause_set"||name=="v2_pause_stale"){
    v2Pending(gate,in);in.gcLegal=in.gcArmContext=false;
    if(name=="v2_pause_stale")in.gcPacketFresh=false;
    const auto out=tick(gate,in,30000);check(out.pending&&out.waitingGc&&!out.automaticReady,"completed request only pauses");
    in.gcLegal=in.gcArmContext=in.gcPacketFresh=true;
    check(tick(gate,in,30040).automaticReady,"same explicit request resumes only with fresh legal context");
  } else if(name=="v2_hold_loses_stop"||name=="v2_hold_loses_set"){
    enter(gate,in);in.gcLegal=false;startWalk(gate,in);
    in.gcArmContext=false;
    check(!tick(gate,in,1000).pending,"partial gesture is not an accepted waiting ticket");
    in.gcArmContext=in.gcPacketFresh=true;
    requestStand(gate,in);in.gcLegal=true;
    check(!tick(gate,in,5000).automaticReady&&!gate.pending(),"old partial context loss cannot be legalized later");
    v2NewWalk(gate,in,5100);
  } else if(name=="v2_bad_context_at_press"){
    enter(gate,in);in.gcArmContext=in.gcLegal=false;startWalk(gate,in);requestStand(gate,in);
    in.gcArmContext=in.gcLegal=true;
    check(!tick(gate,in,9000).automaticReady&&!gate.pending(),"arbitrary illegal WALK is never a wait request");
  } else if(name=="v2_finished_cancels"||name=="v2_unknown_cancels"){
    v2Pending(gate,in);in.gcSessionEnded=true;in.gcLegal=in.gcArmContext=false;
    const auto out=tick(gate,in,2600);check(out.cancelled&&!out.pending,"fresh FINISHED or unknown state ends the request");
    in.gcSessionEnded=false;in.gcLegal=in.gcArmContext=true;
    check(!tick(gate,in,10000).automaticReady,"new PLAY cannot resurrect a completed session ticket");
  } else if(name=="v2_target_missing_cancels"){
    v2Pending(gate,in);in.gcTargetPresent=false;in.gcLegal=in.gcArmContext=false;
    check(tick(gate,in,2600).cancelled&&!gate.pending(),"fresh target identity failure revokes");
    in.gcTargetPresent=in.gcLegal=in.gcArmContext=true;
    check(!tick(gate,in,9000).automaticReady,"target correction does not revive the old ticket");
  } else if(name=="v2_future_recovers"||name=="v2_zero_recovers"||name=="v2_reverse_recovers"){
    in.gcPacketTime=2000;v2Pending(gate,in);
    in.gcTimestampInvalid=name!="v2_reverse_recovers";
    in.gcPacketTime=name=="v2_future_recovers"?1000000:name=="v2_zero_recovers"?0:1900;
    in.gcLegal=in.gcArmContext=false;
    check(tick(gate,in,2600).cancelled&&!gate.pending(),"invalid/reversed packet cannot retain re-entry");
    in.gcTimestampInvalid=false;in.gcPacketTime=2100;in.gcLegal=in.gcArmContext=true;
    check(!tick(gate,in,2640).automaticReady,"correct packet cannot revive old request");
    v2NewWalk(gate,in,2700);
  } else if(name=="v2_gc_reverse_stand_priority"||name=="v2_gc_future_stand_priority"){
    in.gcPacketTime=2000;tick(gate,in,100);
    in.gcPacketTime=name=="v2_gc_reverse_stand_priority"?1900:1000000;
    in.gcTimestampInvalid=name=="v2_gc_future_stand_priority";in.gcLegal=in.gcArmContext=false;
    press(1);const auto out=tick(gate,in,130);
    check(out.entered&&out.prepare,"GC faults must not block physical withdrawal");
  } else if(name=="v2_stand_wins_coalesced"||name=="v2_stand_wins_reverse_order"){
    v2Pending(gate,in);release(2);tick(gate,in,2560);
    if(name=="v2_stand_wins_coalesced"){press(2);press(1);release(1);}
    else{press(1);press(2);release(1);}
    const auto out=tick(gate,in,2600);check(out.entered&&out.prepare&&!out.pending,"fresh STAND wins any coalesced edge order");
    in.executedPrepare=true;in.standOrWalk=in.actualStand=false;in.gcLegal=true;
    check(tick(gate,in,9000).prepare&&!gate.pending(),"STAND cannot reuse earlier or same-frame WALK");
    v2NewWalk(gate,in,9100);
  } else if(name=="v2_stand_prepare_feedback_lag"){
    enter(gate,in);in.gcLegal=false;startWalk(gate,in);
    check(tick(gate,in,2201).pending,"request completed but actual phase still Prepare");
    release(2);press(1);release(1);
    const auto out=tick(gate,in,2230);check(out.entered&&out.prepare&&out.cancelled&&!out.pending,"STAND cancels during real Prepare feedback lag");
    in.gcLegal=true;check(tick(gate,in,9000).prepare&&!gate.pending(),"no old waiting request survives");
  } else if(name=="v2_stand_evidence_restarts"){
    v2Pending(gate,in);in.actualStand=false;tick(gate,in,3000);
    in.gcLegal=true;in.actualStand=true;
    check(!tick(gate,in,3030).automaticReady,"new actual Stand starts confirmation");
    check(!tick(gate,in,3329).automaticReady,"continuous 299ms is insufficient");
    check(tick(gate,in,3330).automaticReady,"continuous 300ms is necessary");
  } else if(name=="v2_identity_matrix"){
    struct Player{unsigned penalty=14;};struct Team{unsigned teamNumber=0;std::array<Player,4> players;};
    struct Packet{unsigned playersPerTeam=3;std::array<Team,2> teams;};
    Packet packet;packet.teams[0].teamNumber=114;packet.teams[1].teamNumber=115;
    check(P::ownPlayerPresent(packet,114,1),"penalty does not erase actual roster identity");
    check(!P::ownPlayerPresent(packet,114,0)&&!P::ownPlayerPresent(packet,114,4),"invalid PID rejected");
    packet.teams[1].teamNumber=114;check(!P::ownPlayerPresent(packet,114,1),"duplicate team rejected");
    packet.teams[0].teamNumber=113;packet.teams[1].teamNumber=115;
    check(!P::ownPlayerPresent(packet,114,1),"missing team rejected");
  } else if(name=="v2_production_gc_predicates"){
    V2Combined combined;V2GameState derived;V2Raw raw;
    auto value=productionContext(combined,derived,raw,true,true);
    check(value.first&&!value.second,"actual fresh penalized PLAY can arm but cannot AUTO");
    combined.unpenalized=true;value=productionContext(combined,derived,raw,true,true);
    check(value.first&&!value.second,"raw NONE alone still requires derived active");
    derived.playerState=V2GameState::active;value=productionContext(combined,derived,raw,true,true);
    check(value.first&&value.second,"raw NONE plus active/fresh permits AUTO");
    combined.stopped=true;derived.stopped=true;value=productionContext(combined,derived,raw,true,true);
    check(!value.first&&!value.second,"STOP blocks admission and AUTO");
    combined.stopped=derived.stopped=false;combined.rawRecoveryAllowed=false;raw.state=2;
    value=productionContext(combined,derived,raw,true,true);check(!value.first&&!value.second,"SET blocks");
    raw.state=3;combined.rawRecoveryAllowed=true;combined.gcPacketTime=7000;
    value=productionContext(combined,derived,raw,false,true);check(!value.first&&!value.second,"old packet cannot release");
    value=productionContext(combined,derived,raw,true,false);check(!value.first,"missing target cannot arm");
  } else return 2;
'''


class PickupPendingReentryV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Reuse the existing production extraction builder, not a copied model.
        original.PhysicalPickupGateTest.setUpClass()
        try:
            source = (Path(original.PhysicalPickupGateTest.temp.name) / "pickup.cpp").read_text()
        finally:
            original.PhysicalPickupGateTest.tearDownClass()
        physical = PHYSICAL.read_text()
        initializer = physical.split("const BoosterPickupControl::Input pickupInput{", 1)[1].split("};", 1)[0]
        arm = re.search(r"\.gcArmContext\s*=\s*(.*?),\s*\.gcPacketFresh", initializer, re.S)
        legal = re.search(r"\.gcLegal\s*=\s*(.*?),\s*\.gcArmContext", initializer, re.S)
        if arm is None or legal is None:
            raise AssertionError("actual producer eligibility predicates not found")
        helpers = HELPERS.replace("/* REAL_ARM */", arm.group(1)).replace("/* REAL_LEGAL */", legal.group(1))
        # Actual source calls the shared class name GameState.
        helpers = helpers.replace("struct V2GameState", "struct GameState").replace("V2GameState", "GameState")
        extra = CASES_CPP.replace("V2GameState", "GameState")
        anchor = "  } else return 2;\n  return 0;\n}"
        if source.count(anchor) != 1:
            raise AssertionError("the source builder main boundary changed")
        source = source.replace("int main(int argc, char** argv)", helpers + "\nint main(int argc, char** argv)", 1)
        source = source.replace(anchor, extra + "  return 0;\n}", 1)
        source = '#include <array>\n#include <utility>\n#include "Tools/BehaviorControl/CombinedLiveGetUpGesture.h"\n' + source
        cls.temp = tempfile.TemporaryDirectory(prefix="pickup-wait-reentry-v2-")
        cpp = Path(cls.temp.name) / "v2.cpp"
        cpp.write_text(source)
        cls.binary = Path(cls.temp.name) / "v2"
        result = subprocess.run([shutil.which("c++") or "c++", "-std=c++20", "-Wall", "-Wextra", "-Werror", "-pthread",
                                 "-I", str(ROOT / "Src/Libs"), "-I", str(ROOT / "Src"), str(cpp), "-o", str(cls.binary)],
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def replay(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, f"{name}: {result.stdout}{result.stderr}")

    def test_diagnostics_are_transition_only_and_identify_same_physical_walk(self):
        source = PHYSICAL.read_text()
        self.assertIn("pickupReasonBefore != physicalPickupGate.reason() || pickup.requested || pickup.cancelled", source)
        for key in ("requested=", "pending=", "waiting_gc=", "released=", "cancelled=", "walk_press=", "buttons=",
                    "raw_gc=", "penalty=", "unpenalized=", "active=", "gc_age_ms=", "phase=", "epoch=", "remote_generation="):
            self.assertIn(key, source)


CASES = (
    "v2_penalty_to_none", "v2_no_wait_timeout", "v2_penalty_not_cleared",
    "v2_pause_stop", "v2_pause_set", "v2_pause_stale",
    "v2_hold_loses_stop", "v2_hold_loses_set", "v2_bad_context_at_press",
    "v2_finished_cancels", "v2_unknown_cancels", "v2_target_missing_cancels",
    "v2_future_recovers", "v2_zero_recovers", "v2_reverse_recovers",
    "v2_gc_reverse_stand_priority", "v2_gc_future_stand_priority",
    "v2_stand_wins_coalesced", "v2_stand_wins_reverse_order", "v2_stand_prepare_feedback_lag",
    "v2_stand_evidence_restarts", "v2_identity_matrix", "v2_production_gc_predicates",
)


def _case(name):
    def test(self):
        self.replay(name)
    return test


for _name in CASES:
    setattr(PickupPendingReentryV2Tests, "test_" + _name, _case(_name))


if __name__ == "__main__":
    unittest.main()
