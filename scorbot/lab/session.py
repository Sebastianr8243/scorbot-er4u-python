"""Guided lab session engine: steps, arming, gates and logging. No print or input.

Every motion goes through Scorbot's own gates (enable, home, jog_joint) and its
fault latch. The session only adds stricter limits: 1 degree steps, base,
shoulder and elbow only, a 10 degree net travel cap per joint from home, and an
armed state that anything unexpected clears. Every arming asks for the MOTORS
LED, because the controller can cut motor power by itself (e-stop, over-current,
time-out) and nothing the SDK reads shows it. The physical stop is the stop.
"""

from __future__ import annotations

from dataclasses import asdict
import json
import time

from ..calibration import signed_count_delta
from ..provenance import motion_source_sha256
from ..session import BestEffortRecorder, SessionWriter
from ..state import JOINTS
from .operator import ENTER, StatusLine
from .moves import MAX_MARKS, MarkedPosition, plan_moves
from .faults import format_guidance, guidance_for
from .review import format_session_review, review_session_rows

EXIT_OK, EXIT_FAILED, EXIT_DECLINED = 0, 1, 3
STEPS = (1.0, 0.5)
TRAVEL_CAP_DEG = 10.0
# Motors whose commanded target is logged with every jog (the dataset action).
ARM_MOTORS = ("base", "shoulder", "elbow", "wrist_motor_1", "wrist_motor_2")
IDLE_DISARM_S = 60.0
IDLE_SAMPLES = 5
STABLE_COUNTS = 2
# A legacy jog ends once the joint is within 20 counts of its target
# (openScorbot/libcomm.py settle loop), so counts may keep settling that far.
DRIFT_COUNTS = 20
JOG_KEYS = {"1": ("base", 1), "q": ("base", -1), "2": ("shoulder", 1),
            "w": ("shoulder", -1), "3": ("elbow", 1), "e": ("elbow", -1)}
CHECKLIST = (
    "Base clamped, path clear, 70 cm around the arm",
    "Physical stop located and tested",
    "Teach pendant on Auto or unplugged; Intelitek software closed",
    "Arm in the known start pose (matches the photo)",
)
MOTORS_KEYS = {"y": "lit", "n": "off", "u": "unsure"}
POWER_KEYS = {"g": "green", "o": "orange", "f": "flashing", "u": "unsure"}
DIRECTION_KEYS = {"t": "toward", "a": "away", "n": "none", "u": "unsure"}
YES_NO_UNSURE = {"y": "yes", "n": "no", "u": "unsure"}
HELP = ("Keys: 1/q base +/-   2/w shoulder +/-   3/e elbow +/-   s step size   "
        "a arm   d disarm   t teleop   b back to start   m mark pose   g go to mark   "
        "? help   x finish.  One press = one step. The physical stop is the stop.")
_MISMATCH_TEXT = {
    ("motors", "off", "lit"): "Software says motors are DISABLED but the MOTORS LED is LIT.",
    ("motors", "lit", "off"): "Software says motors are ENABLED but the MOTORS LED is OFF; "
                              "the controller may have cut motor power.",
    ("power", "green", "orange"): "POWER LED ORANGE: the controller is not communicating.",
    ("power", "green", "flashing"): "POWER LED FLASHING: USB timeout.",
}


class Declined(Exception):
    """The operator chose not to continue; nothing further moves."""


class SessionFailed(RuntimeError):
    """A required check failed; the session ends."""


