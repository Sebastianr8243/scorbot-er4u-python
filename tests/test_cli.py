"""Issue 158 launcher contract. No real USB discovery, device opens or motion.

Only calibration checks take the parsed args; the other check_* helpers take
no arguments. check_packages returns the list from preflight.run_checks().
"""

import argparse
import builtins
import contextlib
import ctypes
import importlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from scorbot import calibration, cli, preflight


REPO_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_TARGETS = {
    ("setup", "lerobot"): ("script", "scripts/setup_lerobot.py"),
    ("camera", "check"): ("module", "scorbot.camera"),
    ("ai", "train"): ("script", "scripts/ai_commands.py"),
    ("ai", "rollout"): ("script", "scripts/ai_commands.py"),
    ("sim", "check"): ("script", "examples/lerobot_sim_check.py"),
    ("sim", "view"): ("script", "examples/mujoco_twin_view.py"),
    ("jog", "preview"): ("script", "examples/preview_jog.py"),
    ("lab", "day"): ("script", "scripts/lab_day.py"),
    ("lab", "session"): ("module", "scorbot.lab"),
    ("lab", "bring-up"): ("script", "scripts/bring_up.py"),
    ("lab", "watch"): ("script", "scripts/watch_lab_log.py"),
    ("lab", "summary"): ("script", "scripts/lab_summary.py"),
    ("analyze", "review-logs"): ("script", "scripts/review_lab_logs.py"),
    ("analyze", "follow"): ("script", "scripts/follow_report.py"),
    ("analyze", "usb-trace"): ("script", "scripts/usb_trace.py"),
    ("analyze", "vendor-check"): ("script", "scripts/vendor_check.py"),
    ("analyze", "fit-calibration"): ("script", "scripts/fit_calibration.py"),
    ("analyze", "build-calibration-csv"): ("script", "scripts/build_calibration_csv.py"),
    ("analyze", "export-dataset"): ("script", "scripts/export_policy_dataset.py"),
}
CHECK_NAMES = {
    "check_python": "Python",
    "check_venv": "Venv",
    "check_packages": "Packages",
    "check_lock": "Locked deps",
    "check_driver": "Controller",
    "check_calibration": "Calibration",
    "check_lerobot": "LeRobot",
}


def invoke(*argv, stdout=None):
    """Run the CLI in-process, including argparse's SystemExit codes."""
    out = stdout if stdout is not None else io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as exc:
            code = exc.code
    return SimpleNamespace(returncode=code, stdout=out.getvalue(), stderr=err.getvalue())


def doctor_args(**overrides):
    return argparse.Namespace(**dict({"json": False, "strict": False, "verbose": False,
                                     "calibration": None, "robot_id": "lab-er4u-1"}, **overrides))


def check(name, level="ok", detail="ready", fix=""):
    return preflight.Check(name, level == "ok", detail, level, fix)


@contextlib.contextmanager
def chosen_checks(**overrides):
    """Patch the small checks, keeping doctor_checks and rendering real."""
    with contextlib.ExitStack() as stack:
        patched = {}
        for helper, name in CHECK_NAMES.items():
            value = overrides.get(helper, check(name))
            result = [value] if helper == "check_packages" else value
            patched[helper] = stack.enter_context(mock.patch.object(cli, helper, return_value=result))
        yield patched


@contextlib.contextmanager
def fake_preflight(device):
    """Use preflight's dynamic-import seam, as tests/test_preflight.py does."""
    backend = object()
    find = mock.Mock(return_value=device)
    modules = {
        "numpy": SimpleNamespace(),
        "usb.core": SimpleNamespace(find=find),
        "libusb_package": SimpleNamespace(get_libusb1_backend=lambda: backend),
        "usb.backend.libusb1": SimpleNamespace(get_backend=lambda: backend),
    }

    def import_module(name):
        if name not in modules:
            raise ImportError(name)
        return modules[name]

    with mock.patch.object(preflight, "import_module", side_effect=import_module), \
            mock.patch.object(preflight.metadata, "version", return_value="1.0"):
        yield find


class ForwardingTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(cli, "REPO_ROOT", REPO_ROOT)
        patcher.start()
        self.addCleanup(patcher.stop)

    def expected_argv(self, command, rest):
        kind, path = EXPECTED_TARGETS[command]
        prefix = [sys.executable, str(REPO_ROOT / path)] if kind == "script" else [sys.executable, "-m", path]
        return [*prefix, *([command[1]] if command[0] in {"camera", "ai"} else []), *rest]

    def test_targets_are_exactly_the_issue_scope(self):
        self.assertEqual(cli.TARGETS, EXPECTED_TARGETS)

    def test_every_target_forwards_exact_argv_and_exit_code(self):
        for command in EXPECTED_TARGETS:
            with self.subTest(command=command), mock.patch.object(cli, "run_child", return_value=7) as child:
                rest = ["--x", "1", "--simulate"]
                result = invoke(*command, *rest)
                self.assertEqual(result.returncode, 7, result.stderr)
                child.assert_called_once_with(self.expected_argv(command, rest))

    def test_launcher_never_adds_authorization_flags(self):
        forbidden = {"--online", "--yes", "--force", "--acknowledge-supervised-motion",
                     "--acknowledge-connect-handshake"}
        for command in EXPECTED_TARGETS:
            with self.subTest(command=command), mock.patch.object(cli, "run_child", return_value=0) as child:
                self.assertEqual(invoke(*command).returncode, 0)
                child.assert_called_once_with(self.expected_argv(command, []))
                self.assertTrue(forbidden.isdisjoint(child.call_args.args[0]))

    def test_user_authorizations_are_forwarded_once_in_place(self):
        # run_child is patched: these strings must never reach a real process.
        for command in EXPECTED_TARGETS:
            for flag in ("--online", "--acknowledge-supervised-motion", "--acknowledge-connect-handshake"):
                with self.subTest(command=command, flag=flag), \
                        mock.patch.object(cli, "run_child", return_value=0) as child:
                    rest = ["--x", "1", flag, "--label", "two words"]
                    self.assertEqual(invoke(*command, *rest).returncode, 0)
                    child.assert_called_once_with(self.expected_argv(command, rest))
                    self.assertEqual(child.call_args.args[0].count(flag), 1)

    def test_help_after_each_task_belongs_to_the_child(self):
        for command in EXPECTED_TARGETS:
            with self.subTest(command=command), mock.patch.object(cli, "run_child", return_value=0) as child:
                self.assertEqual(invoke(*command, "--help").returncode, 0)
                child.assert_called_once_with(self.expected_argv(command, ["--help"]))

    def test_developer_and_bench_scripts_are_unreachable(self):
        excluded = {"build_bench_kit.py", "check_docs.py", "bench_joint.py", "bench_stream.py",
                    "bench_gripper.py", "policy_loop.py", "pick_place.py", "arm_characterise.py"}
        for target in cli.TARGETS.values():
            with self.subTest(target=target):
                name = Path(target[1]).name
                self.assertNotIn(name, excluded)
                self.assertFalse(name.startswith("make_") and name.endswith("_fixture.py"))

    def test_missing_checkout_names_missing_script_and_starts_nothing(self):
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(cli, "REPO_ROOT", Path(folder)), \
                mock.patch.object(cli, "run_child") as child:
            result = invoke("lab", "bring-up")
            self.assertEqual(result.returncode, 1)
            self.assertIn("source checkout", result.stderr.lower())
            self.assertIn("bring_up.py", result.stderr)
            child.assert_not_called()


class ChildProcessTests(unittest.TestCase):
    def test_interrupt_keeps_waiting_for_child_cleanup_without_killing(self):
        proc = mock.Mock()
        proc.wait.side_effect = [KeyboardInterrupt, 3]
        argv = [sys.executable, "fake_script.py", "--simulate"]
        with mock.patch.object(cli.subprocess, "Popen", return_value=proc) as popen:
            self.assertEqual(cli.run_child(argv), 3)
        popen.assert_called_once_with(argv)
        self.assertEqual(proc.wait.call_count, 2)
        proc.kill.assert_not_called()
        proc.terminate.assert_not_called()


