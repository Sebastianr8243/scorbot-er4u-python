"""scripts/read_timeout_check.py: lab step T against a fake kernel32. Never opens a device."""

import importlib.util
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tests.test_transport_device_file import FakeApi

spec = importlib.util.spec_from_file_location(
    "read_timeout_check", Path(__file__).resolve().parents[1] / "scripts" / "read_timeout_check.py")
rtc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rtc)


class ReadTimeoutCheckTests(unittest.TestCase):
    def run_main(self, argv, api):
        out = io.StringIO()
        with redirect_stdout(out):
            code = rtc.main(argv, api=api)
        return code, out.getvalue()

    def test_without_online_it_opens_nothing(self):
        api = FakeApi()
        code, out = self.run_main([], api)
        self.assertEqual(code, 2)
        self.assertEqual(api.calls, [])

    def test_it_never_writes_and_always_closes(self):
        api = FakeApi([b""] * 10)
        code, out = self.run_main(["--online", "--timeout-ms", "50"], api)
        self.assertEqual(code, 1)                                # a fast empty answer is not the driver's 5 s
        self.assertIn("FAIL", out)
        self.assertFalse([c for c in api.calls if c[0] == "write"])
        self.assertEqual(api.calls[-1][0], "close")

    def timed(self, error):
        times = iter([100.0, 105.0])
        original = rtc.time.monotonic
        rtc.time.monotonic = lambda: next(times)
        try:
            return rtc.check(_Link(error), 1500)
        finally:
            rtc.time.monotonic = original

    def test_a_timeout_that_ends_in_five_seconds_passes(self):
        seconds, kind, _ = self.timed(TimeoutError("driver gave up"))
        self.assertEqual((seconds, kind), (5.0, "timeout"))
        self.assertTrue(rtc.passed(seconds, kind))

    def test_only_a_timeout_passes_however_long_it_took(self):
        for error, kind in ((rtc.device_file.DeviceLinkError("device not functioning"), "link error"), (None, "answered")):
            with self.subTest(kind=kind):
                seconds, got, _ = self.timed(error)
                self.assertEqual(got, kind)
                self.assertFalse(rtc.passed(seconds, got))


class _Link:
    def __init__(self, error):
        self.endpoint_in = self
        self.error = error

    def read(self, buffer, timeout):
        if self.error is not None:
            raise self.error
        return 64


if __name__ == "__main__":
    unittest.main()
