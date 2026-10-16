# scripts/

Offline analysis tools and Windows setup. None of the Python scripts open USB or command the arm; keep it that way. Root rules in [../CLAUDE.md](../CLAUDE.md) apply.

A new script gets a row in the table below and in the README's "Scripts and examples" table, in the same PR.

| Script | Purpose | Reads | Notes |
|---|---|---|---|
| `fit_calibration.py` | Fit per-arm counts/degree from physical angle measurements | measurements CSV, limits JSON | Writes with `open("x")` (refuses to overwrite). Prints scale warnings vs legacy scale; warnings never block |
| `build_calibration_csv.py` | Turn guided lab session logs plus physical angle readings into the calibration CSV that `fit_calibration.py` wants (`docs/lab/PHYSICAL_CALIBRATION.md`). The logs give the encoder counts; you give the angle read off the level or protractor for each step | session JSONL logs, a readings file (`log,point,joint,role,physical_angle_deg,requested_target_deg`) | Standard library only. Never opens USB |
| `make_vendor_home_fixture.py` | Reduce a fake-controller recording of the vendor's Home to the golden fixture `tests/test_vendor_home.py` compares against (`tests/fixtures/vendor_home_trace04.json`): every non-`0D` command, with the `0D` setpoint stream dropped and repeats collapsed, plus a `recipe` of the part that does not depend on how many cycles a wait took | the JSONL a named-pipe fake controller wrote while `USBC.dll` ran `Home('A')` | Standard library only. The vendor binaries and the raw recording are never committed; the fixture holds command bytes and numbers, not Intelitek code |
| `review_lab_logs.py` | Structural and count checks on lab JSONL, including stream trials (`review_stream`: required rows, LED steps, the recorded verdict) | `--idle` file, `--bench`, `--stream` and `--gripper` files (repeatable; a stream or gripper record given as `--bench` is reviewed as what it is) | Exit 1 if any problem. Never a safety verdict (`physical_review_required` is always true) |
| `watch_lab_log.py` | Read-only live view of a running lab script from a second terminal | the run's `--output` JSONL and `<stem>.controller.jsonl` | Imports no controller code. `--once` prints one snapshot |
| `usb_trace.py` | Parse USBPcap `.pcapng`/`.pcap`; `summary`, `export`, `compare`, `setpoints` | capture files | Stdlib plus `scorbot.state` and `scorbot.calibration.signed_count_delta`. Runbook: `docs/lab/USB_CAPTURE.md` |
| `home_trace_report.py` | Read a legacy home search back: one line per controller reply (counts, position-error words, switch bits, collision flag) and a summary (error crossings, travel, collision) | the `<run>.controller.jsonl` event log (its `motion_trace` row with `joint: "home"`) | Stdlib plus `scorbot.state` and `signed_count_delta`. Exit 1 if the log has no home trace. Written 2026-10-08 for the 10-06 failure; never run on the arm |
| `replay_vendor_home.py` | Feed a capture's real IN replies to `scorbot.vendor_home` and report the first message where our OUT command differs from the vendor's (cmd, mask, arg16, arg32; not the sequence byte or setpoints) | JSONL from `usb_trace.py export`, OUT and IN rows of one device, alternating | Stdlib plus `scorbot.vendor_home`. Written 2026-10-11 for the first capture of the vendor home on the arm; only run on synthetic data so far. A difference may be a shifted wait, not a wrong command: read the neighbours. Exit 1 on a difference |
| `hand_sweep_report.py` | Read a motors-off recording of one joint moved by hand: start-relative min, max and end counts per joint, the largest step between samples, every home-switch bit as two edge intervals (on between a and b, off between c and d) against its joint's counts, and counts per degree from two phone-level angles (`--angles joint=start:end`) | an idle JSONL from `examples/record_raw_state.py` (real data only; simulated is refused) | Stdlib plus `scorbot.state` and `signed_count_delta`. Written 2026-10-11; only run on synthetic data and the 10-06 idle log (no motion). Warns on motors enabled, skipped jumps, unknown switch bits and coarse sampling (the recorder is 10 Hz at most, so sweep slowly). Prints a NOTE for the wrist roll switch: the vendor home needs it to stay on at least `ROLL_MIN_ON_COUNTS` (800) counts, a prediction from the arm model (`tests/test_vendor_home_geometry.py`). Exit 1 on any warning, 2 on bad input |
| `vendor_check.py` | Compare an exported trace with the layout read from the vendor DLL; one line per claim (matches, CONTRADICTED, not seen) | JSONL from `usb_trace.py export` or `from-log` | Stdlib only. The layout is from disassembly (`docs/protocol/VENDOR_DLL_PROTOCOL.md`); a match is evidence about that trace, never a safety verdict. Exit 1 on a contradiction |
| `export_policy_dataset.py` | Turn `policy_loop.py` records into a training dataset: `data.npz` (per frame: `observation_state` and `observation_counts`, `action` = the clamped target position, `clamped`, `timestamp`, `episode_index`, `frame_index`) plus `meta.json` (fps, motors, units, per-episode provenance, refused runs). One run is one episode | policy_loop JSONL files | NumPy only, no LeRobot (a LeRobot writer would be a second step in its own environment). Refuses failed runs, runs with policy errors, anything that is not a policy run, and, for the whole export, mixed real and simulated data or mixed rates. Never overwrites the output folder. Reloads what it wrote and compares it with the source rows; exit 1 on any difference. Degrees are the vendor prior, labelled UNCALIBRATED. Written 2026-10-16; only run on simulated records |
| `lab_summary.py` | One page from lab-day logs (idle, jog, stream, policy): which way the counts went for a positive request, counts per commanded degree against the vendor prior, counts per measured degree with `--phone joint=deg`, and every reviewer problem. Written 2026-10-16; only run on hand-made records and simulated runs | the lab JSONL files; it tells the kinds apart itself | Reuses `review_lab_logs.py`. A lead to check against what you saw, never a safety or calibration verdict. Exit 1 on any problem |
| `lab_day.ps1` | Runs the steps of `docs/lab/LAB_DAY_ONE_ARM.md` in order: USB check, idle, three jogs, stream, policy run, export. Holds the labels and flags, picks a free log name per step, waits for a typed `GO` before every step that connects to the arm, stops at the first failure. `-DryRun` prints the commands, `-Simulate` runs the whole flow on the simulator, `-From N` resumes. Written 2026-10-16; rehearsed only with `-Simulate` | repo files | **Starts scripts that move the arm**, only through `examples/*.py` and their own prompts; it adds no checks and no motion of its own. Never run by tests, CI or an agent without `-DryRun` or `-Simulate` |
| `build_bench_kit.py` | Zip the working tree for the Windows PC into `dist/` | repo files | Includes all of `references/` (no PDF exclusion) and `dist/usb-tools/zadig-2.9.exe` if present. Check `references/` before running |
| `setup_windows.ps1` | Create `.venv`, `pip install -e ".[windows,test]"`, `pip check`, run unit tests | | No USB access, no driver change |
| `windows_usb_check.ps1` | List `VID_09F1&PID_0007` in Windows and run enumeration-only preflight | | Read-only |

