"""Exercise the camera over loopback HTTP, without camera or arm hardware."""

from importlib.util import find_spec
from pathlib import Path
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

if find_spec("lerobot") is None:
    pytest.skip("needs lerobot (the separate LeRobot venv)", allow_module_level=True)

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins/lerobot_robot_scorbot"))
from lerobot_robot_scorbot.mjpeg_camera import MJPEGCamera, MJPEGCameraConfig  # noqa: E402


@pytest.fixture
def stream():
    stopped = threading.Event()
    paused = threading.Event()
    image = np.zeros((24, 32, 3), np.uint8)
    image[:, :, 2] = 240
    jpeg = cv2.imencode(".jpg", image)[1].tobytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            try:
                while not stopped.is_set():
                    if not paused.is_set():
                        packet = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
                        # Split JPEG markers across writes to exercise chunk buffering.
                        for start in range(0, len(packet), 37):
                            self.wfile.write(packet[start:start + 37])
                        self.wfile.flush()
                    stopped.wait(0.02)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/stream", stopped, paused
    stopped.set()
    server.shutdown()
    server.server_close()
    thread.join(2)


def test_registered_camera_factory(stream):
    from lerobot.cameras import CameraConfig, make_cameras_from_configs
    assert CameraConfig.get_choice_class("mjpeg") is MJPEGCameraConfig
    cameras = make_cameras_from_configs({"front": MJPEGCameraConfig(url=stream[0])})
    assert isinstance(cameras["front"], MJPEGCamera)
    assert cameras["front"].find_cameras() == []


@pytest.mark.parametrize("mode,red_channel", [("rgb", 0), ("bgr", 2)])
def test_connect_read_resize_async_disconnect(stream, mode, red_channel):
    camera = MJPEGCamera(MJPEGCameraConfig(url=stream[0], width=16, height=12, color_mode=mode))
    with camera:
        assert camera.is_connected
        frame = camera.read()
        assert frame.shape == (12, 16, 3)
        assert frame.dtype == np.uint8
        assert frame[:, :, red_channel].mean() > 200
        assert camera.async_read(timeout_ms=500).shape == frame.shape
        assert camera.read_latest().shape == frame.shape
    assert not camera.is_connected
    assert not camera._thread.is_alive()
    camera.disconnect()
    with pytest.raises(ConnectionError):
        camera.read()


def test_stopped_server_does_not_return_cached_frame(stream):
    camera = MJPEGCamera(MJPEGCameraConfig(url=stream[0], timeout_s=0.2))
    camera.connect()
    try:
        assert camera.read().shape == (24, 32, 3)
        stream[1].set()
        time.sleep(0.3)
        with pytest.raises(TimeoutError):
            camera.read()
        with pytest.raises(TimeoutError):
            camera.async_read(timeout_ms=50)
        with pytest.raises(TimeoutError):
            camera.read_latest(max_age_ms=20)
    finally:
        camera.disconnect()


def test_live_but_stalled_stream_times_out(stream):
    camera = MJPEGCamera(MJPEGCameraConfig(url=stream[0], timeout_s=0.2))
    camera.connect()
    try:
        stream[2].set()
        time.sleep(0.3)
        with pytest.raises(TimeoutError):
            camera.async_read(timeout_ms=50)
    finally:
        camera.disconnect()
    assert not camera._thread.is_alive()


def test_async_read_consumes_frames_and_reconnect_starts_fresh(stream):
    camera = MJPEGCamera(MJPEGCameraConfig(url=stream[0], timeout_s=1))
    camera.connect(warmup=False)
    try:
        deadline = time.monotonic() + 1
        while True:
            try:
                camera.read_latest()  # Peek without consuming the first frame.
                break
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.005)
        stream[2].set()
        time.sleep(0.1)  # Let any in-flight frame finish.
        camera.async_read(timeout_ms=100)
        with pytest.raises(TimeoutError):
            camera.async_read(timeout_ms=20)
        assert camera.read_latest().shape == (24, 32, 3)
    finally:
        camera.disconnect()
    stream[2].clear()
    camera.connect(warmup=False)
    try:
        assert camera.async_read(timeout_ms=1000).shape == (24, 32, 3)
    finally:
        camera.disconnect()


def test_connect_failure_cleans_up(stream):
    stream[2].set()
    camera = MJPEGCamera(MJPEGCameraConfig(url=stream[0], timeout_s=0.1))
    with pytest.raises(TimeoutError):
        camera.connect()
    assert not camera.is_connected
    assert not camera._thread.is_alive()


@pytest.mark.parametrize("kwargs", [{"url": "file:///tmp/camera"}, {"url": ""},
                                  {"timeout_s": 0}, {"width": 0}, {"height": -1},
                                  {"fps": 0}, {"color_mode": "gray"}])
def test_invalid_config(kwargs):
    with pytest.raises(ValueError):
        MJPEGCameraConfig(**({"url": "http://localhost/stream"} | kwargs))
