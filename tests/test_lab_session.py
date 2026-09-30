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
ARM = ["a", "door", "ARM"]            # first arming also names the landmark
REARM = ["a", "ARM"]
FINISH = ["x", "n", "g"]
JOG_ORDERS = set(range(4, 14))


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

    def run_session(self, answers):
        self.op = ScriptedOperator(answers)
        session = LabSession(
            profile=PROFILE, operator=self.op,
            robot_factory=lambda **kw: SimulatedScorbot(controller=self.ctrl, **kw),
            data_source="simulated", log_path=self.root / "s.jsonl",
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
                         ["after_connect", "after_enable", "after_disable"])
        self.assertIn("disabled", self.types())
        summary = self.of("summary")[0]
        self.assertEqual((summary["jogs"], summary["problems"]), (3, 0))
        self.assertTrue(all(row["data_source"] == "simulated" for row in self.of("session")))
        self.assertTrue(any("LOG CHECK: 0 problems" in m for m in self.op.shown))

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


if __name__ == "__main__":
    unittest.main()