## fit_calibration.py

- Refuses any row whose `reference_source != "physical"` or `robot_id` mismatch. This is the vendor-display (SCORBASE) rule; do not add a "vendor" role.
- Per joint needs 3 home, 4 fit, 3 verify, 3 move_verify rows, both approach directions in every non-home group. Home spread <= 50 counts and 2 degrees; holdout and motion error <= 2 degrees; fit span >= 20 counts and 2 degrees; soft limits inside the measured range by 2.5 degrees.
- Counts go through `signed_count_delta`, so a measured region must not exceed half the counter range (ambiguity raises).
- It calls `check_soft_limit_span` so it never writes a file `scorbot.calibration.load_calibration` would reject. If you change one side, change the other and `tests/test_arm_control.py`, `tests/test_nominal.py`.
- Only base, shoulder, elbow. Wrist needs a two-motor mapping that does not exist yet.

## Log-reading tools

- `watch_lab_log.py:ALARM_EVENTS` and `review_lab_logs.py` parse row `type` and event names written by `scorbot/robot.py:_record` and `examples/*.py`. Adding or renaming an event means updating these and their tests (`tests/test_watch_lab_log.py`, `tests/test_lab_log_review.py`).
- Rows come from files being appended to. `Follower` buffers a partial last line; keep that. Quiet output does not mean the link is alive (rows are written on events, not per USB packet).
- Legacy logs may lack `data_source` (treated as `real`) or `led_prompts` (missing LED answers are then not a problem). Do not tighten this without a migration note.
- The review refuses to mix files with different `robot_id`, `motion_source_sha256`, or `data_source`, and repeats the SIMULATED banner after the report. Preserve all three.
- Differences use `signed_count_delta`; never `max - min` of signed counts (jumps 65536 at the seam; fixed once already, `test_idle_range_is_wrap_aware_at_the_zero_seam`).
- `watch_lab_log.py` writes ANSI escapes; it enables them on Windows with `os.system("")`. Respect non-TTY output when extending (`docs/design/OPERATOR_UX.md` backlog item 5).

## usb_trace.py

Standard library only; packs and offsets come from `scorbot.state` (`PACKET_MIN_LENGTH`, `decode_state`). The USBPcap header layout is `USBPCAP_HEADER`. Out-packet fields: byte 0 sequence, byte 4 command (`openScorbot/libhex.py`); this comes from code reading, not a capture. `setpoints` reads OUT bytes 12-35 as per-joint value plus sign word (`SETPOINT_REGION`, docs/protocol/PROTOCOL.md 2.3), also unverified; its echo/lead verdict is heuristic, and `--csv` opens with `"x"` (never overwrites). There are no Intelitek captures in the repo, so protocol conclusions from `compare` are unverified until someone records them.

## PowerShell

Scripts use `$ErrorActionPreference = 'Stop'`, resolve the repo root from `$PSScriptRoot`, and are run with `powershell -NoProfile -ExecutionPolicy Bypass -File`. Do not add steps that touch the USB driver (Zadig is manual and documented).

## Testing changes

`python -m unittest tests.test_lab_log_review tests.test_watch_lab_log tests.test_usb_trace tests.test_arm_control tests.test_nominal`. Tests import scripts as `scripts.<name>` (a namespace package, no `__init__.py`), so run from the repo root; renaming a function breaks them. Run the PowerShell scripts only on Windows.

## Do not

- Do not import `openScorbot` protocol modules or open a device from a script. (`fit_calibration.py` imports only `openScorbot.motion_profile`, pure.)
- Do not overwrite outputs; use exclusive create.
- Do not have a review or fit script emit "safe", "calibrated" or "validated" language beyond what its checks prove.
