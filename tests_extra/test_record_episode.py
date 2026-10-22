"""examples/record_episode.py against FakeArm and fake cameras: no arm, no USB, no OpenCV."""

import json
import tempfile
import unittest
from pathlib import Path

from examples import record_episode
from scorbot import bridge
from scorbot.camera.source import FakeSource


def sources():
    return {"top": FakeSource(), "wrist": FakeSource()}


def encode(image, quality):
    return b"jpg%d" % quality


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.out = Path(self.dir.name) / "ep"

    def lines(self):
        text = (self.out / "steps.jsonl").read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines()]

    def rows(self):
        """The step rows, written before the command runs."""
        return [line for line in self.lines() if "state" in line]

    def outcomes(self):
        """What happened to each command, written after it."""
        return {line["step"]: line for line in self.lines() if "outcome" in line}

    def test_each_step_logs_both_frames_the_state_before_and_the_command_given(self):
        arm, cameras = bridge.FakeArm(), sources()
        commands = [{"dx": 10}, {}, {"gripper": False}, {"dz": -5, "gripper": True}]
        record_episode.record(arm, cameras, commands, self.out, task="reach", encode=encode)
        rows = self.rows()
        self.assertEqual([r["step"] for r in rows], [0, 1, 2, 3])
        self.assertEqual(rows[0]["state"]["x"], 300.0)      # the state is read before the command moves the arm
        self.assertEqual(rows[1]["state"]["x"], 310.0)
        self.assertEqual(rows[0]["action"], {"dx": 10, "dy": 0, "dz": 0, "gripper": None})
        self.assertEqual(rows[1]["action"], {"dx": 0, "dy": 0, "dz": 0, "gripper": None})
        self.assertEqual(rows[2]["action"]["gripper"], "close")
        self.assertEqual(rows[3]["state"]["gripper"], "closed")     # what was last commanded
        self.assertEqual(rows[3]["action"], {"dx": 0, "dy": 0, "dz": -5, "gripper": "open"})
        self.assertEqual(set(rows[0]["frames"]), {"top", "wrist"})
        for name, path in rows[2]["frames"].items():
            self.assertEqual((self.out / path).read_bytes(), b"jpg90")
            self.assertTrue(path.startswith(name + "/"))
        self.assertEqual((arm.x, arm.y, arm.z), (310.0, 0.0, 195.0))
        meta = json.loads((self.out / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual((meta["task"], meta["cameras"], meta["steps"]), ("reach", ["top", "wrist"], 4))

    def test_time_and_skew_are_logged(self):
        ticks = iter(x / 10 for x in range(1000))
        record_episode.record(bridge.FakeArm(), sources(), [{}, {}], self.out, encode=encode,
                              clock=lambda: next(ticks))
        first, second = self.rows()
        self.assertGreater(second["t_s"], first["t_s"])
        self.assertGreater(first["frame_skew_s"], 0)
        self.assertGreater(first["state_lag_s"], 0)

    def test_an_episode_folder_is_never_overwritten(self):
        self.out.mkdir()
        with self.assertRaises(FileExistsError):
            record_episode.record(bridge.FakeArm(), sources(), [{}], self.out, encode=encode)

    def test_a_camera_that_returns_no_frame_ends_the_episode_with_the_rows_so_far(self):
        cameras = {"top": FakeSource(), "wrist": FakeSource(fail_at=2)}
        with self.assertRaises(RuntimeError):
            record_episode.record(bridge.FakeArm(), cameras, [{}] * 5, self.out, encode=encode)
        self.assertEqual(len(self.rows()), 2)
        self.assertTrue(json.loads((self.out / "meta.json").read_text(encoding="utf-8"))["ended_early"])

    def test_an_arm_refusal_is_logged_on_its_row_and_stops_the_episode(self):
        class Refusing(bridge.FakeArm):
            def move_by(self, dx=0.0, dy=0.0, dz=0.0):
                raise bridge.ArmError("step ends outside the box")

        with self.assertRaises(bridge.ArmError):
            record_episode.record(Refusing(), sources(), [{}, {"dx": 5}, {}], self.out, encode=encode)
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertNotIn("error", rows[1])
        self.assertEqual(self.outcomes()[1]["outcome"], "refused")
        self.assertIn("outside the box", self.outcomes()[1]["error"])

    def test_the_row_is_on_disk_before_the_arm_moves_and_an_interrupt_is_recorded(self):
        seen = {}

        class Interrupted(bridge.FakeArm):
            def move_by(inner, dx=0.0, dy=0.0, dz=0.0):
                seen["rows_before_motion"] = len(self.rows())
                raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            record_episode.record(Interrupted(), sources(), [{"dx": 5}], self.out, encode=encode)
        self.assertEqual(seen["rows_before_motion"], 1)
        self.assertEqual(self.outcomes()[0]["outcome"], "interrupted")
        self.assertEqual(json.loads((self.out / "meta.json").read_text(encoding="utf-8"))["steps"], 1)

    def test_the_source_is_saved_so_rehearsals_cannot_pass_for_hardware(self):
        record_episode.record(bridge.FakeArm(), sources(), [{}], self.out, encode=encode, source="simulated")
        self.assertEqual(json.loads((self.out / "meta.json").read_text(encoding="utf-8"))["source"], "simulated")


class KeyboardTests(unittest.TestCase):
    def test_keys_become_commands_and_q_ends(self):
        keys = iter("dwrfoc q")
        commands = list(record_episode.keyboard_commands(lambda: next(keys), step_mm=10))
        self.assertEqual(commands, [{"dx": 10}, {"dy": 10}, {"dz": 10}, {"dz": -10},
                                    {"gripper": True}, {"gripper": False}, {}])

    def test_windows_extended_keys_are_consumed_whole(self):
        # Insert, Delete and End arrive as a prefix then R, S, O: they must not become r, s, o commands
        keys = iter(chr(0xe0) + "R" + chr(0xe0) + "S" + chr(0) + "O" + chr(0xe0) + "O" + "q")
        self.assertEqual(list(record_episode.keyboard_commands(lambda: next(keys), step_mm=10)), [])

    def test_unknown_keys_are_ignored(self):
        keys = iter("zzq")
        self.assertEqual(list(record_episode.keyboard_commands(lambda: next(keys), step_mm=10)), [])

    def test_main_does_not_start_without_go(self):
        self.assertEqual(record_episode.main(["--fake"], read=lambda _p: "no"), 1)

    def test_a_fake_rehearsal_runs_end_to_end(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "ep"
            keys = iter("dq")
            code = record_episode.main(["--fake", "--out", str(out)], read=lambda _p: "go", getch=lambda: next(keys),
                                       encode=encode)
            self.assertEqual(code, 0)
            self.assertEqual(json.loads((out / "meta.json").read_text(encoding="utf-8"))["source"], "simulated")
            lines = (out / "steps.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)         # the step row, then its outcome


if __name__ == "__main__":
    unittest.main()
