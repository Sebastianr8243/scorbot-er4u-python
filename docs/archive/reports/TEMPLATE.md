# R-NNNN: <short title, what changed>

| Field | Value |
|---|---|
| ID | R-NNNN |
| Date | YYYY-MM-DD |
| Area | one of: protocol, home, motion, safety, sim, session, lab, tooling, docs |
| Evidence | E0 to E5, see [`../STATUS.md`](../STATUS.md); write the level reached by this work, not the goal |
| Reviewed | none, or reviewer and run id (Codex, Gemini), and how many findings were fixed |
| Branch / commits | branch and short hashes |
| Touches | requirement IDs (R01...), hazard IDs (HZ-nn), BACKLOG items |
| Supersedes | earlier report ID, or none |

## Summary
Two or three sentences. What changed and what it means for the goal. A reader who stops here should still know whether the arm is closer to moving.

## Why
The problem or finding that caused this. Cite the log, review or bench result.

## What changed
Bullets, one per file or behaviour. Say what was added, removed or fixed. No diff narration.

## Verification
What was run and what it printed. Each line is `command` then `result, date`. If something was not run, write "not run" and why. Never copy a result you did not see.

| Check | Command | Result |
|---|---|---|
| | | |

## Evidence level and what it does not show
One line for the level reached, then what this work does not prove, in plain words (for example "the reply side is our model; the real controller's acceptance is unverified"). Hardware claims say "unverified" unless a bench log exists.

## Risks and hazards
New or changed risk, by HZ id. Say "none" if none.

## Open items
What is left, who must decide, and where it is tracked (BACKLOG number, issue, owner decision).

## Links
Specs, protocol docs, logs, PR.
