# Streaming soak requirements
Status: baseline v2 2026-11-09 (approved production-fix task brief; left uncommitted)

## Goal
Make the PL12 scratch streaming trial repeatable on the desk, through Stack 2's real workers and
`device_file` with a fake Win32 API and the pipe-controller plant. Streaming has no DLL equivalent;
this is E2 evidence only, unverified on the arm. A failed safety check must remain a visible failure.

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| SO01 | SHALL NOT | The runner shall open real USB, a DLL, or a native device handle, accept `--online`, or accept any device path other than its desk fake. | test: `tests_extra/test_stream_soak.py::test_refuses_hardware_before_connect`; `test_device_file_uses_only_fake_api` |
| SO02 | SHALL | Each scenario shall connect, enable, run vendor Home on the fake, then call `Scorbot.start_stream`; a new instance and vendor Home shall be used after a fault. | test: `test_each_fault_prevents_further_motion` |
| SO03 | SHALL | The runner shall update a seeded sine-plus-random-step target inside the stream's travel bounds for the requested streaming time (`--minutes`, default 10); initialization and fault observation are additional time. | test: `test_targets_are_repeatable_and_bounded`; script: `python scripts/stream_soak.py --minutes 2 --logs .pytest_cache/soak` |
| SO04 | SHALL | The runner shall inject one reply delayed 0.6 s (above the 0.5 s hold interval), repeated identical replies with the same echo ID for at least 0.6 s, a base error word of 71 (ImpactDetect is 70), and persistent write and read failures, once each by default or selected with `--fault`. | test: `test_each_fault_prevents_further_motion` |
| SO05 | SHALL | After a late/impact reply, the first stale reply returned past its timeout, or the first failed I/O attempt, no subsequent attempted packet shall command a moving arm setpoint; holding or stopping is acceptable. An already transmitted packet and repeats within the reply timeout are excluded. | test: `test_each_fault_prevents_further_motion` |
| SO06 | SHALL | Impact, I/O and reply-timeout failures shall latch the session, queue motors-off through the existing path, attempt motors-off packets, report the error, and refuse targets and new motion until a new instance is connected, enabled and vendor-homed. | test: `test_each_fault_prevents_further_motion` |
| SO07 | SHALL | Each injection shall print one result line containing the fault, error, latch, motors-off queued/attempted/sent, and PASS/FAIL; the runner shall exit 1 on any failed check. | test: `test_each_fault_prevents_further_motion` |
| SO08 | SHALL | Each run shall write exclusively created JSONL files under `--logs` with target updates, attempted and delivered packets/replies, injection boundaries, checks, cleanup and the verdict, including seed and timing. | test: `test_each_fault_prevents_further_motion` |
| SO09 | SHALL | The CI soak shall exercise each fault once in less than 60 s on the desk and shall clean up its legacy workers, including failed scenarios. | test: `test_each_fault_prevents_further_motion` |
| SO10 | SHALL | Production changes shall be confined to the authorized reply watchdog and exact streaming hold fixes, preserving desk-only verification and visible failures. | review: production diff against SO11-SO13; authorized by the 2026-11-09 task brief ("make it secured") |
| SO11 | SHALL | Streaming shall fault after more than `reply_timeout_s` without an advancing controller echo, including a delayed worker step; default 0.5 s, validated like `hold_timeout_s`. The existing fault path shall queue motors-off and refuse further targets. | test: `tests/test_stream_reply_watchdog.py`; `test_each_fault_prevents_further_motion` |
| SO12 | SHALL | Identical consecutive replies (including four repeats and longer runs within the timeout) shall keep tracking; only advancing echo IDs shall refresh reply age, including wrap from 255 to 1. | test: `tests/test_stream_reply_watchdog.py` |
| SO13 | SHALL | After a streaming write succeeds and its read fails, every recovery hold shall retain the last commanded setpoints, including wrist fields when streamed, without reverting to the previous measured pose. Non-stream measured-position stop behavior shall remain unchanged. | test: `tests/test_stream_usb_error.py`; `test_each_fault_prevents_further_motion` |

