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

    def test_a_read_that_ends_in_five_seconds_passes(self):
        times = iter([100.0, 105.0])
        original = rtc.time.monotonic
        rtc.time.monotonic = lambda: next(times)
        try:
            seconds, outcome = rtc.check(_Link(TimeoutError("driver gave up")), 1500)
        finally:
            rtc.time.monotonic = original
        self.assertEqual(seconds, 5.0)
        self.assertIn("timeout", outcome)


class _Link:
    def __init__(self, error):
        self.endpoint_in = self
        self.error = error

    def read(self, buffer, timeout):
        raise self.error


if __name__ == "__main__":
    unittest.main()
