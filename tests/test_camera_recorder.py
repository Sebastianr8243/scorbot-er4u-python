"""Camera recorder threads. FakeSource, injected clock and encoder, no sleeps-as-asserts."""

import itertools
from pathlib import Path
import tempfile
import threading
import unittest

from scorbot.camera.recorder import CameraRecorder
from scorbot.camera.source import FakeSource
from scorbot.camera.stream import CameraStream, scan_stream
from scorbot.session import SessionWriter


def fake_jpeg(image):
    return b"JPEG" + bytes(image[0, :8, 0])


class StepClock:
    """Each call advances a fixed step (33 ms by default, like a 30 fps camera)."""

    def __init__(self, step_ns=33_000_000):
        self._counter = itertools.count()
        self.step_ns = step_ns

    def __call__(self):
        return next(self._counter) * self.step_ns


def wait_for(condition, attempts=500):
    pause = threading.Event()
    for _ in range(attempts):
        if condition():
            return True
        pause.wait(0.01)
    return False


class StuckSource(FakeSource):
    """A camera whose read() never returns from frame `block_at` on, even after close().

    `blocked` is set just before that read: a stop() sent earlier would find the
    reader between two reads, where it sees the stop flag and leaves cleanly.
    """

    def __init__(self, block_at):
        super().__init__(block_at=block_at, release_on_close=False)
        self.blocked = threading.Event()

    def read(self):
        if self._next == self.block_at:
            self.blocked.set()
        return super().read()


