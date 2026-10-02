"""Round trip through the real LeRobot writer. Runs only where lerobot is installed:
.venv-lerobot/Scripts/python.exe -m unittest discover -s tests -p "test_lerobot_export_write.py"
"""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

try:
    from tests.lerobot_fixtures import ARM, COARSE_CLOCK, FINISH, TO_LOOP, paced, record
except ImportError:   # .venv-lerobot has a third-party top-level 'tests' package
    from lerobot_fixtures import ARM, COARSE_CLOCK, FINISH, TO_LOOP, paced, record

HAS_LEROBOT = importlib.util.find_spec("lerobot") is not None


@unittest.skipUnless(HAS_LEROBOT, "lerobot not installed (see docs/LEROBOT_EXPORT.md)")
@unittest.skipIf(COARSE_CLOCK, "coarse monotonic clock: exports are rightly refused")
class RoundTripTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_video_episode_round_trip(self):
        from lerobot.datasets.lerobot_dataset import LeRobotDataset
        from scorbot.lerobot_export.__main__ import main
        path = record(self.root, TO_LOOP + ARM + ["t", "LIVE:r", "reach left", paced("q"),
                                                  paced("q", 0.3), paced("r", 0.3), "t"]
                      + FINISH, camera=True)
        out = self.root / "dataset"
        self.assertEqual(main([str(path), "--out", str(out), "--repo-id", "local/scorbot"]), 0)
        self.assertFalse(any(p.name.startswith("dataset.partial")
                             for p in self.root.iterdir()))
        dataset = LeRobotDataset("local/scorbot", root=out)
        self.assertEqual(dataset.num_episodes, 1)
        self.assertIn("observation.images.main", dataset.features)
        first = dataset[0]
        self.assertEqual(first["task"], "reach left")
        # Independent check: the SDK's own first preview gives the first jog's target
        # (the simulated home is 0, so the target from home is the plan's delta).
        events = [json.loads(line) for line in
                  path.with_name(path.stem + ".controller.jsonl")
                  .read_text(encoding="utf-8").splitlines()]
        preview = next(e for e in events if e["event"] == "motion_preview")
        delta = preview["plan"]["motor_count_deltas"]["base"]
        actions = [float(dataset[i]["action"][0]) for i in range(dataset.num_frames)]
        self.assertIn(float(delta), actions)
        provenance = json.loads((out / "scorbot_provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["data_source"], "simulated")
        self.assertEqual(len(provenance["episodes"]), 1)
        self.assertEqual(provenance["image_latency"], "unmeasured")


if __name__ == "__main__":
    unittest.main()
