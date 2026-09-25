# Toolset upgrades: test tooling, observation sheets, plots

**Status:** Approved direction. The user asked to build all four ideas with
existing, tested tools. Research was done by three subagents (Sonnet and
Haiku), summarised below.
**Date:** 2026-09-26
**Scope:** No arm and no targeting work. Three independent parts, built and
merged in order.

## Part 1: Test tooling

| Need | Tool (pinned) | Decision |
|---|---|---|
| Speed | `pytest>=9.0,<10`, `pytest-xdist>=3.8,<4` | pytest runs the existing `unittest` tests unchanged; `-n auto`. Measured by the research agent: 51 s → 16.4 s on Windows. `python -m unittest discover -s tests` must keep working. |
| Hangs | `pytest-timeout>=2.4,<3` | `timeout = 300`, `timeout_method = "thread"`; Windows has no SIGALRM. A hang becomes a traceback. |
| Coverage | `coverage[toml]>=7.6,<8` | `[tool.coverage.run] source = ["scorbot"]`, `patch = ["subprocess"]` (7.10+) so that example scripts run by tests are measured, and `parallel = true`. In CI: `coverage combine`, then `coverage report --format=markdown >> $GITHUB_STEP_SUMMARY` with `shell: bash`. No external service. No failing threshold yet; report only. |
| Edge cases | `hypothesis>=6.100,<7` | Property tests for `encode_packet`/`decode_state` round-trip, `signed_count_delta` (antisymmetry, and consistency with `(a-b) % 65535`), `csv_safe` (it never changes non-strings), and `increments_for_counts` (sums to counts, every step ≤ speed). |
| Lint | `ruff>=0.6,<1` | `ruff check` on `scorbot/`, `scripts/`, `examples/`, `tests/`, with the default rules (E4, E7, E9, F) only. `openScorbot/` (legacy) is excluded. |

- **Install:** a `dev` extra, `pip install -e ".[dev]"`.
- **README:** "Development checks" gains the one-line fast command.

**Pin note:** the research agent quoted pins narrower than a minor version,
for example `coverage>=7.16,<7.17`. Those lock out bug-fix releases, so this
design uses major-version ranges and records what CI actually resolves.
Ruff stays under 1.0 because its 0.x releases can change rules.

## Part 2: Structured observation sheet

**Format.** `notes.md` stays Markdown and editable by hand, with a block of
`Label: answer` lines. It is parsed with the standard library only (no YAML:
YAML 1.1 turns `14:30` into 870 and `no` into False).

```markdown
# Session <id> observation sheet
notes_schema: 1

Date / time:
Stop operator:
Recorder:
E-stop tested before start:
Start pose matches photo:
Observed direction:
Other joints moved:
Python returned normally:
How run ended:
Discrepancies:
Reviewed by:

## Free notes
```

**Parsing** (`scorbot/session/notes.py`):
- The file is read as `utf-8-sig`, which strips the BOM Notepad adds, and
  split with `splitlines()`, which handles CRLF.
- Only lines before `## Free notes` are fields.
- A field line matches `^([^:#]+?):\s*(.*)$`. The label is compared against
  a fixed label→key map, ignoring case and extra spaces.
- Unknown labels are kept and reported.

**Values:**
- Blank means unfilled.
- `n/a` means not applicable, and `not sure` is an accepted answer.
- **`How run ended`** must be one of `normal return`, `declined prompt`,
  `emergency stop`, `error`, or `other: <text>`.
- `E-stop tested before start`, `Start pose matches photo`, and `Other
  joints moved` must be `yes`, `no`, `n/a`, or `not sure`.

**Required fields:** date/time, stop operator, recorder, e-stop tested, start
pose matches, how run ended, and discrepancies. The rest are optional.
`Reviewed by` is tracked separately as "reviewed" or "not reviewed yet".

**Status**, one of:
- `missing`: no `notes.md`;
- `untouched`: identical to the template;
- `incomplete`: a required field is blank or invalid;
- `complete`.

A sheet with no `notes_schema` line counts as legacy: it is reported, never
an error.

**Where it shows up:**
- **Replay** prints the sheet status.
- **`list`** gets a `NOTES` column.
- **`export`** writes `notes.csv`: one row per field plus `session_id` and
  `data_source`.
- **`compare`** adds `notes` and `run_ended` columns.
- **The session integrity check stays about the recording.** Notes are
  reported separately, as a *warning* for `data_source="real"` sessions
  only. Simulated rehearsals don't nag about paperwork.

**The G1 checklist** observation-sheet table is updated to the same labels,
so the printed sheet and the file match.

## Part 3: Plots and a viewer layout

- **`matplotlib>=3.8,<4`** as an optional `plot` extra. When it is missing,
  the command explains `pip install -e ".[plot]"`.
- **`python -m scorbot.session plot <session-or-folders>... --out DIR`**,
  following the dataviz skill's guidance. It writes PNGs:
  1. `counts_<session>.png`: signed encoder counts against logged time for
     each motor, with command start and result marked as spans.
  2. `count_error.png`: `count_error` for each command and motor across
     sessions, as a strip or dot chart.
  3. `durations.png`: `recorded_duration_ms` for each command kind across
     sessions, labelled "recorder-observed, includes script overhead".
- **Every figure's title carries the data source** (REAL, SIMULATED, or
  SYNTHETIC). Plots that would mix sources are refused, following the same
  rule as `compare`.
- **Headless:** `matplotlib.use("Agg")`. Tests check file output, axes,
  labels, and data extents, with no pixel diffs.
- **`layouts/session_review.json`:** a Foxglove/Lichtblick layout with an
  Image panel, Plot panels for `/robot/state.encoder_counts.*`, and a Raw
  Messages panel for commands and faults. A test validates that it is JSON
  and that its topics exist. Rendering can only be checked by hand in the
  viewer, and that is the user's acceptance step.

## Out of scope

- plotly (image export needs `kaleido`, about 200 MB);
- PlotJuggler (a separate GUI app);
- Rerun (its MCAP support is experimental);
- YAML front matter;
- Codecov;
- a coverage threshold gate.
