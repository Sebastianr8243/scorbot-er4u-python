"""Guided session engine with the simulated controller and scripted answers."""

import json
from pathlib import Path
import tempfile
import unittest

from scorbot import SimulatedScorbot
from scorbot.lab.operator import ScriptedOperator
from scorbot.lab.profile import LabProfile
from scorbot.lab.session import EXIT_DECLINED, EXIT_FAILED, EXIT_OK, LabSession
from scorbot.simulated import SimulatedController

PROFILE = LabProfile("lab-er4u-1", "A-12", "C-3", "WinUSB", "SR")
CHECKLIST = ["y", "y", "y", "y"]
TO_LOOP = CHECKLIST + ["n", "g", "pose matches photo", "HOME", "y", "g", "all axes homed", "y"]
OBS = ["t", "n", ""]                    # toward, no other joint, no note
ARM = ["a", "door", "y", "g", "ARM"]  # first arming names the landmark; LEDs every time
REARM = ["a", "y", "g", "ARM"]
FINISH = ["x", "n", "g"]
MIXED = ["q", "BASE -1"] + OBS + ["q"] + OBS + ["e", "ELBOW -1"] + OBS  # base -2, elbow -1
JOG_ORDERS = set(range(4, 14))


class _ErrorOneController(SimulatedController):
    """Answers every jog with legacy result 1 (joint error word too large)."""

    def _apply(self, payload):
        return 1 if payload[0] in JOG_ORDERS else super()._apply(payload)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class LabSessionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ctrl = SimulatedController()
        self.clock = Clock()

    def tearDown(self):
        self._tmp.cleanup()

    def run_session(self, answers, *, stop_on_key=True, data_source="simulated"):
        self.op = ScriptedOperator(answers)
        self.op.stop_on_key = stop_on_key
        session = LabSession(
            profile=PROFILE, operator=self.op,
            robot_factory=lambda **kw: SimulatedScorbot(controller=self.ctrl, **kw),
            data_source=data_source, log_path=self.root / "s.jsonl",
            session_root=self.root / "sessions", clock=self.clock, sleep=lambda s: None)
        code = session.run()
        self.rows = [json.loads(line) for line in
                     (self.root / "s.jsonl").read_text(encoding="utf-8").splitlines()]
        return code

    def types(self):
        return [row["type"] for row in self.rows]

    def of(self, kind):
        return [row for row in self.rows if row["type"] == kind]

    def jog_commands(self):
        return [c for c in self.ctrl.commands if c and c[0] in JOG_ORDERS]

    def test_full_session_typed_repeat_and_new_move(self):
        code = self.run_session(TO_LOOP + ARM + ["q", "BASE -1"] + OBS + ["q"] + OBS
                                + ["e", "ELBOW -1"] + OBS + FINISH)
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(self.types()[:4], ["session", "profile", "recorder", "checklist"])
        self.assertEqual(len(self.of("idle_sample")), 5)
        self.assertEqual([r["how"] for r in self.of("jog_confirmed")], ["typed", "repeat", "typed"])
        self.assertEqual([r["move"] for r in self.of("jog_confirmed")],
                         ["BASE -1", "BASE -1", "ELBOW -1"])
        self.assertEqual(len(self.jog_commands()), 3)
        self.assertEqual(self.of("armed")[0]["landmark"], "door")
        self.assertEqual(self.of("jog_observation")[0]["direction"], "toward")
        self.assertEqual([r["step"] for r in self.of("led_observation")],
                         ["after_connect", "after_enable", "before_arm", "after_disable"])
        self.assertIn("disabled", self.types())
        summary = self.of("summary")[0]
        self.assertEqual((summary["jogs"], summary["problems"]), (3, 0))
        self.assertTrue(all(row["data_source"] == "simulated" for row in self.of("session")))
        self.assertTrue(any("LOG CHECK: 0 problems" in m for m in self.op.shown))
        panels = [m for m in self.op.shown if m.startswith("SIMULATED panel:")]
        self.assertEqual(panels[:2], ["SIMULATED panel: MOTORS off, POWER green",
                                      "SIMULATED panel: MOTORS lit, POWER green"])

    def test_full_session_on_the_rehearsal_profile(self):
        import dataclasses

        from scorbot.simulated import REHEARSAL_PROFILE
        # What `python -m scorbot.lab --simulate` uses, minus the 3 s homing wait.
        self.ctrl = SimulatedController(
            profile=dataclasses.replace(REHEARSAL_PROFILE, homing_duration_s=0.0),
            start_counts={"base": 2000, "shoulder": -1500, "elbow": 1200})
        code = self.run_session(TO_LOOP + ARM + ["q", "BASE -1"] + OBS + FINISH)
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(len(self.jog_commands()), 1)
        self.assertGreater(len(self.ctrl.homing_trace), 0)
        self.assertEqual(self.of("summary")[0]["problems"], 0)
        self.assertIn("SIMULATED panel: MOTORS lit, POWER green", self.op.shown)

    def test_jog_command_carries_the_full_target_vector(self):
        from scorbot.session.replay import load_session
        self.run_session(TO_LOOP + ARM + ["q", "BASE -1"] + OBS + FINISH)
        [folder] = [p for p in (self.root / "sessions").iterdir() if p.is_dir()]
        [params] = [e["payload"]["params"] for e in load_session(folder).events
                    if e["topic"] == "/robot/command"
                    and e["payload"]["kind"] == "jog_joint"]
        target = params["target_signed_counts"]
        self.assertEqual(set(target), {"base", "shoulder", "elbow", "wrist_motor_1",
                                       "wrist_motor_2"})
        [preview] = self.of("jog_preview")
        self.assertEqual(target["base"], preview["plan"]["target_signed_counts"]["base"])
        before = self.of("before_jog")[0]["state"]["signed_encoder_counts"]
        self.assertEqual(target["elbow"], before["elbow"])

    def test_wrong_typed_move_declines_and_queues_nothing(self):
        code = self.run_session(TO_LOOP + ARM + ["q", "BASE +1"] + FINISH)
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(self.jog_commands(), [])
        self.assertEqual(len(self.of("jog_declined")), 1)
        self.assertEqual(self.of("disarmed")[0]["reason"], "confirmation declined")

    def test_jog_keys_do_nothing_while_disarmed(self):
        self.run_session(TO_LOOP + ["q", "1"] + FINISH)
        self.assertEqual(self.jog_commands(), [])
        self.assertEqual(sum("DISARMED" in m for m in self.op.shown), 2)

    def test_unknown_key_and_wrist_keys_disarm(self):
        self.run_session(TO_LOOP + ARM + ["4"] + REARM + ["z", "q"] + FINISH)
        reasons = [r["reason"] for r in self.of("disarmed")]
        self.assertEqual(reasons, ["unknown key '4'", "unknown key 'z'"])
        self.assertEqual(self.jog_commands(), [])

    def test_idle_timeout_disarms_and_ignores_that_key(self):
        def late_q():
            self.clock.t += 61
            return "q"
        self.run_session(TO_LOOP + ARM + [late_q] + FINISH)
        self.assertEqual(self.of("disarmed")[0]["reason"], "idle for more than 60 s")
        self.assertEqual(self.jog_commands(), [])

    def test_rearming_requires_typed_move_again(self):
        self.run_session(TO_LOOP + ARM + ["q", "BASE -1"] + OBS + ["d"] + REARM
                         + ["q", "BASE -1"] + OBS + FINISH)
        self.assertEqual([r["how"] for r in self.of("jog_confirmed")], ["typed", "typed"])

    def test_step_change_needs_new_typed_move(self):
        self.run_session(TO_LOOP + ARM + ["q", "BASE -1"] + OBS + ["s", "q", "BASE -0.5"]
                         + OBS + FINISH)
        self.assertEqual([r["move"] for r in self.of("jog_confirmed")], ["BASE -1", "BASE -0.5"])
        self.assertEqual([r["how"] for r in self.of("jog_confirmed")], ["typed", "typed"])

    def test_travel_cap_refuses_the_eleventh_degree(self):
        answers = TO_LOOP + ARM + ["q", "BASE -1"] + OBS
        for _ in range(9):
            answers += ["q"] + OBS
        answers += ["q"] + FINISH
        self.run_session(answers)
        self.assertEqual(len(self.jog_commands()), 10)
        refused = self.of("jog_refused")
        self.assertEqual(len(refused), 1)
        self.assertIn("10", refused[0]["reason"])
        self.assertEqual(self.of("disarmed")[-1]["reason"], "travel cap")

    def test_unsure_led_after_connect_fails_before_enable(self):
        code = self.run_session(CHECKLIST + ["u", "g"])
        self.assertEqual(code, EXIT_FAILED)
        self.assertIn("led_gate_failed", self.types())
        self.assertFalse([c for c in self.ctrl.commands if c and c[0] == 17])

    def test_fault_during_jog_latches_and_still_finishes(self):
        def inject_then_type():
            self.ctrl.inject("controller_error")
            return "BASE -1"
        code = self.run_session(TO_LOOP + ARM + ["q", inject_then_type, "n", "g"])
        self.assertEqual(code, EXIT_FAILED)
        self.assertEqual(len(self.of("jog_failed")), 1)
        self.assertTrue(self.of("disable_failed") or self.of("disabled"))
        self.assertEqual(len(self.of("summary")), 1)
        self.assertTrue(any("physical stop" in m for m in self.op.shown))

    def test_every_arming_asks_for_the_leds(self):
        self.run_session(TO_LOOP + ARM + ["q", "BASE -1"] + OBS + ["d"] + REARM + FINISH)
        steps = [r["step"] for r in self.of("led_observation")]
        self.assertEqual(steps.count("before_arm"), 2)
        self.assertEqual(len(self.of("armed")), 2)

    def test_motors_led_off_at_arming_ends_the_session_without_arming(self):
        code = self.run_session(TO_LOOP + ["a", "door", "n", "g", "n", "g"])
        self.assertEqual(code, EXIT_FAILED)
        self.assertEqual(self.of("led_gate_failed")[0]["step"], "before_arm")
        self.assertFalse(self.of("armed"))
        self.assertFalse(self.jog_commands())
        self.assertIn(16, self.motor_commands_after_enable())
        self.assertEqual(len(self.of("summary")), 1)
        self.assertTrue(any("start pose" in m for m in self.op.shown))

    def test_dropped_motors_rehearsal_latches_on_the_next_jog(self):
        # The operator misses the dark MOTORS LED; the next jog still fails safely.
        self.ctrl.drop_motors_after_home = True
        code = self.run_session(TO_LOOP + ARM + ["q", "BASE -1", "n", "g"])
        self.assertEqual(code, EXIT_FAILED)
        self.assertEqual(len(self.of("jog_failed")), 1)
        self.assertFalse(self.jog_commands()[1:])
        self.assertTrue(any("start pose" in m for m in self.op.shown))

    def test_checklist_no_and_home_declined_exit_3(self):
        self.assertEqual(self.run_session(["y", "n"]), EXIT_DECLINED)
        self.assertEqual(self.ctrl.commands, [])
        self.setUp()
        code = self.run_session(CHECKLIST + ["n", "g", "pose", "STOP"])
        self.assertEqual(code, EXIT_DECLINED)
        self.assertFalse([c for c in self.ctrl.commands if c and c[0] == 18])

    def test_end_of_input_in_loop_finishes_safely(self):
        code = self.run_session(TO_LOOP + ARM + ["q", "BASE -1"] + OBS)
        self.assertEqual(code, EXIT_OK)
        self.assertIn("disabled", self.types())
        self.assertEqual(self.of("led_observation")[-1]["motors_led"], "unsure")

    def motor_commands_after_enable(self):
        orders = [c[0] for c in self.ctrl.commands if c]
        return orders[orders.index(17) + 1:] if 17 in orders else []

    def test_error_while_planning_a_jog_still_finishes(self):
        from scorbot.robot import ScorbotError

        class BadPreview(SimulatedScorbot):
            def preview_jog(self, *args, **kwargs):
                raise ScorbotError("preview broke")
        self.op = None
        answers = TO_LOOP + ARM + ["q", "n", "g"]
        op = ScriptedOperator(answers)
        code = LabSession(profile=PROFILE, operator=op,
                          robot_factory=lambda **kw: BadPreview(controller=self.ctrl, **kw),
                          data_source="simulated", log_path=self.root / "s.jsonl",
                          session_root=self.root / "sessions", clock=self.clock,
                          sleep=lambda s: None).run()
        self.rows = [json.loads(line) for line in
                     (self.root / "s.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(code, EXIT_FAILED)
        self.assertEqual(len(self.of("jog_failed")), 1)
        self.assertIn(16, self.motor_commands_after_enable())
        self.assertEqual(len(self.of("summary")), 1)

    def test_declining_after_home_disables_and_finishes(self):
        code = self.run_session(TO_LOOP[:-1] + ["n", "n", "g"])
        self.assertEqual(code, EXIT_DECLINED)
        self.assertIn(16, self.motor_commands_after_enable())
        self.assertIn("disabled", self.types())
        self.assertEqual(self.of("led_observation")[-1]["step"], "after_disable")
        self.assertEqual(len(self.of("summary")), 1)

    def test_ctrl_c_in_loop_disables_then_raises(self):
        def interrupt():
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.run_session(TO_LOOP + ARM + [interrupt])
        self.rows = [json.loads(line) for line in
                     (self.root / "s.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertIn(16, self.motor_commands_after_enable())
        self.assertIn("session_failed", self.types())

    def test_keys_pressed_during_a_jog_are_discarded_before_questions(self):
        self.run_session(TO_LOOP + ARM + ["q", "BASE -1"] + OBS + ["q"] + OBS + FINISH)
        self.assertEqual(self.op.discards, 2)

    def test_jog_failure_shows_guidance_naming_motors_off(self):
        self.ctrl = _ErrorOneController()
        code = self.run_session(TO_LOOP + ARM + ["q", "BASE -1", "n", "g"])
        self.assertEqual(code, EXIT_FAILED)
        self.assertEqual(self.of("fault_guidance")[0]["key"], "error_too_large")
        shown = "\n".join(self.op.shown)
        self.assertIn("motor power", shown)
        self.assertIn("start pose", shown)

    def test_led_gate_at_arming_shows_led_guidance(self):
        self.run_session(TO_LOOP + ["a", "door", "n", "g", "n", "g"])
        self.assertEqual(self.of("fault_guidance")[0]["key"], "led_gate")

    def test_drift_refusal_shows_drift_guidance(self):
        self.run_session(TO_LOOP + ARM + [self.nudge("base", 21, "q")] + FINISH)
        self.assertEqual(self.of("fault_guidance")[0]["key"], "counts_drift")

    def test_declined_before_enable_shows_no_guidance(self):
        self.run_session(["y", "n"])
        self.assertFalse(self.of("fault_guidance"))

    def nudge(self, motor, counts, then):
        def answer():
            self.ctrl.counts[motor] += counts
            return then
        return answer

    def test_counts_drift_beyond_the_settle_band_refuses_the_next_jog(self):
        code = self.run_session(TO_LOOP + ARM + ["q", "BASE -1"] + OBS
                                + [self.nudge("base", 21, "q")] + FINISH)
        self.assertEqual(code, EXIT_OK)
        drift = self.of("counts_drift")
        self.assertEqual(len(drift), 1)
        self.assertEqual(drift[0]["differences"]["base"], 21)
        self.assertEqual(drift[0]["limit"], 20)
        self.assertEqual(len(self.jog_commands()), 1)
        self.assertEqual(self.of("disarmed")[-1]["reason"], "counts drift")

    def test_counts_within_the_settle_band_do_not_refuse(self):
        self.run_session(TO_LOOP + ARM + ["q", "BASE -1"] + OBS
                         + [self.nudge("base", 20, "q")] + OBS + FINISH)
        self.assertFalse(self.of("counts_drift"))
        self.assertEqual(len(self.jog_commands()), 2)

    def test_drift_after_homing_refuses_the_first_jog(self):
        self.run_session(TO_LOOP + ARM + [self.nudge("elbow", -25, "q")] + FINISH)
        self.assertEqual(len(self.of("counts_drift")), 1)
        self.assertFalse(self.jog_commands())

    def test_drift_on_a_motor_no_plan_moves_also_refuses(self):
        self.run_session(TO_LOOP + ARM + [self.nudge("wrist_motor_1", 21, "q")] + FINISH)
        self.assertEqual(self.of("counts_drift")[0]["differences"]["wrist_motor_1"], 21)
        self.assertFalse(self.jog_commands())


    def plan_moves_run(self):
        return [r["move"] for r in self.of("jog_confirmed") if r["how"] in ("back", "goto")]

    def test_back_to_start_after_mixed_jogs(self):
        code = self.run_session(TO_LOOP + ARM + MIXED + ["b", "BACK", "y"] + FINISH)
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(self.of("plan_shown")[0]["moves"], ["ELBOW +1", "BASE +1", "BASE +1"])
        self.assertEqual(self.plan_moves_run(), ["ELBOW +1", "BASE +1", "BASE +1"])
        complete = self.of("plan_complete")[0]
        self.assertEqual((complete["name"], complete["answer"], complete["steps"]),
                         ("start", "yes", 3))
        self.assertEqual(set(complete["count_differences"].values()), {0})
        self.assertEqual(len(self.of("jog_observation")), 3)  # plan steps ask nothing

    def test_declined_back_moves_nothing_and_disarms(self):
        self.run_session(TO_LOOP + ARM + ["q", "BASE -1"] + OBS + ["b", "NO"] + FINISH)
        self.assertEqual(len(self.of("plan_declined")), 1)
        self.assertEqual(len(self.jog_commands()), 1)
        self.assertEqual(self.of("disarmed")[-1]["reason"], "confirmation declined")

    def test_key_during_a_plan_stops_it_after_the_current_step(self):
        original, seen = self.ctrl._apply, []

        def apply(payload):
            code = original(payload)
            if payload[0] in JOG_ORDERS:
                seen.append(payload[0])
                if len(seen) == 4:          # 3 keyed jogs, then the first plan step
                    self.op.pending_keys = 1
            return code
        self.ctrl._apply = apply
        self.run_session(TO_LOOP + ARM + MIXED + ["b", "BACK"] + FINISH)
        stopped = self.of("plan_stopped")[0]
        self.assertEqual((stopped["steps_done"], stopped["reason"]),
                         (1, "stopped by a key press"))
        self.assertEqual(len(self.jog_commands()), 4)
        self.assertEqual(self.of("disarmed")[-1]["reason"], "plan stopped")
        self.assertTrue(any("not an emergency stop" in m for m in self.op.shown))

    def test_drift_during_the_typed_confirmation_refuses_the_jog(self):
        code = self.run_session(TO_LOOP + ARM + ["q", self.nudge("base", 21, "BASE -1")] + FINISH)
        self.assertEqual(code, EXIT_OK)
        drift = self.of("counts_drift")
        self.assertEqual(len(drift), 1)
        self.assertEqual(drift[0]["differences"]["base"], 21)
        self.assertFalse(self.jog_commands())
        self.assertFalse(self.of("jog_confirmed"))
        self.assertEqual(self.of("disarmed")[-1]["reason"], "counts drift")

    def test_key_during_the_last_plan_step_cannot_answer_the_arrival_question(self):
        original, seen = self.ctrl._apply, []

        def apply(payload):
            code = original(payload)
            if payload[0] in JOG_ORDERS:
                seen.append(payload[0])
                if len(seen) == 6:          # 3 keyed jogs, then the last of 3 plan steps
                    self.op.pending_keys = 1
            return code
        self.ctrl._apply = apply
        self.run_session(TO_LOOP + ARM + MIXED + ["b", "BACK", "y"] + FINISH)
        complete = self.of("plan_complete")[0]
        self.assertEqual(complete["keys_during_last_step"], 1)
        self.assertEqual(complete["answer"], "yes")
        self.assertTrue(any("had already finished" in m for m in self.op.shown))

    def test_key_pending_before_the_first_step_stops_the_plan(self):
        def keyed_confirm():
            self.op.pending_keys = 1
            return "BACK"
        self.run_session(TO_LOOP + ARM + ["q", "BASE -1"] + OBS + ["b", keyed_confirm] + FINISH)
        self.assertEqual(self.of("plan_stopped")[0]["steps_done"], 0)
        self.assertEqual(len(self.jog_commands()), 1)
        self.assertEqual(self.op.pending_keys, 0)

    def test_plan_without_key_reading_says_so_in_a_rehearsal(self):
        self.run_session(TO_LOOP + ARM + ["q", "BASE -1"] + OBS + ["b", "BACK", "y"] + FINISH,
                         stop_on_key=False)
        self.assertTrue(any("cannot read keys during the move" in m for m in self.op.shown))
        self.assertFalse(any("Any key during the move" in m for m in self.op.shown))
        self.assertEqual(len(self.of("plan_complete")), 1)

    def test_plan_without_key_reading_is_refused_on_real_data(self):
        self.run_session(TO_LOOP + ARM + ["q", "BASE -1"] + OBS + ["b"] + FINISH,
                         stop_on_key=False, data_source="real")
        refused = self.of("plan_refused")
        self.assertEqual(len(refused), 1)
        self.assertIn("cannot read keys", refused[0]["reason"])
        self.assertEqual(len(self.jog_commands()), 1)
        self.assertFalse(any("Type BACK" in p for p in self.op.prompts))
        self.assertEqual(self.of("disarmed")[-1]["reason"], "plan refused")
        # Real data never shows the modeled panel, even if the robot object has one.
        self.assertFalse(any(m.startswith("SIMULATED panel") for m in self.op.shown))

    def test_mark_then_go_to_it(self):
        self.run_session(TO_LOOP + ARM + ["q", "BASE -1"] + OBS + ["m", "q"] + OBS
                         + ["g", "1", "GOTO P1", "y"] + FINISH)
        mark = self.of("position_marked")[0]
        self.assertEqual((mark["name"], mark["travel"]["base"]), ("P1", -1.0))
        self.assertEqual(self.plan_moves_run(), ["BASE +1"])
        self.assertEqual(set(self.of("plan_complete")[0]["count_differences"].values()), {0})

    def test_go_to_with_no_marks_and_a_tenth_mark_are_refused(self):
        self.run_session(TO_LOOP + ARM + ["g"] + ["m"] * 10 + FINISH)
        self.assertFalse(self.of("plan_shown"))
        self.assertEqual(len(self.of("position_marked")), 9)
        self.assertEqual(len(self.of("mark_refused")), 1)

    def test_invalid_mark_number_moves_nothing(self):
        self.run_session(TO_LOOP + ARM + ["m", "m", "g", "7", "7", "7"] + FINISH)
        self.assertFalse(self.of("plan_shown"))
        self.assertTrue(any("No mark chosen" in m for m in self.op.shown))

    def test_back_while_disarmed_does_nothing(self):
        self.run_session(TO_LOOP + ["b"] + FINISH)
        self.assertFalse(self.of("plan_shown"))
        self.assertTrue(any("press a to arm" in m for m in self.op.shown))

    def test_back_when_already_at_start_asks_nothing(self):
        self.run_session(TO_LOOP + ARM + ["b"] + FINISH)
        self.assertEqual(self.of("plan_shown")[0]["moves"], [])
        self.assertFalse(any("Type BACK" in p for p in self.op.prompts))
        self.assertFalse(self.jog_commands())

    def test_back_after_half_steps_uses_a_half_step(self):
        self.run_session(TO_LOOP + ARM + ["s", "q", "BASE -0.5"] + OBS + ["s", "b", "BACK", "y"]
                         + FINISH)
        self.assertEqual(self.plan_moves_run(), ["BASE +0.5"])

    def test_end_of_input_at_the_arrival_question_still_completes(self):
        code = self.run_session(TO_LOOP + ARM + ["q", "BASE -1"] + OBS + ["b", "BACK"])
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(self.of("plan_complete")[0]["answer"], "unsure")
        self.assertEqual(len(self.of("summary")), 1)

    def test_fault_in_a_plan_latches_and_still_finishes(self):
        def inject_then_type():
            self.ctrl.inject("controller_error")
            return "BACK"
        code = self.run_session(TO_LOOP + ARM + ["q", "BASE -1"] + OBS
                                + ["b", inject_then_type, "n", "g"])
        self.assertEqual(code, EXIT_FAILED)
        self.assertEqual(self.of("plan_stopped")[0]["steps_done"], 0)
        self.assertEqual(len(self.of("jog_failed")), 1)
        self.assertEqual(len(self.of("summary")), 1)


class LabCommandTests(unittest.TestCase):
    def test_dropped_motors_rehearsal_needs_simulate(self):
        import contextlib
        import io

        from scorbot.lab.__main__ import main
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exit_:
            main(["--rehearse-motors-dropped"])
        self.assertEqual(exit_.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
