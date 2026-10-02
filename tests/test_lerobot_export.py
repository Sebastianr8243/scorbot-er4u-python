"""Session-to-LeRobot exporter: load, checks, frames, dry run. No lerobot needed."""

from pathlib import Path
import tempfile
import unittest

from tests.lerobot_fixtures import ARM, COARSE_CLOCK, FINISH, TO_LOOP, paced, record

EPISODE = ARM + ["t", paced("r"), "reach left", paced("q"), paced("q"), paced("r"), "t"]


class LoadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_loads_episodes_jogs_states_and_home(self):
        from scorbot.lerobot_export.load import MOTORS, load_lab_session
        path = record(self.root, TO_LOOP + EPISODE + FINISH)
        data = load_lab_session(path)
        self.assertEqual(data.load_errors, [])
        self.assertEqual(data.data_source, "simulated")
        self.assertEqual(set(data.home_raw), set(MOTORS) | {"gripper"})
        [episode] = data.episodes
        self.assertEqual((episode.number, episode.task, episode.status, episode.camera),
                         (1, "reach left", "completed", False))
        self.assertEqual(len(data.jogs), 2)
        self.assertEqual(data.lab_command_count, 2)
        jog = data.jogs[0]
        self.assertEqual(jog.joint, "base")
        self.assertLess(jog.command_ns, jog.result_ns)
        self.assertEqual(jog.sdk_target_signed["base"], jog.lab_target_signed["base"])
        self.assertTrue(data.states)
        self.assertEqual([e["event"] for e in data.mcap_episodes], ["start", "end"])
        self.assertIsNone(data.camera)

    def test_missing_session_folder_is_reported(self):
        import shutil
        from scorbot.lerobot_export.load import load_lab_session
        path = record(self.root, TO_LOOP + FINISH)
        shutil.rmtree(self.root / "sessions")
        data = load_lab_session(path)
        self.assertTrue(any("MCAP session folder" in e for e in data.load_errors))

    def test_undecodable_trace_packet_is_skipped(self):
        from scorbot.lerobot_export.load import _decode_trace
        from scorbot.simulated import encode_packet
        good = encode_packet({"base": 7}).hex()
        samples = _decode_trace([{"direction": "in", "host_monotonic_ns": 5, "hex": "zz"},
                                 {"direction": "out", "host_monotonic_ns": 6, "hex": good},
                                 {"direction": "in", "host_monotonic_ns": 7, "hex": good}])
        self.assertEqual([t for t, _ in samples], [7])
        self.assertEqual(samples[0][1]["base"], 7)

    def test_rel_is_wrap_aware(self):
        from scorbot.lerobot_export.load import MOTORS, rel
        home = {m: 1 for m in MOTORS}
        raw = dict(home, base=65533)
        self.assertEqual(rel(raw, home)[0], -3)


def make_data(**overrides):
    """A minimal valid session: home 0, one completed episode 0..1 s, no jogs, no camera."""
    from scorbot.lerobot_export.load import MOTORS, Episode, LabSessionData
    home = {m: 0 for m in MOTORS}
    data = LabSessionData(Path("lab.jsonl"), "lab", [], data_source="simulated",
                          home_raw=home, states=[(0, dict(home))], clock_resolution_s=1e-7,
                          episodes=[Episode(1, "task", "completed", None, 0, 1_000_000_000,
                                            False)],
                          mcap_episodes=[{"episode": 1, "event": "start", "status": None},
                                         {"episode": 1, "event": "end",
                                          "status": "completed"}])
    for key, value in overrides.items():
        setattr(data, key, value)
    return data


def make_jog(command_ns, result_ns, *, index=0, delta=142, trace=(), lab_offset=0):
    from scorbot.lerobot_export.load import MOTORS, Jog
    start = {m: 0 for m in MOTORS}
    return Jog(index, "base", command_ns, result_ns, start, {"base": delta},
               {"base": delta}, {"base": delta + lab_offset, "shoulder": 0},
               list(trace), 0)


def video_episode():
    from scorbot.lerobot_export.load import Episode
    return [Episode(1, "t", "completed", None, 0, 1_000_000_000, True)]


def camera_index(stamps_ns):
    from scorbot.camera.stream import StreamIndex
    frames = [{"seq": i, "frame_number": i, "observed_monotonic_ns": t, "width": 64,
               "height": 48} for i, t in enumerate(stamps_ns)]
    return StreamIndex("main", {}, frames, [])


