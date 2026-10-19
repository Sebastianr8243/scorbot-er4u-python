# Hardware gate leftovers (Track A) requirements
Status: baseline v2 2026-10-19

## Goal
Close the last paths where the real arm can be reached without the operator's `SCORBOT_HARDWARE=1` switch, make an agent ask Sebastian before it sets that switch, and give agents ruff feedback on every Python edit. Users: Sebastian and the agents (Claude, Codex) working in this repo.

Already done on `lab-hardware-switch` and not repeated here: real `Scorbot.connect()` refuses without the switch (`tests/test_hardware_switch.py`), `usbc_home --online` and `usbc_jog --online` refuse without it (`tests_extra/test_usbc_home.py`, `tests_extra/test_usbc_jog.py`, `test_online_needs_the_hardware_switch`), and the root `conftest.py` clears the switch and blocks real USB discovery under pytest.

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| HG01 | SHALL NOT | The legacy GUI (`openScorbot/gui.py`) looks up, resets or claims the USB device when `SCORBOT_HARDWARE` is not exactly `1`. | test: `pytest tests/test_gui_hardware_switch.py -k switch_off` (USB find, reset and claim are never called) |
| HG02 | SHALL | With the switch off, the GUI says why it is offline, naming `SCORBOT_HARDWARE`, and then behaves as it does today when no device is found (offline). | test: `pytest tests/test_gui_hardware_switch.py -k message` |
| HG03 | SHALL | With the switch on, the GUI's connection path is unchanged: it calls USB discovery as before. | test: `pytest tests/test_gui_hardware_switch.py -k switch_on` |
| HG09 | SHALL NOT | `tools/usbc_move --online` loads the vendor DLL or writes a log when `SCORBOT_HARDWARE` is not exactly `1`. | test: `pytest tests_extra/test_usbc_move.py -k hardware_switch` |
| HG04 | SHALL | Claude asks Sebastian before running a shell command that sets `SCORBOT_HARDWARE`: Bash `SCORBOT_HARDWARE=… cmd` and `export SCORBOT_HARDWARE=…`, PowerShell `$env:SCORBOT_HARDWARE = …` and `[Environment]::SetEnvironmentVariable('SCORBOT_HARDWARE', …)`, cmd `set SCORBOT_HARDWARE=…`. Codex gets the same rule, where an ask becomes a block. | test: `cd ~/.agents/scripts/lib && node --test guard.test.mjs codex-guard.test.mjs` (one case per form above) |
| HG05 | SHALL NOT | The guard asks on commands that only read or mention the variable (`grep SCORBOT_HARDWARE`, `echo $env:SCORBOT_HARDWARE`, `Select-String`, `git log`). | test: same `node --test` run, one case per form |
| HG06 | SHALL | After Claude edits or writes a `*.py` file in this repo, Claude sees `ruff check` findings for that file within 10 s, in the same turn. | observable: add `import os` unused to a file under `scorbot/`; Claude sees an `F401` line for it; then remove it |
| HG07 | SHALL NOT | The ruff hook changes a file (no `--fix`, no format), blocks an edit, or prints an error when ruff is missing, the file is not Python, or the file is excluded by `pyproject.toml` (`openScorbot/`). | observable: after HG06, `git diff` shows only Claude's edit; an edit to `openScorbot/gui.py` and to a `.md` file produces no ruff output |
| HG08 | SHALL | The repo's verify command still passes with nothing plugged in. | script: `ruff check . && python -m compileall -q scorbot openScorbot scripts examples tests && python -m pytest -n 2 -q tests` exits 0 |

## Out of scope
- Deliberate bypass: Sebastian, or anyone, setting the switch in their own terminal. The gate stops accidents, not intent.
- Copilot on the lab PC: governed by `.github/copilot-instructions.md`, not by the guard hook.
- `scorbot/preflight.py`: it only enumerates the device (no reset, claim or command), and lab steps use it before connecting.
- `scorbot/transport/device_file.py`: not wired into any entry point yet.
- A ctypes DLL load or a plain `python -m unittest` run under tests (the `conftest.py` docstring already says so).
- Pushing, a PR to `main`, or running anything against the real arm.

## Assumptions
- `openScorbot/gui.py` is still launched sometimes. If it is dead code, HG01 to HG03 could become "delete or leave it" instead.
- Agents run in Bash or PowerShell through Claude Code or Codex, so `guard.mjs` and `codex-guard.mjs` see every command.
- A worktree may have no `.venv`; the hook finds ruff through `uv` or the venv, and stays quiet when it cannot (HG07).

## Open questions
- None blocking. Proposed change to the root file, not made: root R01's Check says "no test-time guard blocks a deliberate USB call", which `conftest.py` now partly does. Update that row when the root file is next baselined (Sebastian approves).

## Change log
- 2026-10-19 draft written from the `/assume` round (Sebastian: all ok, #5 explained, committed settings file).
- 2026-10-19 baseline v1, approved by Sebastian.
- 2026-10-19 v2: HG09 added. `usbc_move` (merged to main in #23 after the switch was built) reached the DLL without the switch; found while replaying the switch onto main. Approved by Sebastian.
