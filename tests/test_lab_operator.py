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


if __name__ == "__main__":
    unittest.main()
