# Run log and status line requirements
Status: baseline v1 2026-10-26

## Goal
After a lab run, anyone can read what the arm was asked to do and what came back, from the server's log alone, and see it as a short table. While teleoperating, the operator sees the last result and the last refusal without scrolling. Users: the lab operator, and a reviewer reading a log afterwards.

## Requirements
| ID | Type | Statement | Check |
|----|------|-----------|-------|
| LG01 | SHALL | Every reply the server sends to a client is also written to the event log as a `reply` event carrying the request `id`, `ok`, `code` and `error` when present, `timing_s` when present, and `xyz` when present | test: `tests/test_usbc_server.py::ServerTests::test_every_reply_is_logged` |
| LG02 | SHALL NOT | Logging a reply changes the bytes the client receives, or the order of the log event and the send changes what the client sees | test: `tests/test_usbc_server.py::ServerTests::test_logging_does_not_change_the_reply` |
| LG03 | SHALL | `scripts/summarize_log.py <log.jsonl>` prints one row per command: time, op, outcome (`ok`, `refused: <reason>` or `dll code N`), `timing_s`; then totals of ok, refused and failed | test: `tests/test_summarize_log.py::test_table_and_totals` |
| LG04 | SHALL | The summary still prints for a log whose last line is cut off (a crash), and says the log ended early | test: `tests/test_summarize_log.py::test_truncated_last_line` |
| LG05 | SHALL NOT | `summarize_log.py` modifies the log file, loads `USBC.dll`, or needs anything outside the standard library | test: `tests/test_summarize_log.py::test_log_is_untouched_and_no_dll`; review: its imports list |
| T01 | SHALL | After every key press `examples/teleop.py` prints one status line: step count, the last result (`ok` or `refused: <reason>`), refusals so far, and x, y, z in mm when the reply carries them | test: `tests/test_teleop.py::DriveTests::test_status_line_after_each_key` |
| T02 | SHALL NOT | The status line changes which requests teleop sends to the arm, their order, or the step caps in W03 | test: `tests/test_teleop.py::DriveTests::test_status_line_sends_the_same_requests`; the W03 tests still pass |
| LG06 | SHALL | A desk run on the fake controller produces a log, and `summarize_log.py` on it matches what was sent | observable: `teleop.py` against `tools/usbc_fake` with 3 moves, one of them outside the box; the table shows 2 ok, 1 refused with the box reason |

## Out of scope
- Log rotation, a GUI, remote or cloud logging.
- Changing any refusal rule, the second-client wait, or what the DLL is told to do.
- Judging whether the arm moved correctly: the log records what was asked and what the DLL answered, not the physical result (A09 and the grid mat do that).

## Assumptions
- The server's existing `EventLog` (append-only JSONL, flushed per event) is the single log; no second file.
- Reply lines are small enough to log in full.

## Open questions
- None on a row being built.

## Change log
- 2026-10-26 draft written.