class LabSession:
    def __init__(self, *, profile, operator, robot_factory, data_source, log_path,
                 session_root, preflight=None, clock=time.monotonic, sleep=time.sleep,
                 software_commit="unknown", camera_factory=None):
        self.profile, self.op = profile, operator
        self.robot_factory, self.data_source = robot_factory, data_source
        self.log_path, self.session_root = log_path, session_root
        self.events_path = log_path.with_name(log_path.stem + ".controller.jsonl")
        self.preflight, self.clock, self.sleep = preflight, clock, sleep
        self.software_commit = software_commit
        self.rows: list[dict] = []
        self.robot = self.rec = self.fault = self.landmark = None
        self.homed = self.armed = self.enabled = False
        self.joint, self.step = "base", STEPS[0]
        self.confirmed: set = set()
        self.travel = {"base": 0.0, "shoulder": 0.0, "elbow": 0.0}
        self.jogs = 0
        self.home_counts = self.last_counts = None
        self.marks: list[MarkedPosition] = []
        self.camera_factory, self.camera = camera_factory, None

    # -- plumbing -------------------------------------------------------------

    def _write(self, row_type, /, **fields):
        row = {"type": row_type, "host_monotonic_ns": time.monotonic_ns(), **fields}
        self._stream.write(json.dumps(row, allow_nan=False) + "\n")
        self._stream.flush()
        self.rows.append(row)

    def _status(self):
        self.op.status(StatusLine(self.data_source.upper(), self.homed, self.armed,
                                  self.joint, self.step, self.profile.speed, self.fault))

    def _state(self, row_type, /, **fields):
        state = self.robot.get_state()
        self._write(row_type, state=asdict(state), **fields)
        self.rec.log_state(state)
        return state

    def _guide(self, error_text):
        guidance = guidance_for(error_text)
        self._write("fault_guidance", key=guidance.key, title=guidance.title)
        self.op.show(format_guidance(guidance), "warn")

    # -- run ------------------------------------------------------------------

    def run(self) -> int:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("x", encoding="utf-8") as self._stream:
            self._write("session", schema_version=2, kind="lab_session",
                        robot_id=self.profile.robot_id, data_source=self.data_source,
                        profile=asdict(self.profile), software_commit=self.software_commit,
                        motion_source_sha256=motion_source_sha256(),
                        controller_event_log=self.events_path.name, led_prompts=True)
            self._write("profile", **asdict(self.profile))
            writer = SessionWriter.create(
                self.session_root, data_source=self.data_source,
                robot_id=self.profile.robot_id, controller_id=self.profile.controller_label,
                operator=self.profile.operator, task=f"guided lab session ({self.log_path.name})",
                usb_driver=self.profile.driver)
            with writer:
                self.rec = BestEffortRecorder(writer, warn=lambda m: self.op.show(m, "warn"))
                self._write("recorder", mcap_session=self.rec.path.name)
                try:
                    return self._steps()
                except Declined as declined:
                    self._write("operator_declined", text=str(declined))
                    self.rec.log_decision("declined", reason=str(declined))
                    self.op.show(f"Stopped: {declined}. Nothing further will move.")
                    return EXIT_DECLINED
                except (Exception, KeyboardInterrupt) as error:
                    self._write("session_failed", error_type=type(error).__name__,
                                error=str(error))
                    self.op.show(f"Session failed: {error}. If motor state is uncertain, "
                                 "use the physical stop.", "alarm")
                    if self.enabled:
                        self._guide(str(error))
                    if isinstance(error, KeyboardInterrupt):
                        raise
                    return EXIT_FAILED

    def _steps(self) -> int:
        self._checklist()
        if self.preflight is not None:
            checks = self.preflight()
            for check in checks:
                self.op.show(f"{'PASS' if check.passed else 'FAIL'} {check.name}: {check.detail}")
            self._write("preflight", checks=[asdict(c) for c in checks])
            if not all(check.passed for check in checks):
                raise SessionFailed("preflight failed; no controller connection attempted")
        with self.robot_factory(log_path=self.events_path, robot_id=self.profile.robot_id) as robot:
            self.robot = robot
            self._state("connected")
            self._led("after_connect", motors="off", power="green", required=True)
            self._idle()
            try:
                self._home()
                self._jog_loop()
            except KeyboardInterrupt:
                # Stop asking questions, but still request motors off.
                if self.enabled:
                    self._disable()
                raise
            except Exception:
                # Declined or failed after enable: motors off, LED check, summary.
                if self.enabled:
                    self._finish()
                raise
            self._finish()
        return EXIT_FAILED if self.fault else EXIT_OK

    # -- steps ----------------------------------------------------------------

    def _checklist(self):
        answers = {}
        for item in CHECKLIST:
            answers[item] = self.op.choose(f"{item}  [Enter/y done, n not done] ",
                                           {ENTER: "done", "y": "done", "n": "not done"})
            if answers[item] != "done":
                self._write("checklist", items=answers)
                raise Declined(f"checklist not done: {item}")
        self._write("checklist", items=answers)

    def _led(self, step, *, motors, power=None, required=False):
        self.op.show(f"LED check {step.replace('_', ' ')}: look at the controller front panel.")
        sim = getattr(self.robot, "sim", None)
        if self.data_source == "simulated" and hasattr(sim, "leds"):
            # Rehearsals have no panel to look at; show the modeled one instead.
            panel = sim.leds()
            self.op.show(f"SIMULATED panel: MOTORS {panel['motors']}, POWER {panel['power']}")
        seen_motors = self.op.choose("  MOTORS LED lit? [y/n/u=unsure] ", MOTORS_KEYS)
        seen_power = self.op.choose("  POWER LED colour? [g/o/f/u=unsure] ", POWER_KEYS)
        self._write("led_observation", step=step, motors_led=seen_motors, power_led=seen_power,
                    expected_motors_led=motors, expected_power_led=power)
        self.rec.log_decision(f"motors_led={seen_motors} power_led={seen_power}",
                              reason=f"LED observation {step}")
        for led, expected, seen in (("motors", motors, seen_motors), ("power", power, seen_power)):
            if expected is not None and seen not in ("unsure", expected):
                message = _MISMATCH_TEXT.get((led, expected, seen),
                                             f"{led.upper()} LED {seen}, software expects {expected}.")
                self._write("led_mismatch", step=step, led=led, observed=seen,
                            expected=expected, message=message)
                self.op.show(f"{message} This does not cut motor power; use the physical "
                             "stop if in doubt.", "alarm")
        if required:
            failed = [f"{led}={seen} (expected {expected})" for led, expected, seen in
                      (("motors", motors, seen_motors), ("power", power, seen_power))
                      if expected is not None and seen != expected]
            if failed:
                reason = f"LED check {step} not confirmed: " + ", ".join(failed)
                self._write("led_gate_failed", step=step, reason=reason)
                raise SessionFailed(reason)

    def _idle(self):
        self.op.show(f"Idle check: {IDLE_SAMPLES} readings, 1 s apart. Do not touch the arm.")
        first = last = None
        for index in range(IDLE_SAMPLES):
            if index:
                self.sleep(1.0)
            last = self._state("idle_sample", index=index)
            first = first or last
        changed = {j: signed_count_delta(last.encoder_counts[j], first.encoder_counts[j])
                   for j in JOINTS}
        moving = {j: d for j, d in changed.items() if abs(d) > STABLE_COUNTS}
        if moving:
            self.op.show(f"Counts changed while idle: {moving}. Check before homing.", "warn")
        else:
            self.op.show("Counts stable while idle.")

    def _home(self):
        self.op.show("HOME NEEDED. Homing searches every axis switch from the known start pose.",
                     "warn")
        self._write("start_pose", text=self.op.text("Start pose: does it match the photo? "
                                                    "Describe: ") or "not recorded")
        if not self.op.confirm("Type HOME to enable motors and search home: ", "HOME"):
            raise Declined("declined before homing")
        self.enabled = True          # set first: a failed enable still gets a disable
        self.robot.enable()
        self._write("enabled")
        self._led("after_enable", motors="lit", power="green", required=True)
        command = self.rec.log_command("home", {"start_position_confirmed": True})
        self.robot.home(start_position_confirmed=True)
        self.rec.log_command_result(command, "completed", completion_source="home() returned")
        state = self._state("home_complete")
        self.home_counts = dict(state.encoder_counts)
        self.last_counts = dict(state.encoder_counts)
        self.homed = True
        self._write("home_observation", text=self.op.text(
            "Describe what moved during homing and the final pose: ") or "not recorded")
        answer = self.op.choose("Did home look right, and is the travel path clear? [y/n] ",
                                {"y": "yes", "n": "no"})
        self._write("home_ok", answer=answer)
        if answer != "yes":
            raise Declined("stopped after homing; no jog requested")

    # -- jog loop -------------------------------------------------------------

    def _disarm(self, reason):
        if self.armed:
            self.armed = False
            self._write("disarmed", reason=reason)
            self.op.show(f"DISARMED: {reason}.", "warn")

    def _arm(self):
        if self.landmark is None:
            self.landmark = self.op.text("Name a fixed landmark for directions "
                                         "(e.g. the door): ") or "not recorded"
        # Raises SessionFailed unless MOTORS is seen lit and POWER green.
        self._led("before_arm", motors="lit", power="green", required=True)
        self.op.show("Path clear, hand on the physical stop?")
        if self.op.confirm("Type ARM to arm jogging: ", "ARM"):
            self.armed = True
            self.confirmed.clear()
            self._write("armed", landmark=self.landmark)
        else:
            self.op.show("Not armed.")

    def _jog_loop(self):
        self.op.show(HELP)
        last = self.clock()
        while self.fault is None:
            self._status()
            key = self.op.key("key: ")
            now = self.clock()
            if key in ("", "x"):
                break
            if self.armed and now - last > IDLE_DISARM_S:
                self._disarm(f"idle for more than {IDLE_DISARM_S:g} s")
            elif key == "?":
                self.op.show(HELP)
            elif key == "s":
                self.step = STEPS[(STEPS.index(self.step) + 1) % len(STEPS)]
                self.op.show(f"Step size {self.step:g} degree.")
            elif key == "a":
                self._arm()
            elif key == "d":
                self._disarm("operator")
            elif key == "m":
                self._mark()
            elif key == "g":
                self._goto()
            elif key == "b":
                self._back()
            elif key == "t":
                if not self.armed:
                    self.op.show("Teleop needs the arm armed: press a.")
                else:
                    from .teleop import Teleop
                    if Teleop(self, self.camera).run() == "finish":
                        break
            elif key in JOG_KEYS:
                if self.armed:
                    self._jog(*JOG_KEYS[key])
                else:
                    self.op.show("DISARMED: press a to arm.")
            elif self.armed:
                self._disarm(f"unknown key {key!r}")
            else:
                self.op.show(f"Unknown key {key!r}; press ? for help.")
            last = self.clock()

    def _drift_ok(self, state) -> bool:
        """False (row, alarm, disarm) if counts moved since the last step unprompted."""
        if self.last_counts is None:
            return True
        differences = {motor: signed_count_delta(state.encoder_counts[motor],
                                                 self.last_counts[motor])
                       for motor in JOINTS}
        drift = {motor: d for motor, d in differences.items() if abs(d) > DRIFT_COUNTS}
        if not drift:
            return True
        self._write("counts_drift", differences=differences, limit=DRIFT_COUNTS)
        self.op.show(f"Counts moved by {drift} since the last step with nothing commanded. "
                     "The logged travel no longer describes the pose. Finish (x) and home "
                     "again in a new session.", "alarm")
        self._disarm("counts drift")
        self._guide("counts drift")
        return False

    def _prepare(self, joint, delta):
        """Travel cap, drift check and preview for one step; None if refused or failed."""
        self.joint = joint
        if abs(self.travel[joint] + delta) > TRAVEL_CAP_DEG + 1e-9:
            reason = (f"{joint} would be {self.travel[joint] + delta:+g} degrees from home; "
                      f"the session cap is {TRAVEL_CAP_DEG:g}")
            self._write("jog_refused", joint=joint, delta_deg=delta, reason=reason)
            self.op.show(f"Refused: {reason}.", "alarm")
            self._disarm("travel cap")
            return None
        n = self.jogs + 1
        try:
            before = self.robot.get_state()
        except Exception as error:
            self._jog_failed(n, error)
            return None
        if not self._drift_ok(before):
            return None
        try:
            plan = self.robot.preview_jog(joint, delta, speed=self.profile.speed,
                                          starting_signed_counts=before.signed_encoder_counts)
        except Exception as error:
            self._jog_failed(n, error)
            return None
        self._write("jog_preview", n=n, joint=joint, delta_deg=delta, plan=plan)
        return n, before, plan

    def _execute(self, joint, delta, how, n, before, plan, *, observe=True) -> bool:
        """Run one prepared step through jog_joint; False if it failed (session latched).

        With observe=False (plan steps) no questions are asked and pending keys
        are left for the plan loop, which stops on them.
        """
        move = f"{joint.upper()} {delta:+g}"
        self._write("jog_confirmed", n=n, how=how, move=move)
        self._write("before_jog", n=n, state=asdict(before))
        self.rec.log_state(before)
        # Full commanded target (dataset action): moving motors get the previewed
        # target, the others keep their current count.
        deltas = plan["motor_count_deltas"]
        target = {motor: before.signed_encoder_counts[motor] + deltas.get(motor, 0)
                  for motor in ARM_MOTORS}
        command = self.rec.log_command("jog_joint", {"joint": joint, "delta_degrees": delta,
                                                     "speed": self.profile.speed,
                                                     "target_signed_counts": target})
        try:
            after = self.robot.jog_joint(joint, delta, speed=self.profile.speed)
        except Exception as error:
            self.rec.log_command_result(command, "faulted", detail=str(error))
            self._jog_failed(n, error)
            return False
        self.jogs = n
        self.travel[joint] += delta
        self.last_counts = dict(after.encoder_counts)
        self._write("after_jog", n=n, state=asdict(after))
        self.rec.log_command_result(command, "completed", completion_source="jog_joint() returned")
        self.rec.log_state(after)
        if observe:
            ignored = self.op.discard_pending_keys()
            if ignored:
                self.op.show(f"Ignored {ignored} key(s) pressed while the arm was moving.",
                             "warn")
            direction = self.op.choose(f"Which way did {joint} move relative to "
                                       f"{self.landmark}? [t toward / a away / n none / "
                                       "u unsure] ", DIRECTION_KEYS)
            other = self.op.choose("Did any other joint move? [y/n/u] ", YES_NO_UNSURE)
            note = self.op.text("Note (Enter to skip): ")
            self._write("jog_observation", n=n, direction=direction, other_joint_moved=other,
                        note=note)
        measured = {}
        for motor in JOINTS:
            try:
                measured[motor] = signed_count_delta(after.encoder_counts[motor],
                                                     before.encoder_counts[motor])
            except (KeyError, ValueError):
                measured[motor] = None
        self._write("jog_result", n=n, planned=plan["motor_count_deltas"], measured=measured)
        self.op.show(f"Planned {plan['motor_count_deltas']}, measured "
                     f"{ {m: v for m, v in measured.items() if v} or 'no change'}.")
        return True

    def _jog(self, joint, sign):
        delta = sign * self.step
        prepared = self._prepare(joint, delta)
        if prepared is None:
            return
        n, before, plan = prepared
        move = f"{joint.upper()} {delta:+g}"
        move_key = (joint, sign, self.step)
        if move_key in self.confirmed:
            how = "repeat"
            self.op.show(f"Repeat {move}.")
        else:
            self.op.show(f"Plan {move}: motor counts {plan['motor_count_deltas']} in "
                         f"{len(plan['increments'])} steps (legacy scale, not measured).")
            if not self.op.confirm(f"Type {move} to move: ", move):
                self._write("jog_declined", n=n, move=move)
                self._disarm("confirmation declined")
                return
            self.confirmed.add(move_key)
            how = "typed"
            # The typed confirmation can take a while; counts that moved meanwhile
            # must not become the new baseline, so check again on a fresh read.
            try:
                before = self.robot.get_state()
            except Exception as error:
                self._jog_failed(n, error)
                return
            if not self._drift_ok(before):
                return
        self._execute(joint, delta, how, n, before, plan)

    # -- multi-step moves -----------------------------------------------------

    def _mark(self):
        if len(self.marks) >= MAX_MARKS:
            reason = f"at most {MAX_MARKS} marks per session"
            self._write("mark_refused", reason=reason)
            self.op.show(f"Not marked: {reason}.", "warn")
            return
        state = self.robot.get_state()
        mark = MarkedPosition(f"P{len(self.marks) + 1}", dict(self.travel),
                              dict(state.encoder_counts))
        self.marks.append(mark)
        self._write("position_marked", name=mark.name, travel=mark.travel, counts=mark.counts)
        self.op.show(f"Marked {mark.name}: {mark.travel} (degrees from home, legacy scale).")

    def _goto(self):
        if not self.armed:
            self.op.show("DISARMED: press a to arm.")
            return
        if not self.marks:
            self.op.show("No marked positions yet; press m to mark one.")
            return
        names = {str(i): mark.name for i, mark in enumerate(self.marks, start=1)}
        choice = self.op.choose(f"Go to which mark? [1-{len(self.marks)}] ", names)
        mark = next((m for m in self.marks if m.name == choice), None)
        if mark is None:
            self.op.show("No mark chosen.")
            return
        self._run_plan(mark.name, plan_moves(self.travel, mark.travel), "goto",
                       mark.counts, f"GOTO {mark.name}")

    def _back(self):
        if not self.armed:
            self.op.show("DISARMED: press a to arm.")
            return
        start = {joint: 0.0 for joint in self.travel}
        self._run_plan("start", plan_moves(self.travel, start), "back",
                       self.home_counts, "BACK")

    def _stop_plan(self, name, reason, done, total):
        self.op.discard_pending_keys()     # a buffered key must not reach the jog loop
        self._write("plan_stopped", name=name, reason=reason, steps_done=done)
        self.op.show(f"Move to {name} stopped after {done} of {total} steps ({reason}). "
                     "This is a software pause, not an emergency stop; the physical "
                     "stop is the stop.", "warn")
        self._disarm("plan stopped")

    def _run_plan(self, name, moves, how, target_counts, confirm_text):
        """Show a multi-step move, confirm once, run it step by step, then check arrival."""
        try:
            state = self.robot.get_state()
        except Exception as error:
            self._jog_failed(self.jogs + 1, error)
            return
        if not self._drift_ok(state):
            return
        labels = [move.label for move in moves]
        self._write("plan_shown", name=name, moves=labels)
        if not moves:
            self.op.show(f"Already at {name}.")
            return
        if self.op.can_stop_on_key():
            stop_text = ("Any key during the move stops it after the current step (a "
                         "software pause, not an emergency stop; the physical stop is "
                         "the stop).")
        else:
            stop_text = ("This terminal cannot read keys during the move; only the "
                         "physical stop stops it.")
            if self.data_source == "real":
                reason = "the terminal cannot read keys during the move"
                self._write("plan_refused", name=name, reason=reason)
                self.op.show(f"Move to {name} refused: {stop_text}", "alarm")
                self._disarm("plan refused")
                return
        self.op.show(f"Move to {name}, one joint at a time: {', '.join(labels)}. {stop_text}")
        if not self.op.confirm(f"Type {confirm_text} to run it: ", confirm_text):
            self._write("plan_declined", name=name)
            self._disarm("confirmation declined")
            return
        for done, move in enumerate(moves):
            if self.op.discard_pending_keys() > 0:
                self._stop_plan(name, "stopped by a key press", done, len(moves))
                return
            prepared = self._prepare(move.joint, move.delta_deg)
            if prepared is None or not self._execute(move.joint, move.delta_deg, how,
                                                     *prepared, observe=False):
                self._stop_plan(name, "step refused or failed", done, len(moves))
                return
        ignored = self.op.discard_pending_keys()
        if ignored:
            self.op.show(f"{ignored} key(s) pressed during the last step; the move had "
                         "already finished.", "warn")
        answer = self.op.choose(f"Is the arm at {name}? [y/n/u] ", YES_NO_UNSURE)
        after = self.robot.get_state()
        differences = {motor: signed_count_delta(after.encoder_counts[motor],
                                                 target_counts[motor])
                       for motor in JOINTS}
        self._write("plan_complete", name=name, answer=answer, steps=len(moves),
                    count_differences=differences, keys_during_last_step=ignored)
        self.op.show(f"At {name}: count differences from the target "
                     f"{ {m: d for m, d in differences.items() if d} or 'none'}.")

    def _jog_failed(self, n, error):
        self.fault = str(error)
        self._write("jog_failed", n=n, error=self.fault)
        self.rec.log_fault(self.fault)
        self.op.show(f"Jog failed: {error}. The session is latched; use the physical "
                     "stop if anything is still moving.", "alarm")
        self._guide(self.fault)
        self._disarm("jog failed")

    def _disable(self):
        self.armed = False
        try:
            self.robot.disable()
            self._state("disabled")
        except Exception as error:
            self._write("disable_failed", error=str(error))
            self.op.show(f"Software disable failed ({error}). The SDK queues a best-effort "
                         "disable after a fault, but only the physical stop is certain.", "alarm")

    def _finish(self):
        self._disable()
        self._led("after_disable", motors="off")
        report = review_session_rows(self.rows)
        self._write("summary", jogs=self.jogs, fault=self.fault, problems=len(report["problems"]))
        self.op.show(format_session_review(report))
