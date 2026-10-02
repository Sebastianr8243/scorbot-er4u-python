"""Replaying an exported episode: pure planning (scorbot.lab.replay) and lab key p."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scorbot.lerobot_export.load import MOTORS
from scorbot.lerobot_export.sidecar import SIDECAR, UNITS, step_counts

STEPS = step_counts()


def action(base=0.0, shoulder=0.0, elbow=0.0, wrist=0.0):
    return [float(base), float(shoulder), float(elbow), float(wrist), 0.0]


def write_dataset(root, actions, *, first=None, data_source="simulated",
                  robot_id="lab-er4u-1", episode=0, **record_overrides):
    """A dataset folder with only what replay reads (no LeRobot files needed)."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    record = {"dataset_episode": episode, "session": "s", "source_episode": 1,
              "task": "reach", "fps": 10, "motors": list(MOTORS), "units": UNITS,
              "step_counts": dict(STEPS), "first_state": first or actions[0],
              "final_state": actions[-1], "actions": actions}
    record.update(record_overrides)
    sidecar = root / SIDECAR
    sidecar.write_text(json.dumps(record) + "\n", encoding="utf-8")
    provenance = {"data_source": data_source, "units": UNITS,
                  "sessions": [{"robot_id": robot_id}],
                  "episodes_sidecar": SIDECAR,
                  "episodes_sidecar_sha256": hashlib.sha256(sidecar.read_bytes()).hexdigest()}
    (root / "scorbot_provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
    return root


B = STEPS["base"]
TWO_BASE_STEPS = [action(), action(), action(-B), action(-B + 3), action(-2 * B),
                  action(-2 * B - 2)]


class ReplayPlanningTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def problems(self, folder, *, data_source="simulated", robot_id="lab-er4u-1"):
        from scorbot.lab.replay import load_episode, preflight
        return preflight(load_episode(folder, 0), data_source=data_source, robot_id=robot_id,
                         step_counts=STEPS)

    def test_clean_episode_passes_and_plays_two_steps(self):
        from scorbot.lab.moves import Move
        from scorbot.lab.replay import load_episode, play_moves, start_travel
        folder = write_dataset(self.root / "d", TWO_BASE_STEPS)
        self.assertEqual(self.problems(folder), [])
        episode = load_episode(folder, 0)
        self.assertEqual(start_travel(episode, STEPS), {"base": 0.0, "shoulder": 0.0,
                                                        "elbow": 0.0})
        self.assertEqual(play_moves(episode, STEPS), [Move("base", -1.0), Move("base", -1.0)])

    def test_missing_dataset_and_unknown_episode_are_refused(self):
        from scorbot.lab.replay import ReplayRefused, load_episode
        with self.assertRaisesRegex(ReplayRefused, "scorbot_provenance.json"):
            load_episode(self.root / "nope", 0)
        folder = write_dataset(self.root / "d", TWO_BASE_STEPS)
        with self.assertRaisesRegex(ReplayRefused, "episode 7"):
            load_episode(folder, 7)

    def test_each_preflight_rule(self):
        cases = {
            "data source": dict(data_source="real"),
            "robot": dict(robot_id="other-arm"),
            "units": dict(units="degrees"),
            "motor order": dict(motors=list(reversed(MOTORS))),
            "step counts": dict(step_counts={**STEPS, "base": STEPS["base"] + 5}),
        }
        for word, overrides in cases.items():
            with self.subTest(rule=word):
                folder = write_dataset(self.root / word.replace(" ", "_"), TWO_BASE_STEPS,
                                       **{k: v for k, v in overrides.items()
                                          if k not in ("data_source", "robot_id")},
                                       data_source=overrides.get("data_source", "simulated"),
                                       robot_id=overrides.get("robot_id", "lab-er4u-1"))
                self.assertTrue(any(word in p for p in self.problems(folder)), word)

    def test_edited_sidecar_is_refused(self):
        folder = write_dataset(self.root / "d", TWO_BASE_STEPS)
        sidecar = folder / SIDECAR
        sidecar.write_text(sidecar.read_text(encoding="utf-8").replace('"reach"', '"other"'),
                           encoding="utf-8")
        self.assertTrue(any("changed since export" in p for p in self.problems(folder)))

    def test_cap_wrist_and_jumps_are_refused(self):
        beyond = [action(), action(-11 * B)]
        wrist = [action(), action(wrist=40)]
        jump = [action(), action(-2 * B)]
        two_joints = [action(), action(-B, STEPS["shoulder"])]
        for name, actions in (("cap", beyond), ("wrist", wrist), ("one step", jump),
                              ("one step", two_joints)):
            with self.subTest(case=name):
                folder = write_dataset(self.root / f"{name}{len(actions)}{id(actions)}",
                                       actions)
                self.assertTrue(any(name in p for p in self.problems(folder)), name)

    def test_off_grid_counts_are_refused(self):
        from scorbot.lab.replay import ReplayRefused, to_travel
        self.assertEqual(to_travel(action(-B / 2), STEPS)["base"], -0.5)
        with self.assertRaises(ReplayRefused):
            to_travel(action(-B * 0.27), STEPS)

    def test_final_counts_wrap(self):
        from scorbot.lab.replay import final_counts, load_episode
        folder = write_dataset(self.root / "d", [action(), action(-B)])
        home = {m: 10 for m in MOTORS} | {"gripper": 0}
        counts = final_counts(load_episode(folder, 0), home)
        self.assertEqual(counts["base"], (10 - B) % 65535)
        self.assertEqual(counts["gripper"], 0)


if __name__ == "__main__":
    unittest.main()
