"""Independent CPU replay of real header/Main/Provider paths; no robot API."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import tempfile
import unittest

SOURCE=Path(__file__).resolve().parents[2]
OFFICIAL_COMMIT='2e3374ba896da8288607f9a1881b876642fe18b8'
MAIN=SOURCE/'Src/Apps/Booster/Main.cpp'
PROVIDER=SOURCE/'Src/Modules/Infrastructure/BoosterProvider/BoosterProvider.cpp'
HEADER=SOURCE/'Src/Libs/Platform/BoosterOfficialLiveTest.h'


def block(source,marker):
    start=source.index(marker)
    index=source.index('{',start)+1
    depth=1
    while depth:
        depth+=(source[index]=='{')-(source[index]=='}')
        index+=1
    return source[start:index]


PREAMBLE=r'''
#include <atomic>
#include <array>
#include <cstdint>
#include <cstdlib>
#include <functional>
#include <limits>
#include <string>
#include <vector>
#include "Platform/BoosterOfficialLiveTest.h"
#include "Platform/BoosterMatchSafety.h"
#include "Platform/BoosterPickupControl.h"
namespace C=BoosterOfficialLiveTest;
static void check(bool ok) {if(!ok) std::abort();}
struct Remote {
  unsigned eventValue=1; bool xValue=false;
  std::array<float,4> axes{};
  unsigned event()const{return eventValue;}
  bool x()const{return xValue;}
  float lx()const{return axes[0];} float ly()const{return axes[1];}
  float rx()const{return axes[2];} float ry()const{return axes[3];}
};
static void official() {C::enable(); C::enableOfficialButtonMode();}
[[maybe_unused]] static void armWithoutCognition() {
  const auto now=C::steadyTimeNs();
  C::observePhysicalF1Down(now);
  check(C::authorizePhysicalF1Long(now+1));
  check(C::bodyAuthorized() && !C::stopRequested());
}
'''


class ProductionModePaths(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='bh987-official-paths-')
        cls.dir=Path(cls.temp.name)
        cls.main=MAIN.read_text()
        cls.provider=PROVIDER.read_text()
        cls.header=HEADER.read_text()
        if 'enableOfficialButtonMode' not in cls.header:
            raise RuntimeError('candidate not ready: new official-mode header absent')
        cls.hash_before={str(p.relative_to(SOURCE)):hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in (MAIN,PROVIDER,HEADER)}

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def compile_run(self,name,source,cases):
        cpp=self.dir/(name+'.cpp')
        binary=self.dir/name
        cpp.write_text(source)
        result=subprocess.run(['clang++','-std=c++20','-pthread','-DNDEBUG','-Wall','-Wextra','-Werror','-pedantic',
            '-I',str(SOURCE/'Src/Libs'),str(cpp),'-o',str(binary)],capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stderr)
        for case in cases:
            with self.subTest(case=case):
                result=subprocess.run([str(binary),case],capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)

    def test_actual_header_official_vs_legacy_and_token(self):
        source=PREAMBLE+r'''
int main(int argc,char**argv) {
  check(argc==2); const std::string test=argv[1];
  if(test=="legacy_x") {
    C::enable(); check(!C::officialButtonMode());
    Remote r;r.xValue=true;C::observeRemoteStopX(r,2u);
    check(C::stopRequested()); return 0;
  }
  if(test=="legacy_no_cognition") {
    C::enable(); const auto now=C::steadyTimeNs();C::observePhysicalF1Down(now);
    check(!C::authorizePhysicalF1Long(now+1));return 0;
  }
  if(test=="legacy_rpc") {
    C::enable();check(C::customModeAllowed(C::modeToken()));C::requestStop();
    check(!C::customModeAllowed(C::modeToken()));return 0;
  }
  official();
  if(test=="isolated_long") {
    check(!C::authorizePhysicalF1Long(C::steadyTimeNs()));
    check(!C::bodyAuthorized()&&!C::stopRequested());return 0;
  }
  armWithoutCognition();
  const auto first=C::modeToken();check(C::customModeAllowed(first));
  if(test=="x_twice") {
    Remote r;r.xValue=true;C::observeRemoteStopX(r,2u);C::observeRemoteStopX(r,2u);
    check(!C::stopRequested()&&C::bodyAuthorized());return 0;
  }
  if(test=="bad_axis") {
    Remote r;r.axes[1]=std::numeric_limits<float>::quiet_NaN();C::observeRemoteStopX(r,2u);
    check(C::stopRequested()&&!C::bodyAuthorized());return 0;
  }
  if(test=="explicit_stop") {
    C::requestStop();C::observePhysicalF1Down(C::steadyTimeNs());
    check(!C::authorizePhysicalF1Long(C::steadyTimeNs()+1));
    check(C::stopRequested()&&!C::bodyAuthorized());return 0;
  }
  if(test=="f1_rearm"||test=="new_token") {
    C::observePhysicalF1Down(C::steadyTimeNs());
    check(!C::bodyAuthorized()&&!C::stopRequested()&&!C::customModeAllowed(first));
    C::observePhysicalF1Release();check(!C::bodyAuthorized());
    check(!C::authorizePhysicalF1Long(C::steadyTimeNs()+1));
    armWithoutCognition();
    check(C::modeToken()!=first && !C::customModeAllowed(first) && C::customModeAllowed(C::modeToken()));
    return 0;
  }
  return 2;
}
'''
        self.compile_run('header',source,('legacy_x','legacy_no_cognition','legacy_rpc','isolated_long',
            'x_twice','bad_axis','explicit_stop','f1_rearm','new_token'))

    def test_actual_main_f1_callback_persistent_and_rearm(self):
        callback=block(self.main,'static void buttonEventHandler(const void* msg)')
        source=PREAMBLE+r'''
namespace Main {unsigned stopped=0;void stopOfficialLiveTest(){++stopped;C::requestStop();}}
namespace booster_interface::msg {
  enum Event{PRESS_DOWN,PRESS_UP,LONG_PRESS_START,LONG_PRESS_END};
  struct ButtonEventMsg {
    Event value=PRESS_DOWN;unsigned key=0;
    unsigned button()const{return key;} Event event()const{return value;}
  };
}
struct EmergencyHandler {
  enum State{noEmergency,emergencyPressed,emergencyReleased};
  std::atomic<State> state{emergencyReleased};
  void setState(State value,bool=true){state.store(value);}
};
static EmergencyHandler emergencyHandler;
'''+callback+r'''
static void press(booster_interface::msg::Event e,unsigned key=0) {
  booster_interface::msg::ButtonEventMsg msg;msg.value=e;msg.key=key;buttonEventHandler(&msg);
}
int main(int argc,char**argv) {
  check(argc==2);const std::string test=argv[1];using namespace booster_interface::msg;
  official();BoosterMatchSafety::enable();
  if(test=="isolated_long") {
    press(LONG_PRESS_START);check(!C::bodyAuthorized()&&!Main::stopped);return 0;
  }
  if(test=="short") {
    press(PRESS_DOWN);press(PRESS_UP);check(!C::bodyAuthorized()&&!C::stopRequested()&&!Main::stopped);return 0;
  }
  if(test=="other_button") {
    press(PRESS_DOWN,1);press(LONG_PRESS_START,1);check(!C::bodyAuthorized()&&!Main::stopped);return 0;
  }
  press(PRESS_DOWN);press(LONG_PRESS_START);press(PRESS_UP);
  check(C::bodyAuthorized()&&emergencyHandler.state==EmergencyHandler::noEmergency&&!Main::stopped);
  const auto token=C::modeToken();
  press(PRESS_DOWN);check(!C::bodyAuthorized()&&!C::stopRequested()&&!Main::stopped);
  check(emergencyHandler.state==EmergencyHandler::emergencyPressed&&!C::customModeAllowed(token));
  if(test=="missing_up") {
    press(PRESS_DOWN);press(LONG_PRESS_START);
    check(!C::bodyAuthorized()&&!Main::stopped);
    press(PRESS_UP);press(PRESS_DOWN);press(LONG_PRESS_START);press(PRESS_UP);
    check(C::bodyAuthorized()&&!Main::stopped);return 0;
  }
  press(PRESS_UP);check(emergencyHandler.state==EmergencyHandler::emergencyReleased);
  if(test=="explicit_stop")C::requestStop();
  press(PRESS_DOWN);press(LONG_PRESS_START);press(PRESS_UP);
  if(test=="explicit_stop")check(!C::bodyAuthorized()&&C::stopRequested());
  else check(C::bodyAuthorized()&&!C::stopRequested()&&C::modeToken()!=token);
  check(!Main::stopped);return 0;
}
'''
        self.compile_run('main_f1',source,('isolated_long','short','other_button','rearm','missing_up','explicit_stop'))

    def test_actual_emergency_rpc_epoch_guard(self):
        method=block(self.main,'int changeMode(const booster::robot::RobotMode mode)')
        branch=block(method,'if(!commissionOnly.load())')
        prefix=method[:method.index(branch)]+branch+'\nreturn -99;\n}'
        source=PREAMBLE+r'''
#include "Platform/BoosterAppCommissioning.h"
namespace booster::robot {enum class RobotMode{kDamping=0,kCustom=3};}
using Mode=booster::robot::RobotMode;
struct Client {
  std::vector<Mode> calls;std::function<void(Mode)> hook;
  int ChangeMode(Mode m){calls.push_back(m);if(hook)hook(m);return 0;}
};
static Client client;
struct Handler{std::atomic<bool> commissionOnly{false};
'''+prefix+r'''
};
int main(int argc,char**argv) {
  check(argc==2);const std::string test=argv[1];official();Handler h;
  if(test=="unarmed") {
    check(h.changeMode(Mode::kCustom)!=0&&client.calls.empty());return 0;
  }
  armWithoutCognition();
  if(test=="rpc_ok") {
    check(h.changeMode(Mode::kCustom)==0&&client.calls.size()==1&&client.calls[0]==Mode::kCustom);return 0;
  }
  client.hook=[&](Mode mode){
    if(mode==Mode::kCustom){
      C::observePhysicalF1Down(C::steadyTimeNs());
      if(test=="rearm_during_rpc"){C::observePhysicalF1Release();armWithoutCognition();}
      if(test=="stop_during_rpc")C::requestStop();
    }
  };
  check(h.changeMode(Mode::kCustom)!=0);
  check(client.calls.size()==2&&client.calls[0]==Mode::kCustom&&client.calls[1]==Mode::kDamping);
  return 0;
}
'''
        self.compile_run('rpc_epoch',source,('unarmed','rpc_ok','f1_during_rpc','rearm_during_rpc','stop_during_rpc'))

    def test_source_snapshot_remained_stable(self):
        after={str(p.relative_to(SOURCE)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (MAIN,PROVIDER,HEADER)}
        self.assertEqual(self.hash_before,after,'implementation changed during independent test; rerun frozen source')

    def test_actual_provider_pre_and_post_output_fences(self):
        finish=block(self.provider,'void BoosterProvider::finishFrame2()')
        prefix=finish[finish.index('{')+1:finish.index('DEBUG_RESPONSE(')]
        prefix=prefix.replace('#ifdef TARGET_ROBOT','').replace('#endif','')
        late=finish[finish.rindex('if(BoosterOfficialLiveTest::stopRequested())'):]
        stop=block(late,'if(BoosterOfficialLiveTest::stopRequested())')
        auth=block(late,'if(BoosterOfficialLiveTest::enabled() && !BoosterOfficialLiveTest::bodyAuthorized())')
        damp=block(late,'if(BoosterOfficialLiveTest::officialButtonMode())')
        source=PREAMBLE+r'''
#include "Platform/BoosterAppCommissioning.h"
#define SYNC ((void)0)
#define VERIFY(value) check(value)
namespace booster::robot {enum class RobotMode{kDamping=0,kCustom=3};}
namespace booster_interface::msg {enum Serial{SERIAL=17};}
namespace Main {unsigned stopped=0;void stopOfficialLiveTest(){++stopped;}}
struct Motor {
  void mode(int){} void q(float){} void dq(float){} void kp(float){}
  void kd(float){} void tau(float){} void weight(float){}
};
struct Cmd {std::vector<Motor> motors=std::vector<Motor>(22);
  auto& motor_cmd(){return motors;} void cmd_type(booster_interface::msg::Serial){}
};
struct Publisher {unsigned calls=0;bool succeeds=true;bool Write(Cmd*){++calls;return succeeds;}};
struct Provider {
  Cmd lowCmd;Publisher lowCmdPublisher;
  booster::robot::RobotMode robotMode=booster::robot::RobotMode::kDamping;
  struct {unsigned executedPhase=0;} theMotionInfo;
  unsigned normalCalculated=0,normalWritten=0;
  std::function<void()> duringCalculation;
  void finish(){
'''+prefix+r'''
    ++normalCalculated;
    if(duringCalculation)duringCalculation();
'''+stop+'\n'+auth+'\n'+damp+r'''
    ++normalWritten;
  }
};
int main(int argc,char**argv) {
  check(argc==2);const std::string test=argv[1];Provider p;
  if(test=="legacy_mode0"||test=="legacy_custom_write_failure") {
    C::enable();
    if(test=="legacy_custom_write_failure")p.lowCmdPublisher.succeeds=false;
    p.finish();check(p.lowCmdPublisher.calls==1&&!p.normalWritten);
    check(C::stopRequested()==(test=="legacy_custom_write_failure"));return 0;
  }
  official();
  if(test=="unarmed_mode0"||test=="unarmed_mode0_write_false") {
    p.lowCmdPublisher.succeeds=false;p.finish();
    check(!p.lowCmdPublisher.calls&&!p.normalCalculated&&!p.normalWritten&&!C::stopRequested()&&!Main::stopped);return 0;
  }
  p.robotMode=booster::robot::RobotMode::kCustom;
  if(test=="unarmed_custom") {
    p.finish();check(p.lowCmdPublisher.calls==1&&!p.normalWritten&&!C::stopRequested());return 0;
  }
  if(test=="custom_write_failure") {
    p.lowCmdPublisher.succeeds=false;p.finish();
    check(p.lowCmdPublisher.calls==1&&!p.normalWritten&&!C::stopRequested()&&!Main::stopped);return 0;
  }
  armWithoutCognition();
  if(test=="armed_mode0") {
    p.robotMode=booster::robot::RobotMode::kDamping;p.finish();
    check(!p.normalWritten&&!p.lowCmdPublisher.calls&&!C::stopRequested());return 0;
  }
  if(test=="armed_custom") {
    p.finish();check(p.normalCalculated==1&&p.normalWritten==1&&!p.lowCmdPublisher.calls);return 0;
  }
  p.duringCalculation=[&]{
    C::observePhysicalF1Down(C::steadyTimeNs());
    if(test=="f1_damping_during_calculation")p.robotMode=booster::robot::RobotMode::kDamping;
  };
  p.finish();
  check(p.normalCalculated==1&&!p.normalWritten&&!C::stopRequested()&&!Main::stopped);
  check(p.lowCmdPublisher.calls==(test=="f1_damping_during_calculation"?0u:1u));
  return 0;
}
'''
        self.compile_run('provider_fences',source,('legacy_mode0','legacy_custom_write_failure','unarmed_mode0','unarmed_mode0_write_false',
            'unarmed_custom','custom_write_failure','armed_custom','armed_mode0','f1_during_calculation','f1_damping_during_calculation'))

    def test_actual_key_mapping_single_x_vs_rb_x(self):
        update=block(self.provider,'void BoosterProvider::update(JoystickState& theJoystickState)')
        source=PREAMBLE+r'''
#define SYNC ((void)0)
struct JoystickState {unsigned buttons=0;bool pressed(int n)const{return (buttons&(1u<<n))!=0;}};
struct Provider {
  JoystickState joystickState;struct{std::array<bool,3> pressed{};}joystickKeyStates;
  struct ButtonMapping{int code,key;};
  std::vector<ButtonMapping> buttonMappings={{0,0},{1,1},{2,2}};
  int rtButtonCode=5;
  void update(JoystickState&);
};
'''+update.replace('BoosterProvider::','Provider::')+r'''
int main(int argc,char**argv) {
  check(argc==2);const std::string test=argv[1];official();armWithoutCognition();
  Provider p;JoystickState out;Remote input;
  if(test=="single_x")p.joystickState.buttons=1u;
  else if(test=="rb_x")p.joystickState.buttons=33u;
  else if(test=="rb_a")p.joystickState.buttons=34u;
  else if(test=="rb_b")p.joystickState.buttons=36u;
  else return 2;
  input.xValue=(p.joystickState.buttons&1u)!=0;
  C::observeRemoteStopX(input,2u);C::observeRemoteStopX(input,2u);p.update(out);
  check(!C::stopRequested()&&C::bodyAuthorized());
  check(p.joystickKeyStates.pressed[0]==(test=="rb_x"));
  check(p.joystickKeyStates.pressed[1]==(test=="rb_a"));
  check(p.joystickKeyStates.pressed[2]==(test=="rb_b"));
  return 0;
}
'''
        self.compile_run('key_mapping',source,('single_x','rb_x','rb_a','rb_b'))

    def test_actual_provider_sensor_emergency_recovers_same_process(self):
        method=block(self.provider,'void BoosterProvider::waitForFrameData2()')
        body=method.split('#ifdef TARGET_ROBOT',1)[1].split('#else',1)[0]
        source=PREAMBLE+r'''
#define SYNC ((void)0)
#define ASSERT(value) check(value)
namespace booster::robot {enum class RobotMode{kUnknown=-1,kDamping=0,kCustom=3};}
namespace MotionPhase{enum Type{playDead,walk};}
namespace MotionRequest{enum Motion{playDead,walk};}
namespace SensorData{constexpr float off=-9999.f;}
namespace Joints{constexpr unsigned numOfJoints=22;}
namespace SystemCall{void say(const char*,bool){}}
struct Provider {
  struct Signal{bool fresh=true;bool wait(unsigned){return fresh;}bool tryWait(){return false;}}frameDataSignal;
  unsigned maxDelayForFrameData=50;std::size_t connectedJoints=22;
  booster::robot::RobotMode robotMode=booster::robot::RobotMode::kDamping;
  struct{bool emergencyMode=false;}systemSensorData;
  struct{MotionPhase::Type phase=MotionPhase::playDead;bool isMotion(MotionPhase::Type type)const{return phase==type;}}theMotionInfo;
  struct{MotionRequest::Motion motion=MotionRequest::playDead;}theMotionRequest;
  struct Cmd{std::vector<int> joints=std::vector<int>(22);auto& motor_cmd(){return joints;}}lowCmd;
  std::array<unsigned,22> jointMapping{};
  struct Request{std::array<float,22>angles{};struct{std::array<int,22>stiffnesses{};}stiffnessData;}theJointRequest;
  Provider(){for(unsigned i=0;i<22;++i){jointMapping[i]=i;theJointRequest.angles[i]=SensorData::off;}}
  void wait(){
'''+body+r'''
  }
};
int main(int argc,char**argv) {
  check(argc==2);const std::string test=argv[1];official();Provider p;
  p.wait();check(p.systemSensorData.emergencyMode&&!C::stopRequested());
  armWithoutCognition();p.robotMode=booster::robot::RobotMode::kCustom;
  if(test=="no_low")p.frameDataSignal.fresh=false;
  else if(test=="no_joints")p.connectedJoints=0;
  else if(test=="unknown_mode")p.robotMode=booster::robot::RobotMode::kUnknown;
  else if(test=="partial_joints")p.connectedJoints=21;
  else if(test=="stiff_request"){p.theJointRequest.angles[0]=0.f;p.theJointRequest.stiffnessData.stiffnesses[0]=1;}
  else if(test=="not_playdead")p.theMotionInfo.phase=MotionPhase::walk;
  else if(test!="fresh_safe")return 2;
  p.wait();check(p.systemSensorData.emergencyMode!=(test=="fresh_safe"));
  check(!C::stopRequested()&&C::bodyAuthorized());
  return 0;
}
'''
        self.compile_run('sensor_recovery',source,('fresh_safe','no_low','no_joints','unknown_mode',
            'partial_joints','stiff_request','not_playdead'))

    def test_pd_calculation_byte_identical_to_official(self):
        # The pinned genuine official commit must exist in this repository's
        # ancestry. Missing objects are a failure, never a reason to skip the
        # byte-level oracle or use the current implementation as its own base.
        git_env={key:value for key,value in os.environ.items()
                 if key not in ('GIT_DIR','GIT_WORK_TREE','GIT_COMMON_DIR','GIT_INDEX_FILE')}
        result=subprocess.run(['git','-C',str(SOURCE),'show',
            OFFICIAL_COMMIT+':Src/Modules/Infrastructure/BoosterProvider/BoosterProvider.cpp'],
            capture_output=True,text=True,timeout=20,env=git_env)
        self.assertEqual(result.returncode,0,'Required official baseline unavailable: '+result.stderr)
        base=result.stdout
        self.assertTrue(base,'Required official baseline is empty')
        marker='for(size_t joint = 0; joint < lowCmd.motor_cmd().size(); ++joint)'
        # First is the emergency-release check; final occurrence is native PD.
        current=block(self.provider[self.provider.rindex(marker):],marker)
        original=block(base[base.rindex(marker):],marker)
        self.assertEqual(current,original,'normal official PD/torque loop changed')

    def test_actual_startup_zero_custom_damping_and_cancel_flow(self):
        reset_marker='if(!emergencyHandler.resetMotorCommands())'
        reset=block(self.main[self.main.rindex(reset_marker):],reset_marker)
        start=self.main.index('// Listen to the F1 button on the K1')
        start=self.main.index('if(settings.robotType == Settings::k1)',start)
        end=self.main.index('// END_OFFICIAL_MODE_STARTUP',start)
        fragment=self.main[start:end]
        # Fake process termination is a throwing boundary; branch logic is real.
        fragment=fragment.replace('std::_Exit(EXIT_FAILURE);','throw Terminated{};')
        callback=block(self.main,'static void buttonEventHandler(const void* msg)')
        source=PREAMBLE+r'''
#include <cstdio>
#define SIGTERM 15
namespace booster::robot {enum class RobotMode{kDamping=0,kCustom=3};}
namespace booster::robot::b1 {struct GetModeResponse{booster::robot::RobotMode mode_=booster::robot::RobotMode::kDamping;};}
namespace booster_interface::msg {
  enum Event{PRESS_DOWN,PRESS_UP,LONG_PRESS_START,LONG_PRESS_END};
  struct ButtonEventMsg{Event value=PRESS_DOWN;unsigned button()const{return 0;}Event event()const{return value;}};
}
using Mode=booster::robot::RobotMode;
static std::string scenario;static unsigned fakeTime=100,framesStarted=0,aborts=0;
static int run=1,bhumanPid=0;
static int kill(int,int){return 0;}
static int sem_post(void*){return 0;}
static struct{void*signal=nullptr;}connected;
namespace Settings{enum Type{k1,t1};}
static struct{Settings::Type robotType=Settings::k1;}settings;
static void bhumanStart(const auto&){++framesStarted;}
namespace Time{unsigned getCurrentSystemTime(){return fakeTime;}unsigned getTimeSince(unsigned time){return fakeTime-time;}}
namespace SystemCall{void say(const char*,bool=false){}}
namespace Main{void stopOfficialLiveTest(){++aborts;C::requestStop();run=0;}}
struct Client {
  Mode mode=Mode::kDamping;std::vector<Mode> calls;
  int GetMode(booster::robot::b1::GetModeResponse& out){out.mode_=mode;return scenario=="timeout"?1:0;}
  int ChangeMode(Mode target){calls.push_back(target);mode=target;return 0;}
};
static Client client;
struct EmergencyHandler {
  enum State{noEmergency,emergencyPressed,emergencyReleased};
  std::atomic<State>state{emergencyReleased};bool started=false;unsigned resets=0;
  bool resetMotorCommands(){++resets;return scenario!="reset_failure";}
  void apply(){
    if(state==noEmergency){if(C::customModeAllowed(C::modeToken()))client.ChangeMode(Mode::kCustom);}
    else client.ChangeMode(Mode::kDamping);
  }
  void setState(State target,bool trigger=true){state.store(target);if(started&&trigger)apply();}
  void start(){started=true;apply();}
};
static EmergencyHandler emergencyHandler;
'''+callback+r'''
static void button(booster_interface::msg::Event event){booster_interface::msg::ButtonEventMsg m;m.value=event;buttonEventHandler(&m);}
struct Buttons{void InitChannel(){if(scenario=="f1_cancel")button(booster_interface::msg::PRESS_DOWN);}};
static Buttons buttonEventSubscriber;
struct Subscriber{void InitChannel(){}};
static Subscriber lowStateSubscriber,robotStatusSubscriber;
namespace Thread{void sleep(unsigned time){fakeTime+=time;if(scenario=="signal_cancel"){C::requestStop();run=0;}}}
struct Terminated{};
static int startup(){
'''+reset+'\n'+fragment+r'''
  if(run) bhumanStart(settings);
  return EXIT_SUCCESS;
}
int main(int argc,char**argv){
  check(argc==2);scenario=argv[1];official();BoosterMatchSafety::enable();
  bool terminated=false;int result=-1;
  try{result=startup();}catch(Terminated){terminated=true;}
  if(scenario=="reset_failure"){
    check(result==EXIT_FAILURE&&!framesStarted&&!C::startupConnectionAllowed()&&client.calls.empty());return 0;
  }
  if(scenario=="timeout"||scenario=="signal_cancel"){
    check((terminated||!run)&&!framesStarted&&C::stopRequested()&&!C::startupConnectionAllowed());return 0;
  }
  check(!terminated&&result==EXIT_SUCCESS&&framesStarted==1&&!C::stopRequested());
  check(emergencyHandler.resets==1&&!C::startupConnectionAllowed()&&!C::bodyAuthorized());
  check(client.mode==Mode::kDamping&&!C::customModeAllowed(0));
  if(scenario=="normal"){
    check(client.calls.size()==2&&client.calls[0]==Mode::kCustom&&client.calls[1]==Mode::kDamping);
  }else{
    check(scenario=="f1_cancel");
    for(auto call:client.calls)check(call!=Mode::kCustom);
    button(booster_interface::msg::PRESS_UP);
  }
  button(booster_interface::msg::PRESS_DOWN);button(booster_interface::msg::LONG_PRESS_START);
  button(booster_interface::msg::PRESS_UP);
  check(C::bodyAuthorized()&&!C::stopRequested()&&client.mode==Mode::kCustom&&!aborts);
  return 0;
}
'''
        self.compile_run('startup_flow',source,('normal','f1_cancel','reset_failure','timeout','signal_cancel'))


if __name__=='__main__':unittest.main()
