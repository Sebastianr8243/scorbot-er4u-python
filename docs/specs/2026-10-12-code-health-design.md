# Code health: design (2026-10-12)

Status: approved by the owner on 2026-10-12 (approach C, decisions at the end). Nothing here changes how the arm is driven.

## Goal

Make the codebase easier to change safely, without altering behaviour: close the test gaps around the safety core, split the 1741-line `scorbot/robot.py` into focused modules, and fix the few backlog bugs that need no hardware evidence. Behaviour on the wire and every safety invariant in the root `CLAUDE.md` stay exactly as they are.

## Starting point (measured 2026-10-12)

- 1160 tests pass (`tests` 869, `tests_extra` 291 with 1 skip), ruff is clean, coverage 93% over `scorbot`, `openScorbot` and `scripts` (6957 statements).
- Weakest files: `scorbot/preflight.py` 66%, `scorbot/transport/device_file.py` 71% (real Win32 calls, tested through a fake `Win32Api`), `scorbot/lab/__main__.py` 80%, `scorbot/robot.py` 84% (176 of 1117 statements missed), `scorbot/lab/terminal.py` 85%, `scorbot/calibration.py` 86%, `scorbot/camera/source.py` 86%.
- `robot.py` holds one class, `Scorbot` (1649 lines, 53 methods): the only class that sends commands, owner of the fault latch, the stop events and two worker threads. Largest methods: `home_inch` 149 lines, `vendor_home` 104, `park_at_home` 102, `move_gripper` 102, `pre_home_jog` 97, `_jog_joint` 93, `connect` 86, `_end_stream` 82, `start_stream` 76.

## Not in scope

- Anything in `openScorbot/` that builds packets, sets sequence bytes or sets the `WRITE`/`READ` sleeps (root `CLAUDE.md`, row 1). `from numpy import *` in `libdef.py` stays: that file holds the packet codec, and a changed `abs` or `round` would change bytes.
- New features, simulator realism, tooling and CI changes, documentation consolidation (the owner chose code health only).
- Test-suite speed: about 35 seconds, not a problem.
- Anything that needs the arm or an owner decision (BACKLOG P0, and items 20 to 23 below).

## Approach: tests first, then split in small steps

Rule for every change: no behaviour change, the full suite (`tests` and `tests_extra`) and ruff pass before and after, and a refactor commit contains **no** edit to fault, latch or stop logic.

### Phase 1: characterisation tests

Add tests that pin down what the code does today in the uncovered places, without changing the code:

| Target | Gap | Notes |
|---|---|---|
| `robot.py` | 176 statements, in particular the `connect` block (lines 194 to 276), the pre-home and gripper error branches, the late stop branches in `_end_stream` | Use the existing `SimulatedScorbot` and fake endpoints. A test that cannot reach a branch without USB is marked, not forced |
| `preflight.py` | 66% (lines 37 to 64, 89 to 100) | Enumeration only, never open; test with a fake device list |
| `lab/terminal.py`, `lab/__main__.py` | 85% and 80% | Prompt and argument branches |
| `calibration.py` | 86% | Error branches of `load_calibration` |
| `camera/source.py` | 86% | Fake capture source |

Target: about 95% on the safety modules (`robot.py`, `vendor_home.py`, `vendor_home_source.py`, `streaming.py`, `state.py`, `calibration.py`, `packet.py`), not 100% everywhere. Each new safety test is **mutation-checked**: break the code it covers on purpose, confirm the test fails, restore the file.

### Phase 2: split `robot.py`

Extract by responsibility, one group per commit, in this order (smallest risk first):

1. The homing family (`home_inch`, `vendor_home`, `park_at_home`, `pre_home_jog`, `_coupled_run` and their helpers), about 480 lines, into a module that `Scorbot` composes or mixes in.
2. The gripper (`move_gripper` and its helpers).
3. Stream start and shutdown (`start_stream`, `_end_stream`).
4. Re-measure. Stop if what is left reads well; do not split for its own sake.

Constraints for every extraction:

- `from scorbot.robot import Scorbot` and every public method and attribute keep working. A new test asserts the public surface (names and signatures) against a snapshot taken before the split.
- `_latch_fault`, `request_stop`, `disable` and the stop events stay on `Scorbot`; extracted code reaches them through the instance, never copies them.
- No method body changes beyond what moving requires (imports, `self` access). Diff review checks that each moved body is byte-identical apart from indentation.
- `scorbot/CLAUDE.md` and the root module index are updated in the same commit.

### Phase 3: backlog bugs that need no hardware

Only these, each with a failing test first:

| Item | Why it qualifies |
|---|---|
| BACKLOG 15: pause marker `256` hard-coded in `libsync.py:syncro` while `libcomm` reads `MAX_COUNT` | Same value by default, no byte on the wire changes; guard with a test that a changed `MAX_COUNT` is honoured. Legacy file, so a reviewer must confirm it is not packet construction or timing |

Left for the owner or for data (listed, not done): 20 (calibration faults bypass `_latch_fault`: "decide whether `enabled` should become unknown"), 21 and 22 (measured home duration; Windows Ctrl-C), 23 (what a declined bench run's verdict should be), 19 (above).

### Phase 4: review and record

- Codex and Gemini review each phase: `standard` for tests, `deep` for each extraction. Findings are triaged and logged with `review-log.mjs mark`.
- Everything on a new branch `code-health` cut from `home-diagnostics`, small commits, no push.
- The memory note and `BACKLOG.md` are updated at the end.

## Testing and error handling

- The suite is the safety net; Phase 1 widens it before Phase 2 relies on it.
- A refactor that makes any existing test fail is reverted, not patched around.
- If an extraction needs a behaviour change to work, stop: that is a design change and goes back to the owner.

## Order and size

| Phase | Work | Rough size |
|---|---|---|
| 1 | Characterisation tests, mutation checks | one working session |
| 2 | Three extractions, each reviewed | two to three sessions |
| 3 | One bug, test first | under an hour |
| 4 | Reviews and records | alongside the above |

## Owner decisions (2026-10-12)

1. Item 20 (`enabled` after a calibration fault) stays out of this plan. Revisit after the first lab visit shows what a real fault does.
2. No deeper split of `Scorbot` than the three extractions. Re-read `robot.py` after Phase 2; stop if it reads well. A bigger split would be its own spec.
