# Session analysis tools: design

**Status:** Approved direction ("both: toolset and tests"), 2026-09-25
**Implements:** System design §2 goal 6: compare repeated runs using defined
measures (completion, position error, timing). Also ARCHITECTURE §16:
"Export a flattened CSV for experiments".

## 1. Purpose

Sessions can be recorded and replayed one at a time. A researcher also needs
to:

1. **Find** every session in a folder, and see at a glance which are real,
   simulated, or broken.
2. **Export** a session to CSV, to open in Excel or pandas without learning
   MCAP.
3. **Compare** repeated runs with defined, reproducible measures.

No arm and no camera are required. Everything is tested on simulated and
synthetic sessions.

## 2. Measures (defined once, used everywhere)

A **command record** joins one `/robot/command` with its
`/robot/command_result`, and with the robot states just before and just
after it:

| Field | Definition |
|---|---|
| `status` | The result's status, or `"no_result"` if none was logged |
| `recorded_duration_ms` | Result `logged_monotonic_ns` minus command `logged_monotonic_ns`, in ms. Empty if there is no result. This is the **recorder-observed** duration: it includes script overhead such as a state read or a file write. It is **not** how long the arm took to move. |
| `state_before_seq` | The last `/robot/state` **logged** before the command |
| `state_after_seq` | The first `/robot/state` **logged** after the result |
| `observed_counts` | For each motor, `scorbot.calibration.signed_count_delta(after.encoder_counts, before.encoder_counts)`, the **same function `review_lab_logs.py` uses**, so both tools report identical numbers for a run. An ambiguous difference is reported as `"ambiguous"`, as in the review tool. |
| `planned_counts` | `params["motor_count_deltas"]` when the command recorded it, as the lab scripts do. Otherwise empty. |
| `count_error` | For each motor in `planned_counts`, observed minus planned. Skipped when the observed value is ambiguous. |
| `state_gap_ms` | On **logged** time only: the result's `logged_monotonic_ns` to the after-state's. A state is often read before its result is logged, so the gap can be negative. |

Only states recorded **between** the previous command's result and the
next command count as "before" and "after". A state that belongs to
another command is never borrowed. With no qualifying state, the counts
are empty and the record says why (`"no state before"` or
`"no state after"`).

**Run summary** (one per session): `data_source`, command counts by
status, integrity (errors and warnings), and for each command kind:
- `n`;
- the median and max of `recorded_duration_ms`;
- the mean absolute and max absolute `count_error` per motor.

**Comparison:** a table with one row per session plus a pooled row, per
command kind. The pooled row never mixes sources:
- If the sessions have different `data_source` values, the pooled row is
  replaced by a warning line, and the exit code is 1.
- Sessions with integrity **errors** are left out of the pooled row and
  listed by name, and the exit code is 1. Sessions with only warnings,
  such as a crashed or Ctrl-C'd run, are included. `--include-damaged`
  includes the errored ones on purpose.

## 3. CLI (backward compatible)

`python -m scorbot.session <path>` keeps working as replay. New subcommands:

- **`python -m scorbot.session list <folder>`** finds every `session.mcap`
  underneath. For each it prints the session ID, start time, data source,
  robot, task, event count, and status (`OK`, `WARN n`, or `ERROR n`).
  Newest first. It exits 1 if any session has errors.
- **`python -m scorbot.session export <session> [--out DIR]`** writes three
  files, by default into `<session>/csv/`:
  - `events.csv`: seq, topic, `logged_ms`, `observed_ms`, summary;
  - `states.csv`: seq, `logged_ms`, `observed_ms`, the signed and raw count
    for each motor, `home_switch_bits`, enabled, homed, fault, simulated;
  - `commands.csv`: one row per command record.
  Times are milliseconds from the session's first event. The files are
  UTF-8 with a BOM so Excel opens them correctly. The command refuses to
  overwrite unless given `--force`.
  - **Every row of every CSV has `session_id` and `data_source` columns,**
    so real and simulated rows stay distinguishable after concatenation
    (R-09).
  - **Free-text cells are made safe for Excel.** A cell starting with `=`,
    `+`, `-`, or `@` gets a leading `'`, so a note like "-1 deg" isn't
    read as a formula. This applies to text only, never to numbers.
- **`python -m scorbot.session compare <session-or-folder>... [--csv FILE]`**
  prints the run summaries and the comparison table, and can also write
  the table to CSV.

A path whose first word is not a subcommand is treated as replay. To
replay a folder literally named `list`, use `replay list`.

## 4. Components

- `scorbot/session/analysis.py`, pure functions with no printing:
  - `command_records(session) -> list[CommandRecord]`
  - `state_rows(session) -> list[dict]`
  - `event_rows(session) -> list[dict]`
  - `summarize(session) -> RunSummary`
  - `compare(summaries) -> Comparison`
  - `find_sessions(folder) -> list[Path]`
- `scorbot/session/__main__.py`: the subcommand dispatch and printing.
- Standard library only (`csv`, `statistics`). No pandas.
- **Memory:** `list` and `compare` load one session at a time and keep
  only its summary, never a list of `Session` objects.

## 5. Testing

- **Fixtures come from the real stack:** `SimulatedScorbot` driven by a
  small helper that logs a state, then a command with `motor_count_deltas`,
  then the result, then a state. For simulated jogs, `count_error` must be
  exactly 0 and the observed counts must equal the plan.
- **Degenerate cases:**
  - a command with no result;
  - a command with no state before or after;
  - two commands back to back with a single state between them (that state
    is "after" for the first and "before" for the second, which is allowed,
    and is recorded as shared);
  - an empty session;
  - a session with integrity errors.
- **CSV:** the headers are exact, rows round-trip through `csv.DictReader`,
  a UTF-8 note survives the round trip, and the command refuses to
  overwrite without `--force`.
- **`list`:** it finds nested sessions, shows real, simulated, and broken
  statuses, and exits 1 when one session is broken.
- **`compare`:**
  - it pools identical data sources, and refuses to pool mixed ones
    (warning line, exit 1);
  - it leaves an errored session out of the pooled row (exit 1) unless
    `--include-damaged` is given;
  - a warning-only session is pooled.
- **Agreement with the lab review:** run `bench_joint.py --simulate`. The
  planned and observed counts for its jog, in both `commands.csv` and
  `compare`, must equal `review_lab_logs.py`'s `count_deltas` for that
  run's JSONL.
- **Backward compatibility:** `python -m scorbot.session <dir>` still
  replays.

## 6. Non-goals

Plots, notebooks, LeRobot export, calibration-aware angles (G2), and any
camera-derived measure (G3).
