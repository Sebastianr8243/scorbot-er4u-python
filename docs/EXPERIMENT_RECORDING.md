# Recording and replaying experiments

`scorbot.session` records one experiment as a folder that contains one MCAP
file. You can replay the session in the terminal or in a standard robotics
viewer, with no robot or USB connection. Recording never sends commands to
the robot; your script does that, and logs what happened.

## Try it without hardware

```powershell
.\.venv\Scripts\python.exe examples\make_synthetic_session.py
```

This writes `sessions\<session-id>\` and prints the replay:

```text
================================================================
  SYNTHETIC DATA   session 20260925T081039Z-83cec9
================================================================
  robot        example-arm / controller unknown
  ...
  +      41.9 ms  #8     /operator/decision         choice=jog_base_positive
  +      42.3 ms  #9     /robot/command             cmd-0001 jog_joint {'joint': 'base', 'delta_counts': 50, 'decision_seq': 8}
  +      42.5 ms  #10    /robot/state               base=1010 shoulder=0 elbow=0 ...
  +      73.9 ms  #13    /robot/command_result      cmd-0001 completed

  16 events: /camera/cam0/detections 2, /camera/cam0/image 2, ...
  Integrity: no problems found

  Open session.mcap in Foxglove or Lichtblick for video and plots.
```

To replay any session again:

```powershell
.\.venv\Scripts\python.exe -m scorbot.session sessions\<session-id>
```

The exit code is 0 when the session is intact, 1 when it has integrity
errors, and 2 when the path cannot be opened. `--limit N` shortens the
timeline.

## Viewing video and plots

Open `session.mcap` in either viewer:

- [Foxglove](https://foxglove.dev/download): drag the file into the app.
- [Lichtblick](https://github.com/lichtblick-suite/lichtblick): open-source
  and needs no account.

To see the data:

- Add an **Image** panel on `/camera/cam0/image`.
- Add a **Plot** panel for a path such as `/robot/state.encoder_counts.base`.
- Add a **Raw Messages** panel to see commands, decisions, and faults.

Camera frames use Foxglove's official `foxglove.CompressedImage` schema, so
they display without any plugin. Both viewers read MCAP `log_time` as wall
time.

## Recording from your own script

```python
from scorbot import Scorbot
from scorbot.session import SessionWriter

with SessionWriter.create("sessions", data_source="real", robot_id="lab-er4u-1",
                          operator="your name", task="base jog G1",
                          start_pose_note="photo IMG_0412, arm folded") as rec:
    with Scorbot() as robot:
        robot.enable()
        robot.home()  # only from the documented start pose
        rec.log_state(robot.get_state())
        command_id = rec.log_command("jog_joint", {"joint": "base", "degrees": 1.0, "speed": 5})
        robot.jog_joint("base", 1.0, speed=5)
        rec.log_command_result(command_id, "completed", completion_source="python returned")
        rec.log_state(robot.get_state())
```

Follow the supervised bench procedure for any real motion. The physical
emergency stop stays within reach.

**Rules**
- `data_source` is required and must be `"real"`, `"simulated"`, or
  `"synthetic"`. Replay prints it in capitals so nobody mistakes one kind of
  data for another.
- If your code raises an error inside the `with` block, the recorder logs
  it as a `/session/fault`, closes the file, and lets the error continue.
- One `SessionWriter` can be shared between a camera thread and a robot
  thread.
- A payload that is not valid JSON, such as NaN or an arbitrary Python
  object, raises `SessionError`. Nothing is written in that case, and the
  event sequence stays unbroken.

| Method | Topic |
|---|---|
| `log_state(state, raw_packet=None, observed_monotonic_ns=None)` | `/robot/state` |
| `log_command(kind, params)` returns `"cmd-0001"`, ... | `/robot/command` |
| `log_command_result(command_id, status)`, where status is `completed`, `faulted`, `timeout`, or `rejected` | `/robot/command_result` |
| `log_frame(camera_id, frame_number, image_bytes, format="png"\|"jpeg", width, height, observed_monotonic_ns=...)` | `/camera/<id>/image` |
| `log_detection(camera_id, frame_number, label, bbox_xyxy, confidence, model_id)` | `/camera/<id>/detections` |
| `log_decision(choice, refers_to_seq=None, reason=None)` | `/operator/decision` |
| `log_fault(message)`, `log_note(text)` | `/session/fault`, `/session/note` |

## Lab scripts record automatically

`examples/record_raw_state.py` and `examples/bench_joint.py` write their usual
JSONL files, which `review_lab_logs.py` reads, and they also write an MCAP
session to `<output folder>\sessions\`. Pass `--session-root` to choose a
different folder. The recorder opens before the robot connects, so a problem
with the recording stops the run before the motors are powered.

## The simulated robot

`SimulatedScorbot` is a drop-in `Scorbot` that talks to a fake controller in
memory instead of USB:

```python
from scorbot import SimulatedScorbot

with SimulatedScorbot() as robot:
    robot.enable()
    robot.home(start_position_confirmed=True)
    state = robot.jog_joint("base", 1.0)
    print(state.simulated, state.signed_encoder_counts["base"])  # True 142
