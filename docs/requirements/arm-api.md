# Arm API requirements
Status: draft 2026-10-20 (plan approved by Sebastian; baseline pending his yes)

## Goal
A Python API (`scorbot.bridge.Arm`) that scripts, and later cameras or a VLA, use to move the ER-4U through the vendor DLL session in `tools/usbc_server`. Every claim about where the arm goes is checked against something other than the code: a grid mat, raw encoder counts, SCORBASE.

## Requirements
| ID | Type | Statement | Check |
|----|------|-----------|-------|
| A01 | SHALL | Distances in the API and on the wire are mm; Teach gets micrometres (`UM_PER_MM` 1000); positions carry the raw encoder, joint and xyz arrays next to x, y, z mm | test: `tests_extra/test_usbc_server.py::ServerTests::test_move_by_teaches_micrometres_relative_and_defines_the_vector_once` |
| A02 | SHALL NOT | The server listens on any interface other than 127.0.0.1 | test: `test_usbc_server.py::ServerTests::test_it_listens_on_localhost_only` |
| A03 | SHALL NOT | A move_by step above 50 mm per axis, a non-finite or non-number value, or an unknown op reaches the DLL; no client line can end the session | test: `test_usbc_server.py::ServerTests::test_bad_steps_are_refused_without_a_dll_call`, `::test_odd_fields_cannot_end_the_session` |
| A04 | SHALL NOT | An absolute move runs before this session has homed (refused). No other limit is set by us: the DLL's own workspace decides (owner decision 2026-10-21, no box) | test: `test_usbc_server.py::ServerTests::test_move_to_needs_a_home_and_has_no_box` |
| A05 | SHALL | A stop request from the client, or the client going away (Ctrl+C), during a move calls Stop; the move reply says it was stopped | test: `test_usbc_server.py::ServerTests::test_a_stop_line_during_a_move_calls_stop`, `::test_a_client_that_vanishes_mid_move_gets_the_arm_stopped` |
| A06 | SHALL | Every exit (quit, error, Ctrl+C) ends with Control off and CloseUSBC, unless a DLL call may still be running, in which case serving stops and the operator is told to use the physical stop | test: `test_usbc_server.py::ServerTests::test_it_homes_then_serves_and_quit_ends_with_zero`, `::test_a_move_call_that_never_returns_ends_serving_busy` (shutdown itself: `tests_extra/test_usbc_home.py`) |
| A07 | SHALL | A client that disconnects leaves the homed session for the next client | test: `test_usbc_server.py::ServerTests::test_a_client_that_disconnects_leaves_the_session_for_the_next` |
| A08 | SHALL NOT | Moves or gripper calls are sent while the emergency stop is active | test: `test_usbc_server.py::ServerTests::test_the_emergency_stop_refuses_moves` |
| A09 | SHALL | The characterisation reports the DLL xyz scale per axis, repeatability spread and 3x3 grid error, and records mat readings | test: `tests_extra/test_arm_api.py::CharacteriseTests`; observable: run `examples/arm_characterise.py` on the arm, report in `calibration/` |
| A10 | SHALL | The scripted pick-and-place records success and mat error per run over 10 runs | test: `tests_extra/test_arm_api.py::PickPlaceTests`; observable: `examples/pick_place.py --runs 10` on the arm |

## Out of scope
- Cameras, vision models, VLA (next track).
- Our own USB protocol replacing the DLL.
- Joint-space moves, MoveLinear, trajectories, wrist roll or pitch targets beyond passing the current values through.

## Assumptions
- Teach takes micrometres (vendor header, USNA toolbox); unverified on the arm.
- GetCurrentPosition's xyz uses the same unit (`XYZ_UNITS_PER_MM` 1000): true in the DLL's own model (a 10 mm move_by read back as 9,996 units on the fake controller, 2026-10-20); the physical scale is A09's job.
- OpenGripper, CloseGripper and Speed behave as named; names read from the lab build's export table, never called before.

## Open questions
- Does a second DefineVector in one session fail? The server defines once per session, and Teach then overwrites point 1 fine (desk run on the fake controller, 2026-10-20).

## Change log
- 2026-10-20 draft written from the approved plan (`arm side — a measured Python API`).
- 2026-10-20 desk run: the lab USBC.dll build against `tools/usbc_fake` ran Home, move_by twice, gripper close and open, Speed, move_to (a box was required then; removed 2026-10-21), reconnect and quit with Control off; no refusals from the DLL.
