"""Official resident F1/X mode: real production methods, fake SDK only.

No SSH, robot, service, SDK RPC, ARM program or physical motion is used.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "Src/Apps/Booster/Main.cpp"
PROVIDER = ROOT / "Src/Modules/Infrastructure/BoosterProvider/BoosterProvider.cpp"


def block(source, marker):
    start = source.index(marker)
    opening = source.index("{", start)
    index, depth = opening + 1, 1
    while depth:
        depth += (source[index] == "{") - (source[index] == "}")
        index += 1
    return source[start:index]


PREAMBLE = r'''
#include <atomic>
#include <array>
#include <cmath>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <functional>
#include <limits>
#include <mutex>
#include <string>
#include <utility>
#include <vector>
#define state(name) static_assert(false, "CABSL macro unexpectedly expanded")
#include "Platform/BoosterOfficialLiveTest.h"
#include "Platform/BoosterAppCommissioning.h"
#include "Platform/BoosterMatchSafety.h"
#undef state
namespace C = BoosterOfficialLiveTest;
static void check(bool value){if(!value)std::abort();}
constexpr std::int64_t second=1000000000LL;
static volatile sig_atomic_t run=1;
namespace Main {
 unsigned stops=0;
 void stopOfficialLiveTest(){if(C::stopRequested()){++stops;run=0;}}
}
namespace booster::robot {
 enum class RobotMode{kDamping=0,kOther=1,kCustom=3};
 namespace b1 {constexpr unsigned REMOVE=2;}
}
namespace booster_interface::msg {
 enum ButtonEventType{PRESS_DOWN,PRESS_UP,LONG_PRESS_START,LONG_PRESS_END};
 struct ButtonEventMsg {
  unsigned index=0;ButtonEventType kind=PRESS_DOWN;
  unsigned button()const{return index;}ButtonEventType event()const{return kind;}
 };
 struct RemoteControllerState {
  unsigned code=1,buttons=0;std::array<float,4> axes{};
  unsigned event()const{return code;}
  bool x()const{return buttons&1u;}bool a()const{return buttons&2u;}
  bool b()const{return buttons&4u;}bool y()const{return buttons&8u;}
  bool lb()const{return buttons&16u;}bool rb()const{return buttons&32u;}
  bool lt()const{return buttons&64u;}bool rt()const{return buttons&128u;}
  bool back()const{return buttons&256u;}bool start()const{return buttons&512u;}
  bool ls()const{return buttons&1024u;}bool rs()const{return buttons&2048u;}
  float lx()const{return axes[0];}float ly()const{return axes[1];}
  float rx()const{return axes[2];}float ry()const{return axes[3];}
  bool hat_l()const{return false;}bool hat_lu()const{return false;}bool hat_ld()const{return false;}
  bool hat_r()const{return false;}bool hat_ru()const{return false;}bool hat_rd()const{return false;}
  bool hat_u()const{return false;}bool hat_d()const{return false;}
 };
}
struct FakeClient {
 using Mode=booster::robot::RobotMode;
 std::vector<Mode> requests;Mode actual=Mode::kDamping;
 std::function<void(Mode)> duringRpc;
 int ChangeMode(Mode target){
  requests.push_back(target);
  if(duringRpc){auto once=std::exchange(duringRpc,{});once(target);}
  actual=target;return 0; // A late Custom completion overwrites before the real post-RPC guard.
 }
};
struct EmergencyHandler {
 enum State{noEmergency,emergencyPressed,emergencyReleased};
 std::atomic<State> state{emergencyReleased};unsigned changes=0;
 std::atomic<bool> commissionOnly{false},commissionLatched{false},commissionDampingAck{false},commissionFault{false};
 std::atomic<unsigned> commissionModeFailureCount{0},commissionModeExceptionCount{0};
 std::atomic<int> commissionLastModeRequested{-1},commissionLastModeResult{0};
 FakeClient client;
 void setState(State target,bool=true){state.store(target);++changes;}
 void latchCommissionEmergency(){commissionLatched.store(true);}
'''

AFTER_HANDLER = r'''
};
static EmergencyHandler emergencyHandler;
struct JoystickState {std::array<float,8> axes{};unsigned buttons=0;bool valid=false;};
struct BoosterProvider {
 std::recursive_mutex mutex;JoystickState joystickState;
 void remoteControllerStateHandler(const booster_interface::msg::RemoteControllerState&);
};
#define SYNC std::lock_guard<std::recursive_mutex> lock(mutex)
'''

TESTS = r'''
using namespace booster_interface::msg;
using Mode=booster::robot::RobotMode;
static void button(ButtonEventType event,unsigned index=0){ButtonEventMsg input;input.kind=event;input.index=index;buttonEventHandler(&input);}
static void official(){C::enableOfficialButtonMode();BoosterMatchSafety::enable();}
static void arm(){button(PRESS_DOWN);button(LONG_PRESS_START);check(C::bodyAuthorized());}
static void release(){button(PRESS_UP);check(C::bodyAuthorized());}
static void freshCognition(){for(unsigned frame=1;frame<=5;++frame)C::noteCognitionForF1Authorization(frame,C::steadyTimeNs());}
static void bothReaders(BoosterProvider& provider,const RemoteControllerState& input,bool mainFirst=true){
 if(mainFirst){mainRemoteReader(&input);provider.remoteControllerStateHandler(input);}
 else{provider.remoteControllerStateHandler(input);mainRemoteReader(&input);}
}
int main(int argc,char** argv){
 check(argc==2);const std::string test=argv[1];
 if(test=="header_no_cognition"){
   official();check(C::officialButtonMode()&&!C::bodyAuthorized());
   check(!C::authorizePhysicalF1Long(2*second));
   C::observePhysicalF1Down(second);check(C::authorizePhysicalF1Long(2*second));
   check(C::bodyAuthorized()&&C::diagnostics().cognitionFrames==0&&!C::stopRequested());
 }else if(test=="header_stale_cognition"){
   official();for(unsigned frame=1;frame<=5;++frame)C::noteCognitionForF1Authorization(frame,second);
   C::observePhysicalF1Down(2*second);
   check(C::authorizePhysicalF1Long(100*second)&&C::bodyAuthorized());
 }else if(test=="header_sequence"){
   official();C::observePhysicalF1Down(second);C::observePhysicalF1Release();
   check(!C::authorizePhysicalF1Long(2*second));
   C::observePhysicalF1Down(3*second);check(C::authorizePhysicalF1Long(4*second));
   const auto old=C::modeToken();check(C::customModeAllowed(old));
   C::observePhysicalF1Down(5*second);check(!C::bodyAuthorized()&&!C::customModeAllowed(old));
   C::observePhysicalF1Release();C::observePhysicalF1Down(6*second);
   check(C::authorizePhysicalF1Long(7*second));
   check(C::modeToken()!=old&&!C::customModeAllowed(old)&&C::customModeAllowed(C::modeToken()));
 }else if(test=="callback_no_cognition"||test=="callback_stale_cognition"){
   official();
   if(test=="callback_stale_cognition")for(unsigned frame=1;frame<=5;++frame)C::noteCognitionForF1Authorization(frame,second);
   const auto epoch=BoosterMatchSafety::epoch();arm();release();
   check(BoosterMatchSafety::epoch()>epoch&&emergencyHandler.state==EmergencyHandler::noEmergency);
   check(run==1&&!C::stopRequested()&&!Main::stops);
 }else if(test=="callback_repeat_modes"){
   official();arm();release();
   for(unsigned cycle=0;cycle<32;++cycle){
     const auto epoch=BoosterMatchSafety::epoch();const auto token=C::modeToken();
     button(PRESS_DOWN);
     check(!C::bodyAuthorized()&&!C::stopRequested()&&run==1&&!Main::stops);
     check(emergencyHandler.state==EmergencyHandler::emergencyPressed&&BoosterMatchSafety::epoch()>epoch);
     check(!C::customModeAllowed(token));
     button(PRESS_UP);check(emergencyHandler.state==EmergencyHandler::emergencyReleased);
     button(LONG_PRESS_START);check(!C::bodyAuthorized()); // No invented LONG without a new physical DOWN.
     arm();release();check(emergencyHandler.state==EmergencyHandler::noEmergency&&C::modeToken()!=token);
   }
 }else if(test=="callback_wrong_or_cancelled"){
   official();const auto epoch=BoosterMatchSafety::epoch();
   button(PRESS_DOWN,1);button(LONG_PRESS_START,1);
   check(!C::bodyAuthorized()&&BoosterMatchSafety::epoch()==epoch);
   button(LONG_PRESS_START);check(!C::bodyAuthorized());
   button(PRESS_DOWN);button(PRESS_UP);button(LONG_PRESS_START);
   check(!C::bodyAuthorized()&&!C::stopRequested()&&run==1);
 }else if(test=="x_two_readers"){
   official();BoosterRemoteStand::enable();arm();release();BoosterProvider provider;
   const auto token=C::modeToken();const auto epoch=BoosterMatchSafety::epoch();
   for(unsigned buttons:{1u,33u})for(bool mainFirst:{true,false})for(unsigned duplicate=0;duplicate<20;++duplicate){
     RemoteControllerState input;input.buttons=buttons;bothReaders(provider,input,mainFirst);
     check(!C::stopRequested()&&C::bodyAuthorized()&&run==1&&!Main::stops);
     check(provider.joystickState.valid&&provider.joystickState.buttons==buttons);
     check(C::modeToken()==token&&BoosterMatchSafety::epoch()==epoch);
     check(emergencyHandler.client.requests.empty()); // X never invents a Damping RPC.
   }
 }else if(test=="remove_two_readers"){
   official();BoosterRemoteStand::enable();arm();release();BoosterProvider provider;
   RemoteControllerState input;input.code=booster::robot::b1::REMOVE;input.buttons=33;
   bothReaders(provider,input);bothReaders(provider,input,false);
   check(BoosterRemoteStand::held()&&!provider.joystickState.valid);
   check(!C::stopRequested()&&C::bodyAuthorized()&&run==1&&!Main::stops);
 }else if(test=="finite_axis_fault"||test=="plain_remove_fault"||test=="null_remote_fault"){
   official();arm();release();BoosterProvider provider;RemoteControllerState input;
   if(test=="finite_axis_fault")input.axes[2]=std::numeric_limits<float>::quiet_NaN();
   else if(test=="plain_remove_fault")input.code=booster::robot::b1::REMOVE;
   if(test=="null_remote_fault")mainRemoteReader(nullptr);else bothReaders(provider,input);
   check(C::stopRequested()&&!C::bodyAuthorized()&&run==0&&Main::stops>0);
 }else if(test=="legacy_f1_stop"||test=="legacy_x_stop"){
   C::enable();BoosterMatchSafety::enable();check(!C::officialButtonMode());
   button(PRESS_DOWN);button(LONG_PRESS_START);check(!C::bodyAuthorized());
   freshCognition();button(LONG_PRESS_START);check(C::bodyAuthorized());release();
   if(test=="legacy_f1_stop")button(PRESS_DOWN);
   else{RemoteControllerState input;input.buttons=1;mainRemoteReader(&input);}
   check(C::stopRequested()&&!C::bodyAuthorized()&&run==0&&Main::stops>0);
 }else if(test=="sigint_never_rearm"){
   official();arm();release();sigHandlerOfficialLiveShutdown(SIGINT);
   check(run==0&&C::stopRequested());
   for(unsigned cycle=0;cycle<16;++cycle){
     C::enableOfficialButtonMode();button(PRESS_DOWN);button(PRESS_UP);button(PRESS_DOWN);button(LONG_PRESS_START);
     check(C::stopRequested()&&!C::bodyAuthorized()&&!C::customModeAllowed(C::modeToken())&&run==0);
   }
 }else if(test=="rpc_unarmed"){
   official();check(emergencyHandler.changeMode(Mode::kCustom)==-1&&emergencyHandler.client.requests.empty());
   check(emergencyHandler.changeMode(Mode::kDamping)==0&&emergencyHandler.client.requests.size()==1);
 }else if(test=="rpc_normal"||test=="rpc_down"||test=="rpc_rearmed"||test=="rpc_sigint"||test=="rpc_release"){
   official();arm();release();const auto permission=C::modeToken();
   if(test!="rpc_normal")emergencyHandler.client.duringRpc=[&](Mode target){
     check(target==Mode::kCustom);
     if(test=="rpc_sigint")sigHandlerOfficialLiveShutdown(SIGINT);
     else if(test=="rpc_release")button(PRESS_UP);
     else{
       button(PRESS_DOWN);check(!C::bodyAuthorized()&&!C::stopRequested()&&run==1);
       if(test=="rpc_rearmed"){button(PRESS_UP);arm();release();check(C::modeToken()!=permission);}
     }
   };
   const auto result=emergencyHandler.changeMode(Mode::kCustom);
   const bool shouldReject=test=="rpc_down"||test=="rpc_rearmed"||test=="rpc_sigint";
   check(result==(shouldReject?-1:0));
   if(shouldReject){
     check(emergencyHandler.client.requests.size()==2&&emergencyHandler.client.requests.front()==Mode::kCustom);
     check(emergencyHandler.client.requests.back()==Mode::kDamping&&emergencyHandler.client.actual==Mode::kDamping);
     check(!C::customModeAllowed(permission));
   }else check(emergencyHandler.client.requests.size()==1&&emergencyHandler.client.actual==Mode::kCustom);
   if(test=="rpc_rearmed")check(C::bodyAuthorized()&&!C::stopRequested()&&run==1);
   if(test=="rpc_down")check(!C::bodyAuthorized()&&!C::stopRequested()&&run==1&&!Main::stops);
   if(test=="rpc_sigint")check(C::stopRequested()&&run==0);
 }else return 2;
 return 0;
}
'''


class OfficialButtonModesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = MAIN.read_text()
        cls.provider = PROVIDER.read_text()
        cls.temporary = tempfile.TemporaryDirectory(prefix="bh977-official-button-cpu-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cpp, cls.binary = Path(cls.temporary.name) / "test.cpp", Path(cls.temporary.name) / "test"
        change = block(cls.main, "int changeMode(const booster::robot::RobotMode mode)")
        callback = block(cls.main, "static void buttonEventHandler(const void* msg)")
        signal = block(cls.main, "static void sigHandlerOfficialLiveShutdown(int)")
        remote = "static const auto mainRemoteReader = " + block(cls.main, "[](const void* message)") + ";\n"
        provider = block(cls.provider, "void BoosterProvider::remoteControllerStateHandler(")
        cpp.write_text(PREAMBLE + change + AFTER_HANDLER + signal + callback + remote + provider + TESTS)
        result = subprocess.run(["clang++", "-std=c++20", "-pthread", "-DNDEBUG", "-Wall", "-Wextra", "-Werror",
                                 "-pedantic", "-I", str(ROOT / "Src/Libs"), str(cpp), "-o", str(cls.binary)],
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stderr)

    def cases(self, *names):
        for name in names:
            with self.subTest(case=name):
                result = subprocess.run([str(self.binary), name], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_official_long_needs_real_down_not_old_cognition_or_ten_second_gates(self):
        self.cases("header_no_cognition", "header_stale_cognition", "header_sequence")

    def test_real_main_callback_first_long_and_repeated_resident_mode_cycles(self):
        self.cases("callback_no_cognition", "callback_stale_cognition", "callback_repeat_modes", "callback_wrong_or_cancelled")

    def test_x_and_rb_x_survive_both_real_remote_readers_and_duplicates(self):
        self.cases("x_two_readers")

    def test_remove_and_corrupt_input_keep_their_existing_safety_semantics(self):
        self.cases("remove_two_readers", "finite_axis_fault", "plain_remove_fault", "null_remote_fault")

    def test_old_entry_f1_and_x_stops_remain_unchanged(self):
        self.cases("legacy_f1_stop", "legacy_x_stop")

    def test_explicit_sigint_remains_irreversible(self):
        self.cases("sigint_never_rearm")

    def test_real_change_mode_rpc_rejects_late_custom_from_revoked_or_old_generation(self):
        self.cases("rpc_unarmed", "rpc_normal", "rpc_down", "rpc_rearmed", "rpc_sigint", "rpc_release")

    def test_only_new_977_scope_opts_in_and_post_rpc_guard_is_not_just_a_test_model(self):
        scope = block(self.main, "if(officialMatch977)\n    {")
        old = block(self.main, "if(officialLiveTest)\n    {")
        self.assertEqual(self.main.count("enableOfficialButtonMode();"), 1)
        self.assertIn("BoosterOfficialLiveTest::enableOfficialButtonMode();", scope)
        self.assertNotIn("enableOfficialButtonMode", old)
        callback = block(self.main, "static void buttonEventHandler(const void* msg)")
        self.assertIn("!BoosterOfficialLiveTest::officialButtonMode()", callback)
        self.assertLess(callback.index("observePhysicalF1Down("), callback.index("if(stopMatch)"))
        change = block(self.main, "int changeMode(const booster::robot::RobotMode mode)")
        rpc = change.index("const int result = client.ChangeMode(mode);")
        self.assertLess(change.index("modeToken()"), rpc)
        self.assertIn("customModeAllowed(permission)", change[:rpc])
        self.assertIn("customModeAllowed(permission)", change[rpc:])
        self.assertIn("client.ChangeMode(booster::robot::RobotMode::kDamping)", change[rpc:])


if __name__ == "__main__":
    unittest.main()
