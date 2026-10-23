# 977 official-mode source release

Read `Docs/K1_977_OFFICIAL_F1_RESIDENT_20261024.md` and
`Deploy/K1Match977/README_MATCH.md` before changing this branch's K1 deployment code.

- This branch is based on public commit `35b4d3cee3e8f3d8e8774a01f73cefde44ee0fd4`.
  It preserves the deployed 977 reference's exact production source, not later
  996/987 Pickup, localization, body-gap, or strategy experiments.
- Follow the official B-Human F1 mode semantics: physical F1 enters Damping,
  the application remains resident, and a real long F1 can return to Unstiff.
  Do not invent a single-X global process exit. Explicit process stop remains distinct.
- The official-mode restoration is explicitly enabled only in the exact 977 entry.
  Preserve the established behavior of other targets unless the user requests a change.
- D455 support, identity/path checks, the RB mapping, and the requested RB+A shortcut
  remain documented adaptations. Do not describe the whole deployment as unmodified official code.
- Generic repository settings are not a robot's private runtime configuration.
  Preserve each robot's calibration, models, PD, runtime dependencies, and verified resource pins.
- Source publication, builds, and CPU tests do not authorize robot startup or motion.
  Real F1 residency was tested; getup, standing, walking, kicking, and match stability were not.
- Never commit robot credentials, local keys, private configuration backups, runtime logs,
  SDK libraries, compiled ELF files, or machine-generated TensorRT caches.
  Existing upstream installation fixtures are inherited history, not team credentials.
- Test baselines must use pinned public Git objects, not a developer's home directory.
  Missing baseline objects must fail rather than silently skipping byte-comparison tests.
- A new build can have a different ELF hash even with identical source. Rebind a complete,
  independently verified machine-specific release; never remove hash/permission checks to deploy it.

Minimum offline checks:

```sh
python3 -m unittest discover -s Tests/K1Commissioning -p 'test_*.py'
python3 -m unittest discover -s Tests/K1RealSense -p 'test_*.py'
(cd Deploy/K1Match977 && python3 -m unittest discover -p 'test_*.py')
```
