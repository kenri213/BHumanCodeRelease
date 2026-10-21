"""Replay accepted-Prepare -> GC gap -> fresh recovery using production Gate."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
HARNESS=r'''
#include "Tools/BehaviorControl/CombinedLiveGetUpGesture.h"
#include <cstdlib>
#include <string>
namespace G=CombinedLiveGetUpGesture;
static void check(bool x){if(!x)std::abort();}
static G::Input in(unsigned t,bool prepare=true,bool pressed=false,unsigned age=10){
  G::Input v;v.enabled=v.authorized=v.joystickValid=v.sticksNeutral=v.rawInitial=v.unpenalized=v.imuFinite=v.canPrepare=v.lyingStable=true;
  v.emergency=v.stopped=false;v.frameTime=t;v.gcNow=100000+t;v.gcPacketTime=v.gcNow-age;v.epoch=1;
  v.executedPrepare=prepare;v.neutralButtons=!pressed;v.rbAOnly=pressed;return v;
}
static void accepted(G::Gate& g){g.update(in(100,false));g.update(in(130,false));g.update(in(160,false,true));auto v=in(2160,false,true,1651);check(g.update(v).requestPrepare);check(g.pending());}
static void noRequest(const G::Result& r){check(!r.requestPrepare&&!r.requestGetUp&&!r.requestActive);}
int main(int argc,char**argv){check(argc==2);std::string name=argv[1];
  if(name=="actual_log"){
    G::Gate g;accepted(g);
    auto r=g.update(in(2200,true,true,2009));noRequest(r);check(!r.cancelled&&g.pending());
    check(g.reason()==G::Reason::awaitingFreshGc&&g.pendingMs(2200)==40);
    for(unsigned t:{2500u,3000u,3500u,4000u}){r=g.update(in(t,true,false,3000));noRequest(r);check(!r.cancelled&&g.pending());}
    r=g.update(in(4200,true,false));check(r.requestGetUp&&!r.requestActive&&!g.pending());
    check(!g.update(in(4300,true,true)).requestPrepare);
  }else if(name=="never_accepted"){
    G::Gate g;g.update(in(100,false));g.update(in(130,false));
    for(unsigned t:{160u,2160u,4200u}){auto r=g.update(in(t,false,true,2001));noRequest(r);check(!g.pending());}
    noRequest(g.update(in(4300,false,true))); // Old held chord does not become a new authorization.
  }else if(name=="expiry"){
    G::Gate g;accepted(g);noRequest(g.update(in(2200,true,false,2001)));
    for(unsigned t=2500;t<10160;t+=500){auto r=g.update(in(t,true,false,3000));noRequest(r);check(g.pending());}
    auto r=g.update(in(10160,true,false,3000));noRequest(r);check(g.pending()); // Exactly 8000ms.
    r=g.update(in(10161,true,false,3000));noRequest(r);check(r.cancelled&&!g.pending());check(g.reason()==G::Reason::pendingTimeout);
    noRequest(g.update(in(10200,true,true)));check(!g.pending());
  }else if(name=="hard_changes"){
    for(unsigned bad=0;bad<16;bad++){
      G::Gate g;accepted(g);g.update(in(2200,true,false,3000));check(g.pending());auto v=in(2500,true,false,3000);
      switch(bad){case 0:v.authorized=false;break;case 1:v.epoch=2;break;case 2:v.stopped=true;break;
      case 3:v.rawInitial=false;break;case 4:v.unpenalized=false;break;case 5:v.imuFinite=false;break;
      case 6:v.joystickValid=false;break;case 7:v.sticksNeutral=false;break;case 8:v.emergency=true;break;
      case 9:v.canPrepare=false;break;case 10:v.neutralButtons=v.rbAOnly=v.rbOnly=v.aOnly=false;break;
      case 11:v.gcPacketTime=0;break;case 12:v.gcPacketTime=v.gcNow+1;break;case 13:v.frameTime=2000;break;
      case 14:v.frameTime=2200;break;case 15:v.enabled=false;break;}
      auto r=g.update(v);noRequest(r);check(r.cancelled&&!g.pending());
    }
  }else if(name=="stability"){
    G::Gate g;accepted(g);g.update(in(2200));auto v=in(4100,true,false,3000);v.lyingStable=false;g.update(v);
    noRequest(g.update(in(4200)));noRequest(g.update(in(4499)));check(g.update(in(4500)).requestGetUp);
  }else if(name=="phase_interruption"){
    G::Gate g;accepted(g);g.update(in(2200));g.update(in(3500,false,false,3000));g.update(in(4000,true,false,3000));
    noRequest(g.update(in(4200)));noRequest(g.update(in(5999)));check(g.update(in(6000)).requestGetUp);
  }else if(name=="upright_change"){
    G::Gate g;accepted(g);g.update(in(2200));auto v=in(4100,true,false,3000);v.lyingStable=false;v.uprightStable=true;g.update(v);
    v=in(4200);v.lyingStable=false;v.uprightStable=true;noRequest(g.update(v));
    v=in(4400);v.lyingStable=false;v.uprightStable=true;const auto r=g.update(v);check(r.requestActive&&!r.requestGetUp);
  }else if(name=="freshness_boundary"){
    G::Gate g;accepted(g);noRequest(g.update(in(2200,true,false,2000)));noRequest(g.update(in(4200,true,false,2001)));check(g.pending());
    check(g.update(in(4233,true,false,2000)).requestGetUp);
    check(!G::freshInitial(true,false,true,10000,0));check(!G::freshInitial(true,false,true,10000,10001));
  }else if(name=="no_actual_prepare"){
    G::Gate g;accepted(g);noRequest(g.update(in(2200,false,false,3000)));noRequest(g.update(in(4200,false)));
    auto r=g.update(in(10161,false));check(r.cancelled&&!g.pending());noRequest(r);
  }else if(name=="normal"){
    G::Gate g;accepted(g);noRequest(g.update(in(2200)));noRequest(g.update(in(4199)));check(g.update(in(4200)).requestGetUp);
    for(unsigned t:{4233u,4500u,9000u})noRequest(g.update(in(t,true,true)));
  }else return 2;
}
'''
class GetUpGcPauseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='bh996-gc-pause-');cpp=Path(cls.temp.name)/'test.cpp';cpp.write_text(HARNESS)
        cls.bin=Path(cls.temp.name)/'test'
        r=subprocess.run(['clang++','-std=c++20','-Wall','-Wextra','-Werror','-pedantic','-I',str(ROOT/'Src'),str(cpp),'-o',str(cls.bin)],capture_output=True,text=True)
        if r.returncode:raise RuntimeError(r.stderr)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def run_case(self,name):self.assertEqual(subprocess.run([str(self.bin),name],timeout=10).returncode,0,name)
    def test_actual_accepted_prepare_stale2009_then_fresh_no_repress(self):self.run_case('actual_log')
    def test_stale_input_cannot_accept_a_new_chord(self):self.run_case('never_accepted')
    def test_original_eight_second_limit_does_not_restart(self):self.run_case('expiry')
    def test_all_hard_cancel_conditions_still_cancel_during_gap(self):self.run_case('hard_changes')
    def test_unstable_during_gap_restarts_300ms_evidence(self):self.run_case('stability')
    def test_actual_prepare_interruption_restarts_2000ms(self):self.run_case('phase_interruption')
    def test_lying_to_upright_is_not_old_stability_evidence(self):self.run_case('upright_change')
    def test_2000_and_2001ms_boundary_unchanged(self):self.run_case('freshness_boundary')
    def test_missing_prepare_admission_safely_expires_no_reissue(self):self.run_case('no_actual_prepare')
    def test_normal_one_shot_path_unchanged(self):self.run_case('normal')
if __name__=='__main__':unittest.main()
