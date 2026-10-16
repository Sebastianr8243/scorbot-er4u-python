"""scripts/export_policy_dataset.py: policy_loop records into a dataset that reloads identically.
Hand-made records here; the simulated end-to-end run is in test_policy_loop.py."""

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from scripts import export_policy_dataset as ex

MOTORS = ex.MOTORS


def record(folder, name, *, steps=5, data_source="simulated", rate_hz=5.0, passed=True,
           policy_errors=0, procedure="policy_run", finished=True):
    rows = [{"type": "session", "procedure": procedure, "data_source": data_source, "rate_hz": rate_hz,
             "policy": "reach:base=1", "robot_id": "arm-1", "software_commit": "abc",
             "motion_source_sha256": "f" * 64, "start_pose_note": "near home"}]
    for i in range(steps):
        deg = {m: 0.1 * i * (k + 1) for k, m in enumerate(MOTORS)}
        rows.append({"type": "policy_step", "step": i, "t": i / rate_hz, "measured_deg": deg,
                     "measured_counts": {m: int(d * 100) for m, d in deg.items()},
                     "target_deg": {m: d + 0.05 for m, d in deg.items()}, "clamped": [] if i % 2 else ["base"],
                     "policy_error": None})
    if finished:
        rows.append({"type": "after_policy", "result": {"passed": passed, "policy_errors": policy_errors,
                                                        "problems": [] if passed else ["did not return"]}})
    path = Path(folder) / name
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


class ExportTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name)

    def test_two_runs_become_two_episodes_that_reload_identically(self):
        a, b = record(self.dir, "a.jsonl", steps=5), record(self.dir, "b.jsonl", steps=7)
        meta, refused = ex.export([a, b], self.dir / "ds")
        self.assertEqual((meta["frames"], len(meta["episodes"]), refused), (12, 2, []))
        self.assertEqual(ex.verify(self.dir / "ds", [a, b]), [])
        data = np.load(self.dir / "ds" / "data.npz")
        self.assertEqual(data["observation_state"].shape, (12, 3))
        self.assertEqual(list(data["episode_index"]), [0] * 5 + [1] * 7)
        self.assertEqual(list(data["frame_index"]), list(range(5)) + list(range(7)))
        self.assertIn("UNCALIBRATED", meta["state_unit"])

    def test_bad_runs_are_refused_with_a_reason_and_good_ones_still_export(self):
        good = record(self.dir, "good.jsonl")
        bad = [record(self.dir, "failed.jsonl", passed=False),
               record(self.dir, "errors.jsonl", policy_errors=2),
               record(self.dir, "short.jsonl", steps=1),
               record(self.dir, "other.jsonl", procedure="stream_trial"),
               record(self.dir, "crashed.jsonl", finished=False)]
        meta, refused = ex.export([good, *bad], self.dir / "ds")
        self.assertEqual([e["source"] for e in meta["episodes"]], ["good.jsonl"])
        self.assertEqual(len(refused), 5)
        self.assertEqual(len(meta["refused"]), 5)

    def test_nothing_exportable_exports_nothing(self):
        with self.assertRaises(SystemExit):
            ex.export([record(self.dir, "x.jsonl", passed=False)], self.dir / "ds")
        self.assertFalse((self.dir / "ds").exists())

    def test_real_and_simulated_never_share_a_dataset(self):
        with self.assertRaises(SystemExit):
            ex.export([record(self.dir, "s.jsonl"), record(self.dir, "r.jsonl", data_source="real")],
                      self.dir / "ds")

    def test_mixed_control_rates_are_refused(self):
        with self.assertRaises(SystemExit):
            ex.export([record(self.dir, "a.jsonl", rate_hz=5.0), record(self.dir, "b.jsonl", rate_hz=10.0)],
                      self.dir / "ds")

    def test_an_existing_output_folder_is_never_overwritten(self):
        (self.dir / "ds").mkdir()
        with self.assertRaises(SystemExit):
            ex.export([record(self.dir, "a.jsonl")], self.dir / "ds")

    def test_verify_catches_a_dataset_that_no_longer_matches_its_source(self):
        a = record(self.dir, "a.jsonl")
        ex.export([a], self.dir / "ds")
        data = dict(np.load(self.dir / "ds" / "data.npz"))
        data["action"][2, 0] += 1.0
        np.savez(self.dir / "ds" / "data.npz", **data)
        self.assertTrue(any("action differs" in p for p in ex.verify(self.dir / "ds", [a])))

    def test_main_returns_zero_and_prints_the_summary(self):
        a = record(self.dir, "a.jsonl")
        self.assertEqual(ex.main([str(a), "--out", str(self.dir / "ds")]), 0)


if __name__ == "__main__":
    unittest.main()
