# Simulated robot and recorded bench runs: design

**Status:** Approved direction ("do it"), 2026-09-25
**Implements:** System design §8 item 3 (simulated backend, R-04, R-09) and the
lab-run recording follow-up to the experiment recorder. GitHub work item 3.

## 1. Purpose

- **A fake ER-4U controller.** It lets the team develop and rehearse without
  the arm, through **the same `Scorbot` API and the same safety logic** that
  drive the real controller.
- **Automatic MCAP recording of the lab scripts.** `bench_joint.py` and
  `record_raw_state.py` record the G1 visit into one session file. With
  `--simulate`, the whole G1 procedure can be rehearsed at a desk.

**Success**
- A student can run `bench_joint.py --simulate`, answer the prompts, and get:
  - the normal bench JSONL, which `review_lab_logs.py` accepts;
  - an MCAP session that replays with **SIMULATED** in the banner.
- Offline tests show that the real facade's fault latch works:
  - a timeout, a controller error, a worker crash, stale feedback, or a
    corrupt packet each faults the session;
  - after a fault, all further motion is rejected.

## 2. Key decision: simulate the controller, not the facade

`Scorbot`'s safety behaviour lives in its methods: argument validation, the
enable/home gates, wrist blocking, soft limits, the fault latch on timeouts
and errors, and freshness checks on feedback. The simulator must exercise
that code, not copy it.

**Choice:** `SimulatedScorbot(Scorbot)` overrides only `connect()` and
`disconnect()`, the two methods that touch USB. It plugs in two fakes at the
seams PR #1's tests already use:

- **A command worker.** It consumes `self._commands` and answers on
  `self._results` with the legacy protocol: `0` means done; an error is a
  non-zero code followed by `0`; `528` exits.
- **An input endpoint.** It has `snapshot(after_index, timeout, max_age)`
  and returns `PacketSnapshot`s holding real 64-byte packets that
  `decode_state` parses.

`_command`, `get_state`, `enable`, `home`, `jog_joint`, `preview_jog`,
`move_joint`, and the calibration checks all run **unchanged**. The USB
motion path (`openScorbot/`, `Scorbot.connect`, `Scorbot.disconnect`) is not
modified. This follows system design §8.3.

**Motion model.** It reuses `openScorbot/motion_profile.plan_jog`. A jog
changes the signed motor counts by exactly the plan's `motor_count_deltas`,
including the wrist-motor coupling. Home sets every count to the configured
home counts, which default to `0`. There is no timing, dynamics, backlash,
gravity, or collision model. The simulator doesn't imitate unknown USB
timing (system design §8). An optional `step_delay_s` sleeps once per plan
increment, so a jog visibly takes time.

**Rejected alternatives**
- **A `ControllerBackend` protocol refactor of `Scorbot`.** It is a cleaner
  seam long term, but it rewrites safety code that hasn't been verified on
  the bench yet. Do it after G1.
- **A physics simulator (PyBullet or MuJoCo with the community URDF).** The
  URDF masses are about 3× the real arm and the geometry is unverified.
  Deferred to G4.

## 3. Always marked simulated (R-09)

- `RobotState` gets a new trailing field, `simulated: bool = False`.
  `SimulatedScorbot.get_state()` returns states with `simulated=True`.
- `SimulatedScorbot` adds `"simulated": true` to every JSONL event-log row.
- **`SessionWriter.log_state` refuses to mix sources:**
  - a state with `simulated=True` in a session whose `data_source` is not
    `"simulated"` → `SessionError`;
  - a state with `simulated=False` in a `"simulated"` session → `SessionError`;
  - a state with no `simulated` key (older dicts) is accepted unchanged.

## 4. Fault injection

`robot.sim.inject(kind)` arms a one-shot fault. `kind` is one of:

| Kind | Effect | Expected facade behaviour |
|---|---|---|
| `timeout` | The next command gets no answer | `ScorbotError` after `command_timeout`; faulted |
| `controller_error` | The next command answers `3`, then `0` | `ScorbotError("…error code 3")`; faulted |
| `worker_crash` | The next command answers with an exception | `ScorbotError("…worker crashed…")`; faulted |
| `stale_feedback` | The next `snapshot` raises `TimeoutError` | `ScorbotError`; faulted before any motion is queued |
| `corrupt_packet` | The next packet has an invalid sign byte | `decode_state` raises `ValueError`; faulted |

`robot.sim.commands` lists every payload received, so tests can assert
that nothing was queued. `robot.sim.counts` is the current signed-count
dict.

## 5. Lab scripts record MCAP sessions

**`examples/bench_joint.py` and `examples/record_raw_state.py`:**
- **New flags:** `--session-root` (default `sessions`) and `--simulate`.
- **`--simulate`:**
  - skips USB preflight;
  - uses `SimulatedScorbot`;
  - writes `data_source="simulated"`;
  - labels the JSONL session row `"data_source": "simulated"`.
- **Without it,** `data_source="real"`.
- **The JSONL outputs and their fields are unchanged,** so
  `review_lab_logs.py` keeps working.
- **Mapping** in `bench_joint.py` from existing steps to session events:

| Existing step | Session event |
|---|---|
| connected / home_complete / before_jog / after_jog / disabled | `log_state` |
| HOME | `log_command("home", …)`, then `log_command_result` |
| operator prompts | `log_decision(choice, reason=typed text)` |
| motion preview | `log_note` with the plan summary |
| jog | `log_command("jog_joint", …)`, then `log_command_result` |
| failure | `log_fault`, and `faulted` for an open command |

- **`record_raw_state.py`** logs each sample with `log_state`.

The G1 checklist gains a "rehearse with `--simulate` first" item.

## 6. Files

- **New:**
  - `scorbot/simulated.py` (`SimulatedController`, `SimulatedScorbot`)
  - `tests/test_simulated.py`
- **Changed:**
  - `scorbot/state.py`: the `simulated` field
  - `scorbot/__init__.py`: export `SimulatedScorbot`
  - `scorbot/session/record.py`: the source-mixing guard
  - `examples/bench_joint.py`, `examples/record_raw_state.py`
  - `docs/G1_LAB_CHECKLIST.md`, `docs/EXPERIMENT_RECORDING.md`, `README.md`

## 7. Testing (offline, unittest)

1. **Full cycle:** connect → enable → home → `jog_joint("base", 1)`. The
   signed base count changes by the plan delta (±142), the state is
   `simulated`, and packet indices increase.
2. **Facade gates are reused:**
   - a jog before home is rejected;
   - a wrist jog is rejected with no command queued;
   - a jog above 5° is rejected.
3. **Each fault kind** gives `ScorbotError`, and the next `jog_joint`
   raises. Stale feedback queues no motion.
4. **`disconnect()`** joins the worker, and a new instance connects cleanly.
5. **Event-log rows** carry `simulated: true`.
6. **`import scorbot.simulated`** does not import `usb`.
7. **SessionWriter** refuses mixed sources in both directions.
8. **`bench_joint.py --simulate`** in a subprocess with scripted stdin:
   - it exits 0;
   - its JSONL passes `review_lab_logs.py`;
   - its MCAP loads with no errors and `data_source="simulated"`;
   - the MCAP contains the home and jog commands with results.
9. **`record_raw_state.py --simulate`** writes N samples to both the JSONL
   and the MCAP.
