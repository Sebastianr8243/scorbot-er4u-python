"""Guided session engine with the simulated controller and scripted answers."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

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
# --inch-home (the vendor routine) asks "bring the arm near home first?" before HOME and
# "return home before the motors go off?" after the keys; these answer both with no.
INCH_NO_PRE_HOME = CHECKLIST + ["n", "g", "pose matches photo", "n", "HOME", "y", "g",
                                "all axes homed", "y", "y"]
NO_PARK = ["x", "n"]
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
        # These tests exercise the session's cap mechanism, not its production
        # value (limits.TRAVEL_CAP_DEG, lifted from 10 to 180 on 2026-10-06).
        patcher = mock.patch("scorbot.lab.session.TRAVEL_CAP_DEG", 10.0)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ctrl = SimulatedController()
        self.clock = Clock()

    def tearDown(self):
        self._tmp.cleanup()

    def run_session(self, answers, *, stop_on_key=True, data_source="simulated",
                    **session_kwargs):
        self.op = ScriptedOperator(answers)
        self.op.stop_on_key = stop_on_key
        session = LabSession(
            profile=PROFILE, operator=self.op,
            robot_factory=lambda **kw: SimulatedScorbot(controller=self.ctrl, **kw),
            data_source=data_source, log_path=self.root / "s.jsonl",
            session_root=self.root / "sessions", clock=self.clock, sleep=lambda s: None,
            **session_kwargs)
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

    def test_inch_home_homes_by_inching_with_the_same_typed_words(self):
        # --inch-home: the same prompts (HOME, the observation, home ok), but the home is
        # found by small legacy jogs reading modeled switches; no order 18.
        from scorbot.simulated import SimulatorProfile
        self.ctrl = SimulatedController(profile=SimulatorProfile(model_homing=True),
                                        start_counts={"base": 2000, "shoulder": -1500,
                                                      "elbow": 1200})
        code = self.run_session(INCH_NO_PRE_HOME + NO_PARK + FINISH[1:], inch_home=True)
        self.assertEqual(code, EXIT_OK)
        self.assertEqual([row.get("method") for row in self.of("home_complete")], ["inch"])
        orders = {c[0] for c in self.ctrl.commands if c}
        self.assertFalse(orders & {18}, "no legacy home order was sent")
        self.assertTrue(orders & set(range(4, 10)), "the joints were inched with legacy jogs")
        self.assertIn("Inch homing", " ".join(self.op.shown))

    def routine_controller(self):
        from scorbot.simulated import SimulatorProfile
        self.ctrl = SimulatedController(profile=SimulatorProfile(model_homing=True),
                                        start_counts={"base": 600, "shoulder": -500,
                                                      "elbow": 200})

    def test_pre_home_moves_then_home_then_park_follow_the_vendor_routine(self):
        # Pose, "bring it near home first" yes, enable and LED check, two typed moves, Enter,
        # then HOME, the home observation, home ok; at the end "return home" yes and PARK.
        self.routine_controller()
        answers = (CHECKLIST + ["n", "g", "pose matches photo", "y", "y", "g",
                                "SHOULDER +1", "elbow -2", "base 1", "",
                                "HOME", "all axes homed", "y", "y"]
                   + ["x", "y", "door", "y", "g", "ARM", "PARK"] + FINISH[1:])
        code = self.run_session(answers, inch_home=True)
        self.assertEqual(code, EXIT_OK)
        self.assertIn("wrist alignment is not verified", " ".join(self.op.shown))
        moves = self.of("pre_home_move")
        self.assertEqual([(m["joint"], m["degrees"]) for m in moves],
                         [("shoulder", 1.0), ("elbow", -2.0), ("base", 1.0)])
        self.assertEqual(len(self.of("enabled")), 1, "the motors were enabled once")
        self.assertEqual([r.get("method") for r in self.of("home_complete")], ["inch"])
        parked = self.of("park")
        self.assertEqual(len(parked), 1)
        self.assertTrue(parked[0]["parked"], parked[0])
        orders = [c[0] for c in self.ctrl.commands if c]
        self.assertNotIn(18, orders)
        self.assertTrue(set(orders) & {10, 11}, "the wrist pitch followed")
        self.assertFalse(set(orders) & {12, 13}, "never a wrist roll")
        # the switch line is shown after every move
        self.assertGreaterEqual(sum("Home switches now" in s for s in self.op.shown), 4)

    PRE_HOME_ONE = (CHECKLIST + ["n", "g", "pose matches photo", "y", "y", "g",
                                 "SHOULDER +1", "", "HOME", "all axes homed", "y", "y"]
                    + ["x", "n"] + FINISH[1:])

    def test_the_pre_home_move_is_single_motor_by_default_and_says_so(self):
        self.routine_controller()
        code = self.run_session(self.PRE_HOME_ONE, inch_home=True)
        self.assertEqual(code, EXIT_OK)
        self.assertEqual([m["coupled"] for m in self.of("pre_home_move")], [False])
        shown = " ".join(self.op.shown)
        self.assertIn("only the motor", shown.lower())
        self.assertNotIn("as in SCORBASE", shown)

    def test_the_coupled_pre_home_move_is_opt_in_and_labelled_as_ours(self):
        self.routine_controller()
        code = self.run_session(self.PRE_HOME_ONE, inch_home=True, coupled_pre_home=True)
        self.assertEqual(code, EXIT_OK)
        self.assertEqual([m["coupled"] for m in self.of("pre_home_move")], [True])
        shown = " ".join(self.op.shown)
        self.assertIn("our own", shown)
        self.assertIn("never run on the arm", shown)

    def test_a_wrist_that_is_not_where_home_leaves_it_ends_the_session_before_any_jog(self):
        # Inch homing moves the wrist pitch relative to the forearm and never homes it, so after
        # the search its angle at home is only what the search left: the operator must say so.
        self.routine_controller()
        answers = CHECKLIST + ["n", "g", "pose matches photo", "n", "HOME", "y", "g",
                               "all axes homed", "y", "n", "g"]
        code = self.run_session(answers, inch_home=True)
        self.assertEqual(code, EXIT_DECLINED)
        self.assertEqual(self.of("wrist_ok")[0]["answer"], "no")
        self.assertEqual(self.of("armed"), [])
        self.assertIn("wrist", " ".join(self.op.shown).lower())

    def test_a_stop_during_the_pre_home_step_ends_that_step(self):
        # After a stop the operator must start the next phase again, not type another move.
        from unittest.mock import patch
        from scorbot import MotionStopped
        from scorbot.simulated import SimulatedScorbot
        self.routine_controller()
        calls = []

        def stopped(robot, *args, **kwargs):
            calls.append(args)
            raise MotionStopped("stop requested")

        answers = (CHECKLIST + ["n", "g", "pose matches photo", "y", "y", "g",
                                "SHOULDER +1", "SHOULDER +1", "HOME", "y", "g", "all axes homed",
                                "y", "y"] + ["x", "n"] + FINISH[1:])
        with patch.object(SimulatedScorbot, "pre_home_jog", stopped):
            self.run_session(answers, inch_home=True)
        self.assertEqual(len(calls), 1, "no second move was attempted after the stop")
        self.assertEqual(len(self.of("pre_home_stopped")), 1)
        done = self.of("pre_home_done")
        self.assertTrue(done and done[0].get("stopped"))

    def test_a_pre_home_command_that_is_not_allowed_moves_nothing_and_asks_again(self):
        self.routine_controller()
        answers = (CHECKLIST + ["n", "g", "pose matches photo", "y", "y", "g",
                                "WRIST +1", "SHOULDER +9", "SHOULDER", "gripper 1", "base x",
                                "SHOULDER +1", "",
                                "HOME", "all axes homed", "y", "y"]
                   + ["x", "n"] + FINISH[1:])
        before = len(self.ctrl.commands)
        code = self.run_session(answers, inch_home=True)
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(len(self.of("pre_home_move")), 1)       # only the one good command
        shown = " ".join(self.op.shown)
        for refused in ("WRIST +1", "SHOULDER +9", "gripper 1"):
            self.assertIn(refused, shown)
        self.assertGreater(len(self.ctrl.commands), before)

    def test_park_after_a_disarm_needs_the_arming_checks_again(self):
        # A disarm (idle, counts drift, a refused plan) is a safety decision: the park must not
        # move the arm past it. It asks for the LED check and ARM again, like a jog would.
        self.routine_controller()

        def idle_then_help():
            self.clock.t += 90.0              # past IDLE_DISARM_S: the next key disarms
            return "?"

        answers = (INCH_NO_PRE_HOME + ARM + [idle_then_help, "x", "y", "y", "g", "ARM", "PARK"]
                   + FINISH[1:])
        code = self.run_session(answers, inch_home=True)
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(len(self.of("armed")), 2, "armed for the keys, and again to park")
        self.assertEqual(len(self.of("park")), 1)
        self.assertIn("disarm", " ".join(self.op.shown).lower())

    def test_x_after_a_long_idle_still_needs_a_fresh_arming_to_park(self):
        # Pressing x leaves the key loop before the idle check runs, so the session still
        # believes it is armed. The park must not trust that.
        self.routine_controller()

        def idle_then_x():
            self.clock.t += 90.0
            return "x"

        answers = (INCH_NO_PRE_HOME + ARM + [idle_then_x, "y", "y", "g", "ARM", "PARK"]
                   + FINISH[1:])
        code = self.run_session(answers, inch_home=True)
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(len(self.of("armed")), 2, "armed for the keys, and again to park")
        self.assertEqual(len(self.of("park")), 1)

    def test_the_inch_home_warning_does_not_claim_the_coupled_motors_are_held(self):
        self.routine_controller()
        self.run_session(INCH_NO_PRE_HOME + NO_PARK + FINISH[1:], inch_home=True)
        shown = " ".join(self.op.shown)
        self.assertNotIn("motors held", shown)
        self.assertIn("follow", shown)

    def test_a_park_the_operator_does_not_rearm_for_moves_nothing(self):
        self.routine_controller()
        answers = INCH_NO_PRE_HOME + ["x", "y", "door", "y", "g", "n"] + FINISH[1:]
        code = self.run_session(answers, inch_home=True)
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(self.of("park"), [])

    def test_a_park_that_did_not_complete_fails_the_session_and_says_so(self):
        self.routine_controller()

        def displace_the_roll_then_yes():
            with self.ctrl._lock:             # both wrist motors the same way: a roll
                self.ctrl.counts["wrist_motor_1"] += 200
                self.ctrl.counts["wrist_motor_2"] += 200
            return "y"

        answers = (INCH_NO_PRE_HOME + ["x", displace_the_roll_then_yes, "door", "y", "g", "ARM",
                                       "PARK"] + FINISH[1:])
        code = self.run_session(answers, inch_home=True)
        # The wrist drifted with nothing commanded: the fresh drift check refuses to park.
        self.assertEqual(code, EXIT_FAILED)
        self.assertEqual(self.of("park"), [], "the park never ran")
        self.assertEqual(len(self.of("counts_drift")), 1)
        summary = self.of("summary")[0]
        self.assertGreaterEqual(summary["problems"], 1)
        self.assertTrue(summary["unparked"])

    def test_counts_that_move_while_the_park_prompt_is_open_stop_the_park(self):
        self.routine_controller()

        def displace_then_park():
            with self.ctrl._lock:
                self.ctrl.counts["wrist_motor_1"] += 200
                self.ctrl.counts["wrist_motor_2"] += 200
            return "PARK"

        answers = (INCH_NO_PRE_HOME + ["x", "y", "door", "y", "g", "ARM", displace_then_park]
                   + FINISH[1:])
        code = self.run_session(answers, inch_home=True)
        self.assertEqual(code, EXIT_FAILED)
        self.assertEqual(self.of("park"), [], "the park never ran")
        self.assertEqual(len(self.of("counts_drift")), 1)

    def test_the_log_review_flags_a_park_that_did_not_complete(self):
        from scorbot.lab.review import review_session_rows
        base = [{"type": "session"}]
        baseline = len(review_session_rows(base)["problems"])   # a bare session has its own
        clean = review_session_rows(base + [{"type": "park", "parked": True, "reason": None}])
        self.assertEqual(len(clean["problems"]), baseline)
        for row in ({"type": "park", "parked": False, "reason": "stopped short"},
                    {"type": "park_stopped"}):
            with self.subTest(row=row["type"]):
                self.assertEqual(len(review_session_rows(base + [row])["problems"]), baseline + 1)

    def test_declining_the_park_leaves_the_arm_where_it_is(self):
        self.routine_controller()
        answers = INCH_NO_PRE_HOME + ["x", "n"] + FINISH[1:]
        code = self.run_session(answers, inch_home=True)
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(self.of("park"), [])

    def test_a_fault_during_a_pre_home_move_ends_the_session_with_the_motors_off(self):
        self.routine_controller()
        self.ctrl.inject("controller_error")
        answers = (CHECKLIST + ["n", "g", "pose matches photo", "y", "y", "g",
                                "SHOULDER +1"] + FINISH[1:])
        code = self.run_session(answers, inch_home=True)
        self.assertEqual(code, EXIT_FAILED)
        self.assertIn("session_failed", self.types())
        self.assertIn(16, [c[0] for c in self.ctrl.commands if c])   # motors off

    def test_the_legacy_flow_has_neither_the_pre_home_question_nor_the_park(self):
        code = self.run_session(TO_LOOP + FINISH)
        self.assertEqual(code, EXIT_OK)
        shown = " ".join(self.op.shown)
        self.assertNotIn("near home first", shown)
        self.assertNotIn("PARK", shown)

    def test_the_legacy_home_is_still_the_default(self):
        code = self.run_session(TO_LOOP + FINISH)
        self.assertEqual(code, EXIT_OK)
        self.assertIn(18, {c[0] for c in self.ctrl.commands if c})
        self.assertNotIn("method", self.of("home_complete")[0])

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
