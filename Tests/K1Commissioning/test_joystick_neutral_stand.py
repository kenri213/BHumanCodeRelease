"""SDK-free checks of the extracted source predicate; no compiler or robot.

The actual C++ boolean/Axis expressions are evaluated with equivalent Python
operators. This is source-expression regression, not ARM execution acceptance.
"""
import hashlib
from pathlib import Path
import re
from types import SimpleNamespace
import unittest

ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/'Src/Modules/BehaviorControl/SkillBehaviorControl/Options/HandleJoystick.cpp'
OLD_SHA='5da6239af72e1d7e0d55b09e105b59f713996e21265b278b6f4e2b6859e909dc'


def parenthesized(text,opening):
    depth=1;index=opening+1
    while depth:
        depth+=(text[index]=='(')-(text[index]==')');index+=1
    return text[opening+1:index-1]


def cpp_to_python(expression):
    value=expression.replace('||',' or ').replace('&&',' and ').replace('std::abs','abs')
    value=re.sub(r'!(?!=)','not ',value)
    return re.sub(r'\b(\d+)\.f\b',r'\1.0',value)


def actual_stop(speed=(0.,0.,0.),valid=True,stand=False):
    source=SOURCE.read_text();start=source.index('option((SkillBehaviorControl) HandleJoystickWalk')
    start=source.index('common_transition',start);opening=source.index('if(',start)+2
    expression=cpp_to_python(parenthesized(source,opening))
    joystick=SimpleNamespace(valid=valid,pressed=lambda button:stand if button==12 else False)
    return eval(expression,{'__builtins__':{},'Pose2f':lambda:(0.,0.,0.)},
                {'joystickState':joystick,'standButton':12,'walkSpeed':speed})


def actual_axis(value,dead_zone=.1):
    source=SOURCE.read_text();source=source[source.index('float value(const JoystickState&'):source.index('STREAMABLE(CombineAxes')]
    condition,when_true,when_false=re.search(r'return offset \+ \((.*?) \? (.*?) : (.*?)\);',source,re.S).groups()
    environment={'value':value,'deadZone':dead_zone,'scale':1.,'offset':0.}
    functions={'__builtins__':{},'abs':abs,'sgn':lambda v:1 if v>0 else -1 if v<0 else 0}
    chosen=when_true if eval(cpp_to_python(condition),functions,environment) else when_false
    return eval(cpp_to_python(chosen),functions,environment)


