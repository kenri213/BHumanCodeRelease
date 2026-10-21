"""Pure native CPU tests for the Live-only shortcut; no SDK/robot/ARM build."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
HEADER=ROOT/'Src/Tools/BehaviorControl/LiveGetUpGesture.h'
OPTION=ROOT/'Src/Modules/BehaviorControl/SkillBehaviorControl/Options/HandlePhysicalRobot.cpp'

HARNESS=r'''
#define state(name) static_assert(false, "CABSL macro collision")
#include "Tools/BehaviorControl/LiveGetUpGesture.h"
#undef state
#include <cstdlib>
#include <limits>
#include <string>
namespace G=LiveGetUpGesture;
static void check(bool value) {if(!value) std::abort();}
struct Trace {
  bool released=false,holding=false; unsigned start=0,last=0;
  bool tick(G::Input input) {return G::update(input,released,holding,start,last);}
};
static G::Input good(unsigned frame=100) {
  G::Input in;in.live=in.authorized=in.prepare=in.joystickValid=in.rb=true;
  in.gcActive=in.initial=in.packetInitial=true;in.penalized=false;
  in.frameTime=frame;in.gcNow=frame+50;in.gcPacketTime=frame+40;in.lyingStable=true;
  return in;
}
static bool tick(Trace& trace,unsigned frame,bool a) {
  auto in=good(frame);in.a=in.walk=a;return trace.tick(in);
}
int main(int argc,char** argv) {
  check(argc==2);const std::string name=argv[1];
  if(name=="fresh") {
    Trace t;check(!tick(t,100,true));check(!tick(t,9000,true)); // old held A cannot start.
    check(!tick(t,9010,false));check(!tick(t,9020,true));
    check(!tick(t,11019,true));check(!tick(t,11020,true));check(tick(t,11021,true));
  } else if(name=="neutral") {
    Trace t;auto in=good(100);in.rb=false;check(!t.tick(in));
    check(t.released && !t.holding); // All buttons released in this Prepare.
    check(!tick(t,200,true));check(!tick(t,2200,true));check(tick(t,2201,true));
    check(!t.released && !t.holding);check(!tick(t,5000,true)); // One-shot.
    in=good(5010);in.rb=false;check(!t.tick(in));check(t.released);
    in=good(5020);in.a=in.walk=true;in.rb=false;check(!t.tick(in));
    check(!t.released && !t.holding); // A first, then adding RB, cannot reuse neutral.
    check(!tick(t,5030,true));check(!tick(t,9000,true));
    in=good(9010);in.rb=false;check(!t.tick(in));
    check(!tick(t,9020,true));check(tick(t,11021,true));
  } else if(name=="authorization") {
    for(unsigned gate=0;gate<4;++gate) {
      Trace t;check(!tick(t,100,false));check(!tick(t,110,true));
      auto in=good(3000);in.a=in.walk=true;
      if(gate==0)in.authorized=false;if(gate==1)in.live=false;
      if(gate==2)in.emergency=true;if(gate==3)in.prepare=false;
      check(!t.tick(in));check(!t.released && !t.holding);
      check(!tick(t,6000,true)); // re-entry requires new neutral in this Prepare.
    }
  } else if(name=="gc") {
    for(unsigned gate=0;gate<8;++gate) {
      Trace t;check(!tick(t,100,false));check(!tick(t,110,true));
      auto in=good(3000);in.a=in.walk=true;
      if(gate==0)in.gcActive=false;if(gate==1)in.initial=false;
      if(gate==2)in.packetInitial=false;if(gate==3)in.stopped=true;
      if(gate==4)in.penalized=true;if(gate==5)in.gcPacketTime=in.gcNow-2001;
      if(gate==6)in.gcPacketTime=0;if(gate==7)in.gcPacketTime=in.gcNow+1;
      check(!t.tick(in));check(!t.released && !t.holding);
    }
    Trace t;check(!tick(t,100,false));check(!tick(t,110,true));
    auto in=good(2111);in.a=in.walk=true;in.gcPacketTime=in.gcNow-2000;
    check(t.tick(in)); // exact original two-second freshness boundary accepted.
  } else if(name=="buttons") {
    for(unsigned gate=0;gate<5;++gate) {
      Trace t;check(!tick(t,100,false));check(!tick(t,110,true));
      auto in=good(3000);in.a=in.walk=true;
      if(gate==0)in.joystickValid=false;if(gate==1)in.rb=false;
      if(gate==2)in.b=true;if(gate==3)in.stand=true;if(gate==4)in.a=false;
      check(!t.tick(in));
    }
    Trace t;check(!tick(t,100,false));auto in=good(110);in.a=true;in.walk=false;
    check(!t.tick(in));check(t.released && !t.holding); // one-frame Enhanced lag.
    check(!tick(t,120,true));check(tick(t,2121,true));
  } else if(name=="geometry") {
    constexpr float degree=3.14159265358979323846f/180.f;
    const float angle=60.f*degree,gyro=5.f*degree;
    check(!G::stableLying(angle,0,0,0,0,angle,gyro));
    check(G::stableLying(61.f*degree,0,0,0,0,angle,gyro));
    check(!G::stableLying(0,61.f*degree,gyro,0,0,angle,gyro));
    check(!G::stableLying(0,61.f*degree,0,gyro,0,angle,gyro));
    check(!G::stableLying(0,61.f*degree,0,0,gyro,angle,gyro));
    check(G::stableLying(0,61.f*degree,gyro*.99f,0,0,angle,gyro));
    check(!G::stableLying(std::numeric_limits<float>::quiet_NaN(),1.5f,0,0,0,angle,gyro));
    Trace t;check(!tick(t,100,false));check(!tick(t,110,true));
    auto in=good(3000);in.a=in.walk=true;in.lyingStable=false;check(!t.tick(in));
  } else if(name=="clocks") {
    Trace t;check(!tick(t,100,false));check(!tick(t,110,true));
    auto in=good(2111);in.a=in.walk=true; // Packet later than RGB, not later than current GC clock.
    check(in.gcPacketTime>in.frameTime && in.gcPacketTime<in.gcNow);check(t.tick(in));
    in.frameTime=90;check(!t.tick(in));check(!t.released && !t.holding);
  } else return 2;
}
'''


class LiveGetUpGestureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='live-getup-pure-')
        cls.addClassCleanup(cls.temp.cleanup)
        source=Path(cls.temp.name)/'gesture.cpp';cls.binary=Path(cls.temp.name)/'gesture'
        source.write_text(HARNESS)
        subprocess.run(['/usr/bin/clang++','-std=c++20','-Wall','-Wextra','-Werror','-DNDEBUG',
                        '-I'+str(ROOT/'Src'),str(source),'-o',str(cls.binary)],check=True,capture_output=True,text=True)

    def run_case(self,name):subprocess.run([str(self.binary),name],check=True,capture_output=True,text=True)
    def test_fresh_a_and_strict_two_second_hold(self):self.run_case('fresh')
    def test_all_released_then_simultaneous_rb_a_and_no_a_first_reuse(self):self.run_case('neutral')
    def test_nonlive_unauthorized_emergency_and_new_prepare(self):self.run_case('authorization')
    def test_real_gc_initial_freshness_stop_and_penalty(self):self.run_case('gc')
    def test_raw_rb_a_no_b_and_valid_joystick(self):self.run_case('buttons')
    def test_original_pose_gyro_boundaries_and_nonfinite(self):self.run_case('geometry')
    def test_separate_gc_clock_and_frame_rollback(self):self.run_case('clocks')

    def test_original_paths_and_thresholds_retained(self):
        source=OPTION.read_text()
        self.assertIn('(unsigned int)(2000) pressDuration',source)
        for text in ('(Angle)(15_deg) maxTorsoForWalk','(Angle)(15_deg) maxGyroForWalk',
                     '(Angle)(5_deg) maxGyroForFall','(Angle)(60_deg) minFallAngle',
                     'theEnhancedKeyStates.hitStreak[KeyStates::walk] == 3',
                     'theEnhancedKeyStates.pressedDuration[KeyStates::stand] > pressDuration',
                     'theEnhancedKeyStates.pressedDuration[KeyStates::walk] > pressDuration',
                     'std::abs(theInertialData.gyro.x()) < maxGyroForFall',
                     'std::abs(theInertialData.gyro.y()) < maxGyroForFall',
                     'std::abs(theInertialData.gyro.z()) < maxGyroForFall'):
            self.assertIn(text,source)
        self.assertIn('BoosterOfficialLiveTest::bodyAuthorized()',source)
        self.assertIn('LIVE_GETUP_ACCEPTED',source)

    def test_gc_dependency_and_wall_clock_are_explicit(self):
        source=OPTION.read_text();module=(ROOT/'Src/Modules/BehaviorControl/SkillBehaviorControl/SkillBehaviorControl.h').read_text()
        self.assertIn('.gcNow = Time::getCurrentSystemTime()',source)
        self.assertIn('theGameControllerData.timeLastPacketReceived',source)
        self.assertEqual(module.count('REQUIRES(GameControllerData)'),1)
        self.assertIn('Representations/Communication/GameControllerData.h',module)


if __name__=='__main__':unittest.main()
