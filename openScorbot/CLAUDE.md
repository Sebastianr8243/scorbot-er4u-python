# openScorbot/ (legacy protocol backend)

Original OpenScorbot code (University of La Laguna, GPL-3.0) that builds and sends every USB packet. Treat it as a frozen legacy backend. The SDK in `scorbot/` wraps it; the PyQt GUI (`gui.py`, `main.py`, `open_SCB.ui`) is reference code only. Root rules in [../CLAUDE.md](../CLAUDE.md) apply.

## Hard rules

- Do not change packet construction, sequence-byte handling, message tables (`libhex.py`), or the `WRITE`/`READ` sleeps without evidence: captured traces (`docs/USB_CAPTURE.md`, `scripts/usb_trace.py`), or the vendor disassembly (`docs/VENDOR_DLL_PROTOCOL.md`) for sequences built only from command bytes already in `libhex.py`. A disassembly-based change is tried first on a 1 degree jog; bytes the legacy code never sends still need a capture (root `CLAUDE.md`). The sleeps are required for the controller to answer (comment in `libdef.py:set_msg`). Timing and byte layout are unverified against the Intelitek software.
- Never run, import for side effects, or test anything here against the real device. Tests import these modules only for pure functions (`tests/test_properties.py`, `tests/test_python_api.py`, `tests/test_kinematics.py`).
- Changes to `libcomm.py`, `libdef.py`, `motion_profile.py` alter `scorbot.provenance.motion_source_sha256`, which every lab log records. That is intended; do not bypass it.
- Bug fixes need a test that shows the bug first. Known bugs are pinned as `expectedFailure` in `tests/test_properties.py` (`test_known_bug_*`); when you fix one, the test turns into an unexpected success and you must flip it.

## How it is loaded

- Modules use bare imports (`import libdef`, `import conf`). `scorbot.Scorbot._legacy(name)` prepends this directory to `sys.path` and imports them. Never convert them to package-relative imports without moving every caller (`gui.py`, `main.py` run from inside this directory rely on the bare form). `fit_calibration.py` imports `openScorbot.motion_profile` as a package; that file has no bare imports and must keep it that way.
- `libdef.py` imports `usb.core` at top level, so importing it needs PyUSB. It also does `from math import *` and `from numpy import *`, which shadow builtins (`round`, `abs`, `sum`...). Use `builtins.round` as the file does; do not add new code that calls bare `round`.
- Indentation differs per file. Tabs: `gui.py`, `libcomm.py`, `libdef.py`, `libhex.py`, `libsync.py`. Spaces: `conf.py`, `motion_profile.py`, `moveXYZ.py`, `setHome.py`. Match the file. Many comments are Spanish.

## Config: conf.py and data.json

- `conf.readData(group, key)` re-reads and parses `data.json` on every call (`libdef`, `libcomm`, `setHome` call it inside loops). Module-level constants (`WRITE`, `READ`, `MAX_ERROR`, `VEC_POS`...) are read once at import.
- `conf.setup()` writes `data.json` only if it does not exist and never overwrites it. Editing defaults in `conf.py` has no effect on a machine that already has `data.json`; delete the file (git-ignored, `openScorbot/data.json`) to regenerate. `readData` calls `setup()` if the file is missing, so merely importing `libsync`/`libcomm` creates it.
- `conf.py` values (link lengths, `posRef`, `angRef`, DH terms, per-joint delays, `MAX_ERROR = 40`, homing speeds) are inherited, not measured.

## Arithmetic gotchas (each verified in code)

- Encoders are unsigned 16-bit with modulus 65535 (one's complement), not 65536. `libdef.suma` subtracts 65535 on overflow and sets the sign to `'0000'`; `resta` adds 65535 on underflow and sets `'ffff'`. A step >= 65536 double-overflows (pinned `expectedFailure` in `test_properties.py`).
- The SDK counterpart is `scorbot.calibration.signed_count_delta`. Do not subtract raw counts from `buffer` or `media` in new code.
- Settle checks are not wrap-aware: `abs(dato_in[0] - media[i]) > 20` in `libcomm.move_*` and `setHome.homing` compares a wrapped target with a raw mean. A joint near the 0/65535 seam can be judged unsettled and spin to the 100-iteration cap, returning error code 2.
- `libdef.get_media` averages the last reading into `media` and replaces it when the jump is >= 1000 counts. Values must stay `int` for hex formatting (`detrans`).
- `libdef.get_switch` handles byte 5 with parity for the base and a subtract chain for 16/8/4/2. Any set bit >= 32 makes every non-base switch read wrong (search misses its switch, other joints count as already home). `Scorbot.home` refuses such bytes before sending; the decoder itself is unchanged.
- `libdef.cIn` (inverse kinematics) drops the 16 mm shoulder offset: `m1*m2*[[0],[0],[0],[1]]` is an element-wise NumPy product, so the offset terms it reads are zero. It also returns the wrist-pitch-axis point, not the tool tip. Documented in `tests/test_kinematics.py:LegacyInverseKinematicsFindings`. Do not "fix" it without measured poses; `scorbot/kinematics.py` is the unvalidated comparison model.
- Homing (`setHome.homing`: shoulder, elbow, pitch, roll, base) overshoots: after the switch trips it sends 12 further packets and stops, unlike the manual's back-off until the switch clears (`docs/HARDWARE_REFERENCE.md`, "Homing"). Assumes a fixed start pose. Search deadline `HOME_SEARCH_TIMEOUT_S = 30.0` per axis is provisional; `cancel_event` is checked each loop.
- Jog direction is encoded twice: `libdef.builder` / `libcomm.move_wrist` (which orders add or subtract) and `motion_profile.MOTOR_DIRECTIONS`. They currently agree; change both or neither. `motion_profile.COUNTS_PER_DEGREE` is a legacy assumption, not calibration.
- `libcomm.execute` dispatches by order code: 4-13 jogs, 14-15 clamp, 16 off, 17 on, 18 home, 19 `moveXYZ.controlXYZ`, 528 exit. Order 19 replies error 5 unless homed inside this loop. The result queue is separate from the command queue for SDK use (`cola_result`); the GUI used one queue for both.
- Errors reported by the legacy loops: `1` joint limit (error bytes >= `MAX_ERROR`), `2` joint did not respond (100 loops), `5` not homed. The SDK treats any nonzero result as a fault.

## Testing changes

Pure functions: `python -m unittest tests.test_properties tests.test_motion_profile tests.test_python_api tests.test_kinematics`. Anything that would send bytes must be exercised through `SimulatedController` or fake endpoints, never a device. `ruff check --select F openScorbot` reports legacy issues (star imports, unused imports); leave them unless you are touching those lines.

## Do not

- Do not refactor for style, translate comments, or reformat whole files; diffs must stay reviewable against upstream and against the fingerprint.
- Do not add a new command code to `execute` for the SDK without evidence (a captured trace, or the vendor disassembly under the rule above) and a gate in `Scorbot`.
- Do not touch `gui.py` to change SDK behavior; it needs PyQt5 (`gui` extra) and opens USB on start.
