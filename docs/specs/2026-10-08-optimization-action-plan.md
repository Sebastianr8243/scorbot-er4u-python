# Optimization and simulator action plan (2026-10-08)

Status: proposal, nothing built. Companion to [2026-10-08-simulator-realism-plan.md](2026-10-08-simulator-realism-plan.md), which holds the simulator design. This file is the work breakdown: what to do, in what order, who (which model) does it, and what each step must prove before the next one starts.

## 1. What the research and the code say about the USB path

### 1.1 Where the time goes (read from the code, 2026-10-08)

One legacy message is `libdef.set_msg` -> `check` (`openScorbot/libdef.py:430-457`):

```
readData x3 (MSG_LEN, TIME_OUT_W, TIME_OUT_R: each opens and parses data.json)
fill_msg, bytes.fromhex
epout.write(x, 1500)      -> sleep WRITE 8 ms
epin.read(buffer, 1500)   -> sleep READ  5 ms
```

Budget per message: 13 ms of sleep, plus the USB transfers (one bulk OUT and one bulk IN of 64 bytes, each at least one 1 ms frame at full speed; assumption, the bus speed is not logged), plus Python overhead. The overhead is `readData` (at least 3 file open-and-parse calls per message here, about 8 per packet by the BACKLOG 17 count) and a hex round trip. **Nobody has measured the overhead.** Until it is measured, "optimize it" is a guess.

### 1.2 The two ideas you liked, honestly

| Idea | What the code shows | Verdict |
|---|---|---|
| Pre-allocated buffers | The read buffer is already pre-allocated once (`scorbot/robot.py:234`, `usb.util.create_buffer(wMaxPacketSize)`) and reused by every read. The write side builds a 64-byte `bytes` per message, a few microseconds. | Almost nothing to gain. Do not spend a work package on it. |
| Shorter timeouts | `TIME_OUT_W`/`TIME_OUT_R` are 1500 ms. They matter only when the controller does not answer: a dead link blocks the command worker (and a software stop, which acts at the next packet) for up to 1.5 s per packet. On a healthy link no read comes near that. | Real value is **fault-detection latency**, not speed. Safe only if the longest legitimate read is known. That number is unmeasured (needs a packet trace). Also: the values live in `data.json`, and a stale copy wins (HZ-19), so editing `conf.py` changes nothing on a machine that already has the file. |
| (Found while checking) caching `readData` | The file parse sits **before** each write, so removing it shortens the gap between messages by the parse time. That makes the message rate slightly higher than the one the controller is known to accept. | Valuable for jitter (disk and antivirus time varies), risky as a speed-up. Gate it behind measurement and a capture, see WS5. |
| (Found while checking) the fingerprint gap | `provenance._SOURCE_FILES` hashes `libcomm.py`, `libdef.py`, `motion_profile.py` and the scorbot motion files. `conf.py` and `data.json` (WRITE, READ, timeouts, MAX_ERROR) are not hashed, so a changed timing value would not change `motion_source_sha256`. | Cheap, log-only fix (WS6). |

### 1.3 What the vendor DLL tells us (existing disassembly, `docs/protocol/VENDOR_DLL_PROTOCOL.md` section 2)

- The vendor thread loops `WriteFile` (64 bytes), "a wait", `ReadFile` (64 bytes): the same shape as legacy `check`.
- It does **not** use a fixed 8 ms plus 5 ms. It paces on the echoed message ID: when `sent ID - echoed ID` is below a limit (Buffers 45, HomingBuffers 20, ManualBuffers 4, TPBuffers 6) it waits 1 ms, otherwise about half of `PCPeriod`.
- So the legacy sleeps are the legacy authors' choice, not the vendor's. That is the best lead for "what could really change", but it is a different pacing scheme (pacing on the echoed ID is the open item "lab plan V4") and the DLL's "a wait" and its read timeout have not been read yet.
- Rule from `CLAUDE.md` still binds: the sleeps stay as they are until a captured trace or a disassembly-backed change tried first on a 1 degree jog.

### 1.4 Allowed-change ledger (to fill in during WS5)

| Class | Examples | Rule |
|---|---|---|
| A. No effect on the wire or on message spacing | log fields, config hash in logs, header rows, `execute` spin sleep outside a packet | Do on the desk, test-first |
| B. Changes message spacing or failure timing, not bytes | `readData` cache, read/write timeouts | Desk benchmark first, then a captured before/after, 1 degree jog first |
| C. Bytes or the WRITE/READ sleeps | packet fields, sequence bytes, pacing on the echoed ID | Forbidden without a capture or disassembly evidence |

## 2. Work packages

Subagent count stays small (owner preference): at most two running at once, one writer per file set. Models are suggestions; the owner may swap them.

