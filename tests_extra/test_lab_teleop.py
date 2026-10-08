"""Teleop mode of the guided session: simulated controller, scripted keys."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

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
        # These tests exercise the session's cap mechanism, not its production
        # value (limits.TRAVEL_CAP_DEG, lifted from 10 to 180 on 2026-10-06).
        patcher = mock.patch("scorbot.lab.session.TRAVEL_CAP_DEG", 10.0)
        patcher.start()
        self.addCleanup(patcher.stop)
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
        self.run_session(TO_LOOP + ARM + ["t", "r", "reach left", "q", "r", "y", "t"]
                         + FINISH)
        [start] = self.of("episode_start")
        [end] = self.of("episode_end")
        self.assertEqual((start["episode"], start["task"], start["camera"]),
                         (1, "reach left", False))
        self.assertEqual((end["status"], end["jogs"]), ("completed", 1))
        self.assertEqual([(p["event"], p["status"]) for p in self.mcap_episodes()],
                         [("start", None), ("end", "completed")])
        self.assertEqual(self.op.releases.count("r"), 2)

    def test_operator_marking_the_task_not_done_aborts_the_episode(self):
        self.run_session(TO_LOOP + ARM + ["t", "r", "reach", "q", "r", "n", "t"] + FINISH)
        [end] = self.of("episode_end")
        self.assertEqual((end["status"], end["reason"]),
                         ("aborted", "operator: task not done"))
        self.assertEqual([(p["event"], p["status"]) for p in self.mcap_episodes()],
                         [("start", None), ("end", "aborted")])

    def test_task_asked_once_and_new_task_key(self):
        self.run_session(TO_LOOP + ARM + ["t", "r", "first", "r", "y", "r", "r", "y", "n",
                                          "second", "r", "r", "y", "t"] + FINISH)
        self.assertEqual([r["task"] for r in self.of("episode_start")],
                         ["first", "first", "second"])

    def test_new_task_refused_during_episode(self):
        self.run_session(TO_LOOP + ARM + ["t", "r", "first", "n", "r", "y", "t"] + FINISH)
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


class TeleopReviewFixTests(_Harness):
    def test_task_keys_are_released_before_the_prompt(self):
        seen = []

        def press(key):
            def answer():
                self.op.pending_keys = 5   # auto-repeats queued while held
                return key
            return answer

        def task(text):
            def answer():
                seen.append(self.op.pending_keys)   # repeats must be gone by now
                return text
            return answer
        self.run_session(TO_LOOP + ARM + ["t", press("r"), task("first"), "r", "y",
                                          press("n"), task("second"), "t"] + FINISH)
        self.assertEqual(seen, [0, 0])
        self.assertEqual(self.op.releases[:3], ["t", "r", "r"])

    def test_episode_numbers_continue_after_leaving_teleop(self):
        self.run_session(TO_LOOP + ARM + ["t", "r", "task", "r", "y", "t", "t", "r", "r", "y",
                                          "t"] + FINISH)
        self.assertEqual([r["episode"] for r in self.of("episode_start")], [1, 2])
        self.assertEqual([r["task"] for r in self.of("episode_start")], ["task", "task"])

    def test_camera_opens_before_the_controller_connects(self):
        def broken():
            raise RuntimeError("no camera")
        self.run_session(TO_LOOP + FINISH, camera_factory=broken)
        types = [r["type"] for r in self.rows]
        self.assertLess(types.index("camera_unavailable"), types.index("connected"))

    def test_episode_without_new_frames_is_not_completed(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from scorbot.lab.teleop import Teleop
        rows = []
        session = SimpleNamespace(
            op=ScriptedOperator(["task", "y"]), rec=Mock(), episode_count=0,
            episode_task=None,
            _write=lambda kind, **fields: rows.append((kind, fields)))
        camera = SimpleNamespace(live=lambda: True, problem=lambda: "camera stalled",
                                 frames_written=lambda: 42, drain=lambda: [])
        teleop = Teleop(session, camera)
        teleop._toggle_episode()
        teleop._toggle_episode()
        [end] = [fields for kind, fields in rows if kind == "episode_end"]
        self.assertEqual((end["status"], end["frames"]), ("aborted", 0))
        self.assertIn("no camera frames", end["reason"])


class LabCameraTests(unittest.TestCase):
    def test_recorder_start_failure_closes_source_and_stream(self):
        from unittest.mock import patch
        from scorbot.camera.source import FakeSource
        from scorbot.lab.camera import LabCamera
        from scorbot.session import SessionWriter
        with tempfile.TemporaryDirectory() as folder:
            writer = SessionWriter.create(folder, data_source="simulated", robot_id="arm-1",
                                          camera_ids=["main"])
            source = FakeSource()
            camera = LabCamera(lambda: source)
            with patch("scorbot.lab.camera.CameraRecorder.start",
                       side_effect=RuntimeError("thread start failed")):
                with self.assertRaises(RuntimeError):
                    camera.start(writer)
            self.assertTrue(source._closed.is_set())
            self.assertTrue((writer.path / "camera-main.json").is_file())
            writer.close()


def needs_cv2(test):
    try:
        import cv2  # noqa: F401
    except ImportError:
        return unittest.skip("opencv not installed (pip install .[camera])")(test)
    return test


class TeleopCameraTests(_Harness):
    def wait_live(self, then):
        def answer():
            import time
            for _ in range(300):
                if self.session.camera is not None and self.session.camera.live():
                    break
                time.sleep(0.01)
            return then
        return answer

    def pause(self, seconds, then=TICK):
        def answer():
            import time
            time.sleep(seconds)
            return then
        return answer

    @needs_cv2
    def test_episode_with_camera_records_frames(self):
        from scorbot.camera.source import FakeSource
        from scorbot.camera.stream import scan_stream
        self.run_session(TO_LOOP + ARM + ["t", self.wait_live("r"), "task", "q",
                                          self.pause(0.3, "r"), "y", "t"] + FINISH,
                         camera_factory=lambda: FakeSource(pace=True))
        [end] = self.of("episode_end")
        self.assertEqual(end["status"], "completed")
        self.assertGreater(end["frames"], 0)
        self.assertTrue(self.of("episode_start")[0]["camera"])
        self.assertTrue(self.of("camera_health"))
        self.assertEqual(len(self.of("camera_stopped")), 1)
        [folder] = [p for p in (self.root / "sessions").iterdir() if p.is_dir()]
        self.assertEqual(scan_stream(folder, "main").errors, [])

    @needs_cv2
    def test_stalled_camera_aborts_episode_without_a_key_and_stays_armed(self):
        from scorbot.camera.source import FakeSource
        self.run_session(TO_LOOP + ARM + ["t", self.wait_live("r"), "task"]
                         + [self.pause(0.3)] * 6 + ["t"] + FINISH,
                         camera_factory=lambda: FakeSource(pace=True, block_at=15))
        [end] = self.of("episode_end")
        self.assertEqual((end["status"], end["reason"]), ("aborted", "camera stalled"))
        self.assertFalse(any("camera" in (r.get("reason") or "")
                             for r in self.of("disarmed")))

    @needs_cv2
    def test_failed_camera_refuses_episode(self):
        from scorbot.camera.source import FakeSource
        self.run_session(TO_LOOP + ARM + ["t", self.pause(0.3, "r"), "t"] + FINISH,
                         camera_factory=lambda: FakeSource(pace=True, fail_at=2))
        self.assertEqual(self.of("episode_start"), [])
        refused = [r for r in self.of("teleop_intent") if r["action"] == "episode_start"]
        self.assertIn("camera failed", refused[0]["reason"])

    def test_camera_open_failure_continues_without_video(self):
        def broken():
            raise RuntimeError("no camera at index 0")
        code = self.run_session(TO_LOOP + ARM + ["t", "r", "task", "r", "y", "t"] + FINISH,
                                camera_factory=broken)
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(len(self.of("camera_unavailable")), 1)
        self.assertFalse(self.of("episode_start")[0]["camera"])


if __name__ == "__main__":
    unittest.main()
