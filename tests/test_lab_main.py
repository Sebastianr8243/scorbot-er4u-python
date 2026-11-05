"""Lab command mode gates and wiring; no hardware or real session runs."""

import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scorbot.lab import __main__ as lab_main
from scorbot.lab.profile import LabProfile


class LabMainTests(unittest.TestCase):
    def setUp(self):
        patches = contextlib.ExitStack()
        self.addCleanup(patches.close)
        self.root = Path(patches.enter_context(tempfile.TemporaryDirectory()))
        self.robot = patches.enter_context(mock.patch("scorbot.robot.Scorbot"))
        self.preflight = patches.enter_context(mock.patch("scorbot.preflight.run_checks"))
        self.session = patches.enter_context(mock.patch.object(lab_main, "LabSession"))
        self.session.return_value.run.return_value = 0
        self.operator = patches.enter_context(mock.patch.object(lab_main, "TerminalOperator"))
        self.profile = patches.enter_context(mock.patch.object(
            lab_main, "ensure_profile",
            return_value=LabProfile("test-arm", "A", "C", "none", "XX")))
        self.log_path = patches.enter_context(mock.patch.object(
            lab_main, "next_log_path", return_value=self.root / "session.jsonl"))
        self.termination = patches.enter_context(mock.patch.object(
            lab_main, "termination_as_interrupt", side_effect=contextlib.nullcontext))
        patches.enter_context(mock.patch.object(lab_main, "_software_commit", return_value="test"))

    def assert_refused(self, argv, *messages):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as stop:
            lab_main.main(argv)
        self.assertEqual(stop.exception.code, 2)
        for message in messages:
            self.assertIn(message, stderr.getvalue())
        for entry in (self.robot, self.preflight, self.session, self.operator,
                      self.profile, self.log_path, self.termination):
            entry.assert_not_called()
        self.operator.return_value.show.assert_not_called()

    def test_no_mode_refuses_before_starting_anything(self):
        self.assert_refused([], "--simulate", "--online", "No mode chosen",
                            "nothing was opened", "connecting energises the motors",
                            "physical stop")

    def test_profile_alone_does_not_choose_real_mode(self):
        self.assert_refused(["--profile", "x.json"], "--simulate", "--online")

    def test_online_keeps_real_session_wiring_and_default_logs(self):
        self.assertEqual(lab_main.main(["--online"]), 0)
        self.session.assert_called_once()
        args = self.session.call_args.kwargs
        self.assertIs(args["robot_factory"], self.robot)
        self.assertIs(args["preflight"], self.preflight)
        self.assertEqual(args["data_source"], "real")
        self.log_path.assert_called_once_with(Path("logs"), "test-arm")
        self.assertEqual(args["log_path"], self.root / "session.jsonl")
        self.assertEqual(args["session_root"], Path("logs/sessions"))
        self.session.return_value.run.assert_called_once_with()
        self.robot.assert_not_called()
        self.preflight.assert_not_called()

    def test_online_keeps_explicit_log_folder(self):
        self.assertEqual(lab_main.main(["--online", "--logs", str(self.root)]), 0)
        self.log_path.assert_called_once_with(self.root, "test-arm")
        self.assertEqual(self.session.call_args.kwargs["session_root"], self.root / "sessions")

    def test_simulate_keeps_rehearsal_defaults(self):
        self.assertEqual(lab_main.main(["--simulate"]), 0)
        self.session.assert_called_once()
        args = self.session.call_args.kwargs
        self.assertEqual(args["data_source"], "simulated")
        self.assertIsNone(args["preflight"])
        self.log_path.assert_called_once_with(Path("rehearsal"), "test-arm")
        self.robot.assert_not_called()
        self.preflight.assert_not_called()

    def test_simulate_and_online_are_mutually_exclusive(self):
        self.assert_refused(["--simulate", "--online"], "not allowed with argument")

    def test_dropped_motors_online_still_needs_simulate(self):
        self.assert_refused(["--rehearse-motors-dropped", "--online"],
                            "--rehearse-motors-dropped needs --simulate")

    def test_fake_camera_online_still_needs_simulate(self):
        self.assert_refused(["--camera", "fake", "--online"], "--camera fake needs --simulate")


if __name__ == "__main__":
    unittest.main()
