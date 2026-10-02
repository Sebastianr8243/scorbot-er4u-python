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

    def test_step_counts_are_signed_by_jog_direction(self):
        # A +1 degree elbow jog lowers the elbow count; replay must keep that sign.
        from scorbot.robot import Scorbot
        self.assertEqual(STEPS["elbow"],
                         Scorbot().preview_jog("elbow", 1.0)["motor_count_deltas"]["elbow"])
        self.assertLess(STEPS["elbow"], 0)

    def test_elbow_moves_replay_in_the_recorded_direction(self):
        from scorbot.lab.moves import Move
        from scorbot.lab.replay import load_episode, play_moves
        e = STEPS["elbow"]                       # counts of a +1 degree elbow jog
        folder = write_dataset(self.root / "d", [action(), action(elbow=e)])
        self.assertEqual(play_moves(load_episode(folder, 0), STEPS), [Move("elbow", 1.0)])

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


class LabReplayTests(unittest.TestCase):
    TO_LOOP = ["y"] * 4 + ["n", "g", "pose matches photo", "HOME", "y", "g",
                           "all axes homed", "y"]
    ARM = ["a", "door", "y", "g", "ARM"]
    FINISH = ["x", "n", "g"]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def run_session(self, answers):
        from scorbot import SimulatedScorbot
        from scorbot.lab.operator import ScriptedOperator
        from scorbot.lab.session import LabSession
        from scorbot.simulated import SimulatedController
        from tests.lerobot_fixtures import PROFILE
        self.ctrl = SimulatedController()
        self.op = ScriptedOperator(answers)
        self.runs = getattr(self, "runs", 0) + 1
        log = self.root / f"s{self.runs}.jsonl"   # one log per session (logs are never reused)
        LabSession(profile=PROFILE, operator=self.op,
                   robot_factory=lambda **kw: SimulatedScorbot(controller=self.ctrl, **kw),
                   data_source="simulated", log_path=log,
                   session_root=self.root / "sessions", sleep=lambda s: None).run()
        self.rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    def of(self, kind):
        return [row for row in self.rows if row["type"] == kind]

    def replay(self, folder, *after, episode="0"):
        self.run_session(self.TO_LOOP + self.ARM + ["p", str(folder), episode, *after]
                         + self.FINISH)

    def test_replay_plays_to_the_final_counts(self):
        folder = write_dataset(self.root / "d", TWO_BASE_STEPS)
        self.replay(folder, "PLAY 0", "y")
        [start] = self.of("replay_start")
        self.assertEqual(start["steps"], 2)
        [done] = self.of("plan_complete")
        self.assertEqual(done["name"], "episode 0")
        self.assertLessEqual(abs(done["count_differences"]["base"]), 3)
        self.assertEqual(self.ctrl.counts["base"], -2 * B)

    def test_start_plan_skipped_when_already_there(self):
        folder = write_dataset(self.root / "d", TWO_BASE_STEPS)
        self.replay(folder, "PLAY 0", "y")
        self.assertEqual([r["name"] for r in self.of("plan_shown")], ["episode 0"])

    def test_episode_starting_away_from_home_moves_there_first(self):
        actions = [action(-B), action(-2 * B)]
        folder = write_dataset(self.root / "d", actions, first=action(-B))
        self.replay(folder, "START 0", "y", "PLAY 0", "y")
        self.assertEqual([r["name"] for r in self.of("plan_complete")],
                         ["episode 0 start", "episode 0"])
        self.assertEqual(self.ctrl.counts["base"], -2 * B)

    def test_refusals_disarm_without_motion(self):
        cases = {"missing": self.root / "nope", "robot": write_dataset(
            self.root / "r", TWO_BASE_STEPS, robot_id="other-arm")}
        for name, folder in cases.items():
            with self.subTest(case=name):
                self.replay(folder)
                self.assertEqual(len(self.of("replay_refused")), 1)
                self.assertEqual(self.of("disarmed")[-1]["reason"], "replay refused")
                self.assertEqual(self.ctrl.counts["base"], 0)

    def test_elbow_replay_ends_where_the_recording_ended(self):
        e = STEPS["elbow"]
        folder = write_dataset(self.root / "d", [action(), action(elbow=e),
                                                 action(elbow=2 * e)])
        self.replay(folder, "PLAY 0", "y")
        self.assertEqual(self.ctrl.counts["elbow"], 2 * e)

    def test_play_refused_when_start_pose_not_confirmed(self):
        folder = write_dataset(self.root / "d", [action(-B), action(-2 * B)], first=action(-B))
        self.replay(folder, "START 0", "n")
        self.assertEqual([r["name"] for r in self.of("plan_shown")], ["episode 0 start"])
        self.assertIn("start pose", self.of("replay_refused")[0]["reason"])
        self.assertEqual(self.ctrl.counts["base"], -B)

    def test_recorded_episode_replays_to_the_same_counts(self):
        """Record -> export (sidecar) -> replay, all joints: catches any sign error."""
        from tests.lerobot_fixtures import COARSE_CLOCK, paced, record
        if COARSE_CLOCK:
            self.skipTest("coarse monotonic clock")
        from scorbot.lerobot_export import sidecar
        from scorbot.lerobot_export.__main__ import plan_export
        log = record(self.root / "rec", self.TO_LOOP + self.ARM + [
            "t", paced("r"), "reach", paced("q"), paced("e"), paced("2"), paced("e"),
            paced("r"), "y", "t"] + self.FINISH)
        plan = plan_export([log], fps=10, max_frame_gap_s=0.2, video=False)
        self.assertEqual(len(plan.episodes), 1, plan.refusals)
        data, frames = plan.episodes[0]
        recorded = {m: v for m, v in zip(MOTORS, frames.state[-1])}
        folder = self.root / "dataset"
        folder.mkdir()
        (folder / SIDECAR).write_text(json.dumps(sidecar.episode_record(0, data, frames, 10))
                                      + "\n", encoding="utf-8")
        sha = hashlib.sha256((folder / SIDECAR).read_bytes()).hexdigest()
        (folder / "scorbot_provenance.json").write_text(json.dumps({
            "data_source": "simulated", "units": UNITS, "sessions": [{"robot_id": "lab-er4u-1"}],
            "episodes_sidecar_sha256": sha}), encoding="utf-8")
        self.replay(folder, "PLAY 0", "y")
        for joint in ("base", "shoulder", "elbow"):
            self.assertEqual(self.ctrl.counts[joint], recorded[joint], joint)

    def test_unknown_episode_is_refused(self):
        folder = write_dataset(self.root / "d", TWO_BASE_STEPS)
        self.replay(folder, episode="7")
        self.assertIn("episode 7", self.of("replay_refused")[0]["reason"])

    def test_a_key_during_play_stops_it(self):
        folder = write_dataset(self.root / "d", TWO_BASE_STEPS)

        def play_then_press():
            self.op.pending_keys = 1
            return "PLAY 0"
        self.replay(folder, play_then_press)
        self.assertEqual(len(self.of("plan_stopped")), 1)
        self.assertEqual(self.ctrl.counts["base"], 0)

    def test_replay_needs_arming(self):
        folder = write_dataset(self.root / "d", TWO_BASE_STEPS)
        self.run_session(self.TO_LOOP + ["p"] + self.FINISH)
        self.assertEqual(self.of("replay_start"), [])
        self.assertTrue(any("press a" in m for m in self.op.shown))
        del folder


if __name__ == "__main__":
    unittest.main()