| WS | Package | Class | Model | Depends on |
|---|---|---|---|---|
| WS0 | Measurement harness | A | Sonnet | none |
| WS1 | 10-06 logs analysis and session header | A | Sonnet | none |
| WS2 | Test-suite speed | A | Sonnet | none |
| WS3 | Simulator plant and scenarios (Phases 1, 2 of the simulator plan) | A | Opus (design and plant), Sonnet (tests, scenario files) | WS1 |
| WS4 | Controller-side virtual clock and stateful tests (Phases 3, 4) | A | Sonnet, Opus review | WS3 |
| WS5 | USB path study and change ledger | study only | Opus | WS0 |
| WS6 | Timing config in the fingerprint (log-only) | A | Sonnet | none |
| WS7 | CLAUDE.md slimming | docs | Sonnet | none |
| WS8 | Lab measurement asks added to the capture cards | docs | Sonnet | WS5 |
| WS9 | Independent review at gates | review | Codex and Gemini via the `second-opinion` skill | each gate |

### WS0: measurement harness (Sonnet, about half a day)

Goal: put numbers on the USB path with no hardware.
- `scripts/bench_usb_path.py`: drive `libdef.set_msg` with fake endpoints (as `tests/test_settle_seam.py` does) and time, with `time.perf_counter_ns`, the code between "previous read-sleep ended" and "write called". Report median, p95, max, over 10,000 messages. Run it with `data.json` cold and warm, and with the config read stubbed out, so the prize from caching `readData` is a number.
- The same script reports the sleeps' real duration (`time.sleep(0.008)` actual) on this Python and platform, so we know the real floor on Windows.
- Never opens USB; fake endpoints only. Not part of the unit suite (benchmark), but with one smoke test that it runs with a tiny count.
Accept: a table of microseconds, committed under `docs/evidence/` as text with the Python version and `time.get_clock_info` output.
Decision rule for WS5: if the removable overhead is under about 5 percent of the 13 ms sleep floor, record "not worth the risk" for class B speed-ups and keep only the jitter argument.

### WS1: logs analysis and session header (Sonnet, 1 to 2 hours)

- Script plus `docs/evidence/2026-10-06-lab-logs/ANALYSIS.md`: latencies (disable about 656 ms, enable about 296 ms), the home failure after 4.2 s with code 1 and no rows in between, the power-on counts near zero, the 15.6 ms quantization of `host_monotonic_ns`. Every number reproducible by the script.
- Add Python version, platform and `time.get_clock_info("monotonic")` to the lab session header row in `scorbot/lab` (not `robot.py`). Test-first.
Accept: the script prints the table; a test pins the header fields.

### WS2: test-suite speed (Sonnet, 2 to 3 hours)

Measured 2026-10-08: 1097 passed, 10 skipped in 188 s with `pytest -n 2`; slowest tests are three in `test_inch_home.py` (7.7 to 12.7 s), then a flat tail of 2 to 5 s tests. Low RAM on the desk machine: cap workers.
- Make `pytest -n 2` (not `auto`) the documented fast loop; try `--dist worksteal` and keep it only if it helps.
- Find the sleeps behind the slow tail (`simulated.py` has 0.3 s, 0.5 s and a 3 s rehearsal homing profile; `test_inch_home.py` probably uses `step_delay_s`) and list them. Do not change the simulator here; that is WS4's virtual clock.
Accept: a before/after table; unittest discovery still passes (CI uses unittest).

### WS3: simulator plant and scenarios (Opus designs, Sonnet tests)

Follow phases 1 and 2 of the simulator plan exactly, including the rule that acceptance tests use a plant whose world differs from what the code believes (reversed wrist sign, coupling 20 percent off, scale off, wrong home offset), asserting that an existing safeguard faults within N degrees. Default profile unchanged; all existing tests untouched.
Accept: new tests pass; full suite unchanged; every new parameter carries `status = "modeled, not measured"`.

### WS4: virtual clock and stateful tests (Sonnet, Opus reviews)

Controller side only: `robot.py` and `streaming.py` are in the motion fingerprint and `Stream.source` paces in real time, so they are not touched before the next lab visit. Then Hypothesis `RuleBasedStateMachine` per simulator plan phase 4 (Hypothesis is optional here; tests skip without it).
Accept: a 10-second simulated stream runs in under a second of real time; the state machine catches a deliberately broken gate; runs in CI in under 60 s.

### WS5: USB path study and change ledger (Opus, desk only, no code in `openScorbot/`)

