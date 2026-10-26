# Requirements
Status: draft 2026-10-20 (plan approved by Sebastian; baseline pending his yes)

## Goal
A Python API (`scorbot.bridge.Arm`) that scripts, and later cameras or a VLA, use to move the ER-4U through the vendor DLL session in `tools/usbc_server`. Every claim about where the arm goes is checked against something other than the code: a grid mat, raw encoder counts, SCORBASE.

## Requirements
| ID | Type | Statement | Check |
|----|------|-----------|-------|
| A01 | SHALL | Distances in the API and on the wire are mm; Teach gets micrometres (`UM_PER_MM` 1000); positions carry the raw encoder, joint and xyz arrays next to x, y, z mm; move and gripper replies also carry `timing_s`, seconds per server step | test: `tests/test_usbc_server.py::ServerTests::test_move_by_teaches_micrometres`, `::test_replies_carry_timing` |
| A02 | SHALL NOT | The server listens on any interface other than 127.0.0.1 | test: `test_usbc_server.py::ServerTests::test_it_listens_on_localhost_only` |
| A03 | SHALL NOT | A move_by step above 50 mm per axis, one that ends further outside the measured box than it starts, a non-finite or non-number value, or an unknown op reaches the DLL; no client line can end the session | test: `test_usbc_server.py::ServerTests::test_bad_steps_are_refused_without_a_dll_call`, `::test_odd_fields_cannot_end_the_session`, `::test_move_by_stays_in_the_box`, `::test_move_by_outside_the_box` |
| A04 | SHALL NOT | An absolute move runs before this session has homed, without a box file, or to a target outside the box (refused, never clipped) | test: `test_usbc_server.py::ServerTests::test_move_to_needs_home_and_box`, `::test_move_to_without_a_box_file_is_refused` |
| A05 | SHALL | A stop request from the client, or the client going away (Ctrl+C), during a move calls Stop; the move reply says it was stopped | test: `test_usbc_server.py::ServerTests::test_a_stop_line_during_a_move_calls_stop`, `::test_vanishing_client_stops_the_arm` |
| A06 | SHALL | Every exit (quit, error, Ctrl+C) ends with Control off and CloseUSBC, unless a DLL call may still be running, in which case serving stops and the operator is told to use the physical stop | test: `test_usbc_server.py::ServerTests::test_it_homes_then_serves_and_quit_ends_with_zero`, `::test_hung_move_call` (shutdown itself: `tests/test_usbc_home.py`) |
| A07 | SHALL | A client that disconnects leaves the homed session for the next client | test: `test_usbc_server.py::ServerTests::test_disconnect_keeps_the_session` |
| A08 | SHALL NOT | Moves or gripper calls are sent while the emergency stop is active | test: `test_usbc_server.py::ServerTests::test_the_emergency_stop_refuses_moves` |
| A09 | SHALL | The characterisation reports the DLL xyz scale per axis, repeatability spread and 3x3 grid error, and records mat readings | test: `tests/test_arm_api.py::CharacteriseTests`; observable: run `examples/arm_characterise.py` on the arm, report in `calibration/` |
| A10 | SHALL | The scripted pick-and-place records success and mat error per run over 10 runs | test: `tests/test_arm_api.py::PickPlaceTests`; observable: `examples/pick_place.py --runs 10` on the arm |

| W01 | SHALL | `wrist_by` sends a relative Teach of `[0, 0, 0, dpitch, droll]` in whole DLL units and MoveJoint once; x, y, z are not touched | test: `tests/test_usbc_server.py::ServerTests::test_wrist_by_teaches_pitch_and_roll`; desk run against `tools/usbc_fake` (2026-10-25): +200 pitch gave m1 -5, m2 +5 counts and no change in x, y, z |
| W02 | SHALL NOT | A wrist step above 10000 units on an axis, one that rounds to zero, or a non-finite or non-number value reaches the DLL | test: `test_usbc_server.py::ServerTests::test_bad_wrist_steps_are_refused` |
| W03 | SHALL NOT | `examples/teleop.py` sends an x, y, z step above 20 mm or a wrist step above 5000 units, or moves the arm before a typed `go`; the wrist keys do nothing unless `--wrist-step` is above zero | test: `tests/test_teleop.py::MainTests::test_step_cap`, `::test_wrist_step_cap`, `::test_needs_go`, `DriveTests::test_wrist_keys_need_a_wrist_step` |
| A11 | SHALL NOT | Intelitek manuals, `USBC.dll`, its ini files or captures are committed | script: `git ls-files | grep -i -E "usbc|er4.*ini|pcap"` prints nothing; `scripts/lab_check.py` runs the same check |
| A12 | SHALL NOT | `USBC.dll` is loaded by any code other than the tools in `tools/` | review: `grep -rn "USBC" --include=*.py` shows no other loader |

## Out of scope
- Cameras, vision models, VLA (next track).
- Our own USB protocol replacing the DLL (the `stack2` branch).
- Joint-space moves, MoveLinear, trajectories, absolute wrist targets (`move_to` passes the current pitch and roll through).

## Planned, needs the lab first
Not built. Each needs numbers from the arm ([docs/PLAN.md](docs/PLAN.md), [docs/DESK_FINDINGS.md](docs/DESK_FINDINGS.md)).
- J01 joint jog: one axis at a time through the DLL's `MoveManual` (axis, velocity percent), with `usbc_jog`'s cleanup order. Needs the axis numbers and signs confirmed on the arm, and the wrist branch of MoveManual is untraced.
- H01 hold-to-move: short capped jog bursts while a key is held, stop when presses stop. Needs the measured latency and coasting after a stop; never an open-ended move.

## Assumptions
- Teach takes micrometres (vendor header, USNA toolbox); unverified on the arm.
- GetCurrentPosition's xyz uses the same unit (`XYZ_UNITS_PER_MM` 1000): true in the DLL's own model (a 10 mm move_by read back as 9,996 units on the fake controller, 2026-10-20); the physical scale is A09's job.
- OpenGripper, CloseGripper and Speed behave as named; names read from the lab build's export table, never called before.

## Open questions
- Does a second DefineVector in one session fail? The server defines once per session, and Teach then overwrites point 1 fine (desk run on the fake controller, 2026-10-20).

## Change log
- 2026-10-25 wrist_by added (W01 to W03): relative pitch and roll steps, tested on the fake library and end to end on the vendor DLL against the fake controller; joint jog and hold-to-move listed as planned.
- 2026-10-20 draft written from the approved plan (`arm side — a measured Python API`).
- 2026-10-20 desk run: the lab USBC.dll build against `tools/usbc_fake` ran Home, move_by twice, gripper close and open, Speed, move_to (refused without a box, then run), reconnect and quit with Control off; no refusals from the DLL.
