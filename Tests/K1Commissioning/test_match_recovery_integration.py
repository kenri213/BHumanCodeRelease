"""Real Physical inputs + provider admission + Gate/feedback recovery; CPU only."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
PHYSICAL=ROOT/'Src/Modules/BehaviorControl/SkillBehaviorControl/Options/HandlePhysicalRobot.cpp'
PROVIDER=ROOT/'Src/Modules/Infrastructure/GameStateProvider/GameStateProvider.cpp'


def expression(source,marker):
    start=source.index(marker);opening=source.index('(',start);end=opening+1;depth=1
    while depth:
        depth+=(source[end]=='(')-(source[end]==')');end+=1
    return source[opening+1:end-1]


PREFIX=r'''
#include "Tools/BehaviorControl/CombinedLiveGetUpGesture.h"
#include "Tools/BehaviorControl/LiveGetUpGesture.h"
#include "Platform/BoosterMatchSafety.h"
#include "Platform/BoosterOfficialLiveTest.h"
#include "Platform/BoosterRemoteStand.h"
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <limits>
#include <string>
namespace G=CombinedLiveGetUpGesture;namespace R=BoosterRemoteStand;namespace C=BoosterOfficialLiveTest;
static void check(bool value){if(!value)std::abort();}
constexpr unsigned STATE_INITIAL=0;
namespace MotionPhase {enum Type {playDead,stand,walk,fall,getUp,prepare};}
namespace KeyStates {enum Key {f1,stand,walk};}
struct Settings {unsigned teamNumber=70,playerNumber=2;};
namespace Global {Settings settings;Settings& getSettings(){return settings;}}
namespace Time {unsigned now=0;unsigned getCurrentSystemTime(){return now;}}
struct Player {std::uint8_t penalty=0;};
struct Team {std::uint8_t teamNumber=0;std::array<Player,20> players{};};
struct Packet {
  std::uint8_t state=0,stopped=0,playersPerTeam=3;unsigned timeLastPacketReceived=0;
  std::array<Team,2> teams{};
  Packet(){teams[0].teamNumber=70;teams[1].teamNumber=71;}
} theGameControllerData;
struct GameState {
  enum PlayerState {unstiff,active,penalized};PlayerState playerState=unstiff;
  bool gameControllerActive=true,stopped=false;
  bool isInitial()const{return theGameControllerData.state==0;}
  bool isPenalized()const{return playerState==penalized;}
} theGameState;
struct Vec {float xx=0,yy=0,zz=0;float x()const{return xx;}float y()const{return yy;}float z()const{return zz;}};
struct Imu {Vec angle{1.4f,0.f,0.f},gyro{};} theInertialData;
struct Remote {bool valid=true;unsigned buttons=0;std::array<float,8> axes{};
  bool pressed(unsigned code)const{return buttons&(1u<<code);}} theJoystickState;
struct Enhanced {std::array<bool,3> pressed{};std::array<unsigned,3> pressedDuration{},hitStreak{};} theEnhancedKeyStates;
struct Sensors {bool emergencyMode=false;} theSystemSensorData;
struct Motion {MotionPhase::Type executedPhase=MotionPhase::playDead;} theMotionInfo;
struct Frame {unsigned time=0;} theFrameInfo;
struct Damping {bool isDamping=false;} theDampingState;
constexpr float minFallAngle=1.04719755f,maxGyroForFall=.087266463f;
constexpr float maxTorsoForWalk=.261799388f,maxGyroForWalk=.261799388f;
constexpr unsigned pressDuration=2000;
struct Inputs {LiveGetUpGesture::Input live;G::Input combined;bool recoveryPermitted;};
static Inputs wired(){
  const bool liveMode=C::enabled(),liveAuthorized=C::bodyAuthorized();
'''


SUFFIX=r'''
static void authorize(bool match=true){
  C::enable();if(match){BoosterMatchSafety::enable();R::enable();}
  C::observePhysicalF1Down(1000000000LL);
  for(unsigned f=1;f<=5;++f)C::noteCognitionForF1Authorization(f,2000000000LL);
  check(C::authorizePhysicalF1Long(2000000000LL));C::observePhysicalF1Release();
}
struct Pipeline {
  G::Gate gate;unsigned admitted=0;bool feedbackEnabled=true,prepareRequested=false;
  Inputs last{};
  G::Result step(unsigned frame,unsigned buttons=0,unsigned age=10){
    theFrameInfo.time=frame;Time::now=1000000+frame;
    theGameControllerData.timeLastPacketReceived=age==0xffffffffu?Time::now+1:Time::now-age;
    theJoystickState.buttons=buttons;
    theEnhancedKeyStates.pressed[KeyStates::walk]=(buttons&34u)==34u;
    if(prepareRequested&&feedbackEnabled){check(admitted);theMotionInfo.executedPhase=MotionPhase::prepare;prepareRequested=false;}
    if(providerAdmission()){++admitted;theGameState.playerState=GameState::active;}
    last=wired();const auto result=gate.update(last.combined);publish(last.combined,result);
    if((result.requestPrepare||result.refreshPrepareAdmission)&&theGameState.playerState!=GameState::unstiff)
      prepareRequested=true;
    if(result.requestGetUp)check(finalGetUp(last,result));
    return result;
  }
  void accept(unsigned age=10){
    step(100);step(130);step(160,32);step(200,34);
    check(step(2200,34,age).requestPrepare);check(gate.pending());
  }
  void complete(){
    step(2233);check(admitted&&theGameState.playerState==GameState::active);
    step(2266);check(theMotionInfo.executedPhase==MotionPhase::prepare);
    check(!step(4265).requestGetUp);check(step(4266).requestGetUp);
  }
};
static void noRequest(const G::Result& r){check(!r.requestPrepare&&!r.refreshPrepareAdmission&&!r.requestGetUp&&!r.requestActive);}
int main(int argc,char**argv){
  check(argc==2);const std::string name=argv[1];
  if(name=="states"){
    for(unsigned state=0;state<256;++state)check(G::recoveryStateAllowed(state)==(state==0||state==1||state==3));
    return 0;
  }
  authorize(name!="ordinary_live_play");
  theGameControllerData.state=name=="initial"?0:name=="ready"?1:3;
  if(name=="initial"||name=="ready"||name=="playing"){
    Pipeline p;p.accept();
    check(R::held()==(theGameControllerData.state!=0));
    check(R::bodyRecoveryPending()==(theGameControllerData.state!=0));
    p.complete();return 0;
  }
  if(name=="blocked")return 2;
  if(name=="set"||name=="finished"||name=="stop"||name=="penalty"||name=="stale"||name=="future"||name=="ordinary_live_play"||name=="upright"){
    if(name=="set")theGameControllerData.state=2;
    if(name=="finished")theGameControllerData.state=4;
    if(name=="stop")theGameControllerData.stopped=1;
    if(name=="penalty")theGameControllerData.teams[0].players[1].penalty=1;
    if(name=="upright")theInertialData.angle.xx=0.f;
    Pipeline p;
    for(unsigned frame:{100u,130u,160u,200u,2200u,4300u}){
      const unsigned age=name=="stale"?2001:name=="future"?0xffffffffu:10u;
      noRequest(p.step(frame,frame<160?0:frame==160?32:34,age));
    }
    check(!p.admitted&&!p.gate.pending()&&!R::held());return 0;
  }
  if(name=="admission_gap"){
    Pipeline p;p.accept(1990);
    noRequest(p.step(2233,34,2023));check(!p.admitted&&p.gate.pending());
    check(p.step(3200).refreshPrepareAdmission);check(!p.admitted);
    p.step(3233);check(p.admitted);p.step(3266);check(theMotionInfo.executedPhase==MotionPhase::prepare);
    check(!p.step(5265).requestGetUp);check(p.step(5266).requestGetUp);return 0;
  }
  if(name=="admission_expired"){
    Pipeline p;p.accept();check(!G::consumePrepare(2450,BoosterMatchSafety::epoch(),true));
    check(p.step(2500).refreshPrepareAdmission);p.step(2533);check(p.admitted);
    p.step(2566);check(theMotionInfo.executedPhase==MotionPhase::prepare);return 0;
  }
  if(name=="recovery_hold"){
    Pipeline p;p.accept();check(R::held()&&R::bodyRecoveryPending());
    const auto oldGeneration=R::generation();R::observeSample(0,true);R::observeSample(64,true);R::observeSample(320,true);
    check(!R::tryResume(oldGeneration,2)&&R::held());
    check(BoosterMatchSafety::initialHandoff().update(true,true,false)); // Fresh process directly PLAY.
    check(R::held());p.complete();check(!R::onRecoveryStanding(false));
    check(R::onRecoveryStanding(true)&&R::held()&&!R::bodyRecoveryPending());
    check(R::generation()!=oldGeneration&&R::resumeChoice()==0&&!R::tryResume(oldGeneration,2));
    check(!R::onRecoveryStanding(true));R::observeSample(320,true);check(R::resumeChoice()==0);
    R::observeSample(0,true);R::observeSample(64,true);R::observeSample(320,true);
    check(R::tryResume(R::generation(),2)&&!R::held());return 0;
  }
  if(name=="post_accept_stop"){
    Pipeline p;p.accept();theGameControllerData.stopped=1;
    const auto r=p.step(2233);noRequest(r);check(r.cancelled&&!p.admitted);
    theGameControllerData.stopped=0;noRequest(p.step(2266,34));return 0;
  }
  if(name=="epoch"){
    Pipeline p;p.accept();BoosterMatchSafety::invalidateConfirmation();
    const auto r=p.step(2233);noRequest(r);check(r.cancelled&&!p.admitted);return 0;
  }
  return 2;
}
'''


class MatchRecoveryIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='bh996-recovery-integration-');cls.addClassCleanup(cls.temp.cleanup)
        physical=PHYSICAL.read_text();provider=PROVIDER.read_text()
        live=physical[physical.index('  const LiveGetUpGesture::Input liveInput{'):physical.index('  const bool wasHolding')]
        combined=physical[physical.index('  bool getUpSticksNeutral = true;'):physical.index('  const bool combinedWasPending')]
        permission=physical[physical.index('  const bool matchRecoveryPermitted ='):physical.index('  const auto combinedPreviousReason')]
        publish=physical[physical.index('  if(combinedGetUp.cancelled)'):physical.index('  const bool combinedReasonChanged')]
        admission=provider[provider.index('  const bool combinedPreparePermitted ='):provider.index('  switch(gameState.playerState)')]
        getup=expression(physical,'if(matchRecoveryPermitted\n')
        source=PREFIX+live+combined+permission+'\nreturn {liveInput,combinedInput,matchRecoveryPermitted};}\n'
        source+='static bool providerAdmission(){const bool unstiffButtonsPressed=theEnhancedKeyStates.pressed[KeyStates::f1]||theDampingState.isDamping;\n'+admission+'\nreturn combinedPrepareAdmission;}\n'
        source+='static void publish(const G::Input& combinedInput,const G::Result& combinedGetUp){\n'+publish+'\n}\n'
        source+='static bool finalGetUp(const Inputs& inputs,const G::Result& combinedGetUp){const bool matchRecoveryPermitted=inputs.recoveryPermitted;const bool liveGetUpRequested=false;return '+getup+';}\n'
        source+=SUFFIX
        cpp=Path(cls.temp.name)/'integration.cpp';cpp.write_text(source);cls.binary=Path(cls.temp.name)/'integration'
        result=subprocess.run(['clang++','-std=c++20','-DNDEBUG','-Wall','-Wextra','-Werror','-pedantic','-I',str(ROOT/'Src'),'-I',str(ROOT/'Src/Libs'),str(cpp),'-o',str(cls.binary)],capture_output=True,text=True)
        if result.returncode:raise RuntimeError(result.stderr)

    def case(self,name):
        result=subprocess.run([str(self.binary),name],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,name+'\n'+result.stdout+result.stderr)

    def test_raw_recovery_whitelist_is_exact_initial_ready_play(self):self.case('states')
    def test_initial_cold_start_pipeline_still_works(self):self.case('initial')
    def test_ready_real_inputs_admission_and_actual_prepare_feedback_recover(self):self.case('ready')
    def test_play_real_inputs_admission_and_actual_prepare_feedback_recover(self):self.case('playing')
    def test_set_blocks_getup_admission(self):self.case('set')
    def test_finished_blocks_getup_admission(self):self.case('finished')
    def test_stop_blocks_getup_admission(self):self.case('stop')
    def test_penalized_own_player_blocks_getup_admission(self):self.case('penalty')
    def test_stale_gc_cannot_start_a_chord(self):self.case('stale')
    def test_future_gc_timestamp_cannot_start_a_chord(self):self.case('future')
    def test_nonmatch_live_does_not_get_new_play_recovery_scope(self):self.case('ordinary_live_play')
    def test_upright_play_rb_a_does_not_grant_active_or_walk(self):self.case('upright')
    def test_real_provider_stale_consumed_token_can_refresh_same_intent(self):self.case('admission_gap')
    def test_expired_150ms_token_can_refresh_same_bounded_intent(self):self.case('admission_expired')
    def test_getup_hold_survives_initial_handoff_and_requires_new_poststand_choice(self):self.case('recovery_hold')
    def test_stop_after_acceptance_revokes_admission_and_pending(self):self.case('post_accept_stop')
    def test_epoch_change_after_acceptance_revokes_admission_and_pending(self):self.case('epoch')

    def test_production_standing_evidence_is_real_stand_stable_300ms(self):
        source=(ROOT/'Src/Modules/BehaviorControl/SkillBehaviorControl/Options/HandleJoystick.cpp').read_text()
        start=source.index('const bool realUprightStand =');end=source.index(';',start)
        predicate=source[start:end]
        for token in ('bodyAuthorized()','FallDownState::upright','MotionPhase::stand','lastStandTimeStamp','300'):
            self.assertIn(token,predicate)
        self.assertNotIn('MotionPhase::walk',predicate)
        self.assertIn('onRecoveryStanding(realUprightStand)',source)


if __name__=='__main__':unittest.main()
