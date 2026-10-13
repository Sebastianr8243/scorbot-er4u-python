# R-0001: Project re-baseline: status page, reports, requirements

| Field | Value |
|---|---|
| ID | R-0001 |
| Date | 2026-10-13 |
| Area | docs |
| Evidence | n/a, documentation only; no code or behaviour changed |
| Reviewed | none |
| Branch / commits | `project-status-reports` |
| Touches | all requirements (adds the file); no hazards |
| Supersedes | none |

## Summary
The project had a lot of detail (a 711-line log, 71 backlog rows, 27 specs) and no single page saying what works and how well we know it. This adds that page, a fixed-shape report format with an index, a `project-historian` agent definition, and a draft requirements baseline. Nothing here moves the arm closer; it makes the distance visible.

## Why
Recent work (vendor-home port, fake-controller rig, code health) piled up faster than it could be summarised, and the code is well ahead of the hardware evidence. The owner wanted a re-baseline with a structure that keeps later updates reportable.

## What changed
- `docs/STATUS.md`: capability table with an evidence level per row, the critical path to the lab, open owner decisions.
- `docs/reports/`: `TEMPLATE.md`, `INDEX.md` (the dictionary) and this report. No older work was backfilled; `PROJECT_LOG.md` stays the record before this date.
- `.claude/agents/project-historian.md`: the agent that writes a report after each piece of work and keeps `INDEX.md` and `STATUS.md` current.
- `REQUIREMENTS.md`: draft, not baselined; a requirement row changes only with the owner's approval.
- `docs/README.md`: links to the above.

## Verification
| Check | Command | Result |
|---|---|---|
| Gate suite | `python -m pytest -q -n 2 tests` (project `.venv`, new worktree) | 1011 passed, 11471 subtests, 67 s, 2026-10-13 |
| Each REQUIREMENTS check R01 to R08, R13 to R16 | the pytest commands in the file | all passed, 2026-10-13 |
| R10 | `git ls-files` grep for pdf and dll | printed nothing |
| R09, R11, R17 (review or lab checks), links in the new docs | not run | |

## Evidence level and what it does not show
Documentation only. The STATUS levels were assigned from existing docs and memory of past reviews, not by re-running capabilities today. Only the REQUIREMENTS test checks and the gate suite were run. STATUS keeps test counts out so it does not go stale.

## Risks and hazards
None. The risk is staleness: STATUS goes wrong if reports are written without updating it.

## Open items
- Owner to baseline or edit `REQUIREMENTS.md`; open questions are listed in the file.
- R17 and a link check were not run.

## Links
[`../STATUS.md`](../STATUS.md), [`../../REQUIREMENTS.md`](../../REQUIREMENTS.md)