class SafetyGateTests(unittest.TestCase):
    def launcher(self, *args, stdin=""):
        self.assertNotIn("--online", args)
        return subprocess.run([sys.executable, "-m", "scorbot", *args], cwd=REPO_ROOT,
                              capture_output=True, text=True, timeout=120, input=stdin)

    def test_bring_up_without_mode_refuses_before_connecting(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.launcher("lab", "bring-up", "--logs", folder)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("Real mode needs --online (or use --simulate)", result.stdout)
            self.assertIn("FAIL authorization", result.stdout)
            logs = list(Path(folder).glob("*.jsonl"))
            self.assertEqual(len(logs), 1)
            events = [json.loads(line) for line in logs[0].read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["event"] for row in events], ["ladder_start", "step_fail", "ladder_end"])
            self.assertEqual(events[1]["step"], "authorization")
            self.assertIsNone(events[1]["before"])
            self.assertIsNone(events[1]["after"])

    def test_lab_day_simulated_bad_step_refuses_before_the_first_step(self):
        # Simulated lab_day skips GO, ignoring "no"; the full P/G ladder runs
        # hundreds of replies and a Home. Use its cheap, help-free parse refusal.
        with tempfile.TemporaryDirectory() as folder:
            result = self.launcher("lab", "day", "--simulate", "--from", "STOP",
                                   "--logs", folder, stdin="no\n")
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("invalid choice", result.stderr)
            self.assertIn("STOP", result.stderr)
            self.assertNotIn("=== T:", result.stdout)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_lab_day_requires_explicit_mode(self):
        result = self.launcher("lab", "day", stdin="no\n")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("required", result.stderr)
        self.assertIn("--simulate", result.stderr)
        self.assertIn("--online", result.stderr)
        self.assertNotIn("=== T:", result.stdout)

    def test_plugin_real_arm_refusal_remains_present(self):
        path = REPO_ROOT / "plugins/lerobot_robot_scorbot/lerobot_robot_scorbot/robot.py"
        self.assertIn("REAL_ARM_REFUSED", path.read_text(encoding="utf-8"))


