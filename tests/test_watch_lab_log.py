"""The live lab view reads JSONL only; it never opens USB."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

from scripts.watch_lab_log import Follower, RunView
from scorbot.state import JOINTS


def state(index, base=100, switches=0, fault=None):
    raw = {joint: 100 for joint in JOINTS}
    raw["base"] = base
    return {
        "packet_index": index, "fault": fault, "enabled": True, "homed": True,
        "encoder_counts": raw, "signed_encoder_counts": raw.copy(),
        "encoder_sign_bytes": {joint: 128 for joint in JOINTS},
        "controller_error_counts": {joint: 0 for joint in JOINTS},
        "home_switch_bits": switches,
    }


class WatchLabLogTests(unittest.TestCase):
    def test_follower_waits_for_file_and_holds_partial_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.jsonl"
            follower = Follower(path)
            self.assertEqual(follower.poll(), [])
            with path.open("w", encoding="utf-8") as stream:
                stream.write('{"type": "session"}\n{"type": "conn')
            self.assertEqual(follower.poll(), [{"type": "session"}])
            with path.open("a", encoding="utf-8") as stream:
                stream.write('ected"}\n')
            self.assertEqual(follower.poll(), [{"type": "connected"}])

    def test_view_shows_planned_and_observed_counts_and_switches(self):
        view = RunView()
        view.add({"type": "session", "robot_id": "arm-1", "joint": "base",
                  "requested_delta_deg": 1, "data_source": "real"}, "run")
        view.add({"type": "connected", "state": state(1)}, "run")
        view.add({"type": "motion_preview", "plan": {"motor_count_deltas": {"base": 142}}}, "run")
        view.add({"type": "before_jog", "state": state(3)}, "run")
        view.add({"type": "after_jog", "state": state(4, base=236, switches=0b00011)}, "run")
        screen = view.render()
        base_row = next(line for line in screen.splitlines() if line.startswith("base "))
        self.assertIn("142", base_row)
        self.assertIn("+136", base_row)
        self.assertIn("set: base, shoulder", screen)
        self.assertNotIn("SIMULATED", screen)
        self.assertNotIn("!!!", screen)

    def test_sync_crash_event_raises_an_alarm(self):
        view = RunView()
        view.add({"event": "sync_worker_crashed", "error": "USB read failed"}, "controller")
        screen = view.render()
        self.assertIn("!!! sync_worker_crashed: USB read failed", screen)
        self.assertIn("physical stop", screen)

    def test_older_state_never_replaces_newer_one(self):
        view = RunView()
        disabled = dict(state(9), enabled=False)
        view.add({"type": "disabled", "host_monotonic_ns": 200, "state": disabled}, "run")
        view.add({"event": "motion_complete", "host_monotonic_ns": 100, "state": state(8)},
                 "controller")
        self.assertIn("enabled False", view.render())

    def test_simulated_run_is_labelled(self):
        view = RunView()
        view.add({"type": "session", "data_source": "simulated"}, "run")
        self.assertIn("SIMULATED", view.render())

    def test_view_imports_no_usb_or_controller_code(self):
        self.assertNotIn("usb", sys.modules.get("scripts.watch_lab_log").__dict__)
        source = Path(sys.modules["scripts.watch_lab_log"].__file__).read_text(encoding="utf-8")
        for forbidden in ("import usb", "Scorbot(", "SimulatedScorbot", "openScorbot"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
