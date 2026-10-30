"""The vendor-home port against switch geometries other than the one the recording was made with.

The recording (``tests/fixtures/vendor_home_trace04.json``) used 2000-count wide switch bands. A real
microswitch is probably much narrower, and where the arm sits relative to it is unknown until the hand sweep.
These tests run the whole home on the arm model (``tests/vendor_home_plant.py``) with other widths and
distances and pin what the port needs of the geometry, so the hand sweep can be compared with a prediction:

* base, shoulder, elbow and wrist pitch find and zero their switch with a band as narrow as 50 counts;
* after a zeroing, the joint sits at the switch edge plus the vendor's offset (shoulder -190, elbow +45,
  base 0: ``docs/protocol/VENDOR_HOMING_TRACE.md``);
* the **wrist roll** is the exception. The pre-pass zeroes it 690 counts past its switch edge and the main
  pass has to find the switch again from there. A switch narrower than that offset leaves the home pose off
  the switch, the search drives away from it and the home faults on the travel bound (a safe stop, but the
  home ends). A roll switch that stays on past its edge, or one at least about 800 counts wide, works.

The model is not the arm: these are predictions for the lab, not measurements.
"""

import unittest

from scorbot import vendor_home as vh
from tests.vendor_home_plant import Plant, run


def bands(width, dist, *, roll=None):
    """Switch bands in the model's joint coordinates; ``roll`` overrides the wrist roll's band."""
    b = {0: (-dist - width, -dist), 1: (dist, dist + width), 2: (-dist - width, -dist),
         3: (-dist - width, -dist), 4: (-10**7, -dist) if roll is None else roll}
    return b


class NarrowSwitchTests(unittest.TestCase):
    def test_base_shoulder_elbow_and_pitch_home_with_switches_as_narrow_as_50_counts(self):
        for width in (50, 100, 300):
            for dist in (300, 1500, 6000):
                with self.subTest(width=width, distance=dist):
                    _, result = run(Plant(bands=bands(width, dist)), max_cycles=80000)
                    self.assertEqual(result.axes_done, vh.HOMING_SEQUENCE)

    def test_a_roll_switch_narrower_than_the_vendor_offset_ends_the_home_on_the_travel_bound(self):
        for width in (300, 600):
            with self.subTest(width=width):
                with self.assertRaises(vh.HomeError) as caught:
                    run(Plant(bands=bands(300, 1500, roll=(-1500 - width, -1500))), max_cycles=80000)
                self.assertEqual((caught.exception.code, caught.exception.axis), (vh.ERR_TRAVEL, vh.WRIST_M2))
                stops = [o for o in caught.exception.outs if o.cmd == vh.MOVE and o.arg32 == 0]
                self.assertGreaterEqual(len(stops), 6)             # the abort still stopped every motor

    def test_a_roll_switch_at_least_800_wide_or_one_that_stays_on_homes(self):
        for roll in ((-1500 - 800, -1500), (-1500 - 2000, -1500), (-10**7, -1500)):
            with self.subTest(roll=roll):
                _, result = run(Plant(bands=bands(300, 1500, roll=roll)), max_cycles=80000)
                self.assertEqual(result.axes_done, vh.HOMING_SEQUENCE)

    def test_the_roll_requirement_is_the_vendors_offset_not_an_accident_of_the_model(self):
        self.assertEqual(vh.AXES[vh.WRIST_M2].offset, -690)


class ZeroLandsAtEdgePlusOffsetTests(unittest.TestCase):
    def test_each_joint_ends_at_its_switch_edge_plus_the_vendors_offset(self):
        for dist in (300, 1500, 6000):
            with self.subTest(distance=dist):
                plant = Plant(bands=bands(300, dist))
                run(plant, max_cycles=80000)
                joints = plant.joints()
                self.assertAlmostEqual(joints[0], -dist + vh.AXES[vh.BASE].offset, delta=10)
                self.assertAlmostEqual(joints[1], dist + vh.AXES[vh.SHOULDER].offset, delta=15)
                self.assertAlmostEqual(joints[2], -dist + vh.AXES[vh.ELBOW].offset, delta=15)


if __name__ == "__main__":
    unittest.main()