class MenuTests(unittest.TestCase):
    def test_no_args_lists_everyday_commands_on_separate_lines(self):
        with mock.patch.object(cli, "run_child") as child:
            result = invoke()
        self.assertEqual(result.returncode, 0)
        lines = result.stdout.splitlines()
        positions = []
        for command in ("doctor", "sim check", "sim view", "jog preview", "setup lerobot", "camera check", "record"):
            matches = [i for i, line in enumerate(lines) if line.startswith(f"  scorbot {command} ")]
            self.assertEqual(len(matches), 1, result.stdout)
            positions.extend(matches)
        self.assertEqual(len(set(positions)), 7, result.stdout)
        self.assertIn("scorbot ai --help", result.stdout)
        self.assertIn("scorbot lab --help", result.stdout)
        self.assertIn("scorbot analyze --help", result.stdout)
        child.assert_not_called()

    def test_top_and_group_help_exit_zero_without_a_child(self):
        for args in (("--help",), ("lab", "--help"), ("analyze", "--help")):
            with self.subTest(args=args), mock.patch.object(cli, "run_child") as child:
                result = invoke(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(result.stdout)
                child.assert_not_called()

    def test_typos_offer_nearest_command_and_start_nothing(self):
        for args, suggestion in ((("doctr",), "doctor"), (("lab", "bringup"), "bring-up")):
            with self.subTest(args=args), mock.patch.object(cli, "run_child") as child:
                result = invoke(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn(f"Did you mean: {suggestion}?", result.stderr)
                child.assert_not_called()

    def test_record_prints_the_readme_simulation_command_without_running(self):
        with mock.patch.object(cli, "run_child") as child:
            result = invoke("record")
        self.assertEqual(result.returncode, 0, result.stderr)
        for flag in ("lerobot-record", "--robot.type=scorbot", "--robot.simulate=true",
                     "--teleop.type=scorbot_joint_keys", "--dataset.push_to_hub=false"):
            self.assertIn(flag, result.stdout)
        self.assertNotIn("--robot.simulate=false", result.stdout)
        child.assert_not_called()


class DoctorReadOnlyTests(unittest.TestCase):
    def test_text_and_json_never_open_device_or_write_files(self):
        accesses = []
        write_opens = []

        class Device:
            def __getattribute__(self, name):
                safe = {"idVendor": 0x09F1, "idProduct": 0x0007, "bus": 1, "address": 2}
                if name in safe:
                    return safe[name]
                accesses.append(name)
                raise AssertionError(f"doctor accessed USB device attribute {name}")

        def readonly_open(original):
            def guarded(file, mode="r", *args, **kwargs):
                if any(flag in mode for flag in "wax+"):
                    write_opens.append((file, mode))
                    self.fail(f"write open: {file}, {mode}")
                return original(file, mode, *args, **kwargs)
            return guarded

        for args in (("doctor",), ("doctor", "--json")):
            with self.subTest(args=args), contextlib.ExitStack() as stack:
                find = stack.enter_context(fake_preflight(Device()))
                api = stack.enter_context(mock.patch("scorbot.transport.device_file.Win32Api"))
                link = stack.enter_context(mock.patch("scorbot.transport.device_file.DeviceLink"))
                child = stack.enter_context(mock.patch.object(cli, "run_child"))
                stack.enter_context(mock.patch.object(cli, "winreg", None))
                kernel = mock.Mock()
                stack.enter_context(mock.patch.object(ctypes, "WinDLL", return_value=kernel, create=True))
                if hasattr(ctypes, "windll"):
                    stack.enter_context(mock.patch.object(ctypes.windll, "kernel32", kernel))
                stack.enter_context(mock.patch("builtins.open", side_effect=readonly_open(builtins.open)))
                stack.enter_context(mock.patch("io.open", side_effect=readonly_open(io.open)))
                result = invoke(*args)
                self.assertIn(result.returncode, (0, 1), result.stderr)
                self.assertTrue(result.stdout)
                self.assertGreaterEqual(find.call_count, 1, "doctor must reuse preflight enumeration")
                for call in find.call_args_list:
                    self.assertEqual(call.kwargs["idVendor"], 0x09F1)
                    self.assertEqual(call.kwargs["idProduct"], 0x0007)
                api.assert_not_called()
                link.assert_not_called()
                kernel.CreateFileW.assert_not_called()
                kernel.CreateFileA.assert_not_called()
                child.assert_not_called()
                self.assertEqual(accesses, [])
                self.assertEqual(write_opens, [])
                if "--json" in args:
                    json.loads(result.stdout)


class DoctorOutputTests(unittest.TestCase):
    def test_doctor_checks_aggregates_each_small_check(self):
        with chosen_checks() as patched:
            results = cli.doctor_checks(doctor_args())
        self.assertIsInstance(results, list)
        self.assertEqual({item.name for item in results}, set(CHECK_NAMES.values()))
        self.assertTrue(all(isinstance(item, preflight.Check) for item in results))
        for helper in patched.values():
            helper.assert_called_once()

    def test_packages_reuses_all_preflight_checks(self):
        checks = [preflight.Check("numpy", True, "imports"),
                  preflight.Check("libusb backend", True, "fake backend"),
                  preflight.Check("USB 09F1:0007", False, "not visible")]
        with mock.patch.object(preflight, "run_checks", return_value=checks) as run:
            self.assertEqual(cli.check_packages(), checks)
        run.assert_called_once_with()

    def test_doctor_enumerates_usb_once(self):
        """check_driver reuses the preflight rows doctor already has instead of running them again."""
        checks = [preflight.Check("USB 09F1:0007", True, "visible")]
        with mock.patch.object(preflight, "run_checks", return_value=checks) as run, \
                mock.patch.object(cli, "check_calibration", return_value=check("Calibration")), \
                mock.patch.object(cli, "check_lerobot", return_value=check("LeRobot")), \
                mock.patch.object(cli, "check_lock", return_value=check("Locked deps")):
            cli.doctor_checks(doctor_args())
        run.assert_called_once_with()

    def test_intelitek_driver_makes_invisible_usb_a_warning_not_a_failure(self):
        rows = [preflight.Check("USB 09F1:0007", False, "Not visible to libusb")]
        erusb = check("Controller", "warn", "09F1:0007 registered driver: ERUSB", "use --transport auto")
        absent = check("Controller", "warn", "09F1:0007 has no registered driver", "plug in")
        unplugged = check("Controller", "warn", "09F1:0007 registered driver: ERUSB (not connected now)", "plug in")
        for driver, expected in ((erusb, ("warn", 0)), (absent, ("fail", 1)), (unplugged, ("fail", 1))):
            with self.subTest(driver=driver.detail), chosen_checks(check_driver=driver),                     mock.patch.object(cli, "check_packages", return_value=rows):
                data = json.loads(invoke("doctor", "--json").stdout)
            usb = next(row for row in data["checks"] if row["name"] == "USB 09F1:0007")
            self.assertEqual((usb["level"], data["exit"]), expected)

    def test_text_marks_fixes_summary_and_ascii_without_ansi(self):
        with chosen_checks(
                check_python=check("Python", "ok", "python ready", "OK_FIX_MUST_NOT_PRINT"),
                check_venv=check("Venv", "warn", "venv missing", "WARN_FIX_COMMAND"),
                check_lock=check("Locked deps", "fail", "lock mismatch", "FAIL_FIX_COMMAND"),
                check_lerobot=check("LeRobot", "skip", "optional", "SKIP_FIX_MUST_NOT_PRINT")):
            result = invoke("doctor")
        self.assertEqual(result.returncode, 1)
        lines = result.stdout.splitlines()
        self.assertIn("Scorbot doctor", lines[0])
        self.assertIn("never opens the controller", lines[0])
        for mark, name, detail in (("ok", "Python", "python ready"), ("!!", "Venv", "venv missing"),
                                  ("XX", "Locked deps", "lock mismatch"), ("--", "LeRobot", "optional")):
            self.assertTrue(any(line.startswith(f" [{mark}]") and name in line and detail in line
                                for line in lines), result.stdout)
        for fix in ("WARN_FIX_COMMAND", "FAIL_FIX_COMMAND"):
            line = next(line for line in lines if fix in line)
            self.assertTrue(line.startswith("    "), line)
        self.assertNotIn("OK_FIX_MUST_NOT_PRINT", result.stdout)
        self.assertNotIn("SKIP_FIX_MUST_NOT_PRINT", result.stdout)
        self.assertRegex(lines[-1], r"1 problems?\b.*1 warnings?\b")
        self.assertRegex(lines[-1], r"Next:.*scorbot")
        self.assertEqual(result.stdout.count("Next:"), 1)
        result.stdout.encode("ascii")
        self.assertNotIn("\x1b", result.stdout)

    def test_exit_status_for_ok_warn_fail_and_strict(self):
        for level, flags, expected in (("ok", [], 0), ("skip", [], 0), ("warn", [], 0),
                                       ("fail", [], 1), ("warn", ["--strict"], 1)):
            with self.subTest(level=level, flags=flags), \
                    chosen_checks(check_lock=check("Locked deps", level, "detail", "fix")):
                result = invoke("doctor", *flags)
                self.assertEqual(result.returncode, expected, result.stdout)

    def test_json_is_one_object_with_exact_shape_counts_and_exit(self):
        for level, strict, expected in (("warn", False, 0), ("warn", True, 1), ("fail", False, 1)):
            with self.subTest(level=level, strict=strict), \
                    chosen_checks(check_lock=check("Locked deps", level, "mismatch", "sync deps")):
                flags = ["--json", "--strict"] if strict else ["--json"]
                result = invoke("doctor", *flags)
                data = json.loads(result.stdout)
                self.assertEqual(set(data), {"checks", "problems", "warnings", "exit"})
                self.assertEqual(len(data["checks"]), len(CHECK_NAMES))
                for row in data["checks"]:
                    self.assertEqual(set(row), {"name", "level", "detail", "fix"})
                row = next(row for row in data["checks"] if row["name"] == "Locked deps")
                self.assertEqual(row, {"name": "Locked deps", "level": level,
                                       "detail": "mismatch", "fix": "sync deps"})
                self.assertEqual(data["problems"], int(level == "fail"))
                self.assertEqual(data["warnings"], int(level == "warn"))
                self.assertEqual(data["exit"], expected)
                self.assertEqual(result.returncode, expected)
                self.assertEqual(result.stderr, "")

    def test_no_color_even_on_tty_when_no_color_is_set(self):
        class Tty(io.StringIO):
            def isatty(self):
                return True

        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}), \
                chosen_checks(check_lock=check("Locked deps", "warn", "mismatch", "sync")):
            result = invoke("doctor", stdout=Tty())
        self.assertEqual(result.returncode, 0)
        self.assertIn(" [!!]", result.stdout)
        self.assertNotIn("\x1b", result.stdout)
        result.stdout.encode("ascii")

    def test_empty_level_derives_ok_or_fail_from_legacy_passed_field(self):
        for passed, expected in ((True, "ok"), (False, "fail")):
            with self.subTest(passed=passed), \
                    chosen_checks(check_python=preflight.Check("Python", passed, "legacy")):
                data = json.loads(invoke("doctor", "--json").stdout)
                row = next(row for row in data["checks"] if row["name"] == "Python")
                self.assertEqual(row["level"], expected)
                self.assertEqual(data["exit"], int(not passed))

    def test_doctor_rejects_python_3_11(self):
        for version, expected in (((3, 11, 9), "fail"), ((3, 12, 0), "ok")):
            with self.subTest(version=version), mock.patch.object(cli.sys, "version_info", version):
                self.assertEqual(cli.check_python().level, expected)

    def test_doctor_options_reach_calibration_check(self):
        with chosen_checks() as patched:
            result = invoke("doctor", "--json", "--strict", "--verbose", "--calibration", "my robot.json",
                            "--robot-id", "desk-test")
        self.assertEqual(result.returncode, 0, result.stderr)
        args = patched["check_calibration"].call_args.args[0]
        self.assertEqual(str(args.calibration), "my robot.json")
        self.assertEqual(args.robot_id, "desk-test")
        self.assertTrue(args.verbose)
        self.assertTrue(args.strict)
        with chosen_checks() as patched:
            invoke("doctor")
        self.assertEqual(patched["check_calibration"].call_args.args[0].robot_id, "lab-er4u-1")


