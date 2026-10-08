"""Frame sources. No camera hardware: OpenCV is replaced by a fake module."""

import json
import math
import threading
import unittest

import numpy as np

from scorbot.camera.source import FakeSource, OpenCVSource, decode_frame_number


class FakeCapture:
    def __init__(self, index, backend, module):
        self.module, self.props, self.opened = module, {}, True
        module.opened_with = (index, backend)

    def isOpened(self):  # noqa: N802 (OpenCV's name)
        return self.opened

    def set(self, prop, value):
        self.props[prop] = value
        return prop in self.module.accepted

    def get(self, prop):
        return self.module.read_back.get(prop, self.props.get(prop, 0.0))

    def read(self):
        return True, np.zeros((self.module.frame_h, self.module.frame_w, 3), np.uint8)

    def release(self):
        self.opened = False


class FakeCV2:
    CAP_DSHOW, CAP_ANY = 700, 0
    CAP_PROP_FRAME_WIDTH, CAP_PROP_FRAME_HEIGHT, CAP_PROP_FPS = 3, 4, 5
    CAP_PROP_AUTOFOCUS, CAP_PROP_FOCUS = 39, 28
    CAP_PROP_AUTO_EXPOSURE, CAP_PROP_EXPOSURE = 21, 15
    CAP_PROP_BUFFERSIZE = 38

    def __init__(self, frame_w=320, frame_h=240, accepted=(3, 4, 5), read_back=None):
        self.frame_w, self.frame_h = frame_w, frame_h
        self.accepted, self.read_back = set(accepted), dict(read_back or {})

    def VideoCapture(self, index, backend):  # noqa: N802 (OpenCV's name)
        return FakeCapture(index, backend, self)


class FakeSourceTests(unittest.TestCase):
    def test_frames_carry_their_number(self):
        source = FakeSource()
        source.open()
        numbers = [decode_frame_number(source.read()[1]) for _ in range(3)]
        self.assertEqual(numbers, [0, 1, 2])
        self.assertEqual(source.settings()["backend"], "fake")

    def test_fail_at_raises(self):
        source = FakeSource(fail_at=1)
        source.open()
        source.read()
        with self.assertRaises(RuntimeError):
            source.read()

    def test_block_at_is_released_by_close(self):
        source = FakeSource(block_at=0)
        source.open()
        result = []
        thread = threading.Thread(target=lambda: result.append(source.read()))
        thread.start()
        source.close()
        thread.join(2)
        self.assertEqual(result, [(False, None)])


class OpenCVSourceTests(unittest.TestCase):
    def test_settings_record_request_set_result_and_raw_read_back(self):
        cv2 = FakeCV2(read_back={39: 2.0, 21: -1.0})
        source = OpenCVSource(1, width=320, height=240, fps=30.0, focus=10.0,
                              exposure=-6.0, warmup_s=0.0, cv2_module=cv2)
        source.open()
        settings = source.settings()
        self.assertEqual(settings["requested"]["width"], 320)
        self.assertTrue(settings["set_ok"]["width"])
        self.assertFalse(settings["set_ok"]["exposure"])
        self.assertEqual(settings["read_back"]["autofocus"], 2.0)  # raw, not interpreted
        self.assertEqual(settings["actual"]["width"], 320)
        self.assertNotIn("accepted", json.dumps(settings))
        json.dumps(settings, allow_nan=False)
        source.close()

    def test_requests_a_one_frame_driver_buffer(self):
        # Industry practice for low-latency capture: a 1-frame buffer so read()
        # returns the newest frame rather than a queued older one.
        cv2 = FakeCV2(accepted=(3, 4, 5, 38))
        source = OpenCVSource(0, warmup_s=0.0, cv2_module=cv2)
        source.open()
        settings = source.settings()
        self.assertEqual(settings["requested"]["buffer_size"], 1)
        self.assertTrue(settings["set_ok"]["buffer_size"])
        self.assertIn("buffer_size", settings["read_back"])

    def test_actual_size_is_verified_from_frames(self):
        cv2 = FakeCV2(frame_w=160, frame_h=120)
        source = OpenCVSource(0, width=640, height=480, warmup_s=0.0, cv2_module=cv2)
        source.open()
        self.assertEqual((source.settings()["actual"]["width"],
                          source.settings()["actual"]["height"]), (160, 120))

    def test_non_finite_read_back_is_none(self):
        cv2 = FakeCV2(read_back={5: math.nan})
        source = OpenCVSource(0, warmup_s=0.0, cv2_module=cv2)
        source.open()
        self.assertIsNone(source.settings()["read_back"]["fps"])

    def test_unopened_camera_raises(self):
        cv2 = FakeCV2()
        original = cv2.VideoCapture

        def closed(index, backend):
            capture = original(index, backend)
            capture.opened = False
            return capture
        cv2.VideoCapture = closed
        with self.assertRaises(RuntimeError):
            OpenCVSource(0, warmup_s=0.0, cv2_module=cv2).open()


if __name__ == "__main__":
    unittest.main()
