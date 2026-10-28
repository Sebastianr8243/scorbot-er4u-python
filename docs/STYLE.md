# Documentation style

Purpose: one format for every page, so a reader knows where to look.

## Page types

| Type | Opens with | Model |
|---|---|---|
| How-to | Purpose, Before you start, numbered steps, each with its expected result | A lab procedure: [lab/RUN_THE_ARM.md](lab/RUN_THE_ARM.md) |
| Checklist | Purpose, WARNING, table of steps with Expect, Pass if, Stop if | [lab/LAB_RUN_SHEET.md](lab/LAB_RUN_SHEET.md) |
| Reference | Purpose, then one block per item: Syntax, Inputs, Returns, Refused when, Example | A function reference page: [API.md](API.md) |
| Results note | Purpose, what was run, a table of results, what it does not settle | [DESK_FINDINGS.md](DESK_FINDINGS.md) |
| Requirements | Status, Goal, a table of ID, type, statement and check, Out of scope | [requirements/arm-api.md](requirements/arm-api.md) |

## Rules

- Start with the answer or the action. State the result of a step after the step.
- Put a WARNING or CAUTION line before the step it applies to, never after.
- Write units in every number (mm, degrees, counts, seconds). Say which are measured.
- A claim about the arm that no bench log backs is written "unverified", once, in the table or banner that covers it. Do not repeat it in every sentence.
- Name roles, not people ("the lab operator", "the reviewer"). Do not mention coding tools, reviewers or chats.
- Windows commands are PowerShell, one per line, using `.\.venv\Scripts\python.exe`.
- Requirement IDs are unique across `docs/requirements/`.
- Link to a page instead of copying its table or diagram.

`python scripts/check_docs.py` enforces the mechanical rules above (names, control characters, `.venv` path, duplicate IDs, links).
