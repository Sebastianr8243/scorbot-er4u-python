"""scripts/read_timeout_check.py: lab step T against a fake kernel32. Never opens a device."""

import importlib.util
import io
import unittest
import unittest.mock
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
        self.run_main(["--online", "--timeout-ms", "50"], api)
        self.assertFalse([c for c in api.calls if c[0] == "write"])
        self.assertEqual(api.calls[-1][0], "close")

    def run_clocked(self, seconds_per_read, reads=10):
        """main() on the fake kernel32, where each empty read takes ``seconds_per_read`` on a fake clock."""
        api = _SlowApi([b""] * reads, seconds_per_read)
        with unittest.mock.patch.object(rtc.time, "monotonic", api.now):
            return self.run_main(["--online"], api)

    def test_the_drivers_five_second_timeout_passes(self):
        code, out = self.run_clocked(5.0)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("PASS read took 5.00 s, ended as timeout"), out)

    def test_a_timeout_out_of_range_fails(self):
        code, out = self.run_clocked(2.0)
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("FAIL read took 2.00 s, ended as timeout"), out)

    def test_empty_reads_at_once_are_a_finding_not_a_pass(self):
        code, out = self.run_clocked(0.0)                        # what the lab PC did on 2026-11-12
        self.assertEqual(code, rtc.FINDING_EXIT)
        self.assertNotEqual(rtc.FINDING_EXIT, 0)
        self.assertEqual(len(out.splitlines()), 1)
        self.assertTrue(out.startswith("FINDING read took 0.00 s, ended as link error"), out)
        self.assertIn("driver returned no bytes at once; the link is not proven dead, run the probe", out)

    def test_a_slow_link_error_is_still_a_fail(self):
        code, out = self.run_clocked(0.2)                        # 10 empty reads, 2 s in all: not "at once"
        self.assertEqual(code, 1)
        self.assertIn("FAIL", out)
        self.assertEqual(rtc.outcome(0.5, "link error", "device not functioning"), "FAIL")

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


class _SlowApi(FakeApi):
    """The fake kernel32 on a fake clock: each read moves it on by ``seconds_per_read``."""

    def __init__(self, replies, seconds_per_read):
        super().__init__(replies)
        self.clock = 100.0
        self.step = seconds_per_read

    def now(self):
        return self.clock

    def read_file(self, handle, size):
        self.clock += self.step
        return super().read_file(handle, size)


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
