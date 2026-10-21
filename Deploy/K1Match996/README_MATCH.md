# K1 match recovery reference release (996)

This is the reviewed 996-specific launcher accompanying the K1/D455 source.
It is not a generic installer and must not be run unchanged on another robot.
Identity guards, helper/APP hashes, paths, service names, input-router originals
and camera calibration must be bound and checked separately for each target.

The reference APP SHA-256 is
`37a2c4ba62a4e182aac542fe3aa8144793265ef1a6e8d6d9c4aa971388816c6e`.
Only GameController source **192.168.50.37** is accepted by this source release.
The reference is 115/team, player 2, SField; these settings are not credentials
or a permission to override a target's configuration.

## Changes included

- D455 RGB adapter with real intrinsics and timestamp/freshness checks.
- Fresh physical F1 authorization and X/F1 emergency stop.
- Native READY/SET/PLAY entry; no extra RB+Y localization confirmation.
- Normal controller REMOVE requests a latched Stand hold instead of terminating
  and removing joint support. X, invalid input and lower-level faults retain
  their emergency paths. This does not repair USB hardware faults.
- Reconnection cannot replay old stick commands. Observe a new neutral input,
  then press and release LT+plus for manual or LT+minus for automatic control.
- Manual RB+A recovery is allowed in fresh, unpenalized, non-STOP INITIAL,
  READY or PLAYING. SET, FINISHED and unknown states remain blocked. Non-INITIAL
  admission requires a genuinely stable lying pose; it does not grant football
  walking/kicking rights.
- Real Prepare interpolation, stable-pose checks and the original bounded
  recovery deadline remain. A lost one-frame admission can be refreshed only
  within the same accepted and bounded intent.
- In-match manual recovery first holds Stand. After actual Stand feedback for
  300ms, discard earlier neutral/LT evidence and require a new handover.
- Team selection ignores stale/noncommunicating PLAY candidates; shared ball
  evidence is aged at the current time.

## Reference operating sequence

After a reviewed per-robot installation, run its `scripts/start_match.sh` and
wait for `ready_for_operator=true`. Use a fresh F1 authorization and deliberate
RB+A hold. After an in-match recovery, wait for `MATCH_RECOVERY_STANDING`, check
the real pose/direction, then neutralize and press/release LT-minus to return
to automatic football. Never infer correct half/heading from symmetric lines.

There is no fixed ten-minute match limit or automatic APP restart. X/F1/stop
can remove support and cause a standing robot to fall; they are not sit-down
commands. Firmware upgrade, installation and physical tests are separate steps.

## Validation and limitations

The reviewed reference passed 262 source and 47 launcher offline tests and an
isolated ARM64 build. No physical recovery/USB-loss or complete match acceptance
is claimed. Rebuilt ELF hashes can differ with build/debug paths: finalize all
actual artifact pins and run `Tools/K1Commissioning/check_match_release.py`
before deployment. Do not publish robot backups, private keys, credentials,
binary payloads or another machine's TensorRT cache.