class RecorderTests(unittest.TestCase):
    def setUp(self):
        # A stuck-writer test leaves its file open in an abandoned thread.
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.session = SessionWriter.create(Path(self._tmp.name), data_source="simulated",
                                            robot_id="arm-1", camera_ids=["wrist"])
        self.stream = CameraStream.create(self.session, "wrist")

    def tearDown(self):
        self.session.close()
        self._tmp.cleanup()

    def recorder(self, source, **kwargs):
        kwargs.setdefault("clock", StepClock())
        kwargs.setdefault("encoder", fake_jpeg)
        kwargs.setdefault("max_latency_s", 1e9)
        source.open()
        return CameraRecorder(source, self.stream, **kwargs)

    def test_frames_written_in_order_with_their_stamps(self):
        recorder = self.recorder(FakeSource())
        done = threading.Event()
        original = self.stream.log_frame

        def counting(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[0] >= 20:
                done.set()
            return result
        self.stream.log_frame = counting
        recorder.start()
        self.assertTrue(done.wait(5))
        recorder.stop()
        index = scan_stream(self.session.path, "wrist")
        self.assertEqual(index.errors, [])
        numbers = [f["frame_number"] for f in index.frames]
        self.assertEqual(numbers, sorted(numbers))
        stamps = [f["observed_monotonic_ns"] for f in index.frames]
        self.assertEqual(stamps, sorted(stamps))
        self.assertEqual(recorder.status, "stopped")
        health = recorder.drain_health()
        self.assertTrue(health)
        # Thread interleaving can stretch gaps between stamps, so "degraded" is
        # possible here; "failed" never is.
        self.assertNotIn("failed", {row["status"] for row in health})

    def test_blocked_writer_drops_oldest_by_bytes(self):
        gate = threading.Event()
        original = self.stream.log_frame

        def blocked(*args, **kwargs):
            gate.wait(5)
            return original(*args, **kwargs)
        self.stream.log_frame = blocked
        one_frame = 64 * 48 * 3
        recorder = self.recorder(FakeSource(), queue_bytes=4 * one_frame)
        recorder.start()
        wait_for(lambda: recorder.counts["dropped_queue"] >= 10)
        gate.set()
        recorder.stop()
        self.assertGreaterEqual(recorder.counts["dropped_queue"], 10)
        self.assertTrue(any(row["status"] == "degraded" for row in recorder.drain_health()))

    def test_stale_frames_are_dropped_not_written_late(self):
        recorder = self.recorder(FakeSource(), clock=StepClock(step_ns=2_000_000_000),
                                 max_latency_s=1.0)
        recorder.start()
        wait_for(lambda: recorder.counts["dropped_late"] >= 3)
        recorder.stop()
        self.assertGreaterEqual(recorder.counts["dropped_late"], 3)

    def test_source_failure_fails_spares_the_robot_and_never_scans_clean(self):
        recorder = self.recorder(FakeSource(fail_at=5))
        recorder.start()
        self.assertTrue(wait_for(lambda: recorder.failure))
        self.session.log_note("robot recording continues")
        status = recorder.stop()
        self.assertTrue(status.startswith("failed"))
        self.assertIn("frame 5", recorder.failure)
        self.assertTrue(self.stream.sidecar_path.is_file())
        self.assertTrue(any(row["status"] == "failed" for row in recorder.drain_health()))
        # A failed capture never scans clean.
        errors = scan_stream(self.session.path, "wrist").errors
        self.assertTrue(any("capture failed" in f.message for f in errors))

    def test_write_failure_fails_the_recorder(self):
        def broken(*args, **kwargs):
            raise OSError("disk full")
        self.stream.log_frame = broken
        recorder = self.recorder(FakeSource())
        recorder.start()
        self.assertTrue(wait_for(lambda: recorder.failure))
        recorder.stop()
        self.assertIn("disk full", recorder.failure)

    def test_repeated_empty_reads_fail_the_recorder(self):
        class Empty(FakeSource):
            def read(self):
                return False, None
        recorder = self.recorder(Empty())
        recorder.start()
        self.assertTrue(wait_for(lambda: recorder.failure))
        recorder.stop()
        self.assertIn("no frame", recorder.failure)

    def test_stuck_read_does_not_block_stop_and_is_recorded_in_the_stream(self):
        source = StuckSource(block_at=0)
        recorder = self.recorder(source)
        recorder.start()
        self.assertTrue(source.blocked.wait(10))
        # stop() gives the threads 3/4 of the timeout, and all of that goes on the
        # stuck reader, so the writer must already be gone by then: only then is
        # the stream closed with the finding. 0.5 s leaves it 0.375 s to see the
        # stop flag.
        status = recorder.stop(timeout_s=0.5)
        self.assertIn("stuck", status)
        # The sidecar is written by a helper thread that stop() waits on for the
        # last 1/8 s only; a slow disk can finish it later.
        self.assertTrue(wait_for(lambda: any(
            "stuck" in f.message
            for f in scan_stream(self.session.path, "wrist").errors)))

    def test_a_read_that_sticks_after_some_frames_does_not_block_stop_either(self):
        source = StuckSource(block_at=3)
        recorder = self.recorder(source)
        recorder.start()
        self.assertTrue(source.blocked.wait(10))
        self.assertIn("stuck", recorder.stop(timeout_s=0.5))

    def test_stuck_writer_leaves_no_sidecar_and_stop_returns(self):
        never = threading.Event()

        entered = threading.Event()

        def stuck(*args, **kwargs):
            entered.set()
            never.wait()
        self.stream.log_frame = stuck
        recorder = self.recorder(FakeSource())
        recorder.start()
        self.assertTrue(wait_for(lambda: recorder.counts["frames"] > 0))
        # A writer still waiting for its first frame would leave cleanly on stop.
        self.assertTrue(entered.wait(10))
        # The writer never returns, so any timeout gives the same result.
        status = recorder.stop(timeout_s=0.2)
        self.assertIn("stuck", status)
        self.assertFalse(self.stream.sidecar_path.exists())

    def test_stop_is_bounded_even_if_closing_the_camera_hangs(self):
        import time

        class HangingClose(FakeSource):
            def close(self):
                threading.Event().wait()
        recorder = self.recorder(HangingClose())
        recorder.start()
        started = time.monotonic()
        # close() never returns, so any timeout gives the same result.
        status = recorder.stop(timeout_s=0.2)
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertIn("stuck", status)

    def test_last_write_time_is_tracked(self):
        recorder = self.recorder(FakeSource())
        self.assertIsNone(recorder.last_write_ns)
        recorder.start()
        self.assertTrue(wait_for(lambda: recorder.last_write_ns is not None))
        recorder.stop()

    def test_stream_close_hang_does_not_block_stop(self):
        import time
        never = threading.Event()
        self.stream.close = lambda: never.wait()
        recorder = self.recorder(FakeSource())
        recorder.start()
        started = time.monotonic()
        status = recorder.stop(timeout_s=0.5)
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertIn("closing the stream", status)

    def test_frames_queued_at_stop_are_counted(self):
        import time
        original = self.stream.log_frame

        def slow(*args, **kwargs):
            time.sleep(0.02)
            return original(*args, **kwargs)
        self.stream.log_frame = slow
        recorder = self.recorder(FakeSource())
        recorder.start()
        wait_for(lambda: recorder.counts["frames"] > 50)
        recorder.stop()
        counts = recorder.counts
        self.assertGreater(counts["dropped_stop"], 0)
        self.assertEqual(counts["frames"], counts["written"] + counts["dropped_queue"]
                         + counts["dropped_late"] + counts["dropped_stop"])

    def test_stop_twice_and_before_start(self):
        idle = CameraRecorder(FakeSource(), None, encoder=fake_jpeg)
        self.assertEqual(idle.stop(), "idle")
        recorder = self.recorder(FakeSource())
        recorder.start()
        first = recorder.stop()
        self.assertEqual(recorder.stop(), first)

    def test_stop_is_harmless_after_failure(self):
        recorder = self.recorder(FakeSource(fail_at=0))
        recorder.start()
        self.assertTrue(wait_for(lambda: recorder.failure))
        self.assertTrue(recorder.stop().startswith("failed"))

    def test_without_a_stream_frames_are_only_measured(self):
        source = FakeSource()
        source.open()
        recorder = CameraRecorder(source, None, encoder=fake_jpeg, clock=StepClock())
        recorder.start()
        self.assertTrue(wait_for(lambda: recorder.counts["frames"] >= 10))
        self.assertEqual(recorder.stop(), "stopped")


class RealEncoderTests(unittest.TestCase):
    def test_default_encoder_makes_a_jpeg(self):
        try:
            import cv2  # noqa: F401
        except ImportError:
            self.skipTest("opencv not installed (pip install .[camera])")
        from scorbot.camera.recorder import encode_jpeg
        source = FakeSource()
        source.open()
        data = encode_jpeg(source.read()[1], 90)
        self.assertEqual(data[:2], b"\xff\xd8")


if __name__ == "__main__":
    unittest.main()
