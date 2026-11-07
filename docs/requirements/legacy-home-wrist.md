# Legacy Home wrist encoding requirements
Status: baseline v1 2026-11-06 (authorized by the task brief; bench validation pending)

## Goal
Use the existing controller count frame for legacy Home's wrist targets and avoid
re-reading configuration on each message. This is a draft change for a bench test,
unverified on the arm.

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| HW01 | SHALL | Pitch and roll search, offset and braking targets encode both wrist pairs in the controller count frame, including low words 0, 1, 65535, 65536 and -1 with their applicable sign words. | test: `tests/test_legacy_home_encoding.py::HomeWristEncodingTests` |
| HW02 | SHALL | Each of the six construction sites has explicit old/new hex evidence; non-wrist slots remain unchanged. | test: `tests/test_legacy_home_encoding.py::HomeWristEncodingTests`; byte table below |
| HW03 | SHALL NOT | Change `check()`'s per-message `TIME_OUT_R` read: it sits in the write-to-read gap, and shortening that gap needs a capture (`tests/test_libdef_config_reads.py`). Dropped from this change on review. | test: `tests/test_libdef_config_reads.py::LibdefConfigReads::test_set_msg_reads_only_the_read_timeout` |
| HW04 | SHALL NOT | Validation opens USB, loads the controller DLL, or moves the real arm. | review: pure field statements and fake endpoints only; pytest USB backstop in `conftest.py` |
| HW05 | SHALL NOT | This task commits, merges, publishes, adds dependencies, changes homing search logic, or claims arm validation. | review: final diff and command history; CHANGELOG entry |

## Encoding evidence
`slotHex(top, low)` writes `top * 65536 + low - 0x7FFFFF` as signed
32-bit little endian. For every integer low word L in 0..65535:

- sign `0000`: old `detrans(L) + sign` is L; `countField` is L + 1.
- sign `ffff`: old is L - 65536; `countField` is L - 65535.

Thus the old target is exactly one count low for both sign words throughout that
range (131072 pairs). For example `(0, '0000')` changes `00000000` (0) to
`01000000` (1). `(65535, 'ffff')` changes `ffffffff` (-1) to `00000000` (0).
This follows the existing count-frame requirements and the signed slot layout in
`docs/protocol/VENDOR_DLL_PROTOCOL.md` sections 3 and 6; controller behavior is
unverified on the arm.

Outside 0..65535, the one-count claim no longer applies: `detrans` indexes only
four characters of `format(low, '04x')`. 65536 becomes `0010` (4096), while -1
becomes `01-0`, which `bytes.fromhex` rejects. `suma` permits 65536 on the `0000`
side and `resta` permits -1 on the `ffff` side.

The following four-byte slot table applies independently to **both wrist motors
at every site**: pitch search (original lines 348-351), pitch offset (387), pitch
braking (408), roll search (493), roll offset (532), roll braking (551).
The tests exercise each slot in both positions with a different other motor.

| low | sign | old slot text | new slot hex |
|---|---|---|---|
| 0 | 0000 | 00000000 | 01000000 |
| 1 | 0000 | 01000000 | 02000000 |
| 65535 | 0000 | ffff0000 | 00000100 |
| 0 | ffff | 0000ffff | 0100ffff |
| 1 | ffff | 0100ffff | 0200ffff |
| 65535 | ffff | ffffffff | 00000000 |
| 65536 | 0000 | 00100000 (truncated) | 01000100 |
| -1 | ffff | 01-0ffff (invalid hex; no packet) | 0000ffff |

For wrist motor 1 `(0, '0000')` and motor 2 `(65535, 'ffff')`, the
complete eight-byte `signal_out` changes as follows at each construction site.
Line numbers identify the original source; both motors also receive every slot
case above independently in the golden test.

| Site (original line) | Old `signal_out` | New `signal_out` |
|---|---|---|
| Pitch search (348-351) | 00000000ffffffff | 0100000000000000 |
| Pitch offset (387) | 00000000ffffffff | 0100000000000000 |
| Pitch braking (408) | 00000000ffffffff | 0100000000000000 |
| Roll search (493) | 00000000ffffffff | 0100000000000000 |
| Roll offset (532) | 00000000ffffffff | 0100000000000000 |
| Roll braking (551) | 00000000ffffffff | 0100000000000000 |

