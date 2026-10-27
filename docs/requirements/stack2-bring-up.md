# Stack 2 desk bring-up requirements
Status: baseline v1 2026-10-26 (Sebastian's task brief and follow-up)

## Goal
Rehearse the Stack 2 connection and lab ladder without USB, then provide a supervised lab procedure. Hardware behaviour remains unverified.

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| B01 | SHALL | Fake endpoints accept 64-byte writes, fill IN buffers, model configurable reply latency and run the unchanged legacy connection. | test: `tests/test_dry_link.py` |
| B02 | SHALL | Probe uses the complete connection handshake, decodes one state and closes; it warns that connecting briefly energises then releases motors. | test: `tests/test_transport_probe.py` |
| B03 | SHALL | Ladder runs probe, enable, Home, one 1 degree jog and back, then optional sine stream; vendor Home is default, legacy Home explicit. | test: `tests/test_bring_up.py` |
| B04 | SHALL NOT | Real mode opens a link without presence and exact typed confirmations; any surprise permits later ladder motion. | test: `tests/test_bring_up.py` |
| B05 | SHALL | JSONL is exclusively created before connection and flushed first; every simulated line and row says SIMULATED. | test: `tests/test_bring_up.py` |
| B06 | SHALL NOT | Legacy packet bytes, sleeps, limits or dependencies change. | script: `git diff -- openScorbot pyproject.toml uv.lock scorbot/limits.py` prints nothing |
| B07 | SHALL | Runbook gives commands, PASS criteria, abort rules and unresolved driver/stream questions in fewer than 100 lines. | observable: read `docs/lab/BRING_UP_RUNBOOK.md`; run documented simulated command |

## Out of scope
- Real USB/device access during implementation or verification; commits and pushes.
- New motion capabilities, controller timing claims or mixed real/simulated comparisons.

## Assumptions
- Plant replies support status polls; simulated timing is a configurable model only.
- Existing SDK gates remain authoritative.

## Open questions
- Driver cancellation and controller stream-loss behaviour require bench evidence.

## Change log
- 2026-10-26 Sebastian specified Home before jog, vendor Home by default, and a full-handshake probe.

## Verification (2026-10-26)
| IDs | Result | Evidence |
|---|---|---|
| B01-B05 | pass | Full suite: 1467 passed, 1 skipped, 12315 subtests passed in 99.29 s; includes all three new test modules. |
| B06 | pass | `git diff -- openScorbot pyproject.toml uv.lock scorbot/limits.py` empty; Ruff and compileall exit 0. |
| B07 | pass | Runbook 85 lines; default simulated CLI and legacy Home with adaptive stream both exit 0 with PASS at every step. |

Shared venv, `PYTHONPATH` set to the worktree. The requested pytest invocation failed
before collection: sandbox denied `%TEMP%/pytest-of-sebas`. The full successful retry
added `--basetemp .pytest-stack2-full -o cache_dir=.pytest-stack2-cache` only.
Claude read-only review produced no output in three minutes and was stopped: no review result.
No USB/device was opened, no real motion run, no commit or push. Hardware remains unverified.