## Recovery
For a latched fault, disconnect and create a **new** instance, then connect, enable and vendor Home
before new motion. The runner performs this on a fresh fake plant for every scenario. Late/stale
acceptance is checked before runner cleanup: its own stop must never count as fault detection.
Target-update hold and the reply watchdog are independent. The soak keeps updating targets
during late/stale feedback. A stale run is allowed to track within the reply timeout; its packet
boundary is the first expired reply returned, excluding the write already transmitted for it.
A faulted stream retains its last accepted OUT setpoints through stop, queued motors-off and idle.
Ordinary stream stops and non-stream jog stops still use the measured position (`libdef.stopMov`).
After a clean stream, order 16 and EXIT follow the encoders as before; they do not reuse the stream's
stop/close hold. Only an I/O error or `source.core.fault` retains that hold for motors-off, and an
intervening non-stream order clears the retained stream hold.
SO13 supersedes the older fault-idle clause of ST16 in `streaming.md` for SDK stream faults;
the older feature baseline is otherwise unchanged.

## Out of scope
- Hardware access, arm motion, DLL byte equivalence, and new dependencies.
- Committing, pushing or opening a PR in this worktree.
- Treating successful writes on a broken link as confirmation that real motors are off.

## Assumptions
- The pipe fake's plant is a model, with no evidence about real timing, inertia or gravity.
- Fake Home uses a reply-driven clock and no legacy sleeps; streaming pacing and injected delays use real wall time.
- The task brief authorizes this baseline; no root requirements are changed.

## Open questions
None. The 2026-11-09 brief authorizes the production fixes and timeout tolerance above.

## Verification (2026-11-09, desk only)
The five-fault soak passes with zero moving packets after each applicable fault boundary,
including while the full `tests tests_extra` suite runs under default `-n auto` (32.89 s runner).
This is E2 evidence, still unverified on the arm.

| ID | Result | Evidence |
|---|---|---|
| SO01 | pass | Hardware-option refusal and fake-API tests pass; the soak forbids native Win32 API construction. No native device or DLL was opened. |
| SO02 | pass | Every scenario uses a fresh fake and SDK instance; logs order vendor Home before stream start. |
| SO03 | pass | Seeded target bounds and reproducibility tests pass; the CI runner streams five healthy intervals before injecting faults. The historical two-minute run was not repeated for this fix. |
| SO04 | pass | JSONL records all five faults: 0.6 s late/stale, ImpactDetect 71, persistent failed writes and reads. |
| SO05 | pass | All five scenarios record zero moving packets after the applicable boundary. A write already transmitted for the first expired stale reply is excluded. |
| SO06 | pass | All five latch, queue/attempt motors-off, report the expected fault, refuse targets/motion/reconnect and finish cleanup. Failed-link writes do not establish physical motor state. |
| SO07 | pass | Five PASS result lines and an exit-0 summary; earlier red runs returned 1 and exposed late/stale/read-hold failures. |
| SO08 | pass | Exclusive simulated-data JSONL logs include target, packet, injection, watchdog boundary, result and cleanup events. |
| SO09 | pass | Review correction: the five-fault soak test passed in 27.75 s within `tests_extra`. Earlier combined default-xdist suite: soak test 34.27 s (runner 32.89 s) with other tests running. Standalone default-xdist: test 38.01 s, full invocation 56.23 s; without xdist: full invocation 31.31 s. All five cleanup checks pass. Fake Home uses a packet clock, faster coarse travel and no setup cancellation-timer threads. |
| SO10 | pass | Production diff implements the authorized watchdog and exact fault hold. Simulator replies now advance the echo ring; ordinary measured-position stops stay intact. |
| SO11 | pass | Adapter tests cover stale echoes, one delayed/fresh reply, a delayed first worker call, invalid echoes, custom timeout, latch/queued disable and refusal. Public settings validate 0.05 through 2 s like hold timeout. |
| SO12 | pass | Fifty identical replies through 0.49 s keep tracking; changing counts with a repeated echo cannot refresh age; 255 -> 1 does refresh it. |
| SO13 | pass | Failed/partial read tests preserve accepted arm/wrist setpoints through stop retries, motors-off, queued disable and exit. Core faults retain the same hold without an I/O failure. Clean streams (including an empty stream with changing stop feedback) preserve complete encoder-following motors-off packets for order 16 and EXIT; an intervening non-stream order clears a fault hold. Fault idle retains the same hold after collection. Jog-stop tests remain unchanged. |

