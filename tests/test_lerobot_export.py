"""Session-to-LeRobot exporter: load, checks, frames, dry run. No lerobot needed."""

from pathlib import Path
import tempfile
import unittest

from tests.lerobot_fixtures import ARM, FINISH, TO_LOOP, paced, record

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


if __name__ == "__main__":
    unittest.main()
