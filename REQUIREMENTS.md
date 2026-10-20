# OpenScorbot requirements
Status: draft (not baselined; the owner approves or edits before it becomes baseline v1)

## Goal
Supervised Python control of the Intelitek ScorBot ER-4U through its original Controller-USB, for the lab owner and the agents working with them: connect, read state, home and make small joint moves safely, with every claim about the real arm backed by a bench log. Offline tools, simulators and tests never touch the hardware.

Rows are drawn from the safety invariants in `CLAUDE.md` and the hazards in `docs/archive/design/SAFETY_CASE.md`; if a row and `CLAUDE.md` disagree, raise it as a change, do not pick one quietly. `pytest` below means `python -m pytest` in the project environment (`uv sync --locked --extra windows --extra test --extra planning`).

## Requirements

### Must never happen
| ID | Type | Statement | Check |
|---|---|---|---|
| R01 | SHALL NOT | A test, simulator or CI run opens USB or moves the arm. Importing the SDK loads no USB module. | test: `pytest tests/test_simulated.py -k test_import_loads_no_usb_and_leaves_sys_path_alone`. Partial: no test-time guard blocks a deliberate USB call, so also review: a reader checks any new test or script for `connect()` without `--simulate` |
| R02 | SHALL NOT | Any code, doc or name calls `disable()` or `request_stop()` an emergency stop. | test: `pytest tests/test_software_stop.py -k not_an_emergency_stop` |
| R03 | SHALL NOT | The SDK sends motion after any fault has latched the session. | test: `pytest tests/test_simulated.py -k test_every_fault_kind_latches_the_session` |
| R04 | SHALL NOT | `jog_joint` moves a wrist joint, and the wrist roll is never jogged. The only wrist moves are the owner-approved exceptions in `CLAUDE.md` (the vendor home, and the wrist pitch follower in coupled moves). | test: `pytest tests/test_arm_control.py tests/test_coupled_moves.py -k "test_wrist_jog_rejected_without_queuing_motion or only_wrist_pitch_ever_moves"` |
| R05 | SHALL NOT | A single SDK jog exceeds 5 degrees, or a speed outside 1 to 20 is accepted. | test: `pytest tests/test_arm_control.py -k test_jog_ceiling_cannot_be_raised_past_five_degrees` |
| R06 | SHALL NOT | `home()` runs without `start_position_confirmed=True`, with motors disabled, or with a home-switch byte of 32 or more. | test: `pytest tests/test_python_api.py -k test_home_refuses_switch_byte_the_legacy_decoder_misreads` |
| R07 | SHALL NOT | Real and simulated data share one session, review or comparison. | test: `pytest tests/test_session.py tests/test_analysis.py -k "rejected_in or mixed_sources"` |
| R08 | SHALL NOT | Calibration is built from vendor-display (SCORBASE) readings. | test: `pytest tests/test_arm_control.py -k test_vendor_display_cannot_validate_calibration` |
| R09 | SHALL NOT | A packet is built or sent whose bytes or sequence the legacy code never sent without a capture or recorded vendor-DLL evidence (`CLAUDE.md`, second invariant). | review: for any change under `openScorbot/` or the vendor-home bytes, a reviewer compares the hash from `scorbot/provenance.py:motion_source_sha256` before and after and reads the cited evidence |
| R10 | SHALL NOT | Intelitek manuals, `USBC.dll`, or its decompiled output are committed. | script: `git ls-files references \| grep -i -E "pdf\|dll"` prints nothing; `git ls-files \| grep -i usbc.dll` prints nothing |
| R11 | SHALL NOT | `USBC.dll` is loaded by any path other than the tools in `tools/` (`usbc_home`, `usbc_jog`, `usbc_move`, `usbc_fake`). | review: `grep -rn "USBC" --include=*.py` shows no other loader |
| R24 | SHALL NOT | `home_inch()` runs without `operator_at_stop=True`, with motors disabled, with a switch byte of 32 or more, or with `max_search_deg` outside 0 to 180; it never homes the wrist and never jogs more than 1 degree per step. | test: `pytest tests/test_inch_home.py` |
| R25 | SHALL NOT | A stream target leads the arm by more than the jog ceiling (5 degrees) before the session faults. | test: `pytest tests_extra -k stream` (not yet checked that a lead-limit test exists) |
| R26 | SHALL NOT | `examples/bench_joint.py` accepts a `--delta` above 1 degree. | observable: `python examplesench_joint.py --simulate --delta 2 ...` (arguments as in `CLAUDE.md`) refuses with an error and writes no motion row |