```

**Same code as the real robot.** Every command still goes through the real
`Scorbot` methods: argument checks, the enable/home gates, wrist blocking,
timeouts, and the fault latch. Only the USB connection is replaced, so
anything you build against it runs the same way on the arm.

**Always labelled.** Every state has `simulated=True`, and every event-log row
has `"simulated": true`. A `SessionWriter` refuses to put simulated states in
a `real` session, or real states in a `simulated` one.

**Rehearsing lab scripts.** Add `--simulate` to `record_raw_state.py` or
`bench_joint.py` to rehearse the G1 visit at a desk. See the
[G1 checklist](G1_LAB_CHECKLIST.md).

**Testing failures.** Arm a one-shot fault with `robot.sim.inject(kind)`:

| Kind | What happens |
|---|---|
| `timeout` | The next command never answers. The session faults after `command_timeout`. |
| `late_answer` | The next command answers only after the session has already timed out. The stale answer must not unlock anything. |
| `controller_error` | The next command returns error code 3. |
| `worker_crash` | The command worker crashes and stops. |
| `stale_feedback` | The next state read gets no fresh packet. |
| `corrupt_packet` | The next packet has an invalid encoder sign byte. |

After any of these, the session is faulted and every further motion is
refused, just as on hardware. `robot.sim.commands` lists every command the
fake controller received.

**What it does not model.** A jog changes the encoder counts by exactly the
legacy plan, and homing sets every count to 0, or to `home_counts` if you set
it. It has no real timing, no physics, no backlash or gravity, no collisions,
and no real home-switch behaviour. Passing in simulation proves the
**software** logic. It says nothing about the physical arm.

## Two timestamps: logged and observed

Every event has a `_rec` block, taken from the recording process's
monotonic clock:

- `seq`: a global event number. It starts at 0 and has no gaps in an
  intact file.
- `logged_monotonic_ns`: when the recorder wrote the event. It never goes
  backwards.
- `observed_monotonic_ns`: when the thing actually happened, if the source
  knows. Examples are the camera capture time, or the packet time from
  `RobotState.host_monotonic_ns`. It is `null` when the source did not say.

**Clock resolution.** On Windows with Python 3.10–3.12, the monotonic clock
only ticks about every 15.6 ms. Python 3.13 ticks every 100 ns.
`metadata.json` records which clock was used in
`clock.monotonic_implementation` and its step size in
`clock.monotonic_resolution_s`. Use Python 3.13 on the lab PC when timing
matters. `setup_windows.ps1` already recommends it.

To match camera frames with robot states, use the observed time and always
report the gap between them:

```python
from scorbot.session.replay import load_session, nearest

session = load_session("sessions/<session-id>")
for frame in (e for e in session.events if e["topic"] == "/camera/cam0/image"):
    t = frame["payload"]["_rec"]["observed_monotonic_ns"]
    state, gap_ns = nearest(session.events, t)
    print(frame["payload"]["_rec"]["frame_number"], state["seq"], gap_ns / 1e6, "ms")
```

MCAP's own `publish_time` falls back to the logged time when nothing was
observed. For analysis, use `_rec`, not `publish_time`.

## What is in a session folder

| File | Contents |
|---|---|
| `session.mcap` | Every event and camera frame. It is written one message at a time and handed to the OS immediately. If Python crashes or you press Ctrl-C, you lose at most the message being written. A power cut or OS crash can lose more, because messages are not forced to disk one by one. |
| `metadata.json` | Robot and controller IDs, operator, task, start pose, data source, code commit and source fingerprint, Python and package versions, calibration file hash, and the clock anchor. It is rewritten with `ended_utc` and `closed_cleanly` when the session closes. |
| `notes.md` | A blank observation sheet. Fill it in by hand during or after the run. |

## Integrity messages

| Message | Meaning |
|---|---|
| WARNING `Session was not closed cleanly` | Python crashed, the e-stop ended the run, or recording is still going. Every event that was written is still there. |
| WARNING `Final record is truncated` | The last message was cut off by a crash. Everything before it is intact. |
| WARNING `Crash left N zero bytes at the end` | A power loss or OS crash left empty space at the end of the file. The events before it are intact. |
| WARNING `Command cmd-0003 has no result` | A command was logged, but its outcome never was. Check `notes.md`. |
| ERROR `seq gap or duplicate` | Events are missing from the middle of the file. |
| ERROR `Corrupted data at byte N` | The file is damaged before its end. |
| ERROR `Data checksum mismatch` | A cleanly closed file was changed after recording. |
| ERROR `Logged time goes backwards` | The recording clock is inconsistent. Don't trust the timing. |
| ERROR `Damaged record at byte N in a closed session` | A file that was closed properly is damaged partway through. The events after that point can't be read. |
| ERROR `metadata.json disagrees with the metadata recorded in session.mcap` | Someone edited `metadata.json`, or it belongs to a different session. Replay shows the values recorded inside `session.mcap`, which are checksum-protected. |
| ERROR `metadata.json records N events but M could be read` | Events are missing from a session that closed normally. |

If recording fails part-way, for example because the disk is full, the
writer stops and raises `SessionError` on every later call. It records
`closed_cleanly: false` and `write_error` in `metadata.json`.

Keep sessions out of git. `*.mcap` and `sessions/` are ignored. Archive
them with the matching `notes.md`.