class JoystickNeutralStandTests(unittest.TestCase):
    def test_neutral_valid_joystick_selects_stand(self):self.assertTrue(actual_stop())

    def test_nonzero_forward_reverse_sideways_and_omni_rotation_not_forced_stand(self):
        for speed in ((0.,.2,0.),(0.,-.2,0.),(0.,0.,.2),(0.,0.,-.2),(.2,0.,0.),(-.2,0.,0.)):
            with self.subTest(speed=speed):self.assertFalse(actual_stop(speed))

    def test_invalid_or_original_stand_button_still_overrides(self):
        self.assertTrue(actual_stop((0.,.2,0.),valid=False))
        self.assertTrue(actual_stop((0.,.2,0.),stand=True))

    def test_exact_deadzone_boundaries_and_small_residuals(self):
        for value in (-.1,-.09,0.,.09,.1):
            axis=actual_axis(value);self.assertEqual(axis,0.)
            self.assertTrue(actual_stop((0.,axis,0.)))

    def test_just_outside_deadzone_is_not_silently_suppressed(self):
        for value in (-.1001,.1001,-1.,1.):
            axis=actual_axis(value);self.assertNotEqual(axis,0.)
            self.assertFalse(actual_stop((0.,axis,0.)))

    def test_only_one_production_predicate_changed(self):
        raw=SOURCE.read_bytes();addition=b' || walkSpeed == Pose2f()'
        # Strip only the independently tested physical Pickup additive overlay.
        pickup_overlay=b'''    if(BoosterPickupControl::consumeAutomaticRelease())
    {
      BoosterMatchSafety::cancelJoystickAfterGetUp();
      goto inactive;
    }
    if(BoosterPickupControl::held())
    {
      BoosterMatchSafety::cancelJoystickAfterGetUp();
      goto remoteLost;
    }
'''
        self.assertEqual(raw.count(pickup_overlay),1)
        raw=raw.replace(pickup_overlay,b'',1)
        fresh_manual=b'BoosterPickupControl::manualTakeoverAllowed() && '
        self.assertEqual(raw.count(fresh_manual),1)
        raw=raw.replace(fresh_manual,b'',1)
        # Normalize only the separately exercised remote-loss overlay. Retain
        # the original official SHA below; do not replace it with this build's.
        self.assertEqual(raw.count(b'#include <cstdio>\n'),1)
        raw=raw.replace(b'#include <cstdio>\n',b'',1)
        marker=b'    if(BoosterRemoteStand::held())\n    {'
        self.assertEqual(raw.count(marker),1)
        begin=raw.index(marker);opening=raw.index(b'{',begin);end=opening+1;depth=1
        while depth:
            depth+=(raw[end:end+1]==b'{')-(raw[end:end+1]==b'}');end+=1
        raw=raw[:begin]+raw[end+1:]
        loss_state=b'''  // Physical and STOP options run first. A lost remote cannot leave old manual
  // speeds or automatic READY/PLAY running, nor turn into an unstiff request.
  state(remoteLost)
  {
    action
    {
      LookForward();
      Stand();
    }
  }

'''
        self.assertEqual(raw.count(loss_state),1)
        raw=raw.replace(loss_state,b'',1)
        hardened_walk=b'''      if(joystickState.valid && !joystickState.pressed(standButton)
         && std::all_of(joystickState.axes.begin(), joystickState.axes.end(),
             [](float value) {return std::isfinite(value);})
         && walkSpeed != Pose2f())'''
        self.assertEqual(raw.count(hardened_walk),1)
        raw=raw.replace(hardened_walk,b'      if(walkSpeed != Pose2f())',1)
        self.assertEqual(raw.count(addition),1)
        # The separately tested opt-in match handoff is an additive overlay.
        # Strip ONLY its exact include and exact common-transition bytes; keep
        # the strict original file hash for all non-match joystick behavior.
        match_include=b'#include "Platform/BoosterMatchSafety.h"\n'
        live_include=b'#include "Platform/BoosterOfficialLiveTest.h"\n'
        time_include=b'#include "Platform/Time.h"\n'
        getup_overlay=b'''  // A completed fresh RB+A get-up offers manual control once, and only after
  // every button is released and all axes are neutral. Never move on held input.
  const bool getUpManualReady = BoosterOfficialLiveTest::bodyAuthorized()
      && joystickState.valid && joystickState.buttons == 0
      && std::all_of(joystickState.axes.begin(), joystickState.axes.end(),
          [](float value) {return std::isfinite(value) && std::abs(value) <= 0.1f;})
      && theFallDownState.state == FallDownState::upright
      && (theMotionInfo.executedPhase == MotionPhase::stand || theMotionInfo.executedPhase == MotionPhase::walk);
  // An explicit return to automatic always wins over a deferred get-up offer,
  // including while this option is inactive or get-up is still finishing.
  if(joystickState.valid && allPressed(deactivateButtons))
    BoosterMatchSafety::cancelJoystickAfterGetUp();

'''
        match_handoff=b'''  common_transition
  {
    if(automaticEntryHandoff)
    {
      BoosterMatchSafety::cancelJoystickAfterGetUp();
      goto inactive;
    }
    // Retained compatibility for an optional historical confirmation node;
    // the normal match sequence no longer calls that node or waits for RB+Y.
    if(BoosterMatchSafety::consumeJoystickRelease())
    {
      BoosterMatchSafety::cancelJoystickAfterGetUp();
      Say({.text = "Automatic control enabled"});
      goto inactive;
    }
  }

'''
        self.assertEqual(raw.count(match_include),1)
        self.assertEqual(raw.count(live_include),1)
        self.assertEqual(raw.count(time_include),1)
        self.assertEqual(raw.count(getup_overlay),1)
        self.assertEqual(raw.count(match_handoff),1)
        entry_start=raw.index(b'  // READY normally ends placement/manual control ONCE.')
        entry_end=raw.index(b'  common_transition',entry_start)
        entry_overlay=raw[entry_start:entry_end]
        self.assertIn(b'initialHandoff().update(',entry_overlay)
        self.assertIn(b'freshHandoffGc',entry_overlay)
        self.assertEqual(raw.count(entry_overlay),1)
        nonmatch=raw.replace(match_include,b'',1).replace(live_include,b'',1).replace(time_include,b'',1).replace(getup_overlay,b'',1).replace(entry_overlay,b'',1).replace(match_handoff,b'',1)
        new_activation=b'''         && ((allPressed(activateButtons) && !theMotionInfo.isMotion(MotionPhase::playDead))
             || BoosterMatchSafety::consumeJoystickAfterGetUp(getUpManualReady)))'''
        old_activation=b'''         && allPressed(activateButtons)
         && !theMotionInfo.isMotion(MotionPhase::playDead))'''
        self.assertEqual(nonmatch.count(new_activation),1)
        nonmatch=nonmatch.replace(new_activation,old_activation,1)
        activation_cancel=b'''        BoosterMatchSafety::cancelJoystickAfterGetUp();
        Say({.text = "Remote control activated"});'''
        self.assertEqual(nonmatch.count(activation_cancel),1)
        nonmatch=nonmatch.replace(activation_cancel,b'        Say({.text = "Remote control activated"});',1)
        self.assertEqual(hashlib.sha256(nonmatch.replace(addition,b'',1)).hexdigest(),OLD_SHA)
        self.assertIn(b'Say({.text = "Remote control activated"})',raw)
        self.assertIn(b'Say({.text = "Remote control deactivated"})',raw)

    def test_original_stand_action_and_both_walk_states_use_common_gate(self):
        source=SOURCE.read_text();start=source.index('option((SkillBehaviorControl) HandleJoystickWalk')
        block=source[start:source.index('option((SkillBehaviorControl) HandleJoystickHead',start)]
        self.assertLess(block.index('common_transition'),block.index('state(walkOmnidirectional)'))
        self.assertLess(block.index('common_transition'),block.index('state(walkStraight)'))
        self.assertIn('Stand();',block)

    def test_upright_and_new_getup_remain_separate_and_present(self):
        physical=(SOURCE.parent/'HandlePhysicalRobot.cpp').read_text()
        self.assertIn('theEnhancedKeyStates.pressedDuration[KeyStates::walk] > pressDuration',physical)
        self.assertIn('LiveGetUpGesture::update',physical);self.assertIn('LIVE_GETUP_ACCEPTED',physical)
        self.assertIn('theEnhancedKeyStates.hitStreak[KeyStates::walk] == 3',physical)
        self.assertNotIn('LiveGetUpGesture',SOURCE.read_text())


if __name__=='__main__':unittest.main()
