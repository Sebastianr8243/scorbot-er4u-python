"""FakeArm stands in for Arm in every script and plugin test, so the two must offer the same calls."""

import inspect
import unittest

from scorbot.bridge import Arm, FakeArm

# Arm talks to the server; these have no meaning on a fake that has no socket.
ARM_ONLY = {"ping", "run", "send"}


def public_calls(cls):
    return {name: inspect.signature(member) for name, member in inspect.getmembers(cls, inspect.isfunction)
            if not name.startswith("_")}


class ArmContractTests(unittest.TestCase):
    def test_fakearm_has_every_public_call_of_arm(self):
        missing = set(public_calls(Arm)) - set(public_calls(FakeArm)) - ARM_ONLY
        self.assertEqual(missing, set(), "a call added to Arm needs the same call on FakeArm")

    def test_fakearm_has_no_call_arm_lacks(self):
        extra = set(public_calls(FakeArm)) - set(public_calls(Arm))
        self.assertEqual(extra, set(), "a call only FakeArm has would pass tests that fail on the real arm")

    def test_shared_calls_take_the_same_arguments(self):
        real, fake = public_calls(Arm), public_calls(FakeArm)
        for name in set(real) & set(fake):
            with self.subTest(call=name):
                self.assertEqual(list(real[name].parameters), list(fake[name].parameters))
                self.assertEqual([p.default for p in real[name].parameters.values()],
                                 [p.default for p in fake[name].parameters.values()])


if __name__ == "__main__":
    unittest.main()
