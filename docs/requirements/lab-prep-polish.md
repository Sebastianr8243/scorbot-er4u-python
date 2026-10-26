# Lab-prep polish requirements
Status: baseline v1 2026-10-27

## Goal
Make the first lab run easier to run and to read back: a second client is told the server is busy instead of hanging, teleop says what to do after an arm error, and the lab sheet has a hazard table with a measured pass or fail and an outside check for every step. Users: Sebastian at the lab, and the professor reviewing.

## Requirements
| ID | Type | Statement | Check |
|----|------|-----------|-------|
| B01 | SHALL | While one client is connected and between commands, a second connection to the server receives one line `{"ok": false, "busy": true, "error": "<text naming the busy server>"}` and is closed, within 1 s | test: `tests/test_usbc_server.py::ServerTests::test_a_second_client_is_told_the_server_is_busy` |
| B02 | SHALL NOT | A second connection changes the first client's session: its next command still gets its own reply, the Home count stays 1, and the first client's disconnect still keeps the session for the next one | test: `tests/test_usbc_server.py::ServerTests::test_a_second_client_does_not_disturb_the_first` |
| B03 | SHALL NOT | A second connection delays, interrupts or stops a move in progress: the server answers it only after that move's reply has been sent, and no `Stop` call is made | test: `tests/test_usbc_server.py::ServerTests::test_a_second_client_waits_for_a_running_move` |
| B04 | SHALL | `Arm.send` raises `ArmError` whose message is the server's busy text when the server answers a busy refusal | test: `tests/test_arm_api.py::test_a_busy_server_is_reported_as_busy` |
| T03 | SHALL | When teleop ends on an arm error that is not a plain refusal, it prints the error and then a line saying the arm may have moved and to read `where` before the next move | test: `tests/test_teleop.py::MainTests::test_an_arm_error_says_to_check_where` |
| T04 | SHALL NOT | Teleop sends another move after an arm error that is not a plain refusal (the loop still ends, as today) | test: `tests/test_teleop.py::DriveTests::test_dll_error_ends_the_loop` |
| D01 | SHALL | `docs/lab/LAB_RUN_SHEET.md` has a hazard table for the first move and, for every arm step, a numeric pass or fail limit and the outside check that settles it (ruler, dial indicator, vendor software readout) | review: Sebastian reads the sheet and confirms each step row has both |
| D02 | SHALL | The lab sheet marks every limit as a desk guess until the lab run confirms it, and has a signature line for the person at the red stop | review: same read |
| C01 | SHALL | Ctrl+C at the key prompt of `teleop --fake` ends it within 2 s with `ended` and exit 0 | observable: `.\.venv\Scripts\python.exe examples	eleop.py --fake`, type `go`, wait for the first status-free prompt, press Ctrl+C; `ended` prints and the shell prompt returns |

## Out of scope
- Making teleop continue after a DLL error: that stop is deliberate (the arm may have moved), so only its message changes.
- Queueing or serving two clients at once; the server stays one client at a time.
- Anything that moves the arm, and any change to refusal rules, step caps or box checks.
- Reconnect or auto re-Home after a server crash (needs the arm).

## Assumptions
- The server only looks at a second connection between commands, from its existing idle poll (`IDLE_POLL_S`); during a DLL call the second client still waits, then gets the busy line at once.
- The pass limits on the lab sheet reuse numbers already in the repo (about 1000 DLL units per mm, off-axis under 1 mm, a few percent on scale); none is measured.

## Open questions
- None on a row being built.

## Change log
- 2026-10-27 baseline v1. Sebastian said "do 1 2 and 3" for the plan in chat; the only change from that plan is that a DLL error still ends teleop (T04), reported in the PR.
