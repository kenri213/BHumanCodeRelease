"""996-only remote-loss Stand: compile real callbacks with fake SDK messages.

No devices, DDS, mode RPC, service actions, SSH or ARM executable are used.
Header/callback tests validate actual production policy, not equivalent Python.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT=Path(__file__).resolve().parents[2]
MAIN=ROOT/'Src/Apps/Booster/Main.cpp'
PROVIDER=ROOT/'Src/Modules/Infrastructure/BoosterProvider/BoosterProvider.cpp'


def body(source,marker):
    start=source.index(marker);opening=source.index('{',start);position=opening+1;depth=1
    while depth:
        depth+=(source[position]=='{')-(source[position]=='}');position+=1
    return source[opening:position]


PREFIX=r'''
#include <algorithm>
#include <array>
#include <atomic>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <limits>
#include <string>
#include "Platform/BoosterRemoteStand.h"
#include "Platform/BoosterOfficialLiveTest.h"
#include "Platform/BoosterAppCommissioning.h"
#include "Platform/BoosterMatchSafety.h"
namespace R=BoosterRemoteStand;
namespace C=BoosterOfficialLiveTest;
static void check(bool value){if(!value)std::abort();}
namespace Main {unsigned stops=0;void stopOfficialLiveTest(){++stops;}}
namespace booster::robot::b1 {constexpr unsigned REMOVE=0x606;}
namespace booster_interface::msg {
  enum ButtonEventType {PRESS_DOWN,PRESS_UP,LONG_PRESS_START};
  struct ButtonEventMsg {
    unsigned value=0;ButtonEventType type=PRESS_DOWN;
    unsigned button()const{return value;}ButtonEventType event()const{return type;}
  };
  struct RemoteControllerState {
    unsigned code=0x603,buttons=0;
    std::array<float,4> axes{};
    unsigned event()const{return code;}
    float lx()const{return axes[0];}float ly()const{return axes[1];}
    float rx()const{return axes[2];}float ry()const{return axes[3];}
    bool x()const{return buttons&1u;}bool a()const{return buttons&2u;}
    bool b()const{return buttons&4u;}bool y()const{return buttons&8u;}
    bool lb()const{return buttons&16u;}bool rb()const{return buttons&32u;}
    bool lt()const{return buttons&64u;}bool rt()const{return buttons&128u;}
    bool back()const{return buttons&256u;}bool start()const{return buttons&512u;}
    bool ls()const{return buttons&1024u;}bool rs()const{return buttons&2048u;}
    bool hat_l()const{return false;}bool hat_lu()const{return false;}bool hat_ld()const{return false;}
    bool hat_r()const{return false;}bool hat_ru()const{return false;}bool hat_rd()const{return false;}
    bool hat_u()const{return false;}bool hat_d()const{return false;}
  };
}
struct EmergencyHandler {
  enum State {noEmergency,emergencyPressed,emergencyReleased};
  std::atomic<State> state{emergencyReleased};
  void setState(State value,bool=true){state.store(value);}
};
static EmergencyHandler emergencyHandler;
struct JoystickState {std::array<float,8> axes{};unsigned buttons=0;bool valid=false;};
class BoosterProvider {
public:
  JoystickState joystickState;
  void remoteControllerStateHandler(const booster_interface::msg::RemoteControllerState&);
};
#define SYNC
namespace MotionPhase {enum Type {playDead,stand,walk,fall,getUp,prepare};}
struct Pose2f {float rotation=0.f,x=0.f,y=0.f;};
struct MotionRequest {
  enum Motion {playDead,stand,walk,fallRequest,prepare};
  Motion motion=walk;Pose2f walkSpeed{.4f,.6f,.8f};
  bool shouldInterceptBall=true,shouldWalkOutOfBallLine=true;
  bool isWalking()const{return motion==walk;}
};
'''


SUFFIX=r'''
using Remote=booster_interface::msg::RemoteControllerState;
static void authorize(bool policy=true){
  C::enable();
  if(policy){R::enable();BoosterMatchSafety::enable();}
  C::observePhysicalF1Down(1000000000LL);
  for(unsigned frame=1;frame<=5;++frame)C::noteCognitionForF1Authorization(frame,2000000000LL);
  check(C::authorizePhysicalF1Long(2000000000LL)&&C::bodyAuthorized());
  C::observePhysicalF1Release();
}
static void send(BoosterProvider& provider,Remote& remote){
  earlyRemoteCallback(&remote);
  provider.remoteControllerStateHandler(remote);
}
static void remove(BoosterProvider& provider,Remote& remote){
  remote.code=booster::robot::b1::REMOVE;send(provider,remote);
  remote.code=0x604;
}
static void neutral(BoosterProvider& provider,Remote& remote){
  remote.buttons=0;remote.axes.fill(0.f);send(provider,remote);
}
int main(int argc,char**argv){
  check(argc==2);const std::string test=argv[1];BoosterProvider provider;Remote remote;
  check(!R::enabled()&&!R::held()&&!C::enabled());
  if(test=="default"){
    R::latchDisconnect();R::observeSample(0u,true);
    check(!R::enabled()&&!R::held()&&R::resumeChoice()==0u);
    check(!R::tryResume(R::generation(),1u));return 0;
  }
  if(test=="motion_no_authority"){
    R::enable();R::latchDisconnect();C::enable();
    const auto value=filterMotion(MotionRequest(),MotionPhase::walk);
    check(value.motion==MotionRequest::walk&&value.shouldInterceptBall);return 0;
  }
  if(test=="ordinary_live_remove"){
    authorize(false);remove(provider,remote);
    check(C::stopRequested()&&!C::bodyAuthorized()&&Main::stops&&!R::enabled());return 0;
  }
  authorize();
  if(test=="match_remove"){
    remote.axes={.6f,-.4f,.7f,-.8f};remote.buttons=576u;send(provider,remote);
    check(provider.joystickState.valid&&provider.joystickState.buttons==576u);
    remove(provider,remote);
    check(R::held()&&!C::stopRequested()&&C::bodyAuthorized()&&!Main::stops);
    check(!provider.joystickState.valid&&!provider.joystickState.buttons);
    for(float axis:provider.joystickState.axes)check(axis==0.f);
    check(R::resumeChoice()==0u);return 0;
  }
  if(test=="x"||test=="remove_x"||test=="nan"||test=="inf"||test=="remove_nan"||test=="null"||test=="f1"){
    if(test=="null")earlyRemoteCallback(nullptr);
    else if(test=="f1"){
      booster_interface::msg::ButtonEventMsg message;buttonEventHandler(&message);
    }else{
      if(test=="x"||test=="remove_x")remote.buttons=1u;
      if(test=="nan"||test=="remove_nan")remote.axes[0]=std::numeric_limits<float>::quiet_NaN();
      if(test=="inf")remote.axes[3]=std::numeric_limits<float>::infinity();
      if(test=="remove_x"||test=="remove_nan")remote.code=booster::robot::b1::REMOVE;
      send(provider,remote);
    }
    check(C::stopRequested()&&!C::bodyAuthorized()&&Main::stops);
    C::enable();check(C::stopRequested()&&!C::bodyAuthorized());return 0;
  }
  remove(provider,remote);check(R::held()&&!C::stopRequested());
  const auto generation=R::generation();
  if(test=="motion_guard"){
    for(auto phase:{MotionPhase::playDead,MotionPhase::stand,MotionPhase::walk,MotionPhase::fall,MotionPhase::getUp,MotionPhase::prepare})
      for(auto requested:{MotionRequest::playDead,MotionRequest::stand,MotionRequest::walk,MotionRequest::fallRequest,MotionRequest::prepare}){
        MotionRequest original;original.motion=requested;const auto value=filterMotion(original,phase);
        const bool filtered=(phase==MotionPhase::stand||phase==MotionPhase::walk)
                            &&(requested==MotionRequest::walk||requested==MotionRequest::stand);
        check(value.motion==(filtered?MotionRequest::stand:requested));
        if(filtered){check(value.walkSpeed.rotation==0.f&&value.walkSpeed.x==0.f&&value.walkSpeed.y==0.f);
          check(!value.shouldInterceptBall&&!value.shouldWalkOutOfBallLine);}
        else check(value.walkSpeed.rotation==.4f&&value.walkSpeed.x==.6f&&value.walkSpeed.y==.8f
                   &&!value.shouldInterceptBall&&!value.shouldWalkOutOfBallLine);
      }
    return 0;
  }
  if(test=="old_input"){
    remote.axes[0]=.8f;remote.buttons=576u;send(provider,remote);
    check(R::held()&&R::resumeChoice()==0u&&!R::tryResume(generation,1u));
    remote.axes.fill(0.f);send(provider,remote); // Held reconnect chord is not fresh neutral.
    check(R::held()&&R::resumeChoice()==0u&&!R::tryResume(generation,1u));return 0;
  }
  if(test=="neutral_alone"){
    for(unsigned i=0;i<20;++i)neutral(provider,remote);
    check(R::held()&&R::resumeChoice()==0u&&!R::tryResume(generation,0u));
    check(!C::stopRequested()&&C::bodyAuthorized());return 0;
  }
  if(test=="manual"||test=="automatic"||test=="manual_release"||test=="automatic_release"){
    neutral(provider,remote);check(R::held()&&R::resumeChoice()==0u);
    remote.buttons=64u;send(provider,remote); // Actual LT modifier prefix is a separate SDK event.
    check(R::held()&&R::resumeChoice()==0u);
    const unsigned choice=test=="manual"||test=="manual_release"?1u:2u;
    remote.buttons=choice==1u?576u:320u;send(provider,remote);
    check(R::held()&&R::resumeChoice()==choice);
    if(test=="manual_release"||test=="automatic_release"){
      remote.buttons=64u;send(provider,remote);check(R::held()&&R::resumeChoice()==choice);
      neutral(provider,remote);check(R::held()&&R::resumeChoice()==choice);
    }
    check(!R::tryResume(generation,choice==1u?2u:1u));
    check(R::tryResume(generation,choice)&&!R::held());
    check(!R::tryResume(generation,choice));
    check(C::bodyAuthorized()&&!C::stopRequested()&&!Main::stops);return 0;
  }
  if(test=="new_generation"){
    neutral(provider,remote);remote.buttons=576u;send(provider,remote);
    check(R::resumeChoice()==1u);remove(provider,remote);
    check(R::generation()!=generation&&R::held()&&R::resumeChoice()==0u);
    check(!R::tryResume(generation,1u));
    neutral(provider,remote);remote.buttons=320u;send(provider,remote);
    check(R::resumeChoice()==2u&&!R::tryResume(generation,2u));
    check(R::tryResume(R::generation(),2u)&&!R::held());return 0;
  }
  if(test=="wrong_keys"){
    neutral(provider,remote);remote.buttons=512u;send(provider,remote);
    check(R::held()&&R::resumeChoice()==0u);
    remote.buttons=576u;remote.axes[1]=.7f;send(provider,remote);
    check(R::held()&&R::resumeChoice()==0u);return 0;
  }
  return 2;
}
'''


class RemoteDisconnectStandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory=tempfile.TemporaryDirectory(prefix='bh996-remote-stand-')
        cls.addClassCleanup(cls.directory.cleanup)
        main=MAIN.read_text();provider=PROVIDER.read_text()
        early=body(main,'liveRemoteSubscriber("rt/remote_controller_stat2", [](const void* message)')
        physical_f1=body(main,'static void buttonEventHandler(const void* msg)')
        provider_body=body(provider,'void BoosterProvider::remoteControllerStateHandler(')
        source=PREFIX+'\nstatic void buttonEventHandler(const void* msg)\n'+physical_f1
        source+='\nstatic void earlyRemoteCallback(const void* message)\n'+early
        source+='\nvoid BoosterProvider::remoteControllerStateHandler(const booster_interface::msg::RemoteControllerState& remoteControllerState)\n'+provider_body
        motion=(ROOT/'Src/Modules/MotionControl/MotionEngine/MotionEngine.cpp').read_text()
        guard=body(motion,'if(BoosterRemoteStand::held() && BoosterOfficialLiveTest::bodyAuthorized()')
        start=motion.index('if(BoosterRemoteStand::held() && BoosterOfficialLiveTest::bodyAuthorized()')
        predicate=motion[start:motion.index('{',start)]
        source+='\nstatic MotionRequest filterMotion(MotionRequest motionRequest,MotionPhase::Type type){\n'
        source+='struct Phase{MotionPhase::Type type;}value{type};const Phase* phase=&value;\n'+predicate+guard+'\nreturn motionRequest;}\n'
        source+='\n'+SUFFIX
        path=Path(cls.directory.name)/'callbacks.cpp';path.write_text(source)
        cls.binary=Path(cls.directory.name)/'callbacks'
        result=subprocess.run(['clang++','-std=c++20','-DNDEBUG','-Wall','-Wextra','-Werror','-pedantic',
                               '-I',str(ROOT/'Src/Libs'),str(path),'-o',str(cls.binary)],capture_output=True,text=True)
        if result.returncode:raise RuntimeError(result.stderr)

    def run_case(self,name):
        result=subprocess.run([str(self.binary),name],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,name+'\n'+result.stderr)

    def test_default_disabled_does_not_create_remote_hold(self):self.run_case('default')
    def test_ordinary_live_remove_retains_original_stop(self):self.run_case('ordinary_live_remove')
    def test_actual_main_and_provider_remove_clear_input_and_retain_f1_authority(self):self.run_case('match_remove')
    def test_x_keeps_terminal_stop(self):self.run_case('x')
    def test_remove_with_x_keeps_terminal_stop(self):self.run_case('remove_x')
    def test_nan_axis_keeps_terminal_stop(self):self.run_case('nan')
    def test_inf_axis_keeps_terminal_stop(self):self.run_case('inf')
    def test_remove_with_nan_keeps_terminal_stop(self):self.run_case('remove_nan')
    def test_actual_early_null_message_keeps_terminal_stop(self):self.run_case('null')
    def test_actual_f1_callback_keeps_terminal_stop(self):self.run_case('f1')
    def test_old_stick_and_preheld_lt_chord_cannot_resume(self):self.run_case('old_input')
    def test_reconnection_and_neutral_do_not_automatically_clear_hold(self):self.run_case('neutral_alone')
    def test_real_neutral_then_lt_plus_576_allows_explicit_manual_resume(self):self.run_case('manual')
    def test_real_neutral_then_lt_minus_320_allows_explicit_automatic_resume(self):self.run_case('automatic')
    def test_manual_chord_natural_release_lt_then_zero_keeps_one_shot_proof(self):self.run_case('manual_release')
    def test_automatic_chord_natural_release_lt_then_zero_keeps_one_shot_proof(self):self.run_case('automatic_release')
    def test_new_loss_generation_rejects_old_resume_proof(self):self.run_case('new_generation')
    def test_wrong_keys_or_non_neutral_axes_do_not_resume(self):self.run_case('wrong_keys')
    def test_actual_motion_guard_stands_only_upright_walk_stand_phases_and_requests(self):self.run_case('motion_guard')
    def test_motion_guard_does_not_grant_missing_f1_body_authority(self):self.run_case('motion_no_authority')

    def test_996_main_enables_policy_only_inside_match_scope(self):
        source=MAIN.read_text();scope=body(source,'if(officialMatchMode)')
        self.assertEqual(source.count('BoosterRemoteStand::enable();'),2)
        self.assertEqual(scope.count('BoosterRemoteStand::enable();'),1)
        self.assertIn('BoosterRemoteStand::enable();',scope)
        self.assertIn('actualSerial != "1424725032007"',source)
        self.assertIn('"--k1-996-match"',source)
        target=body(source,'if(officialMatch977)\n    {')
        self.assertEqual(target.count('BoosterRemoteStand::enable();'),1)
        self.assertLess(target.index('match977IdentityAllowed(actualSerial, bhDir)'),
                        target.index('BoosterRemoteStand::enable();'))
        self.assertIn('return EXIT_FAILURE',target[:target.index('BoosterRemoteStand::enable();')])

    def test_actual_behavior_hold_precedes_ready_handoff_and_automatic_options(self):
        source=(ROOT/'Src/Modules/BehaviorControl/SkillBehaviorControl/Options/HandleJoystick.cpp').read_text()
        main=source[source.index('option((SkillBehaviorControl) HandleJoystick,'):]
        transition=body(main,'common_transition')
        self.assertLess(transition.index('if(BoosterRemoteStand::held())'),transition.index('if(automaticEntryHandoff)'))
        held=body(transition,'if(BoosterRemoteStand::held())')
        for token in ('bodyAuthorized()','joystickState.valid','neutralAxes','!theSystemSensorData.emergencyMode',
                      '!theGameState.stopped','FallDownState::upright','MotionPhase::stand','MotionPhase::walk',
                      'BoosterRemoteStand::tryResume(generation, choice)','goto remoteLost'):
            self.assertIn(token,held)
        lost=body(main,'state(remoteLost)')
        self.assertIn('Stand();',lost);self.assertIn('LookForward();',lost)
        self.assertNotIn('PlayDead',lost);self.assertNotIn('tryResume',lost)
        config=(ROOT/'Config/Scenarios/Default/optionPlaySoccer.cfg').read_text()
        self.assertLess(config.index('HandlePhysicalRobot'),config.index('HandleStoppedState'))
        self.assertLess(config.index('HandleStoppedState'),config.index('HandleJoystick'))
        self.assertLess(config.index('HandleJoystick'),config.index('HandleGameState'))

    def test_motion_override_remains_below_hard_body_and_imu_protections(self):
        source=(ROOT/'Src/Modules/MotionControl/MotionEngine/MotionEngine.cpp').read_text()
        guard=source.index('if(BoosterRemoteStand::held() && BoosterOfficialLiveTest::bodyAuthorized()')
        self.assertLess(guard,source.index('if(forceSitDown)',guard))
        self.assertLess(guard,source.index('theFallGenerator.shouldCatchFall',guard))
        self.assertLess(guard,source.index('MotionRobotHealth::bodyDisconnect',guard))
        provider=PROVIDER.read_text()
        finish=body(provider,'void BoosterProvider::finishFrame2()')
        self.assertEqual(finish.count('if(BoosterOfficialLiveTest::stopRequested())'),2)


if __name__=='__main__':unittest.main()