class CheckTests(unittest.TestCase):
    def reasons(self, data, *, video=False, fps=10):
        from scorbot.lerobot_export.checks import exportable
        episodes, refusals = exportable(data, fps=fps, max_frame_gap_s=0.2, video=video)
        return episodes, [r.reason for r in refusals]

    def test_clean_session_exports_its_episode(self):
        episodes, reasons = self.reasons(make_data())
        self.assertEqual(len(episodes), 1)
        self.assertEqual(reasons, [])

    def test_session_without_home_is_refused(self):
        _, reasons = self.reasons(make_data(home_raw=None))
        self.assertTrue(any("home" in r for r in reasons))

    def test_coarse_clock_is_refused(self):
        _, reasons = self.reasons(make_data(clock_resolution_s=0.0156))
        self.assertTrue(any("clock" in r for r in reasons))

    def test_failed_session_is_refused(self):
        _, reasons = self.reasons(make_data(rows=[{"type": "session_failed",
                                                   "host_monotonic_ns": 5}]))
        self.assertTrue(any("session_failed" in r for r in reasons))

    def test_episode_without_end_is_skipped(self):
        from scorbot.lerobot_export.load import Episode
        data = make_data(episodes=[Episode(1, "t", "missing end", "no episode_end row", 0,
                                           None, False)])
        episodes, reasons = self.reasons(data)
        self.assertEqual(episodes, [])
        self.assertTrue(any("not completed" in r for r in reasons))

    def test_mcap_episode_marks_must_match(self):
        _, reasons = self.reasons(make_data(mcap_episodes=[]))
        self.assertTrue(any("MCAP" in r for r in reasons))

    def test_fault_inside_window_is_refused(self):
        _, reasons = self.reasons(make_data(rows=[{"type": "counts_drift",
                                                   "host_monotonic_ns": 500_000_000}]))
        self.assertTrue(any("counts_drift" in r for r in reasons))

    def test_lab_and_sdk_targets_must_agree(self):
        far = make_data(jogs=[make_jog(200_000_000, 250_000_000, lab_offset=25)])
        near = make_data(jogs=[make_jog(200_000_000, 250_000_000, lab_offset=3)])
        self.assertTrue(any("target" in r for r in self.reasons(far)[1]))
        self.assertEqual(self.reasons(near)[1], [])

    def test_unmatched_jog_is_refused(self):
        jog = make_jog(200_000_000, 250_000_000)
        jog.lab_target_signed = None
        self.assertTrue(any("matched" in r for r in self.reasons(make_data(jogs=[jog]))[1]))

    def test_two_jogs_in_one_interval_are_refused(self):
        data = make_data(jogs=[make_jog(210_000_000, 220_000_000),
                               make_jog(250_000_000, 260_000_000, index=1)])
        self.assertTrue(any("one grid interval" in r for r in self.reasons(data)[1]))

    def test_video_jog_without_trace_coverage_is_refused(self):
        from scorbot.lerobot_export.load import MOTORS
        slow = make_jog(150_000_000, 450_000_000)
        covered = make_jog(150_000_000, 450_000_000,
                           trace=[(t, {m: 0 for m in MOTORS})
                                  for t in range(160_000_000, 450_000_000, 13_000_000)])
        camera = camera_index(range(0, 1_300_000_000, 33_000_000))
        bad = make_data(jogs=[slow], camera=camera, episodes=video_episode())
        good = make_data(jogs=[covered], camera=camera, episodes=video_episode())
        self.assertTrue(any("packet" in r for r in self.reasons(bad, video=True)[1]))
        self.assertEqual(self.reasons(good, video=True)[1], [])

    def test_frame_gap_is_refused(self):
        data = make_data(camera=camera_index([0, 900_000_000]), episodes=video_episode())
        self.assertTrue(any("frame gap" in r for r in self.reasons(data, video=True)[1]))

    def test_too_short_episode_is_refused(self):
        from scorbot.lerobot_export.load import Episode
        data = make_data(episodes=[Episode(1, "t", "completed", None, 0, 50_000_000, False)])
        self.assertTrue(any("too short" in r for r in self.reasons(data)[1]))

    def test_export_level_rules(self):
        from scorbot.lerobot_export.checks import check_export
        real, sim = make_data(data_source="real"), make_data()
        self.assertTrue(any("real and simulated" in r.reason
                            for r in check_export([real, sim], video=False)))
        self.assertTrue(any("--no-video" in r.reason
                            for r in check_export([sim], video=True)))


