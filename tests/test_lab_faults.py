"""Error text to plain-language guidance: pure, no robot."""

import unittest

from scorbot.lab.faults import COMMON_STEPS, Guidance, format_guidance, guidance_for

CASES = {
    "Command timed out; physical stop may be required": "timeout",
    "Controller is faulted: Command timed out; physical stop may be required": "timeout",
    "Legacy controller returned error code 1": "error_too_large",
    "Legacy controller returned error code 2": "not_settled",
    "Legacy controller returned error code 10": "unknown",
    "Controller feedback is unavailable: Simulated stale feedback: no fresh controller "
    "response": "feedback",
    "USB command worker stopped: USB write failed": "worker_crash",
    "USB sync worker is not running: USB sync worker stopped: boom": "worker_crash",
    "LED check before_arm not confirmed: motors=off (expected lit)": "led_gate",
    "counts drift": "counts_drift",
    "Controller is faulted: Legacy controller returned error code 3": "faulted",
    "something new": "unknown",
    "": "unknown",
}


class GuidanceTests(unittest.TestCase):
    def test_known_texts_map_to_their_keys(self):
        for text, key in CASES.items():
            with self.subTest(text=text):
                self.assertEqual(guidance_for(text).key, key)

    def test_every_guidance_ends_with_the_common_steps(self):
        for text in CASES:
            guidance = guidance_for(text)
            self.assertIsInstance(guidance, Guidance)
            self.assertEqual(guidance.steps, COMMON_STEPS)

    def test_common_steps_name_the_stop_the_start_pose_and_holding(self):
        joined = " ".join(COMMON_STEPS)
        self.assertIn("physical stop", joined)
        self.assertIn("start pose", joined)
        self.assertIn("Connecting turns the motors on", joined)
        self.assertIn("unverified", joined)

    def test_inferred_causes_say_probably(self):
        for text in ("Legacy controller returned error code 1",
                     "Legacy controller returned error code 2",
                     "USB command worker stopped: x", "LED check after_enable not confirmed",
                     "counts drift"):
            with self.subTest(text=text):
                self.assertIn("probably", guidance_for(text).meaning.lower())

    def test_error_too_large_lists_motors_off_as_a_cause(self):
        self.assertIn("motor power", guidance_for("Legacy controller returned error code 1").meaning)

    def test_format_numbers_the_steps(self):
        text = format_guidance(guidance_for("Legacy controller returned error code 1"))
        self.assertTrue(text.startswith("Joint error too large: "))
        self.assertIn("\n  1. ", text)
        self.assertIn(f"\n  {len(COMMON_STEPS)}. ", text)
        self.assertNotIn("emergency stop", text.lower().replace("not an emergency stop", ""))


if __name__ == "__main__":
    unittest.main()