class LockCheckTests(unittest.TestCase):
    def test_lock_compares_only_installed_packages_with_normalized_names(self):
        locked = ('version = 1\n'
                  '[[package]]\nname = "numpy"\nversion = "2.0"\n'
                  '[[package]]\nname = "Demo.Mixed_Name"\nversion = "1.0"\n'
                  '[[package]]\nname = "locked-only"\nversion = "9.0"\n')

        def version(name):
            installed = {"numpy": "2.0", "demo-mixed-name": "2.0"}
            if name not in installed:
                raise importlib.metadata.PackageNotFoundError(name)
            return installed[name]

        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "uv.lock").write_text(locked, encoding="utf-8")
            with mock.patch.object(cli, "REPO_ROOT", Path(folder)), \
                    mock.patch.object(importlib.metadata, "version", side_effect=version) as installed:
                result = cli.check_lock()
        self.assertEqual(result.level, "warn", result.detail)
        self.assertRegex(result.detail.lower(), r"demo[-_.]mixed[-_.]name")
        self.assertIn("1.0", result.detail)
        self.assertIn("2.0", result.detail)
        self.assertNotIn("numpy", result.detail.lower())
        self.assertNotIn("locked-only", result.detail.lower())
        self.assertTrue(result.fix)
        installed.assert_any_call("demo-mixed-name")

    def test_missing_required_dependency_fails(self):
        locked = ('version = 1\n'
                  '[[package]]\nname = "scorbot-er4u"\nversion = "0.1.0"\n'
                  'dependencies = [{ name = "mcap" }, { name = "numpy" }]\n'
                  '[[package]]\nname = "mcap"\nversion = "1.5"\n'
                  '[[package]]\nname = "numpy"\nversion = "2.0"\n'
                  '[[package]]\nname = "extra-only"\nversion = "1.0"\n')

        def version(name):
            if name == "numpy":
                return "2.0"
            raise importlib.metadata.PackageNotFoundError(name)

        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "uv.lock").write_text(locked, encoding="utf-8")
            with mock.patch.object(cli, "REPO_ROOT", Path(folder)), \
                    mock.patch.object(importlib.metadata, "version", side_effect=version):
                result = cli.check_lock()
        self.assertEqual(result.level, "fail", result.detail)
        self.assertIn("mcap", result.detail)
        self.assertNotIn("extra-only", result.detail)


