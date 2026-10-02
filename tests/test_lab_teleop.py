"""Teleop mode of the guided session: simulated controller, scripted keys."""

import json
from pathlib import Path
import tempfile
import unittest

from scorbot import SimulatedScorbot
from scorbot.lab.operator import TICK, ScriptedOperator
from scorbot.lab.profile import LabProfile
from scorbot.lab.session import EXIT_OK, LabSession
from scorbot.simulated import SimulatedController

PROFILE = LabProfile("lab-er4u-1", "A-12", "C-3", "WinUSB", "SR")
TO_LOOP = ["y"] * 4 + ["n", "g", "pose matches photo", "HOME", "y", "g", "all axes homed", "y"]
ARM = ["a", "door", "y", "g", "ARM"]
FINISH = ["x", "n", "g"]
JOG_ORDERS = set(range(4, 14))


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class _Harness(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.ctrl = SimulatedController()
        self.clock = Clock()

    def tearDown(self):
        self._tmp.cleanup()

    def run_session(self, answers, *, release_gate=True, camera_factory=None):
        self.op = ScriptedOperator(answers)
        self.op.release_gate = release_gate
        self.session = LabSession(
            profile=PROFILE, operator=self.op,
            robot_factory=lambda **kw: SimulatedScorbot(controller=self.ctrl, **kw),
            data_source="simulated", log_path=self.root / "s.jsonl",
            session_root=self.root / "sessions", clock=self.clock, sleep=lambda s: None,
            camera_factory=camera_factory)
        code = self.session.run()
        self.rows = [json.loads(line) for line in
                     (self.root / "s.jsonl").read_text(encoding="utf-8").splitlines()]
        return code

    def of(self, kind):
        return [row for row in self.rows if row["type"] == kind]

    def jogs(self):
        return [c for c in self.ctrl.commands if c and c[0] in JOG_ORDERS]

    def mcap_episodes(self):
        from scorbot.session.replay import load_session
        [folder] = [p for p in (self.root / "sessions").iterdir() if p.is_dir()]
        return [e["payload"] for e in load_session(folder).events
                if e["topic"] == "/session/episode"]


class TeleopTests(_Harness):
    def test_press_moves_without_questions(self):
        code = self.run_session(TO_LOOP + ARM + ["t", "q", "q", "t"] + FINISH)
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(len(self.jogs()), 2)
        self.assertEqual(self.of("jog_observation"), [])
        self.assertEqual([r["how"] for r in self.of("jog_confirmed")], ["teleop", "teleop"])
        self.assertEqual(self.op.releases, ["t", "q", "q", "t"])
        self.assertTrue(all(r["accepted"] for r in self.of("teleop_intent")
                            if r["action"] == "jog"))
        self.assertEqual(len(self.of("teleop_start")), 1)

    def test_teleop_needs_arming(self):
        self.run_session(TO_LOOP + ["t"] + FINISH)
        self.assertEqual(self.of("teleop_start"), [])
        self.assertTrue(any("press a" in m for m in self.op.shown))

    def test_refused_without_release_gate(self):
        self.run_session(TO_LOOP + ARM + ["t"] + FINISH, release_gate=False)
        self.assertEqual(len(self.of("teleop_refused")), 1)
        self.assertEqual(self.jogs(), [])

    def test_repeats_queued_during_a_step_are_discarded_and_logged(self):
        def press_with_repeats():
            self.op.pending_keys = 7
            return "q"
        self.run_session(TO_LOOP + ARM + ["t", press_with_repeats, "t"] + FINISH)
        self.assertEqual(len(self.jogs()), 1)
        [intent] = [r for r in self.of("teleop_intent") if r["action"] == "jog"]
        self.assertEqual(intent["discarded_keys"], 7)

    def test_cap_refusal_disarms_and_leaves_teleop(self):
        self.run_session(TO_LOOP + ARM + ["t"] + ["q"] * 11 + FINISH)
        self.assertEqual(len(self.jogs()), 10)
        self.assertEqual(self.of("disarmed")[-1]["reason"], "travel cap")
        self.assertFalse(self.of("teleop_intent")[-1]["accepted"])
        self.assertEqual(len(self.of("teleop_end")), 1)

    def test_unknown_key_disarms(self):
        self.run_session(TO_LOOP + ARM + ["t", "z"] + FINISH)
        self.assertTrue(self.of("disarmed")[-1]["reason"].startswith("unknown key"))
        self.assertEqual(self.of("teleop_intent")[-1]["key"], "z")

    def test_idle_disarms_after_fifteen_seconds(self):
        def tick_later():
            self.clock.t += 16.0
            return TICK
        self.run_session(TO_LOOP + ARM + ["t", tick_later] + FINISH)
        self.assertIn("idle", self.of("disarmed")[-1]["reason"])

    def test_episode_start_and_end(self):
        self.run_session(TO_LOOP + ARM + ["t", "r", "reach left", "q", "r", "t"] + FINISH)
        [start] = self.of("episode_start")
        [end] = self.of("episode_end")
        self.assertEqual((start["episode"], start["task"], start["camera"]),
                         (1, "reach left", False))
        self.assertEqual((end["status"], end["jogs"]), ("completed", 1))
        self.assertEqual([(p["event"], p["status"]) for p in self.mcap_episodes()],
                         [("start", None), ("end", "completed")])
        self.assertEqual(self.op.releases.count("r"), 2)

    def test_task_asked_once_and_new_task_key(self):
        self.run_session(TO_LOOP + ARM + ["t", "r", "first", "r", "r", "r", "n", "second",
                                          "r", "r", "t"] + FINISH)
        self.assertEqual([r["task"] for r in self.of("episode_start")],
                         ["first", "first", "second"])

    def test_new_task_refused_during_episode(self):
        self.run_session(TO_LOOP + ARM + ["t", "r", "first", "n", "r", "t"] + FINISH)
        self.assertEqual([r["task"] for r in self.of("episode_start")], ["first"])
        self.assertTrue(any("close the episode" in m for m in self.op.shown))

    def test_empty_task_refuses_episode(self):
        self.run_session(TO_LOOP + ARM + ["t", "r", "", "t"] + FINISH)
        self.assertEqual(self.of("episode_start"), [])

    def test_disarm_aborts_open_episode(self):
        self.run_session(TO_LOOP + ARM + ["t", "r", "task", "z"] + FINISH)
        [end] = self.of("episode_end")
        self.assertEqual((end["status"], end["reason"]), ("aborted", "disarmed"))

    def test_finish_aborts_open_episode(self):
        code = self.run_session(TO_LOOP + ARM + ["t", "r", "task", "x", "n", "g"])
        self.assertEqual(code, EXIT_OK)
        [end] = self.of("episode_end")
        self.assertEqual((end["status"], end["reason"]), ("aborted", "finish"))
        self.assertEqual(len(self.of("summary")), 1)


if __name__ == "__main__":
    unittest.main()