1. Inventory every `time.sleep`, timeout, `readData` call and loop in `openScorbot/libcomm.py`, `libdef.py`, `libsync.py`, `setHome.py` into the ledger of section 1.4, with line numbers.
2. Read, with the existing Ghidra tooling (`tools/usbc_analysis/README.md`; never commit the DLL or decompiled output), what the DLL's "a wait" is, what its `ReadFile` timeout is, and whether the handle is opened overlapped. Write findings with the repo's evidence labels (V verified in code, I inferred) in a new section of `docs/protocol/VENDOR_DLL_PROTOCOL.md`.
3. Using WS0's numbers, write the math: per-message budget, the largest jitter source, the cost of a dead-link detection at 1500 ms versus a candidate value, what the stream's 24 ms step leaves for the host. State the unknowns (bus speed, longest legitimate read).
4. For each candidate (cache `readData`, shorter timeouts, `execute` spin sleep) give: class, expected gain with the number, risk, the exact lab evidence needed, the test that pins it.
Accept: a ledger a reviewer can check line by line; no candidate is implemented in this package.

### WS6: timing config in the fingerprint (Sonnet, 1 to 2 hours)

Log-only: compute a hash of the effective timing and limit values (`WRITE`, `READ`, `TIME_OUT_W`, `TIME_OUT_R`, `MAX_ERROR`, `MSG_LEN`) and write it, with the values, into the session header and next to `motion_source_sha256` in lab logs. Do not change `provenance._SOURCE_FILES` semantics (that would change every existing hash): add a second field. Test-first; also shows a stale `data.json` (HZ-19) in the log.
Accept: a test that changing a value in a temporary `data.json` changes the new hash and not the old one.

### WS7: CLAUDE.md slimming (Sonnet, 1 to 2 hours)

Move each owner override's history out of the safety table into `docs/design/SAFETY_CASE.md`; keep one line per invariant with its test names. No rule may be lost: the agent lists every sentence it removed and where it went. Owner reads the diff before commit.

### WS8: lab measurement asks (Sonnet, after WS5)

Add to the lab cards what the next visit must record so class B changes can be decided: IN-read latency distribution from a packet trace, message spacing, Python version and clock header, and one idle capture with the unmodified code as the baseline.

### WS9: independent review

Use the `second-opinion` skill at these gates: after WS3 (plant), after WS5 (ledger, since it argues for changes near the motion path), before any class B change. Treat findings as input to verify.

## 3. Order and gates

```
Week 1 (desk):  WS0 -> WS5 ; WS1, WS2, WS6, WS7 in parallel with them (small, independent)
Week 2 (desk):  WS3 -> WS4 ; WS8 after WS5
Lab visit:      baseline capture and header only (WS8); no change to any timing
Week 3+ (desk): class B change, only if WS0/WS5 say the prize is real,
                implemented behind a flag, tried on a 1 degree jog first
```

Gates:
- **G-A (after WS0):** is the removable overhead a real fraction of the 13 ms floor? If not, class B stops at "jitter only".
- **G-B (after WS5 and a lab capture):** is the longest legitimate IN read known? Only then can a shorter timeout be proposed.
- **G-C (any class B change):** captured before/after trace, a flag to switch it off, the 1 degree jog first, a second-opinion review.

## 4. Rules for every agent

- No USB, ever. Use the simulator or fake endpoints. Do not run any lab script without `--simulate`.
- Do not edit `openScorbot/` packet construction, sequence bytes or the `WRITE`/`READ` sleeps. WS5 produces a ledger; it does not change those files.
- Python: `.venv/Scripts/python.exe`. Tests: `python -m unittest discover -s tests` or `pytest -n 2` (RAM). Read the `CLAUDE.md` of the folder you edit first. Match tab/space style in legacy files.
- Test first for every behaviour change; show the failing run.
- Say "unverified" about anything hardware-related unless a trace exists; keep the SIMULATED labels.
- Commit only when asked, on a branch, imperative subject, no AI attribution lines.
- Report what you did not do or could not verify.

## 5. Prompt template for a subagent

> You are doing WS<N> of `docs/specs/2026-10-08-optimization-action-plan.md`. Read that section, section 4 (rules) and the folder `CLAUDE.md` files you touch before editing. Scope: <files>. Out of scope: everything else, especially `openScorbot/` packet code and the `WRITE`/`READ` sleeps. Deliver: <accept criteria from the section>. Run the tests named there and paste the real output. List anything you skipped or could not verify. Do not commit.

## 6. Sources

- Python `time` docs, Windows clock and `sleep` notes: https://docs.python.org/3/library/time.html
- Windows timer resolution discussion: https://discuss.python.org/t/higher-resolution-timers-on-windows/16153
- libusb synchronous I/O and timeouts: https://www.cs.unm.edu/~hjelmn/libusb_hotplug_api/group__syncio.html
- libusb1 sync transfers, buffer notes: https://tessl.io/registry/tessl/pypi-libusb1/3.3.0/files/docs/sync-transfers.md
- Hypothesis stateful testing: https://hypothesis.readthedocs.io/en/latest/stateful.html
- pytest-xdist how-to: https://pydevtools.com/handbook/how-to/how-to-run-tests-in-parallel-with-pytest-xdist/
