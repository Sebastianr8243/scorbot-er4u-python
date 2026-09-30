"""Session -> Rerun mapping; runs without rerun installed."""

import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scorbot.session.__main__ import main as session_main
from scorbot.session.replay import Finding, Session, load_session
from scorbot.session.rerun_view import Item, RerunUnavailable, info_markdown, session_items

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
            event(1, "/camera/cam0/image", 10, data="é", format="png"),   # non-ASCII
        ]))
        self.assertEqual(by_path(items, "camera/cam0/image"), [])

    def test_detections_of_one_frame_are_one_box_set_and_cleared_on_next_frame(self):
        png = "iVBORw0KGgo="   # base64 of the PNG signature is enough here
        items = session_items(session([
            event(0, "/camera/cam0/image", 0, data=png, format="png"),
            event(1, "/camera/cam0/detections", 5, frame_number=1, label="a",
                  bbox_xyxy=[0, 0, 1, 1], confidence=0.5),
            event(2, "/camera/cam0/detections", 6, frame_number=1, label="b",
                  bbox_xyxy=[2, 2, 3, 3], confidence=0.25),
            event(3, "/camera/cam0/image", 100, data=png, format="png"),
        ]))
        path = "camera/cam0/image/detections"
        boxes = [i for i in by_path(items, path) if i.kind == "boxes"]
        self.assertEqual(len(boxes), 1)
        self.assertEqual(boxes[0].value, {"xyxy": [[0.0, 0.0, 1.0, 1.0], [2.0, 2.0, 3.0, 3.0]],
                                          "labels": ["a 0.50", "b 0.25"]})
        self.assertEqual((boxes[0].time_s, boxes[0].seq), (0.005, 1))
        clears = [(i.time_s, i.seq) for i in by_path(items, path) if i.kind == "clear"]
        self.assertEqual(clears, [(0.0, 0), (0.1, 3)])   # each new image drops old boxes

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
        boxes = [i for i in by_path(items, "camera/cam0/image/detections") if i.kind == "boxes"]
        self.assertTrue(boxes)
        self.assertEqual(len(boxes[0].value["xyxy"][0]), 4)
        self.assertEqual(boxes[0].value["labels"], ["red_block 0.90"])
        self.assertIn("# SYNTHETIC session", items[0].value)
        times = [i.time_s for i in items if i.time_s is not None]
        self.assertEqual(min(times), 0.0)
        self.assertIsInstance(items[0], Item)


class ViewCommandTests(unittest.TestCase):
    def setUp(self):
        from examples.make_synthetic_session import write_synthetic_session
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.session_path = write_synthetic_session(self.root / "sessions")

    def tearDown(self):
        self._tmp.cleanup()

    def run_main(self, *args):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = session_main(["view", *map(str, args)])
        return code, err.getvalue()

    def test_existing_save_path_is_refused_and_kept(self):
        target = self.root / "out.rrd"
        target.write_bytes(b"keep")
        code, err = self.run_main(self.session_path, "--save", target)
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)
        self.assertEqual(target.read_bytes(), b"keep")

    def test_missing_rerun_prints_install_hint(self):
        with patch("scorbot.session.rerun_view.require_rerun",
                   side_effect=RerunUnavailable('Viewing needs Rerun: pip install -e ".[viz]"')):
            code, err = self.run_main(self.session_path, "--save", self.root / "new.rrd")
        self.assertEqual(code, 2)
        self.assertIn('pip install -e ".[viz]"', err)
        self.assertNotIn("Traceback", err)
        self.assertFalse((self.root / "new.rrd").exists())

    def test_unopenable_session_exits_2(self):
        code, _ = self.run_main(self.root / "missing", "--save", self.root / "x.rrd")
        self.assertEqual(code, 2)

    def test_integrity_errors_exit_1_after_viewing(self):
        damaged = Session(Path("d"), {"data_source": "real"}, [],
                          [Finding("error", "CRC mismatch")])
        with patch("scorbot.session.__main__._open", return_value=damaged), \
                patch("scorbot.session.rerun_view.require_rerun"), \
                patch("scorbot.session.rerun_view.view") as view:
            code, err = self.run_main("d", "--save", self.root / "d.rrd")
        self.assertEqual(code, 1)
        view.assert_called_once()
        self.assertIs(view.call_args.args[0], damaged)

    @unittest.skipUnless(importlib.util.find_spec("rerun"), "rerun-sdk not installed")
    def test_save_writes_a_recording(self):
        target = self.root / "synthetic.rrd"
        code, err = self.run_main(self.session_path, "--save", target)
        self.assertEqual(code, 0, err)
        self.assertGreater(target.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
