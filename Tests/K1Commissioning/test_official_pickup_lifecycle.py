"""Replay real GSP generation -> Physical reset and native RB+A; no robot."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

import test_pickup_lying_recovery as lying

ROOT = Path(__file__).resolve().parents[2]
GSP = ROOT / "Src/Modules/Infrastructure/GameStateProvider/GameStateProvider.cpp"
PHYSICAL = lying.PHYSICAL

DEFINITIONS = r'''
namespace GameState {enum PlayerState {active,unstiff,penalized};}
struct {GameState::PlayerState playerState=GameState::active;} theGameState;
namespace KeyStates {enum Key {f1,stand,walk};}
struct {bool pressed[3]{};unsigned pressedDuration[3]{};} theEnhancedKeyStates;
struct {bool isDamping=false;} theDampingState;
constexpr unsigned STATE_INITIAL=0;
namespace Global {
struct Settings{unsigned teamNumber=114,playerNumber=1;};
static Settings settings;
static const Settings& getSettings(){return settings;}
}
namespace Time{static unsigned getCurrentSystemTime(){return theFrameInfo.time+100;}}
'''

METHODS = r'''
  unsigned lastOfficialF1Generation=0;
  void synchronizeRunningGeneration(){lastOfficialF1Generation=C::modeToken()&~7u;}
  void gsp(bool prepareContext=false,bool offerRbaTicket=false){
    auto& gameState=theGameState;
    const unsigned pressDurationUnstiffToPrepare=2000;
    bool mainButtonPressed=false;
    const auto setToActive=[&]{gameState.playerState=GameState::active;};
    const auto setToUnstiff=[&]{gameState.playerState=GameState::unstiff;};
    /* REAL_GENERATION_AND_UNSTIFF */
    theGameControllerData.stopped=!prepareContext;
    theGameControllerData.timeLastPacketReceived=theFrameInfo.time+90;
    if(offerRbaTicket)G::offerPrepare(theFrameInfo.time,BoosterMatchSafety::epoch());
    /* REAL_COMBINED_ADMISSION */
    /* REAL_PLAYER_SWITCH */
    (void)mainButtonPressed;
  }
  void physicalF1Hook(){
    const bool liveAuthorized=C::bodyAuthorized();
    /* REAL_PHYSICAL_RESET */
  }
'''

HELPERS = r'''
static P::Input pickupInput(unsigned frame){
  P::Input in;in.enabled=in.authorized=in.imuFinite=in.uprightStable=true;
  in.gcArmContext=in.gcPacketFresh=in.gcTargetPresent=true;in.emergency=false;
  in.gcLegal=false;in.gcPacketTime=5000;in.frame=frame;in.epoch=BoosterMatchSafety::epoch();
  in.remoteGeneration=R::generation();in.buttons=P::physicalButtons();return in;
}
static void primeWaiting(Replay& replay,bool realStand){
  P::Input in=pickupInput(160);in.executedPrepare=true;
  replay.physicalPickupGate.update(in);
  for(unsigned frame=200;frame<=2240;frame+=40){
    if(frame==200)P::observePhysicalButton(2,true);
    in.frame=frame;in.buttons=P::physicalButtons();replay.physicalPickupGate.update(in);
  }
  check(replay.physicalPickupGate.pending(),"fixture: real long WALK established pending request");
  theMotionInfo.executedPhase=MotionPhase::prepare;
  if(realStand){
    in.executedPrepare=false;in.standOrWalk=in.actualStand=true;
    for(unsigned frame=2280;frame<=2640;frame+=40){in.frame=frame;replay.physicalPickupGate.update(in);}
    theMotionInfo.executedPhase=MotionPhase::stand;
  }
  P::setHeld(true);P::offerAutomaticRelease(BoosterMatchSafety::epoch());
}
static void f1Cycle(bool rearm){
  BoosterMatchSafety::invalidateConfirmation();C::observePhysicalF1Down(C::steadyTimeNs());
  if(rearm){check(C::authorizePhysicalF1Long(C::steadyTimeNs()+1),"real DOWN then LONG");C::observePhysicalF1Release();}
}
static void nativeRbaAfterReset(Replay& replay){
  P::observePhysicalButton(2,false);
  auto in=good(3000);in.epoch=BoosterMatchSafety::epoch();
  check(!at(replay,in,3000,false).requestPrepare,"new recovery initially only synchronizes");
  check(!at(replay,in,3040,false).requestPrepare,"fresh neutral is not Prepare");
  check(!at(replay,in,3080,true).requestPrepare,"new RB+A starts original hold");
  const auto accepted=at(replay,in,5080,true);
  check(accepted.requestPrepare&&!accepted.requestGetUp&&!accepted.requestActive,"new RB+A uses original Prepare admission");
  replay.gsp(true,true);check(theGameState.playerState==GameState::active,"original admission can leave Unstiff");
  check(!at(replay,in,5120,false).requestGetUp,"real Prepare duration cannot be skipped");
  check(at(replay,in,7120,false).requestGetUp,"original native GetUp path remains reachable");
  check(!P::held()&&!P::consumeAutomaticRelease(),"old Pickup AUTO does not survive native recovery");
}
'''

CASES = r'''
  } else if(name=="official_pending_prepare"||name=="official_pending_stand"||name=="coalesced_prepare"||name=="coalesced_stand"){
    C::enableOfficialButtonMode();replay.synchronizeRunningGeneration();
    primeWaiting(replay,name.find("stand")!=std::string::npos);
    const auto oldCount=P::physicalButtons().walkPress();
    const bool coalesced=name.find("coalesced")!=std::string::npos;
    f1Cycle(coalesced);
    // Coalesced cycle has no observed unauthorized/key/damping frame.
    check(C::bodyAuthorized()==coalesced,"fixture: authorized state matches the actual event sequence");
    check(!theEnhancedKeyStates.pressed[KeyStates::f1]&&!theDampingState.isDamping,"no injected key or Damping shortcut");
    theGameState.playerState=GameState::active;
    replay.gsp();check(theGameState.playerState==GameState::unstiff,"GSP must consume F1 generation before Physical");
    replay.physicalF1Hook();
    check(!replay.physicalPickupGate.active()&&!P::held()&&!P::consumeAutomaticRelease(),"resident F1 discards the old process-local transaction");
    if(!coalesced){check(C::authorizePhysicalF1Long(C::steadyTimeNs()+1),"LONG can rearm same process");C::observePhysicalF1Release();}
    theMotionInfo.executedPhase=MotionPhase::playDead;
    replay.gsp();replay.physicalF1Hook();
    check(theGameState.playerState==GameState::unstiff&&!replay.physicalPickupGate.active(),"LONG alone never auto-Prepares or resumes play");
    check(P::physicalButtons().walkPress()==oldCount,"no synthetic second physical WALK");
    nativeRbaAfterReset(replay);
  } else if(name=="native_rb_x_unstiff"){
    C::enableOfficialButtonMode();replay.synchronizeRunningGeneration();primeWaiting(replay,true);
    theEnhancedKeyStates.pressed[KeyStates::f1]=true;replay.gsp();
    check(C::bodyAuthorized()&&theGameState.playerState==GameState::unstiff,"native mapped RB+X is not APP termination");
    replay.physicalF1Hook();check(!replay.physicalPickupGate.active()&&!P::held()&&!P::consumeAutomaticRelease(),"native unstiff clears old Pickup");
    theEnhancedKeyStates.pressed[KeyStates::f1]=false;nativeRbaAfterReset(replay);
  } else if(name=="ordinary_penalty_retains_pickup"||name=="legacy_unstiff_keeps_old_semantics"){
    const bool official=name=="ordinary_penalty_retains_pickup";
    if(official)C::enableOfficialButtonMode();
    replay.synchronizeRunningGeneration();primeWaiting(replay,true);
    theGameState.playerState=official?GameState::penalized:GameState::unstiff;
    replay.gsp();replay.physicalF1Hook();
    check(replay.physicalPickupGate.pending()&&P::held(),"ordinary penalty or nonofficial mode must not discard Pickup");
  } else if(name=="held_manual_choice"||name=="held_automatic_choice"||name=="first_f1_no_remote_hold"){
    C::enableOfficialButtonMode();replay.synchronizeRunningGeneration();
    const bool hold=name!="first_f1_no_remote_hold";
    const unsigned choice=name=="held_manual_choice"?1:2;
    if(hold){R::latchDisconnect();R::observeSample(0,true);R::observeSample(choice==1?576:320,true);check(R::resumeChoice()==choice,"old choice fixture");}
    const auto oldGeneration=R::generation();f1Cycle(true);replay.gsp();replay.physicalF1Hook();
    if(hold)check(R::held()&&R::generation()>oldGeneration&&!R::resumeChoice()&&!R::tryResume(oldGeneration,choice),"held-only CAS retires old manual/automatic choice");
    else check(!R::held()&&R::generation()==oldGeneration,"first no-hold F1 must not invent a remote Stand lock");
  } else if(name=="coalesced_does_not_consume_rba_prepare"){
    C::enableOfficialButtonMode();replay.synchronizeRunningGeneration();primeWaiting(replay,true);
    f1Cycle(true);replay.gsp(true,true);replay.physicalF1Hook();
    check(theGameState.playerState==GameState::unstiff&&!replay.physicalPickupGate.active(),"new F1 generation blocks even an old pending Prepare admission");
  } else if(name=="generation_is_consumed_once"){
    C::enableOfficialButtonMode();replay.synchronizeRunningGeneration();f1Cycle(true);
    replay.gsp();replay.physicalF1Hook();check(theGameState.playerState==GameState::unstiff,"new real generation unstiffs");
    theGameControllerData.state=STATE_INITIAL;
    theEnhancedKeyStates.pressedDuration[KeyStates::stand]=2001;
    replay.gsp(true,false);check(theGameState.playerState==GameState::active,"fresh native long STAND may leave Unstiff in its allowed INITIAL context");
    theEnhancedKeyStates.pressedDuration[KeyStates::stand]=0;replay.gsp();
    check(theGameState.playerState==GameState::active,"the same consumed F1 generation cannot repeatedly unstiff later native actions");
  } else return 2;
'''


class OfficialPickupLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        lying.PickupLyingRecoveryTest.setUpClass()
        try:
            source = (Path(lying.PickupLyingRecoveryTest.temp.name) / "lying.cpp").read_text()
        finally:
            lying.PickupLyingRecoveryTest.tearDownClass()
        gsp = GSP.read_text()
        prefix = gsp.split("  // BEGIN_OFFICIAL_F1_GENERATION\n", 1)[1].split("  bool mainButtonPressed", 1)[0]
        player_switch = "switch(gameState.playerState)" + lying.body(gsp, "switch(gameState.playerState)")
        combined = gsp[gsp.index("  const bool combinedPreparePermitted ="):gsp.index("  switch(gameState.playerState)")]
        physical = PHYSICAL.read_text()
        hook = physical.split("  // BEGIN_OFFICIAL_F1_PICKUP_LIFECYCLE\n", 1)[1].split("  // END_OFFICIAL_F1_PICKUP_LIFECYCLE", 1)[0]
        methods = METHODS.replace("/* REAL_GENERATION_AND_UNSTIFF */", prefix).replace("/* REAL_PLAYER_SWITCH */", player_switch).replace("/* REAL_PHYSICAL_RESET */", hook).replace("/* REAL_COMBINED_ADMISSION */", combined)
        source = source.replace("struct {unsigned state = 3;} theGameControllerData;", "struct RawPlayer{unsigned penalty=0;};struct RawTeam{unsigned teamNumber=114;RawPlayer players[3];};struct{unsigned state=3,stopped=0,playersPerTeam=3,timeLastPacketReceived=0;RawTeam teams[1];}theGameControllerData;", 1)
        source = source.replace("class Replay {", DEFINITIONS + "\nclass Replay {", 1)
        source = source.replace("  G::Result frame(G::Input", methods + "\n  G::Result frame(G::Input", 1)
        source = source.replace("int main(int argc, char** argv)", HELPERS + "\nint main(int argc, char** argv)", 1)
        anchor = "  } else return 2;\n  return 0;\n}"
        if source.count(anchor) != 1:
            raise AssertionError("actual Physical replay boundary changed")
        source = source.replace(anchor, CASES + "  return 0;\n}\n", 1)
        cls.temp = tempfile.TemporaryDirectory(prefix="bh987-resident-pickup-")
        cpp = Path(cls.temp.name) / "resident.cpp"
        cpp.write_text(source)
        cls.binary = Path(cls.temp.name) / "resident"
        built = subprocess.run([shutil.which("c++") or "c++", "-std=c++20", "-Wall", "-Wextra", "-Werror",
                                "-I", str(ROOT / "Src/Libs"), "-I", str(ROOT / "Src"), str(cpp), "-o", str(cls.binary)],
                               capture_output=True, text=True, timeout=60)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def replay(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, f"{name}: {result.stdout}{result.stderr}")


for _name in ("official_pending_prepare", "official_pending_stand", "coalesced_prepare", "coalesced_stand",
              "native_rb_x_unstiff", "ordinary_penalty_retains_pickup", "legacy_unstiff_keeps_old_semantics", "generation_is_consumed_once",
              "held_manual_choice", "held_automatic_choice", "first_f1_no_remote_hold", "coalesced_does_not_consume_rba_prepare"):
    setattr(OfficialPickupLifecycleTests, "test_" + _name, lambda self, name=_name: self.replay(name))


if __name__ == "__main__":
    unittest.main()
