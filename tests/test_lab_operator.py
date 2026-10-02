"""The scripted operator can simulate keys pressed while the arm moves."""

import unittest

from scorbot.lab.operator import ScriptedOperator


class ScriptedOperatorTests(unittest.TestCase):
    def test_pending_keys_are_returned_once_by_discard(self):
        op = ScriptedOperator([])
        self.assertEqual(op.discard_pending_keys(), 0)
        op.pending_keys = 2
        self.assertEqual(op.discard_pending_keys(), 2)
        self.assertEqual(op.discard_pending_keys(), 0)
        self.assertEqual(op.discards, 3)


class ScriptedTeleopTests(unittest.TestCase):
    def test_tick_and_release(self):
        from scorbot.lab.operator import TICK
        op = ScriptedOperator([TICK, "Q"])
        self.assertIsNone(op.key_or_tick("k: ", 0.2))
        self.assertEqual(op.key_or_tick("k: ", 0.2), "q")
        op.pending_keys = 4
        self.assertEqual(op.wait_for_release("q"), 4)
        self.assertEqual(op.releases, ["q"])
        self.assertTrue(op.can_wait_for_release())
        op.release_gate = False
        self.assertFalse(op.can_wait_for_release())


if __name__ == "__main__":
    unittest.main()