Before the production fix: the focused unit invocation reported **11 failed, 28 passed,
25 subtests passed**. The soak reported late/stale tracking and one moving dropped-read
recovery packet. After the initial direct fix, separate handoff tests reproduced two more
failures in queued-disable/idle retention; the focused checks then passed (93 tests and
107 subtests). Log files under `.pytest_cache/stream-*.log` retain the command output.

Review correction: the earlier verification missed motors-off after an unfaulted stream. The new
packet regressions failed before the correction in all four clean-stream subcases (normal/empty
stream, order 16 then EXIT/direct EXIT): `4 failed, 3 passed, 16 deselected in 2.61s`.
The same invocation after the correction passed: `3 passed, 16 deselected, 4 subtests passed in 2.02s`.
The added core-fault test plus failed-read and reply-watchdog coverage then passed:
`28 passed, 14 subtests passed in 16.96s`. Output is retained in `.pytest_cache/review-hold-*.log`.
These are desk fakes; this correction does not add hardware evidence.

Commands use `C:/Users/sebas/startup/openScorbot/.venv/Scripts/python.exe`, with
`PYTHONPATH=C:/Users/sebas/startup/openScorbot-stream-soak`. `TEMP` and `TMP` point to
`.pytest_cache/desk-temp` inside this worktree because sandbox access to the default temp root
is denied. Disabling xdist also requires clearing the repo's `-n auto` addopts; the literal
`-p no:xdist` command alone exits with `unrecognized arguments: -n`.

The `tests`, `tests_extra`, docs and Ruff entries below are rerun for the review correction;
the soak-only and combined-suite entries retain the earlier verification evidence.

| Command after the venv Python path | Exact result tail |
|---|---|
| `-m pytest -q tests_extra/test_stream_soak.py` | `8 passed in 56.23s` |
| `-m pytest -q tests_extra/test_stream_soak.py -p no:xdist -o addopts=` | `8 passed in 31.31s` |
| `-m pytest -q tests` | `1436 passed, 1 warning, 14392 subtests passed in 131.51s (0:02:11)` |
| `-m pytest -q tests_extra` | `625 passed, 21 skipped, 1048 subtests passed in 57.84s` |
| `-m pytest -q tests tests_extra` | `2058 passed, 21 skipped, 1 warning, 15436 subtests passed in 201.71s (0:03:21)` |
| `scripts/check_docs.py` | `docs ok` |
| `-m ruff check .` | `All checks passed!` |

The warning is the existing `test_home_stop.py` worker comparing a `MagicMock` sequence to
an integer in `libdef.countByte1`; it was observed and left unchanged. The 21 skips require
optional roboticstoolbox, torch, mujoco or the separate LeRobot environment; no dependencies were added.
The two-minute and
default ten-minute runs were not repeated. Hardware remains unverified on the arm.

## Changed files
- Production: `scorbot/streaming.py`, `scorbot/stream_control.py`, `scorbot/robot.py`,
  `scorbot/simulated.py`, `openScorbot/libcomm.py`.
- Soak/tests: `scripts/stream_soak.py`, `tests_extra/test_stream_soak.py`,
  `tests/test_stream_reply_watchdog.py`, `tests/test_stream_usb_error.py`,
  `tests/test_streaming_b.py`, `tests/fixtures/scorbot_surface.json`.
- Documentation/indexes: `README.md`, `docs/README.md`, `docs/STATUS.md`,
  `docs/protocol/SOURCE_AUDIT.md`, `docs/requirements/stream-soak.md`, `openScorbot/README.md`,
  `scorbot/README.md`, `scripts/README.md`, `tests/README.md`, `tests_extra/README.md`.
- Local ignored note: one dated evidence line added to `LESSONS.md`.

## Change log
- 2026-11-08: SO01-SO10 recorded from the approved task brief.
- 2026-11-09: The task brief authorized production fixes ("make it secured"); SO05/SO06 now specify the reply-timeout boundary and latch, SO10 records authorization, and SO11-SO13 specify watchdog, repeat tolerance and exact hold. SO09 uses load-independent fake Home.
- 2026-11-09: Review correction restores encoder-following motors-off after clean streams and verifies clearing a fault hold after a non-stream order; no requirement scope changed. Retained holds are reported only by the I/O-error and core-fault paths.