### Must work
| ID | Type | Statement | Check |
|---|---|---|---|
| R12 | SHALL | The no-hardware gate suite passes with no failures in under 2 minutes on the lab PC. | script: `python -m pytest -n 2 tests`; exit 0, failures 0, wall time under 120 s |
| R13 | SHALL | Encoder count differences are taken with the 65535 wrap, so a move across 0/65535 settles. | test: `pytest tests/test_settle_seam.py` |
| R14 | SHALL | The motion code path is fingerprinted into every lab log, so a log shows which code produced it. | test: `pytest tests/test_provenance.py` |
| R15 | SHALL | The shoulder is refused a jog past its joint limit above home. | test: `pytest tests/test_arm_control.py -k test_shoulder_jogs_stop_short_of_the_joint_limit_above_home` |
| R16 | SHALL | A wrist moving the wrong way during a coupled move is caught within about 4 degrees of pitch and faults the session (simulator). | test: `pytest tests/test_coupled_moves.py -k test_a_wrist_that_physically_moves_the_wrong_way_is_caught_within_a_few_degrees` |
| R17 | SHALL | Every simulated rehearsal output says SIMULATED. | script: `python -m scorbot.lab --simulate --profile rehearsal\lab.json --logs rehearsal` output contains `SIMULATED` on every status line |

### Lab procedure (checked at the bench)
| ID | Type | Statement | Check |
|---|---|---|---|
| R18 | SHALL | The first arm motion by our own code is a 1 degree step, with one person's hand on the physical emergency stop and the arm path clear. | observable: visit B log shows the first `motion` row with `delta` of 1 degree or less, and the lab card records the stop operator's initials |
| R19 | SHALL | The vendor stack is recorded with USBPcap, and the hand-sweep report is read, before our own vendor home runs on the arm. | observable: `docs/archive/lab/VISIT_A_AGENT_CHECKLIST.md` steps all ticked, the capture file and `hand_sweep_report.py` output saved under `docs/archive/evidence/` |
| R20 | SHALL | If the hand sweep shows the wrist roll switch narrower than about 750 counts, the owner is told before any vendor home. | observable: the report's NOTE line is quoted in the visit record and the owner's reply is noted |

### Documentation
| ID | Type | Statement | Check |
|---|---|---|---|
| R21 | SHALL | Every claim about the real arm says "unverified" unless a bench log backs it. | review: the owner or reviewer spot-checks 5 hardware claims in `docs/STATUS.md` and the PR |
| R22 | SHALL | Each piece of work that changes behaviour, evidence, a requirement or a hazard has a report in `docs/archive/reports/`, with its row in `INDEX.md` and `STATUS.md` updated in the same commit. | observable: `git diff --stat origin/main` for the PR lists a new `docs/archive/reports/R-*.md`, `docs/archive/reports/INDEX.md` and `docs/STATUS.md` |
| R23 | SHALL | A report states what was run and printed; a check that was not run is written as "not run". | review: a reviewer re-runs two commands from the report's Verification table and matches the result |

## Out of scope
- Cartesian or millimetre-target motion on the arm (removed 2026-10-08).
- Autonomous or unattended operation; a controller-independent emergency stop in software.
- Any claim that `disable()` or `request_stop()` stops the arm.
- A GUI front end (the PyQt GUI is kept as reference code only).
- A certification claim (ISO 10218, IEC 61508 and similar); `SAFETY_CASE.md` says it is not one.
- Calibrated absolute moves before a validated physical calibration exists.

## Assumptions
- The physical emergency stop is the only authoritative stop (`SAFETY_CASE.md` A3).
- The arm starts near its legacy home pose and the base is secured (A5).
- Exactly one program owns the controller; Intelitek software is closed (A4).
- Manual values and vendor-DLL numbers are priors, not measurements of this arm.
- The legacy bytes are accepted by the controller (one idle run, 2026-09-29).
- The controller's communication-failure motor shutdown exists as the manual says (never observed, HZ-16).

## Open questions
- R12: the gate ran 1011 passed in 67 s on the owner's PC on 2026-10-13 (`CLAUDE.md` says about 645; stale). Confirm the lab PC is as fast.
- R17, R25, R26: checks not run yet.

Decided 2026-10-13 by the owner: "near home" confirmation stays lab procedure; shoulder bound stays as the vendor has it; stream lead rule is its own row (R25), the 5 degree jog rule stays R05.

## Change log
- 2026-10-13 draft written by Claude from `CLAUDE.md`, `SAFETY_CASE.md` and `STATUS.md`; no row approved yet.
- 2026-10-13 R24 added after the Gemini check (`engineer-check.sh`, run 20261013-012709-12247-engineer, exit 10 = it has questions, which are the Open questions above); not yet a go.
- 2026-10-13 R25, R26 added; open questions answered by the owner as above. Still draft: the owner has not said baseline.
- 2026-10-20 R11 widened to every `tools/usbc_*` tool, with no agent restriction; the hardware switch, typed words and acknowledge flags were removed (owner: "if i want it to be ran run it"). `docs/archive/requirements/hardware-gate.md` is retired.
