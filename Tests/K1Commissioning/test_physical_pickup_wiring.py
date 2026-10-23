"""Production physical callback/arbitration regressions; no SDK, SSH or motors."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from test_remote_disconnect_stand import PREFIX, body

ROOT = Path(__file__).resolve().parents[2]
OPTIONS = ROOT / "Src/Modules/BehaviorControl/SkillBehaviorControl/Options"


class PhysicalPickupWiringTests(unittest.TestCase):
    def compile_run(self, source):
        with tempfile.TemporaryDirectory(prefix="pickup-production-wiring-") as folder:
            cpp, binary = Path(folder) / "test.cpp", Path(folder) / "test"
            cpp.write_text(source)
            built = subprocess.run(["c++", "-std=c++20", "-Wall", "-Wextra", "-Werror", "-DNDEBUG",
                                    "-I", str(ROOT / "Src/Libs"), str(cpp), "-o", str(binary)],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_actual_body_callback_records_only_physical_stand_walk_edges(self):
        source = (ROOT / "Src/Modules/Infrastructure/BoosterProvider/BoosterProvider.cpp").read_text()
        callback = body(source, "void BoosterProvider::buttonEventHandler(")
        self.compile_run(r'''
#include "Platform/BoosterPickupControl.h"
#include <array>
#include <cstdlib>
namespace BoosterAppCommissioning {
  bool active=false;bool enabled(){return active;}
  enum class StopReason{f1};unsigned stops=0;void requestStop(StopReason){++stops;}
}
namespace booster_interface::msg {
  enum ButtonEventType{PRESS_DOWN,PRESS_UP,LONG_PRESS_START};
  struct ButtonEventMsg {unsigned index;ButtonEventType kind;
    unsigned button()const{return index;}ButtonEventType event()const{return kind;}};
}
namespace KeyStates{enum Key{f1,stand,walk};}
struct BoosterProvider {
  struct Keys{std::array<bool,3> pressed{};} buttonKeyStates;
  void buttonEventHandler(const booster_interface::msg::ButtonEventMsg&);
};
#define SYNC
void BoosterProvider::buttonEventHandler(const booster_interface::msg::ButtonEventMsg& buttonEvent)
''' + callback + r'''
static void check(bool value){if(!value)std::abort();}
int main(){
  using namespace booster_interface::msg;namespace P=BoosterPickupControl;
  BoosterProvider provider;
  provider.buttonEventHandler({1,PRESS_DOWN});
  check(P::physicalButtons().standDown()&&P::physicalButtons().standPress()==1);
  check(provider.buttonKeyStates.pressed[KeyStates::stand]);
  provider.buttonEventHandler({1,LONG_PRESS_START});
  provider.buttonEventHandler({1,PRESS_DOWN});check(P::physicalButtons().standPress()==1);
  provider.buttonEventHandler({1,PRESS_UP});check(!P::physicalButtons().standDown());
  provider.buttonEventHandler({2,PRESS_DOWN});check(P::physicalButtons().walkPress()==1);
  provider.buttonEventHandler({2,PRESS_UP});
  BoosterAppCommissioning::active=true;
  provider.buttonEventHandler({1,PRESS_DOWN});check(P::physicalButtons().standPress()==1);
  provider.buttonEventHandler({0,PRESS_DOWN});check(BoosterAppCommissioning::stops==1);
}
''')

    def test_actual_motion_guard_prevents_pickup_ball_intercept_without_overriding_recovery(self):
        motion = (ROOT / "Src/Modules/MotionControl/MotionEngine/MotionEngine.cpp").read_text()
        marker = "if((BoosterRemoteStand::held() || BoosterPickupControl::held()) && BoosterOfficialLiveTest::bodyAuthorized()"
        opening = motion.index(marker)
        guard = motion[opening:motion.index("{", opening)] + body(motion, marker)
        self.compile_run(PREFIX + r'''
static MotionRequest filtered(MotionRequest motionRequest,MotionPhase::Type type){
  struct Phase{MotionPhase::Type type;} value{type};const Phase* phase=&value;
''' + guard + r'''
  return motionRequest;
}
int main(){
  (void)booster::robot::b1::REMOVE;
  (void)emergencyHandler;
  C::enable();C::observePhysicalF1Down(1000000000LL);
  for(unsigned i=1;i<=5;++i)C::noteCognitionForF1Authorization(i,2000000000LL);
  check(C::authorizePhysicalF1Long(2000000000LL));
  BoosterPickupControl::setHeld(true);check(!R::held());
  for(auto phase:{MotionPhase::stand,MotionPhase::walk}){
    MotionRequest request;const auto result=filtered(request,phase);
    check(result.motion==MotionRequest::stand&&result.walkSpeed.x==0.f&&result.walkSpeed.y==0.f);
    check(!result.shouldInterceptBall&&!result.shouldWalkOutOfBallLine);
  }
  for(auto phase:{MotionPhase::fall,MotionPhase::getUp,MotionPhase::prepare,MotionPhase::playDead}){
    MotionRequest request;request.motion=MotionRequest::prepare;
    const auto result=filtered(request,phase);check(result.motion==request.motion);
    check(!result.shouldInterceptBall&&!result.shouldWalkOutOfBallLine);
  }
  R::enable();R::latchDisconnect();const auto old=R::generation();R::latchDisconnect();
  check(!R::resumeFromPhysicalPickup(old)&&R::held());
  check(R::resumeFromPhysicalPickup(R::generation())&&!R::held());
}
''')

    def test_physical_priority_and_old_intents_are_explicitly_revoked(self):
        source = (OPTIONS / "HandlePhysicalRobot.cpp").read_text()
        common = body(source, "common_transition")
        self.assertLess(common.index("physicalPickupGate.active()"), common.index("combinedGetUp.requestPrepare"))
        active = source[source.index("if(physicalPickupGate.active())"):source.index("const bool combinedWasPending")]
        for operation in ("revokePrepare()", "cancelJoystickAfterGetUp()", "initialHandoff().disarm()", "consumeJoystickRelease()"):
            self.assertIn(operation, active)
        self.assertIn("physicalPickupGate.finishAutomatic();", common)
        self.assertIn("resumeFromPhysicalPickup(pickupInput.remoteGeneration)", common)
        self.assertLess(common.index("pickup.suspended"), common.index("pickup.automaticReady"))
        recovery = body(source, "state(pickupNativeRecovery)")
        self.assertNotIn("FallRequest();", recovery)
        self.assertIn("Stand();", recovery)
        self.assertIn("MotionPhase::prepare", recovery)
        self.assertIn("PlayDead();", recovery)

    def test_lifetime_and_late_gc_rejection_cannot_release_a_pickup(self):
        header = (ROOT / "Src/Modules/BehaviorControl/SkillBehaviorControl/SkillBehaviorControl.h").read_text()
        behavior = (ROOT / "Src/Modules/BehaviorControl/SkillBehaviorControl/SkillBehaviorControl.cpp").read_text()
        physical = (OPTIONS / "HandlePhysicalRobot.cpp").read_text()
        self.assertIn("BoosterPickupControl::Gate physicalPickupGate;", header)
        variables = physical[physical.index("vars("):physical.index("#if defined(TARGET_ROBOT)")]
        self.assertNotIn("physicalPickupGate", variables)
        self.assertLess(behavior.index("physicalPickupVisited = false;"), behavior.index("beginFrame(theFrameInfo.time);"))
        self.assertGreater(behavior.index("if(!physicalPickupVisited) physicalPickupGate.suspend();"), behavior.index("endFrame();"))
        cancel = physical[physical.index("if(!pickupInput.enabled"):physical.index("if(physicalPickupGate.active())")]
        self.assertIn("!pickupInput.gcLegal", cancel)
        self.assertIn("automaticReleaseEpoch() != pickupInput.epoch", cancel)
        self.assertIn("cancelAutomaticRelease()", cancel)
        joystick = (OPTIONS / "HandleJoystick.cpp").read_text()
        transition = body(joystick[joystick.index("option((SkillBehaviorControl) HandleJoystick,"):], "common_transition")
        self.assertLess(transition.index("consumeAutomaticRelease()"), transition.index("automaticEntryHandoff"))
        self.assertLess(transition.index("BoosterPickupControl::held()"), transition.index("BoosterRemoteStand::held()"))


if __name__ == "__main__":
    unittest.main()
