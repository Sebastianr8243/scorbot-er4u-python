# Session Analysis Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** `list`, `export`, and `compare` subcommands, built on pure analysis functions, with measures defined once.

**Architecture:** `scorbot/session/analysis.py` holds the pure functions, with no printing. `__main__.py` dispatches subcommands, and a bare path still means replay. Each session is loaded, summarized, and dropped before the next.

**Tech Stack:** stdlib (`csv`, `statistics`, `dataclasses`), plus `scorbot.calibration.signed_count_delta`.

**Spec:** `docs/superpowers/specs/2026-09-25-session-analysis-design.md`

## Global Constraints

- Observed counts use `signed_count_delta` on raw `encoder_counts`. An ambiguous difference is the string `"ambiguous"`.
- Every CSV row carries `session_id` and `data_source`. CSVs are `utf-8-sig`, with formula-safe text cells.
- The pooled comparison never mixes data sources, and leaves out errored sessions unless `--include-damaged` is given.
- `python -m scorbot.session <path>` still replays.
- Python 3.10 compatible. No new dependencies.

## Review Focus

1. **A session whose `/robot/state` payloads lack `encoder_counts` for some joint**, for example synthetic data. Expected: those joints are skipped, with no crash. (Task 1)
2. **`export` on a crashed, unclosed session.** Expected: it exports what was read and prints the warnings. (Task 2)
3. **`list` over a folder containing a non-session `.mcap` or an empty `sessions/`.** Expected: skipped or reported, and the command continues. (Task 2)
4. **`compare` given the same session twice.** Expected: it is counted once. (Task 2)
5. **A command whose `params` holds a non-dict `motor_count_deltas`.** Expected: the planned counts are empty, with no crash. (Task 1)

### Task 1: analysis.py

**Produces:**
- `CommandRecord` (a dataclass)
- `command_records(session)`
- `event_rows(session)`, `state_rows(session)`, `command_rows(session)`
- `event_summary(event)`, moved from `__main__._summary`
- `RunSummary`, built with `summarize(session, path)`
- `Comparison`, built with `compare(summaries, include_damaged=False)`
- `find_sessions(paths)`
- `csv_safe(value)`

- [ ] **Step 1: Write the failing tests** in `tests/test_analysis.py`.
  - A simulated fixture (`SimulatedScorbot`, home, then two base jogs) gives a `count_error` of all 0, and observed counts equal the plan.
  - A command with no result gives `status="no_result"`.
  - A command with no state before or after gets a note and empty counts.
  - A back-to-back pair shares one state.
  - An empty session gives no records.
  - **RF1:** a state missing some joints skips them.
  - **RF5:** `motor_count_deltas` that isn't a dict gives empty planned counts.
  - `summarize` and `compare`:
    - identical sources pool;
    - mixed sources give no pooled row and `exit_code` 1;
    - an errored session is left out and `exit_code` is 1;
    - `include_damaged` puts it back;
    - a warning-only session is included.
  - `csv_safe` prefixes a quote for `=`, `+`, `-`, and `@`, and leaves numbers alone.
- [ ] **Step 2:** Run them. They should fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run them, and the full suite. They should pass.
- [ ] **Step 5:** Commit.

### Task 2: CLI subcommands

- [ ] **Step 1: Write the failing tests.**
  - `list`:
    - finds nested sessions and shows REAL, SIMULATED, and ERROR;
    - exits 1 with a broken session;
    - **RF3:** a stray non-session `.mcap` and an empty folder are reported or skipped.
  - `export`:
    - the headers are exact;
    - the `session_id` and `data_source` columns are present;
    - UTF-8 survives, and a formula-looking note is quoted;
    - it refuses to overwrite without `--force`;
    - **RF2:** a crashed session exports.
  - `compare`:
    - it produces a table;
    - `--csv` writes;
    - mixed sources exit 1;
    - **RF4:** a duplicated path is counted once.
  - A bare path still replays.
  - **Agreement:** `bench_joint --simulate` gives counts in `commands.csv` equal to `review_lab_logs`' `count_deltas`.
- [ ] **Step 2:** Run them. They should fail.
- [ ] **Step 3:** Implement the dispatch in `__main__.py`.
- [ ] **Step 4:** Run the full suite. It should pass.
- [ ] **Step 5:** Commit.

### Task 3: Docs

- [ ] Add an "Analysing sessions" section to `EXPERIMENT_RECORDING.md`, with the measures table and a real `compare` output pasted in.
- [ ] Add a README pointer.
- [ ] Commit, push the branch, and check that CI is green.
