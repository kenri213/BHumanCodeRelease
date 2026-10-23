"""Production Gate replay for the observed 1580 -> 2006ms GC gap; no robot."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

import test_pickup_pending_reentry_v2 as v2

ROOT = Path(__file__).resolve().parents[2]

HELPERS = r'''
static void v3Packet(P::Input& in,unsigned now,unsigned stamp,bool none=false,
                     unsigned state=3,bool stopped=false,bool active=false){
  V2Combined combinedInput;GameState theGameState;V2Raw theGameControllerData;
  combinedInput.gcNow=now;combinedInput.gcPacketTime=stamp;
  combinedInput.rawInitial=state==0;combinedInput.rawRecoveryAllowed=state==1||state==3;
  combinedInput.stopped=stopped;combinedInput.unpenalized=none;
  theGameState.stopped=stopped;theGameState.playerState=active?GameState::active:GameState::penalized;
  theGameControllerData.state=state;
  const bool pickupPacketFresh=/* REAL_FRESH */;
  const bool pickupTargetPresent=true;
  const auto context=productionContext(combinedInput,theGameState,theGameControllerData,pickupPacketFresh,pickupTargetPresent);
  in.gcArmContext=context.first;in.gcLegal=context.second;
  in.gcPacketFresh=pickupPacketFresh;in.gcPacketTime=stamp;
  in.gcTimestampInvalid=/* REAL_INVALID */;
  constexpr unsigned STATE_SET=2;
  in.gcSessionEnded=/* REAL_ENDED */;
}
static void v3Start(P::Gate& gate,P::Input& in){
  v3Packet(in,9960,8420);enter(gate,in);
  v3Packet(in,10000,8420);check(in.gcPacketFresh&&in.gcArmContext&&!in.gcLegal,"1580ms penalized context admits only waiting intent");
  startWalk(gate,in);
}
static void v3Gap(P::Gate& gate,P::Input& in){
  v3Start(gate,in);
  v3Packet(in,10426,8420);check(!in.gcPacketFresh&&!in.gcTimestampInvalid,"2006ms is ordinary stale, not an invalid stamp");
  check(!tick(gate,in,626).cancelled,"ordinary age crossing cannot erase an admitted WALK");
}
static void v3FinishWaiting(P::Gate& gate,P::Input& in){
  v3Packet(in,12001,8420);const auto out=tick(gate,in,2201);
  check(out.requested&&out.pending&&out.waitingGc&&out.stand&&!out.automaticReady,"the same >2s WALK completes only a Stand/wait request");
  in.executedPrepare=false;in.standOrWalk=in.actualStand=true;
  check(!tick(gate,in,2231).automaticReady,"first actual Stand cannot authorize AUTO");
  check(tick(gate,in,2531).waitingGc&&!tick(gate,in,2532).automaticReady,"stale feedback never releases even after real Stand");
}
'''

CASES_CPP = r'''
  } else if(name=="v3_observed_1580_2006_same_walk"){
    v3Gap(gate,in);const auto count=P::physicalButtons().walkPress();v3FinishWaiting(gate,in);
    release(2);v3Packet(in,12400,12390,true,3,false,true);
    check(tick(gate,in,2600).automaticReady&&P::physicalButtons().walkPress()==count,"fresh NONE/active releases without a second WALK");
    gate.finishAutomatic();check(!tick(gate,in,2640).automaticReady&&!gate.pending(),"release remains one shot");
  } else if(name=="v3_stale_at_initial_press"||name=="v3_zero_at_initial_press"||name=="v3_future_at_initial_press"){
    const unsigned stamp=name=="v3_stale_at_initial_press"?7999:name=="v3_zero_at_initial_press"?0:13000;
    v3Packet(in,9960,stamp);enter(gate,in);
    v3Packet(in,10000,stamp);startWalk(gate,in);requestStand(gate,in);
    v3Packet(in,12400,12390,true,3,false,true);
    check(!tick(gate,in,2600).automaticReady&&!gate.pending(),"a press starting without a fresh valid stamp cannot be retrospectively admitted");
  } else if(name=="v3_short_release_during_gap"){
    v3Gap(gate,in);release(2);check(!tick(gate,in,1800).pending,"short release creates no completed waiting request");
    v3Packet(in,12400,12390,true,3,false,true);
    check(tick(gate,in,9000).prepare&&!gate.pending(),"later fresh NONE cannot resurrect a short press");
  } else if(name=="v3_gap_then_fresh_stop"||name=="v3_gap_then_fresh_set"||name=="v3_completion_fresh_stop"||name=="v3_completion_fresh_set"){
    v3Gap(gate,in);const bool stop=name.find("stop")!=std::string::npos;
    const unsigned frame=name.find("completion")!=std::string::npos?2201:1000;
    v3Packet(in,12001,11990,true,stop?3:2,stop,true);
    check(tick(gate,in,frame).cancelled&&!gate.pending(),"fresh STOP/SET revokes an incomplete gesture, including completion frame");
    v3Packet(in,12400,12390,true,3,false,true);
    check(!tick(gate,in,9000).automaticReady&&!gate.pending(),"later legal or stale state cannot revive the revoked press");
  } else if(name=="v3_gap_zero_stamp"||name=="v3_gap_future_stamp"||name=="v3_gap_reversed_stamp"){
    v3Gap(gate,in);const unsigned stamp=name=="v3_gap_zero_stamp"?0:name=="v3_gap_future_stamp"?13000:8419;
    v3Packet(in,11000,stamp);
    check(tick(gate,in,1000).cancelled&&!gate.pending(),"zero/future/reversed is not an ordinary packet gap");
    v3Packet(in,12400,12390,true,3,false,true);requestStand(gate,in);
    check(!tick(gate,in,9000).automaticReady&&!gate.pending(),"corrected stamps cannot revive the invalidated WALK");
  } else if(name=="v3_gap_epoch"||name=="v3_gap_remote"||name=="v3_gap_identity"||name=="v3_gap_finished"){
    v3Gap(gate,in);
    if(name=="v3_gap_epoch")++in.epoch;
    if(name=="v3_gap_remote")++in.remoteGeneration;
    if(name=="v3_gap_identity"){v3Packet(in,11000,10990);in.gcTargetPresent=in.gcArmContext=false;}
    if(name=="v3_gap_finished")v3Packet(in,11000,10990,false,4);
    check(tick(gate,in,1000).cancelled&&!gate.pending(),"ordinary gap cannot mask a structural invalidation");
    v3Packet(in,12400,12390,true,3,false,true);requestStand(gate,in);
    check(!tick(gate,in,9000).automaticReady&&!gate.pending(),"old press remains revoked");
  } else if(name=="v3_final_fresh_boundary"||name=="v3_final_raw_none_derived_penalty"){
    v3Gap(gate,in);v3FinishWaiting(gate,in);
    if(name=="v3_final_fresh_boundary"){
      v3Packet(in,12400,10399,true,3,false,true);check(!tick(gate,in,2600).automaticReady,"age2001 remains forbidden at AUTO");
      v3Packet(in,12401,10401,true,3,false,true);check(tick(gate,in,2640).automaticReady,"age2000 preserves the original final boundary");
    }else{
      v3Packet(in,12400,12390,true,3,false,false);check(!tick(gate,in,2600).automaticReady,"raw NONE alone does not bypass derived active");
      v3Packet(in,12440,12430,true,3,false,true);check(tick(gate,in,2640).automaticReady,"both raw and derived lawful states are required");
    }
  } else if(name=="v3_gap_fault"||name=="v3_gap_unsafe_phase"){
    v3Gap(gate,in);
    if(name=="v3_gap_fault")in.emergency=true;
    else in.executedPrepare=in.standOrWalk=in.actualStand=false;
    const auto out=tick(gate,in,1000);
    check(out.suspended&&out.cancelled&&!out.pending&&!out.automaticReady,"body protection still outranks a retained ordinary gap");
  } else if(name=="v3_gap_new_stand"){
    v3Gap(gate,in);press(1);const auto out=tick(gate,in,1000);
    check(out.entered&&out.prepare&&out.cancelled&&!gate.pending(),"new physical STAND always wins during a gap");
    release(1);v3Packet(in,12400,12390,true,3,false,true);
    check(tick(gate,in,9000).prepare&&!gate.pending(),"held old WALK cannot escape the new STAND lock");
  } else return 2;
'''


class PickupGcGapReentryV3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        v2.PickupPendingReentryV2Tests.setUpClass()
        try:
            source = (Path(v2.PickupPendingReentryV2Tests.temp.name) / "v2.cpp").read_text()
        finally:
            v2.PickupPendingReentryV2Tests.tearDownClass()
        physical = v2.PHYSICAL.read_text()
        fresh = re.search(r"const bool pickupPacketFresh\s*=\s*(.*?);", physical, re.S)
        initializer = physical.split("const BoosterPickupControl::Input pickupInput{", 1)[1].split("};", 1)[0]
        invalid = re.search(r"\.gcTimestampInvalid\s*=\s*(.*?),\s*\.frame", initializer, re.S)
        ended = re.search(r"\.gcSessionEnded\s*=\s*(.*?),\s*\.gcTimestampInvalid", initializer, re.S)
        if any(value is None for value in (fresh, invalid, ended)):
            raise AssertionError("actual producer freshness/stamp/session predicates not found")
        helpers = HELPERS.replace("/* REAL_FRESH */", fresh.group(1)).replace("/* REAL_INVALID */", invalid.group(1)).replace("/* REAL_ENDED */", ended.group(1))
        anchor = "  } else return 2;\n  return 0;\n}"
        if source.count(anchor) != 1:
            raise AssertionError("production harness boundary changed")
        source = source.replace("int main(int argc, char** argv)", helpers + "\nint main(int argc, char** argv)", 1)
        source = source.replace(anchor, CASES_CPP + "  return 0;\n}", 1)
        cls.temp = tempfile.TemporaryDirectory(prefix="pickup-gc-gap-v3-")
        cpp = Path(cls.temp.name) / "v3.cpp"
        cpp.write_text(source)
        cls.binary = Path(cls.temp.name) / "v3"
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


CASES = (
    "v3_observed_1580_2006_same_walk", "v3_stale_at_initial_press", "v3_zero_at_initial_press", "v3_future_at_initial_press", "v3_short_release_during_gap",
    "v3_gap_then_fresh_stop", "v3_gap_then_fresh_set", "v3_completion_fresh_stop", "v3_completion_fresh_set",
    "v3_gap_zero_stamp", "v3_gap_future_stamp", "v3_gap_reversed_stamp", "v3_gap_epoch", "v3_gap_remote",
    "v3_gap_identity", "v3_gap_finished", "v3_final_fresh_boundary", "v3_final_raw_none_derived_penalty", "v3_gap_new_stand",
    "v3_gap_fault", "v3_gap_unsafe_phase",
)


def _case(name):
    def test(self):
        self.replay(name)
    return test


for _name in CASES:
    setattr(PickupGcGapReentryV3Tests, "test_" + _name, _case(_name))


if __name__ == "__main__":
    unittest.main()