class FrameTests(unittest.TestCase):
    def frames(self, data, video=False, fps=10):
        from scorbot.lerobot_export.frames import resample
        return resample(data, data.episodes[0], fps=fps, video=video)

    def test_grid_and_state_hold_at_rest(self):
        out = self.frames(make_data())
        self.assertEqual(len(out.times_ns), 11)
        self.assertTrue(all(row == [0.0] * 5 for row in out.state))
        self.assertEqual(out.action, out.state)
        self.assertEqual(out.task, "task")

    def test_short_jog_labels_exactly_one_tick(self):
        from scorbot.lerobot_export.load import MOTORS
        after = dict({m: 0 for m in MOTORS}, base=142)
        jog = make_jog(205_000_000, 215_000_000)
        # The post-jog reading arrives late (350 ms) so a target label is visible.
        data = make_data(jogs=[jog], states=[(0, {m: 0 for m in MOTORS}), (350_000_000, after)])
        out = self.frames(data)
        labelled = [k for k, row in enumerate(out.action) if row != out.state[k]]
        self.assertEqual(labelled, [3])              # 300 ms: first tick after the command
        self.assertEqual(out.action[3][0], 142.0)

    def test_action_is_target_while_in_flight_and_state_follows_trace(self):
        from scorbot.lerobot_export.load import MOTORS
        rest = {m: 0 for m in MOTORS}
        trace = [(t, dict(rest, base=(t - 150_000_000) // 3_000_000))
                 for t in range(160_000_000, 450_000_000, 13_000_000)]
        jog = make_jog(150_000_000, 450_000_000, trace=trace)
        data = make_data(jogs=[jog], states=[(0, rest), (451_000_000, dict(rest, base=142))])
        out = self.frames(data)
        self.assertEqual([row[0] for row in out.action[2:5]], [142.0] * 3)
        self.assertTrue(0 < out.state[3][0] < 142)
        self.assertEqual(out.action[6][0], out.state[6][0])

    def test_state_across_the_wrap_is_continuous(self):
        from scorbot.lerobot_export.load import MOTORS
        home = dict({m: 0 for m in MOTORS}, base=1)
        data = make_data(home_raw=home, states=[(0, dict(home, base=65533))])
        self.assertEqual(self.frames(data).state[0][0], -3.0)

    def test_frame_index_is_latest_at_or_before_tick(self):
        data = make_data(camera=camera_index(range(0, 1_200_000_000, 40_000_000)),
                         episodes=video_episode())
        out = self.frames(data, video=True)
        self.assertEqual(out.frame_seq[:4], [0, 2, 5, 7])   # 0, 100, 200, 300 ms


class CliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *args):
        import contextlib
        import io
        from scorbot.lerobot_export.__main__ import main
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main([str(a) for a in args])
        return code, out.getvalue()

    @unittest.skipIf(COARSE_CLOCK, "coarse monotonic clock: exports are rightly refused")
    def test_dry_run_lists_kept_and_refused_and_writes_nothing(self):
        path = record(self.root, TO_LOOP + ARM + ["t", paced("r"), "reach", paced("q"),
                                                  paced("r"), paced("r"), paced("z")]
                      + FINISH)
        code, text = self.run_cli(path, "--out", self.root / "ds", "--repo-id", "local/test",
                                  "--no-video", "--dry-run")
        self.assertEqual(code, 0, text)
        self.assertIn("KEEP", text)
        self.assertIn("SKIP", text)
        self.assertIn("SIMULATED", text)
        self.assertFalse((self.root / "ds").exists())

    def test_existing_output_is_refused(self):
        path = record(self.root, TO_LOOP + EPISODE + FINISH)
        (self.root / "ds").mkdir()
        code, text = self.run_cli(path, "--out", self.root / "ds", "--repo-id", "local/t",
                                  "--no-video")
        self.assertEqual(code, 1)
        self.assertIn("already exists", text)

    def test_no_camera_needs_no_video_flag(self):
        path = record(self.root, TO_LOOP + EPISODE + FINISH)
        code, text = self.run_cli(path, "--out", self.root / "ds", "--repo-id", "local/t",
                                  "--dry-run")
        self.assertEqual(code, 1)
        self.assertIn("--no-video", text)

    def test_importing_the_package_does_not_import_lerobot(self):
        import subprocess
        import sys
        code = ("import sys, scorbot.lerobot_export.__main__, scorbot.lerobot_export.checks;"
                "assert 'lerobot' not in sys.modules")
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
