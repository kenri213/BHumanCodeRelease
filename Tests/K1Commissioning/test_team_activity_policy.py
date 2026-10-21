"""Offline real-policy/candidate regression. No SDK, services, or robot motion."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BEHAVIOR = ROOT/'Src/Modules/BehaviorControl/StrategyBehaviorControl/Behavior.cpp'
ROSTER = ROOT/'Src/Modules/BehaviorControl/StrategyBehaviorControl/StrategyBehaviorControl.cpp'

PREFIX = r'''
#include "Tools/BehaviorControl/TeamActivityPolicy.h"
#include <array>
#include <cstdlib>
#include <string>
namespace Tactic { namespace Position {
enum Type { goalkeeper, defender, forward };
bool isGoalkeeper(Type p) { return p==goalkeeper; }
}}
namespace OwnFreeKick { constexpr int ownKickInOwnHalf=10; int toSetPlay(int n){return n;} }
struct Frame {unsigned time=10000;};
struct Params {bool requireFreshTeamActivity=true;unsigned teamActivityTimeout=10000;unsigned ballSeenThreshold=1000;unsigned ballLostThreshold=4000;};
struct Agent {int number=3;unsigned timestamp=0,timeWhenBallLastSeen=0;bool disagreeOnBall=false,isUpright=true;Tactic::Position::Type position=Tactic::Position::defender;int acceptedSetPlay=0;};
struct Game {bool opponent=false;bool isFreeKick()const{return false;}bool isGoalKick()const{return false;}bool isForOpponentTeam()const{return opponent;} };
static bool eligible(Agent candidate, bool enabled=true, unsigned now=10000, bool wasActive=false, bool ballNeeded=true, bool opponent=false) {
  Frame theFrameInfo;theFrameInfo.time=now;
  Params theBehaviorParameters;theBehaviorParameters.requireFreshTeamActivity=enabled;
  Agent self;self.number=2;const bool wasIntendedStrikerDefined=false;
  const std::array<bool,3> intendedStrikerMap{};
  const std::array<const Agent*,1> otherAgents{&candidate};
  struct {bool isValid=false;} theTeamBallModel;
  Game theGameState;theGameState.opponent=opponent;
'''
SUFFIX = r'''
  return canBeActive(candidate,wasActive,ballNeeded);
}
static void check(bool v){if(!v)std::abort();}
int main(int argc,char**argv){
  check(argc==2);const std::string name=argv[1];Agent a;
  if(name=="never_heard") {check(eligible(a,false));check(!eligible(a,true));}
  else if(name=="stale_peer") {a.timestamp=a.timeWhenBallLastSeen=1000;check(eligible(a,false,20000));check(!eligible(a,true,20000));}
  else if(name=="stale_ball") {a.timestamp=9500;a.timeWhenBallLastSeen=5999;check(eligible(a,false));check(!eligible(a,true));}
  else if(name=="fresh_peer") {a.timestamp=a.timeWhenBallLastSeen=9500;check(eligible(a,true));}
  else if(name=="self") {a.number=2;a.timestamp=a.timeWhenBallLastSeen=10000;check(eligible(a,true));a.timeWhenBallLastSeen=5000;check(!eligible(a,true));}
  else if(name=="upright") {a.timestamp=a.timeWhenBallLastSeen=9500;a.isUpright=false;check(!eligible(a));}
  else if(name=="disagreement") {a.timestamp=a.timeWhenBallLastSeen=9500;a.disagreeOnBall=true;check(!eligible(a));}
  else if(name=="goalkeeper") {a.timestamp=a.timeWhenBallLastSeen=9500;a.position=Tactic::Position::goalkeeper;check(!eligible(a,true,10000,false,true,true));}
  else if(name=="future") {a.timestamp=a.timeWhenBallLastSeen=10001;check(!eligible(a,true));}
  else if(name=="roster") {
    using namespace TeamActivityPolicy;
    check(retainAgent(true,false,false,10000,0,10000)); // READY/SET keep formation placeholders.
    check(retainAgent(false,true,false,10000,0,10000)); // Legacy mode untouched.
    check(retainAgent(true,true,true,10000,0,10000)); // Never prune self.
    check(!retainAgent(true,true,false,10000,0,10000));
    check(retainAgent(true,true,false,15000,5000,10000));
    check(!retainAgent(true,true,false,15001,5000,10000));
    check(retainAgent(true,true,false,15001,15000,10000)); // Real new packet re-admits peer.
  }
  else if(name=="clock") {using namespace TeamActivityPolicy;check(!fresh(0,0,4000));check(!fresh(10000,10001,4000));check(fresh(25,0xfffffff6u,40));check(!fresh(25,0xfffffff6u,34));}
  else if(name=="bounds") {using namespace TeamActivityPolicy;check(boundedTimeout(0)==5000);check(boundedTimeout(10000)==10000);check(boundedTimeout(999999)==60000);}
  else return 3;
}
'''

class TeamActivityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='bh996-peer-')
        source=BEHAVIOR.read_text()
        start=source.index('  const auto canBeActive =')
        end=source.index('  // closestToTeamBall',start)
        cpp=Path(cls.temp.name)/'test.cpp'
        cpp.write_text(PREFIX+source[start:end]+SUFFIX)
        cls.binary=Path(cls.temp.name)/'test'
        result=subprocess.run(['clang++','-std=c++20','-Wall','-Wextra','-Werror','-pedantic','-I',str(ROOT/'Src'),str(cpp),'-o',str(cls.binary)],capture_output=True,text=True)
        if result.returncode:raise RuntimeError(result.stderr)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def run_case(self,name):self.assertEqual(subprocess.run([str(self.binary),name],timeout=10).returncode,0,name)
    def test_never_heard_SET_ball_does_not_win(self):self.run_case('never_heard')
    def test_frozen_message_is_not_permanently_fresh(self):self.run_case('stale_peer')
    def test_ball_age_is_now_not_at_send_time(self):self.run_case('stale_ball')
    def test_real_fresh_teammate_can_still_win(self):self.run_case('fresh_peer')
    def test_own_local_ball_is_still_required(self):self.run_case('self')
    def test_fallen_peer_protection_unchanged(self):self.run_case('upright')
    def test_disagreeing_ball_protection_unchanged(self):self.run_case('disagreement')
    def test_opponent_goalkeeper_permission_unchanged(self):self.run_case('goalkeeper')
    def test_future_timestamps_rejected(self):self.run_case('future')
    def test_roster_grace_readiness_and_rejoin(self):self.run_case('roster')
    def test_timestamp_boundaries(self):self.run_case('clock')
    def test_config_timeout_bounds(self):self.run_case('bounds')

    def test_one_roster_is_used_before_self_pointer_and_assignment(self):
        text=ROSTER.read_text()
        self.assertLess(text.index('TeamActivityPolicy::retainAgent'),text.index('Agent* self = nullptr'))
        self.assertIn('theGameState.isPlaying()',text)
        self.assertIn('agent.timestamp, theBehaviorParameters.teamActivityTimeout',text)
        self.assertIn('MATCH_STRATEGY pid=',text)

    def test_optional_profile_not_global_speed_or_model_change(self):
        self.assertIn('requireFreshTeamActivity = false;', (ROOT/'Config/Scenarios/Default/behaviorParameters.cfg').read_text())
        text=(ROOT/'Config/Scenarios/K1RealSense/behaviorParameters.cfg').read_text()
        self.assertIn('requireFreshTeamActivity = true;',text)
        self.assertIn('ballLostThreshold = 4000;',text)
        self.assertIn('ballSearchThresholdNoTeam = 2000;',text)
        self.assertIn('ballSearchThresholdTeam = 3000;',text)
        self.assertNotIn('speed =',text)

if __name__=='__main__':unittest.main()
