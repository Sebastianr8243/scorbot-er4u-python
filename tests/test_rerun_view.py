"""Session -> Rerun mapping; runs without rerun installed."""

from pathlib import Path
import tempfile
import unittest

from scorbot.session.replay import Finding, Session, load_session
from scorbot.session.rerun_view import Item, info_markdown, session_items

T0 = 1_700_000_000_000_000_000


def event(seq, topic, ms, **payload):
    return {"seq": seq, "topic": topic, "schema": None, "log_time": T0 + ms * 1_000_000,
            "publish_time": T0 + ms * 1_000_000, "payload": payload}


def session(events=(), data_source="real", findings=()):
    meta = {"session_id": "s1", "data_source": data_source, "robot_id": "arm-1",
            "operator": "XX", "task": "bench", "code": {"git_commit": "abc", "git_dirty": False}}
    return Session(Path("s1"), meta, list(events), list(findings))


def by_path(items, path):
    return [item for item in items if item.path == path]


class SessionItemsTests(unittest.TestCase):
    def test_state_counts_prefer_signed_and_skip_bad_values(self):
        items = session_items(session([
            event(0, "/robot/state", 0, encoder_counts={"base": 65530, "shoulder": 7},
                  signed_encoder_counts={"base": -5, "shoulder": 7},
                  controller_error_counts={"base": 2}, home_switch_bits=4),
            event(1, "/robot/state", 250, encoder_counts={"base": 12, "shoulder": None,
                                                           "elbow": "x", "gripper": True}),
        ]))
        base = by_path(items, "state/counts/base")
        self.assertEqual([(i.value, i.time_s, i.seq) for i in base],
                         [(-5.0, 0.0, 0), (12.0, 0.25, 1)])
        self.assertEqual(by_path(items, "state/counts/shoulder")[0].value, 7.0)
        self.assertEqual(len(by_path(items, "state/counts/shoulder")), 1)   # None skipped
        self.assertEqual(by_path(items, "state/counts/elbow"), [])            # string skipped
        self.assertEqual(by_path(items, "state/counts/gripper"), [])          # bool skipped
        self.assertEqual(by_path(items, "state/controller_error/base")[0].value, 2.0)
        self.assertEqual(by_path(items, "state/home_switch_bits")[0].value, 4.0)
        self.assertTrue(all(i.kind == "scalar" for i in items if i.path.startswith("state/")))

    def test_event_log_levels_and_text(self):
        items = session_items(session([
            event(0, "/robot/command", 0, command_id="c1", kind="jog_joint", params={"joint": "base"}),
            event(1, "/robot/command_result", 10, command_id="c1", status="timeout"),
            event(2, "/robot/command_result", 20, command_id="c2", status="completed"),
            event(3, "/operator/decision", 30, choice="MOVE"),
            event(4, "/session/note", 40, text="n" * 80),
            event(5, "/session/fault", 50, message="stale feedback"),
        ]))
        log = by_path(items, "events")
        self.assertEqual([i.level for i in log], ["INFO", "WARN", "INFO", "INFO", "INFO", "ERROR"])
        self.assertTrue(log[0].value.startswith("/robot/command: c1 jog_joint"))
        self.assertEqual(log[4].value, "/session/note: " + "n" * 80)   # notes not truncated
        self.assertIn("FAULT stale feedback", log[5].value)
        self.assertTrue(all(i.kind == "text_log" for i in log))

    def test_malformed_payload_is_logged_not_raised(self):
        items = session_items(session([event(0, "/robot/command", 0, command_id="c1")]))
        self.assertEqual(by_path(items, "events")[0].value, "/robot/command: (unreadable payload)")

    def test_bad_image_data_is_skipped(self):
        items = session_items(session([
            event(0, "/camera/cam0/image", 0, data="!!not base64!!", format="png"),
        ]))
        self.assertEqual(by_path(items, "camera/cam0/image"), [])

    def test_info_document_labels_source_and_findings(self):
        empty = session_items(session(data_source="simulated",
                                      findings=[Finding("error", "CRC mismatch")]))
        self.assertEqual(len(empty), 1)
        info = empty[0]
        self.assertEqual((info.path, info.kind, info.time_s), ("session/info", "document", None))
        self.assertIn("# SIMULATED session", info.value)
        self.assertIn("not evidence from the physical arm", info.value)
        self.assertIn("ERROR: CRC mismatch", info.value)
        real = info_markdown(session())
        self.assertIn("# REAL session", real)
        self.assertNotIn("not evidence", real)
        self.assertIn("No findings.", real)

    def test_synthetic_session_images_and_detections(self):
        from examples.make_synthetic_session import write_synthetic_session
        with tempfile.TemporaryDirectory() as directory:
            loaded = load_session(write_synthetic_session(directory))
        items = session_items(loaded)
        images = by_path(items, "camera/cam0/image")
        self.assertTrue(images)
        self.assertEqual(images[0].value["media_type"], "image/png")
        self.assertTrue(images[0].value["contents"].startswith(b"\x89PNG"))
        boxes = by_path(items, "camera/cam0/image/detections")
        self.assertTrue(boxes)
        self.assertEqual(len(boxes[0].value["xyxy"]), 4)
        self.assertEqual(boxes[0].value["label"], "red_block 0.90")
        self.assertIn("# SYNTHETIC session", items[0].value)
        times = [i.time_s for i in items if i.time_s is not None]
        self.assertEqual(min(times), 0.0)
        self.assertIsInstance(items[0], Item)


if __name__ == "__main__":
    unittest.main()
