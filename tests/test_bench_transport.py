"""The bench scripts reach the controller on Intelitek's driver: ``--transport auto`` by default.

The libusb preflight cannot see a controller bound to ERUSB, so it runs only for ``--transport pyusb``
(as ``scorbot doctor`` treats it). A stand-in robot records how it was built and stops there: no USB.
"""

import contextlib
from importlib.util import find_spec
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from examples import bench_gripper, bench_joint, bench_stream, policy_loop
from scorbot import Scorbot
from scorbot.preflight import Check

HAS_RUCKIG = find_spec("ruckig") is not None
LABELS = ["--robot-id", "test-arm", "--arm-label", "arm-plate", "--controller-label", "box-plate",
          "--driver", "intelitek", "--operator", "tester", "--start-pose-note", "desk"]
SCRIPTS = {
    "bench_joint": (bench_joint, ["--joint", "base", "--delta", "1"], False),
    "bench_stream": (bench_stream, ["--motor", "base", "--delta", "1", "--hold-s", "1"], True),
    "bench_gripper": (bench_gripper, ["--moves", "open", "close"], False),
    "policy_loop": (policy_loop, ["--policy", "reach:base=1"], True),
}


class Built(Exception):
    """Raised by the stand-in robot once it is built for a session, before any connection."""


class ChooseBackendTests(unittest.TestCase):
    def test_auto_and_device_file_skip_the_libusb_check_and_bind_the_transport(self):
        for transport in ("auto", "device_file"):
            with self.subTest(transport=transport), contextlib.redirect_stdout(io.StringIO()):
                checks = mock.Mock(return_value=[Check("USB", False, "libusb cannot see ERUSB")])
                real = mock.Mock()
                factory, source = bench_joint.choose_backend(False, real, object, checks,
                                                             transport=transport)
                checks.assert_not_called()
                self.assertEqual(source, "real")
                factory(log_path="x", robot_id="r")
                real.assert_called_once_with(log_path="x", robot_id="r", transport=transport)

    def test_pyusb_runs_the_libusb_check_and_a_failure_stops(self):
        checks = mock.Mock(return_value=[Check("USB", False, "not visible")])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(bench_joint.choose_backend(False, mock.Mock(), object, checks,
                                                         transport="pyusb"))
        checks.assert_called_once_with()

    def test_simulate_ignores_the_transport(self):
        simulated = object()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bench_joint.choose_backend(True, object, simulated, mock.Mock(),
                                                        transport="pyusb"), (simulated, "simulated"))


class ScriptTransportTests(unittest.TestCase):
    def run_script(self, name, *extra):
        module, args, _ = SCRIPTS[name]
        built = self.built = []

        class StandIn(Scorbot):
            def __init__(self, **kwargs):
                if "log_path" in kwargs:          # the session robot, not the offline preview
                    built.append(kwargs)
                    raise Built
                super().__init__(**kwargs)

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        argv = [f"{name}.py", "--output", str(Path(folder.name) / "run.jsonl"), *LABELS, *args,
                "--acknowledge-supervised-motion", *extra]
        self.checks = mock.Mock(return_value=[Check("USB 09F1:0007", False, "not visible to libusb")])
        with mock.patch.object(sys, "argv", argv), mock.patch.object(module, "Scorbot", StandIn), \
                mock.patch.object(module, "run_checks", self.checks), \
                mock.patch("builtins.input", side_effect=AssertionError("no prompt before connect")), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                return module.main()
            except Built:
                return "built"

    def scripts(self):
        for name, (_, _, needs_ruckig) in SCRIPTS.items():
            if needs_ruckig and not HAS_RUCKIG:
                continue
            yield name

    def test_default_is_auto_without_the_libusb_check(self):
        for name in self.scripts():
            with self.subTest(script=name):
                self.assertEqual(self.run_script(name), "built")
                self.checks.assert_not_called()
                self.assertEqual(self.built[0]["transport"], "auto")

    def test_device_file_is_passed_through_without_the_libusb_check(self):
        for name in self.scripts():
            with self.subTest(script=name):
                self.assertEqual(self.run_script(name, "--transport", "device_file"), "built")
                self.checks.assert_not_called()
                self.assertEqual(self.built[0]["transport"], "device_file")

    def test_pyusb_keeps_the_libusb_check_and_stops_on_its_failure(self):
        for name in self.scripts():
            with self.subTest(script=name):
                self.assertEqual(self.run_script(name, "--transport", "pyusb"), 1)
                self.checks.assert_called_once_with()
                self.assertEqual(self.built, [])

    def test_the_acknowledgement_is_still_required(self):
        for name in self.scripts():
            module, args, _ = SCRIPTS[name]
            with self.subTest(script=name), tempfile.TemporaryDirectory() as folder:
                argv = [f"{name}.py", "--output", str(Path(folder) / "run.jsonl"), *LABELS, *args]
                with mock.patch.object(sys, "argv", argv), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as caught:
                        module.main()
                self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