## Out of scope
- Bench execution, USB discovery, controller DLL loading and arm motion.
- `moveXYZ.py`, other Home joints, switch logic, speeds, steps, braking and delays.
- Changes to the historical scope of `count-frame.md` (CT05 excluded Home in that work).
- Commits and publishing; leave the worktree ready for draft bench review.

## Assumptions
- The existing `countField` frame is the intended Home frame, as authorized in the brief.

## Open questions
- Does the physical controller accept these corrected Home targets? Requires a supervised bench test.

## Change log
- 2026-11-06 HW01-HW05 baseline from the supplied task brief; uncommitted by request.

## Offline verification (resumed 2026-11-06, two workers maximum)
- Before the fix: `uv run --frozen pytest tests/test_legacy_home_encoding.py -q -n 2 --tb=short`
  reported 97 failed (96 site/slot subtests and the timeout test), 3 passed and 8 subtests passed
  in 2.58 s. The timeout failure observed three configuration reads for three messages.
  Both source files were temporarily replaced with their `HEAD` versions, then restored
  byte for byte in a `finally` block, with SHA-256 equality checked.
- After the fix: `uv run --frozen pytest tests/test_legacy_home_encoding.py tests/test_count_frame.py tests/test_legacy_golden.py -q -n 2 --tb=short`
  reported 32 passed and 642 subtests passed in 3.21 s. Existing golden fixtures were unchanged.
- An exhaustive pure-function check of all 131072 in-range `(low, sign)` pairs
  confirmed the one-count difference. Only `slotHex`, `countField` and `detrans`
  function definitions were compiled from source; no legacy module was imported.
- `uv run --frozen ruff check .`, `uv run --frozen python -m compileall -q scorbot openScorbot scripts examples tests tests_extra`,
  `uv run --frozen python scripts/check_docs.py` and `git diff --check` passed.
- Sandbox adaptation: `UV_CACHE_DIR=.pytest_cache/uv`, `TEMP=TMP=.pytest_cache/tmp`,
  `UV_PYTHON_DOWNLOADS=never`; the default uv cache and pytest temporary directory
  were inaccessible. No dependency changes were needed.
- Final full suite: `uv run --frozen pytest tests tests_extra -q -n 2` reported
  **1 failed, 1958 passed, 18 skipped, 1 warning, 15467 subtests passed in 177.77 s**.
  The unchanged `tests/test_libdef_config_reads.py:44` explicitly expects one
  `TIME_OUT_R` configuration read per message; it now receives none, as HW03 requires.
  No existing tests or golden fixtures were edited. The carried index row for
  the new test in `tests/README.md` was retained.
- The full run emitted a `PytestUnhandledThreadExceptionWarning` from
  `test_a_stopped_home_leaves_the_motors_on_and_reports_only_stopped`:
  fake-worker shutdown passed a `MagicMock` sequence byte to `countByte1`, causing
  a `TypeError`. This existing test and its worker shutdown code were unchanged.

| ID | Result | Evidence |
|---|---|---|
| HW01 | pass | All 96 new site/slot subtests passed; step-range checks passed. |
| HW02 | pass | Explicit old/new goldens and full-region assertions passed at all six sites; original golden fixtures unchanged. |
| HW03 | SHALL NOT | Change `check()`'s per-message `TIME_OUT_R` read: it sits in the write-to-read gap, and shortening that gap needs a capture (`tests/test_libdef_config_reads.py`). Dropped from this change on review. | test: `tests/test_libdef_config_reads.py::LibdefConfigReads::test_set_msg_reads_only_the_read_timeout` |
| HW04 | pass | New checks execute pure field statements and fake endpoints only; full pytest runs use the repository USB backstop and fake controller libraries. Physical behavior not checked. |
| HW05 | pass | Reviewed diff: encoding, timeout cache, tests and documentation only; no git mutation or publishing commands. Bench validation remains pending. |

## Review note (mission control, 2026-11-06)

The `TIME_OUT_R` cache was removed before commit: the existing `test_libdef_config_reads.py` keeps that read on purpose (it is part of the write-to-read gap). The evidence lines above that mention the timeout test describe the run before that removal.