class DriverCheckTests(unittest.TestCase):
    def registry(self, started=1):
        base = r"SYSTEM\CurrentControlSet\Enum\USB\VID_09F1&PID_0007"
        service_enum = r"SYSTEM\CurrentControlSet\Services\ERUSB\Enum"
        instance = base + r"\FAKE_CONTROLLER"
        hive = object()

        class Key(str):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def open_key(root, path, *args, **kwargs):
            full = path if root is hive else str(root) + "\\" + path
            if full not in (base, instance, service_enum):
                raise FileNotFoundError(full)
            return Key(full)

        def enum_key(key, index):
            if str(key) == base and index == 0:
                return "FAKE_CONTROLLER"
            raise OSError("no more keys")

        def query_value(key, name):
            if str(key) == instance and name == "Service":
                return "ERUSB", 1
            if str(key) == service_enum and name == "Count" and started is not None:
                return started, 4
            raise FileNotFoundError(name)

        return SimpleNamespace(HKEY_LOCAL_MACHINE=hive, KEY_READ=0x20019, KEY_WOW64_64KEY=0x100,
                               OpenKey=mock.Mock(side_effect=open_key), EnumKey=mock.Mock(side_effect=enum_key),
                               QueryValueEx=mock.Mock(side_effect=query_value), CloseKey=mock.Mock(),
                               QueryInfoKey=lambda key: (1, 0, 0) if str(key) == base else (0, 1, 0))

    def test_erusb_without_libusb_visibility_is_warning_with_transport_fix(self):
        registry = self.registry()
        invisible = [preflight.Check("USB 09F1:0007", False, "Not visible to libusb")]
        with mock.patch.object(cli.sys, "platform", "win32"), \
                mock.patch.object(cli, "winreg", registry), fake_preflight(None), \
                mock.patch.object(preflight, "run_checks", return_value=invisible):
            result = cli.check_driver()
        self.assertEqual(result.level, "warn", result.detail)
        self.assertIn("ERUSB", result.detail)
        self.assertIn("--transport auto", result.fix)
        self.assertIn("docs/lab/WINDOWS_BENCH_RUN.md", result.fix)
        registry.QueryValueEx.assert_any_call(mock.ANY, "Service")

    def test_stale_erusb_entry_without_started_device_says_not_connected(self):
        # The USB Enum key outlives unplugging; the driver's Services Enum Count is what Windows started now.
        invisible = [preflight.Check("USB 09F1:0007", False, "Not visible to libusb")]
        for started in (0, None):
            registry = self.registry(started=started)
            with self.subTest(started=started), mock.patch.object(cli.sys, "platform", "win32"), \
                    mock.patch.object(cli, "winreg", registry), \
                    mock.patch.object(preflight, "run_checks", return_value=invisible):
                result = cli.check_driver()
            self.assertEqual(result.level, "warn")
            self.assertIn("not connected", result.detail)
            self.assertRegex(result.fix.lower(), r"plug.*controller")

    def test_non_windows_skips_without_registry_access(self):
        registry = self.registry()
        with mock.patch.object(cli.sys, "platform", "linux"), \
                mock.patch.object(cli, "winreg", registry), fake_preflight(None):
            result = cli.check_driver()
        self.assertEqual(result.level, "skip")
        registry.OpenKey.assert_not_called()

    def test_absent_registry_device_warns_to_plug_in_controller(self):
        # Pick warn (rather than skip): a lab user needs a concrete next step.
        registry = self.registry()
        registry.OpenKey.side_effect = FileNotFoundError("controller absent")
        with mock.patch.object(cli.sys, "platform", "win32"), \
                mock.patch.object(cli, "winreg", registry), fake_preflight(None):
            result = cli.check_driver()
        self.assertEqual(result.level, "warn")
        self.assertRegex(result.fix.lower(), r"plug.*controller")


