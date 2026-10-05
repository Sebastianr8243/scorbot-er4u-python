"""The legacy settle checks near the 0/65535 count seam (backlog item 5). No USB.

A jog is judged settled when the measured count is within 20 of the target.
Raw counts wrap at 65535, so a target at 0 and a reading of 65532 are 3 counts
apart, not 65532.
"""

from importlib.util import find_spec
import queue
import threading
import unittest
from unittest import mock

from openScorbot.motion_profile import count_distance
from scorbot.calibration import signed_count_delta
from scorbot.robot import Scorbot

try:
    from tests.test_software_stop import ENCODER_OFFSETS, JOINTS, FollowingController
except ImportError:      # discovered with -s tests: the folder itself is on sys.path
    from test_software_stop import ENCODER_OFFSETS, JOINTS, FollowingController


class CountDistanceTests(unittest.TestCase):
    def test_either_side_of_the_seam_is_close(self):
        self.assertEqual(count_distance(3, 65533), 5)
        self.assertEqual(count_distance(65533, 3), 5)
        self.assertEqual(count_distance(0, 65535), 0)
        self.assertEqual(count_distance(65535, 0), 0)

    def test_away_from_the_seam_it_is_the_plain_difference(self):
        for a in range(0, 65536, 997):
            for b in range(0, 65536, 1009):
                if abs(a - b) <= 32767:
                    with self.subTest(a=a, b=b):
                        self.assertEqual(count_distance(a, b), abs(a - b))

    def test_it_agrees_with_the_sdk_signed_delta_and_is_bounded(self):
        for a in range(0, 65536, 811):
            for b in range(0, 65536, 823):
                with self.subTest(a=a, b=b):
                    self.assertLessEqual(count_distance(a, b), 32767)
                    self.assertEqual(count_distance(a, b), count_distance(b, a))
                    try:
                        expected = abs(signed_count_delta(a, b))
                    except ValueError:      # the exact half-range pair is ambiguous
                        continue
                    self.assertEqual(count_distance(a, b), expected)


class SeamArm(FollowingController):
    """The fake arm, reporting the base 3 counts low whenever it is within 10 of zero.

    A joint at rest on zero reads a little either side of it (-3 is raw 65532 with
    the negative sign byte), which is what the settle check meets on the real arm.
    """

    def __init__(self, start, stop_event=None):
        super().__init__(stop_event)
        self.position = [start] * len(JOINTS)
        self._report()

    def _report(self):
        for index, (value, offset) in enumerate(zip(self.position, ENCODER_OFFSETS)):
            if index == 0 and abs(value) <= 10:
                value -= 3
            raw = value if value >= 0 else 65535 + value
            self.reply[offset:offset + 2] = raw.to_bytes(2, "little")
            self.reply[offset + 2] = 128 if value >= 0 else 127


@unittest.skipUnless(find_spec("usb"), "PyUSB is needed to import the legacy modules")
class LegacySettleSeamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        robot = Scorbot()
        robot._legacy("conf").setup()
        cls.comm = robot._legacy("libcomm")

    def setUp(self):
        sleeping = mock.patch("time.sleep")
        sleeping.start()
        self.addCleanup(sleeping.stop)

    def jog_base(self, start, order):
        arm = SeamArm(start)
        buffer = bytearray(arm.reply)
        reads, results = queue.Queue(), queue.Queue()
        reads.put([start] * len(JOINTS))
        self.comm.move_hips(1, arm, arm, buffer, order, reads, results, 10, 1.0,
                            threading.Event())
        return arm, list(results.queue)

    def test_a_jog_that_lands_on_zero_from_above_settles_on_a_reading_just_below_zero(self):
        arm, results = self.jog_base(142, 4)              # one degree back to zero
        self.assertEqual(arm.position[0], 0)
        self.assertEqual(results, [], "result 2 would be a spurious motion feedback failure")

    def test_a_jog_that_does_not_cross_the_seam_is_unchanged(self):
        arm, results = self.jog_base(1000, 5)
        self.assertEqual(arm.position[0], 1142)
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