class CalibrationCheckTests(unittest.TestCase):
    def test_no_calibration_warns_with_physical_calibration_guide(self):
        result = cli.check_calibration(doctor_args())
        self.assertEqual(result.level, "warn")
        self.assertIn("docs/lab/PHYSICAL_CALIBRATION.md", result.fix)

    def test_bad_json_and_invalid_schema_report_loader_message(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.json"
            for content in ("{not json", "{}"):
                with self.subTest(content=content):
                    path.write_text(content, encoding="utf-8")
                    with self.assertRaises((ValueError, OSError)) as error:
                        calibration.load_calibration(path, robot_id="lab-er4u-1")
                    result = cli.check_calibration(doctor_args(calibration=str(path)))
                    self.assertEqual(result.level, "fail")
                    self.assertIn(str(error.exception), result.detail)

    def test_valid_calibration_uses_requested_robot_id(self):
        for robot_id in ("lab-er4u-1", "desk-test"):
            with self.subTest(robot_id=robot_id), \
                    mock.patch.object(calibration, "load_calibration", return_value=object()) as load:
                result = cli.check_calibration(doctor_args(calibration="validated.json", robot_id=robot_id))
            self.assertEqual(result.level, "ok", result.detail)
            load.assert_called_once()
            self.assertEqual(Path(load.call_args.args[0]), Path("validated.json"))
            self.assertEqual(load.call_args.kwargs, {"robot_id": robot_id})

    def test_any_loader_exception_becomes_failed_check(self):
        with mock.patch.object(calibration, "load_calibration", side_effect=RuntimeError("loader failed")):
            result = cli.check_calibration(doctor_args(calibration="validated.json"))
        self.assertEqual(result.level, "fail")
        self.assertIn("loader failed", result.detail)


class LeRobotCheckTests(unittest.TestCase):
    def test_missing_optional_packages_skip_without_importing_them(self):
        with mock.patch.object(importlib.util, "find_spec", return_value=None) as spec, \
                mock.patch("builtins.__import__", wraps=builtins.__import__) as imports, \
                mock.patch.object(importlib, "import_module", wraps=importlib.import_module) as dynamic:
            result = cli.check_lerobot()
        self.assertEqual(result.level, "skip")
        self.assertIn("optional", result.detail.lower())
        self.assertEqual({call.args[0] for call in spec.call_args_list}, {"lerobot", "lerobot_robot_scorbot"})
        for call in [*imports.call_args_list, *dynamic.call_args_list]:
            self.assertFalse(call.args[0].startswith("lerobot"), call)


class PreflightCompatibilityTests(unittest.TestCase):
    def test_old_check_constructor_keeps_default_fields(self):
        result = preflight.Check("legacy", True, "detail")
        self.assertEqual(result.level, "")
        self.assertEqual(result.fix, "")

    def test_preflight_pass_fail_lines_and_summary_are_unchanged(self):
        checks = [preflight.Check("Python", True, "supported"), preflight.Check("numpy", False, "missing")]
        out = io.StringIO()
        with mock.patch.object(preflight, "run_checks", return_value=checks), contextlib.redirect_stdout(out):
            self.assertEqual(preflight.main(), 1)
        self.assertEqual(out.getvalue(),
                         "PASS Python: supported\nFAIL numpy: missing\n"
                         "Preflight failed. Resolve the failed checks before using scorbot.Scorbot.connect().\n")


if __name__ == "__main__":
    unittest.main()
