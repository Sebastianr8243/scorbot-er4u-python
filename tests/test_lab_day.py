"""scripts/lab_day.py: the Stack 2 lab-day runner's pass lines, GO gates and stop rules. Opens no USB."""

import importlib.util
import io
import json
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location(
    "lab_day", Path(__file__).resolve().parents[1] / "scripts" / "lab_day.py")
lab_day = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab_day)

from scripts import bring_up, lab_summary     # noqa: E402  (after the spec load, like the module under test)


def idle_packet(gripper=0):
    data = bytearray(64)
    for offset in (19, 24, 29, 34, 39):
        data[offset + 2] = 128
    data[44:47] = (gripper + 0x7FFFFF).to_bytes(3, "little")     # raw24 = controller count + 0x7FFFFF
    return bytes(data)


def timing(**changes):
    base = {"replies": 200, "lag": {"3": 150, "46": 50},
            "gap_ms": {"min": 15.0, "median": 18.8, "p95": 22.0, "max": 40.0},
            "repeats": {"count": 50, "longest": 2}}
    base.update(changes)
    return f"REAL counts {{'base': 400, 'shoulder': 0}}; switches 0x00\nREAL timing {json.dumps(base)}\n"


class PassLineTests(unittest.TestCase):
    def test_the_10_06_pattern_passes(self):
        # A quarter of replies repeating and a lag near 46 are normal at this pace (ledger PL06, PL07).
        self.assertEqual(lab_day.judge_probe(timing(), 200), [])

    def test_each_line_can_fail(self):
        cases = {"replies": timing(replies=150), "gap median": timing(gap_ms={"median": 30}),
                 "no usable ID": timing(lag={"3": 190, "null": 10}), "worst echo lag": timing(lag={"61": 200}),
                 "in a row": timing(repeats={"count": 5, "longest": 5})}
        for words, output in cases.items():
            with self.subTest(words):
                problems = lab_day.judge_probe(output, 200)
                self.assertEqual(len(problems), 1, problems)
                self.assertIn(words, problems[0])
        self.assertEqual(lab_day.judge_probe("REAL FAIL no device\n", 200), ["the probe printed no timing line"])

    def test_x_needs_a_joint_away_from_zero(self):
        self.assertEqual(lab_day.judge_x(lab_day.probe_counts(timing())), [])
        zeroed = "REAL counts {'base': 65533, 'shoulder': 3}; switches 0x00\n"
        # the probe prints the counts' low words: 65533 is -3 (docs/requirements/count-frame.md)
        self.assertEqual(lab_day.probe_counts(zeroed), {"base": -3, "shoulder": 3})
        self.assertIn("re-zeroed", lab_day.judge_x(lab_day.probe_counts(zeroed))[0])
        self.assertEqual(lab_day.judge_x(None), ["the probe printed no counts line"])

    def test_switches_must_each_be_seen_alone(self):
        watch = lab_day.SwitchWatch()
        self.assertIn("one at a time", watch.see(3))
        for bit in (1, 2, 4, 8):
            self.assertIn("switch seen", watch.see(bit))
            self.assertIsNone(watch.see(bit))
        self.assertFalse(watch.done())
        self.assertEqual(watch.missing(), ["roll"])
        watch.see(16)
        self.assertTrue(watch.done())

    def test_gripper_span_reads_the_home_trace(self):
        def packet(gripper):
            return {"direction": "in", "host_monotonic_ns": 0, "hex": idle_packet(gripper).hex()}
        rows = [{"event": "probe"},
                {"event": "motion_trace", "joint": "vendor_home",
                 "packets": [packet(0), {"direction": "out", "host_monotonic_ns": 0, "hex": "00" * 64},
                             packet(-120), packet(3000), packet(2900)]}]
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "run.jsonl"
            log.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            self.assertEqual(lab_day.gripper_span(log), 3120)
            log.write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
            self.assertIsNone(lab_day.gripper_span(log))


class RunnerTests(unittest.TestCase):
    def run_main(self, argv, answers, steps):
        replies = iter(answers)
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()) as out:
            code = lab_day.main([*argv, "--logs", folder], ask=lambda _: next(replies), steps=steps)
        return code, out.getvalue()

    def recorder(self, results=None):
        ran = []
        steps = {name: (lambda name=name: ran.append(name) or (results or {}).get(name, []))
                 for name in lab_day.STEPS}
        return ran, steps

    def test_every_connecting_step_waits_for_go(self):
        ran, steps = self.recorder()
        code, out = self.run_main(["--online"], ["GO", "go", "GO"], steps)
        self.assertEqual((code, ran), (3, ["T", "S", "G"]))      # T connects to nothing, so it needs no GO
        self.assertIn("SKIPPED P", out)

    def test_declining_a_motion_step_skips_it_and_the_log_says_skipped(self):
        ran, steps = self.recorder()
        with tempfile.TemporaryDirectory() as parent, redirect_stdout(io.StringIO()) as out:
            replies = iter(["GO", "GO", "no"])
            code = lab_day.main(["--online", "--logs", parent], ask=lambda _: next(replies), steps=steps)
            rows = day_rows(parent)
        self.assertEqual((code, ran), (3, ["T", "S", "P"]))
        self.assertIn("SKIPPED G", out.getvalue())
        results = {row["step"]: row["result"] for row in rows if row["event"] == "lab_step"}
        self.assertEqual(results, {"T": "pass", "S": "pass", "P": "pass", "G": "skipped"})
        self.assertEqual((rows[0]["event"], rows[-1]["event"], rows[-1]["exit"]), ("lab_day_start", "lab_day_end", 3))
        self.assertTrue(all(row["data_source"] == "real" and row["simulated"] is False for row in rows))

    def test_a_jog_skipped_inside_g_reaches_the_index_and_the_exit(self):
        def bring_up_run(argv, capture):
            logs = Path(argv[argv.index("--logs") + 1])
            logs.mkdir(parents=True)
            rows = [{"event": "step_skipped", "step": "shoulder velocity jog"},
                    {"event": "step_pass", "step": "base velocity jog"}]
            (logs / "real-1.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            return 0, ""
        replies = iter(["GO", "GO", "GO", "ALIGNED"])
        with tempfile.TemporaryDirectory() as parent, redirect_stdout(io.StringIO()), \
                unittest.mock.patch.object(lab_day, "step_t", return_value=[]), \
                unittest.mock.patch.object(lab_day, "step_s", return_value=[]), \
                unittest.mock.patch.object(lab_day, "step_p", return_value=[]), \
                unittest.mock.patch.object(lab_day, "step_g", lambda args, folder, ask=input, real=lab_day.step_g:
                                           real(args, folder, bring_up_run, ask=ask)), \
                unittest.mock.patch.object(lab_day.subprocess, "Popen", side_effect=AssertionError("real tool")), \
                unittest.mock.patch.object(lab_day.subprocess, "run", side_effect=AssertionError("real tool")):
            code = lab_day.main(["--online", "--transport", "pyusb", "--logs", parent], ask=lambda _: next(replies))
            rows = day_rows(parent)
        (g,) = [row for row in rows if row["event"] == "lab_step" and row["step"] == "G"]
        self.assertEqual((g["result"], g["skipped"]), ("pass", ["shoulder velocity jog"]))
        self.assertEqual((code, rows[-1]["exit"]), (3, 3))

    def test_an_interrupt_still_closes_the_index(self):
        def interrupted():
            raise KeyboardInterrupt
        for argv, steps, ask in ((["--simulate", "--from", "P"], {"P": interrupted}, lambda _: ""),
                                 (["--online", "--from", "P"], {"P": list}, unittest.mock.Mock(side_effect=EOFError))):
            with self.subTest(argv=argv), tempfile.TemporaryDirectory() as parent, redirect_stdout(io.StringIO()):
                with self.assertRaises((KeyboardInterrupt, EOFError)):
                    lab_day.main([*argv, "--logs", parent], ask=ask, steps=steps)
                rows = day_rows(parent)
                self.assertEqual(rows[-1]["event"], "lab_day_end")
                self.assertEqual((rows[-1]["exit"], rows[-1]["interrupted"]), (130, True))

    def test_a_fail_is_logged_with_its_problems(self):
        ran, steps = self.recorder({"P": ["gap median 30 ms"]})
        with tempfile.TemporaryDirectory() as parent, redirect_stdout(io.StringIO()):
            self.assertEqual(lab_day.main(["--simulate", "--logs", parent], ask=lambda _: "", steps=steps), 1)
            rows = day_rows(parent)
        failed = [row for row in rows if row["event"] == "lab_step" and row["result"] == "fail"]
        self.assertEqual([(row["step"], row["problems"]) for row in failed], [("P", ["gap median 30 ms"])])
        self.assertEqual(rows[-1]["exit"], 1)

    def test_it_stops_at_the_first_fail_and_says_where_to_resume(self):
        ran, steps = self.recorder({"P": ["gap median 30 ms"]})
        code, out = self.run_main(["--online", "--from", "S"], ["GO", "GO"], steps)
        self.assertEqual((code, ran), (1, ["S", "P"]))
        self.assertIn("FAIL P: gap median 30 ms", out)
        self.assertIn("--from P", out)

    def test_a_failed_t_stops_before_anything_connects(self):
        ran, steps = self.recorder({"T": ["read_timeout_check did not PASS"]})
        code, _ = self.run_main(["--online"], ["GO", "GO", "GO"], steps)
        self.assertEqual((code, ran), (1, ["T"]))

    def test_an_exception_is_a_fail_not_a_traceback(self):
        def boom():
            raise OSError("device gone")
        code, out = self.run_main(["--simulate", "--from", "G"], [], {"G": boom})
        self.assertEqual(code, 1)
        self.assertIn("FAIL G: OSError: device gone", out)

    def test_simulated_runs_ask_nothing_and_all_pass(self):
        ran, steps = self.recorder()
        self.assertEqual(self.run_main(["--simulate"], [], steps)[0], 0)
        self.assertEqual(ran, list(lab_day.STEPS))

    def test_mode_is_required(self):
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()), \
                unittest.mock.patch("sys.stderr", io.StringIO()):
            lab_day.main([])

    def test_no_mode_refuses_before_opening_anything(self):
        ran, steps = self.recorder()
        with tempfile.TemporaryDirectory() as parent, redirect_stdout(io.StringIO()), \
                unittest.mock.patch("sys.stderr", io.StringIO()), \
                unittest.mock.patch("scorbot.Scorbot", side_effect=AssertionError("opened the robot")), \
                unittest.mock.patch.object(lab_day.subprocess, "Popen", side_effect=AssertionError("ran a tool")), \
                unittest.mock.patch.object(lab_day.subprocess, "run", side_effect=AssertionError("ran a tool")):
            with self.assertRaises(SystemExit):
                lab_day.main(["--logs", parent], ask=lambda _: "GO", steps=steps)
            self.assertEqual(list(Path(parent).iterdir()), [])      # not even the dated folder
        self.assertEqual(ran, [])


def day_rows(parent):
    """The rows of the one dated lab-day log under ``parent``."""
    (folder,) = Path(parent).glob("lab-*")
    return [json.loads(line) for line in (folder / "lab-day.jsonl").read_text(encoding="utf-8").splitlines()]


class SimulatedDayTests(unittest.TestCase):
    """The real runners on the simulator, child processes included. Opens no USB."""

    def test_simulate_runs_every_step_with_a_velocity_jog_per_axis_into_one_log(self):
        with tempfile.TemporaryDirectory() as parent:
            # P's pass line judges the real controller's pacing; the simulated probe paces with time.sleep, which
            # rounds up to about 15.6 ms on Windows Python 3.10 (gap median 31 ms on CI). The pass lines have their own
            # tests (PassLineTests); this one checks the flow and the logs, so the probe still runs but is not judged.
            with redirect_stdout(io.StringIO()) as out, \
                    unittest.mock.patch.object(lab_day, "judge_probe", return_value=[]) as judged:
                code = lab_day.main(["--simulate", "--logs", parent], ask=lambda _: self.fail("simulate asked"))
            self.assertEqual(code, 0, out.getvalue()[-2000:])
            self.assertIn("timing", judged.call_args.args[0])         # the probe ran and printed its timing line
            rows = day_rows(parent)
            (folder,) = Path(parent).glob("lab-*")
            steps = {row["step"]: row for row in rows if row["event"] == "lab_step"}
            self.assertEqual(list(steps), list(lab_day.STEPS))
            # T and S cannot run on the simulator: they say so, they do not pass
            self.assertEqual({k: row["result"] for k, row in steps.items()},
                             {"T": "n/a", "S": "n/a", "P": "pass", "G": "pass"})
            self.assertTrue(all(row["data_source"] == "simulated" for row in rows))
            self.assertEqual(steps["P"]["logs"], ["probe-reads.jsonl"])
            (bring_up_log,) = steps["G"]["logs"]
            ladder = [json.loads(line) for line in (folder / bring_up_log).read_text(encoding="utf-8").splitlines()]
            passed = [row["step"] for row in ladder if row["event"] == "step_pass"]
            for axis in ("base", "shoulder", "elbow"):
                self.assertIn(f"{axis} velocity jog", passed)
            jogs = [(row["joint"], row["speed"]) for row in ladder if row["event"] == "velocity_jog_start"]
            speed = bring_up.VELOCITY_JOG_SPEED
            self.assertEqual(jogs, [(axis, sign * speed) for axis in ("base", "shoulder", "elbow") for sign in (1, -1)])
            self.assertIn("--sheet", out.getvalue())                 # it says how to make the record sheet

            sheet = io.StringIO()
            with redirect_stdout(sheet):
                lab_summary.main([str(folder / "lab-day.jsonl"), "--sheet"])
        text = sheet.getvalue()
        for axis in ("base", "shoulder", "elbow"):
            self.assertIn(f"{axis} velocity jog: out", text)
        self.assertIn("G pass", text)
        self.assertIn("T n/a", text)

    def test_the_sheet_says_which_steps_and_jogs_were_skipped(self):
        stamp = {"timestamp_utc": "2026-11-05T00:00:00+00:00", "data_source": "simulated", "simulated": True}
        day = [{"event": "lab_day_start", **stamp},
               {"event": "lab_step", "step": "P", "result": "skipped", "problems": [], "logs": [], **stamp},
               {"event": "lab_step", "step": "G", "result": "pass", "problems": [],
                "logs": ["bring-up/ladder.jsonl"], **stamp},
               {"event": "lab_day_end", "exit": 3, **stamp}]
        ladder = [{"event": "step_skipped", "step": "shoulder velocity jog", **stamp},
                  {"event": "velocity_jog_start", "joint": "base", "speed": 5, **stamp},
                  {"event": "velocity_jog_complete", "joint": "base", "speed": 5, "reason": "stop",
                   "setpoint_counts": -61, **stamp},
                  {"event": "velocity_jog_start", "joint": "base", "speed": -5, **stamp},
                  {"event": "velocity_jog_complete", "joint": "base", "speed": -5, "reason": "stop",
                   "setpoint_counts": 61, **stamp},
                  {"event": "step_pass", "step": "base velocity jog", **stamp}]
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "bring-up").mkdir()
            for name, rows in (("lab-day.jsonl", day), ("bring-up/ladder.jsonl", ladder)):
                (Path(folder) / name).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            with redirect_stdout(io.StringIO()) as out:
                lab_summary.main([str(Path(folder) / "lab-day.jsonl"), "--sheet"])
        text = out.getvalue()
        self.assertIn("P skipped", text)
        self.assertIn("base velocity jog: out -61 counts, back +61 counts (stop)", text)
        self.assertIn("shoulder velocity jog: skipped", text)
        self.assertIn("elbow velocity jog: not in the logs", text)

    def test_the_sheet_refuses_index_paths_outside_its_folder(self):
        stamp = {"timestamp_utc": "2026-11-05T00:00:00+00:00", "data_source": "simulated", "simulated": True}
        jog = [{"event": "velocity_jog_complete", "joint": "base", "speed": 5, "reason": "stop",
                "setpoint_counts": -61, **stamp}]
        with tempfile.TemporaryDirectory() as parent:
            folder = Path(parent) / "lab-1"
            folder.mkdir()
            (Path(parent) / "outside.jsonl").write_text(json.dumps(jog[0]) + "\n", encoding="utf-8")
            day = [{"event": "lab_day_start", **stamp},
                   {"event": "lab_step", "step": "G", "result": "pass", "problems": [],
                    "logs": ["../outside.jsonl"], **stamp}]
            (folder / "lab-day.jsonl").write_text("".join(json.dumps(r) + "\n" for r in day), encoding="utf-8")
            with redirect_stdout(io.StringIO()) as out:
                lab_summary.main([str(folder / "lab-day.jsonl"), "--sheet"])
        text = out.getvalue()
        self.assertIn("outside the lab-day folder", text)
        self.assertIn("base velocity jog: not in the logs", text)

    def test_the_sheet_pairs_out_and_back_within_one_log_by_sign(self):
        stamp = {"timestamp_utc": "2026-11-05T00:00:00+00:00", "data_source": "simulated", "simulated": True}

        def out_only(counts):
            return [{"event": "velocity_jog_complete", "joint": "base", "speed": 5, "reason": "stop",
                     "setpoint_counts": counts, **stamp}]
        day = [{"event": "lab_day_start", **stamp},
               {"event": "lab_step", "step": "G", "result": "pass", "problems": [],
                "logs": ["bring-up/a.jsonl", "bring-up/b.jsonl"], **stamp}]
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "bring-up").mkdir()
            for name, rows in (("lab-day.jsonl", day), ("bring-up/a.jsonl", out_only(-61)),
                               ("bring-up/b.jsonl", out_only(-62))):
                (Path(folder) / name).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            with redirect_stdout(io.StringIO()) as out:
                lab_summary.main([str(Path(folder) / "lab-day.jsonl"), "--sheet"])
        text = out.getvalue()
        self.assertNotIn("back -62", text)
        self.assertIn("base velocity jog: out -62 counts, no back jog (stop)", text)


class StepTests(unittest.TestCase):
    def test_t_is_skipped_on_winusb_and_runs_the_check_otherwise(self):
        calls = []
        run = lambda argv, capture: calls.append(argv) or (0, "PASS")   # noqa: E731
        with redirect_stdout(io.StringIO()):
            self.assertEqual(lab_day.step_t(SimpleNamespace(simulate=False, transport="pyusb"), run), [])
            self.assertEqual(calls, [])
            self.assertEqual(lab_day.step_t(SimpleNamespace(simulate=False, transport="auto"), run), [])
        self.assertIn("--online", calls[0])

    def test_p_judges_x_only_when_asked(self):
        zeroed = "REAL counts {'base': 1}\n" + timing().split("\n")[1]
        run = lambda argv, capture: (0, zeroed)                          # noqa: E731
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            args = SimpleNamespace(simulate=False, transport="pyusb", x=False)
            self.assertEqual(lab_day.step_p(args, Path(folder), run), [])
            args.x = True
            self.assertIn("re-zeroed", lab_day.step_p(args, Path(folder), run)[0])

    def test_g_runs_the_bring_up_with_the_velocity_jog_in_the_same_mode(self):
        for simulate, mode in ((True, "--simulate"), (False, "--online")):
            calls = []
            run = lambda argv, capture, calls=calls: calls.append(argv) or (0, "")   # noqa: E731
            args = SimpleNamespace(simulate=simulate, transport="pyusb")
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
                lab_day.step_g(args, Path(folder), run, ask=lambda _: "ALIGNED")
                self.assertIn("--velocity-jog", calls[0])
                self.assertIn(mode, calls[0])
                self.assertNotIn("--online" if simulate else "--simulate", calls[0])

    def test_steps_that_cannot_run_here_say_so(self):
        run = lambda argv, capture: (0, "PASS")                          # noqa: E731
        with redirect_stdout(io.StringIO()):
            skipped = lab_day.step_t(SimpleNamespace(simulate=False, transport="pyusb"), run)
            ran = lab_day.step_t(SimpleNamespace(simulate=False, transport="auto"), run)
            simulated_s = lab_day.step_s(SimpleNamespace(simulate=True, transport="auto"))
        self.assertIsInstance(skipped, lab_day.NotRun)
        self.assertIsInstance(simulated_s, lab_day.NotRun)
        self.assertEqual((list(skipped), skipped.reason != ""), ([], True))
        self.assertNotIsInstance(ran, lab_day.NotRun)

    def test_g_passes_only_when_the_operator_types_aligned(self):
        run = lambda argv, capture: (0, "")                              # noqa: E731
        args = SimpleNamespace(simulate=False, transport="pyusb")
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            self.assertEqual(lab_day.step_g(args, Path(folder), run, ask=lambda _: "ALIGNED"), [])
            for answer in ("", "aligned", "no"):
                self.assertIn("tape mark", lab_day.step_g(args, Path(folder), run, ask=lambda _, a=answer: a)[0])
            failed = lambda argv, capture: (1, "")                       # noqa: E731
            asked = []
            self.assertIn("bring_up exited 1", lab_day.step_g(args, Path(folder), failed, ask=asked.append)[0])
            self.assertEqual(asked, [])                                  # a failed Home is never "aligned"

    def test_s_watches_switches_then_disconnects(self):
        bits = iter([0, 1, 0, 2, 4, 8, 16])

        class Robot:
            _device = object()
            disconnected = False

            def __init__(self, transport):
                self.transport = transport

            def get_state_and_packet(self):
                return SimpleNamespace(enabled=False, homed=False, fault=None, simulated=False), idle_packet()

            def get_state(self):
                return SimpleNamespace(home_switch_bits=next(bits))

            def disconnect(self):
                Robot.disconnected = True

        with redirect_stdout(io.StringIO()) as out:
            problems = lab_day.step_s(SimpleNamespace(simulate=False, transport="pyusb"), Robot, pause=lambda _: None)
        self.assertEqual(problems, [])
        self.assertTrue(Robot.disconnected)
        self.assertIn("roll switch seen", out.getvalue())

    def test_s_fails_when_a_switch_never_shows(self):
        class Robot:
            _device = object()

            def __init__(self, transport):
                pass

            def get_state_and_packet(self):
                return SimpleNamespace(enabled=False, homed=False, fault=None, simulated=False), idle_packet()

            def get_state(self):
                return SimpleNamespace(home_switch_bits=1)

            def disconnect(self):
                pass

        ticks = iter(range(0, 1000, 60))
        with redirect_stdout(io.StringIO()):
            problems = lab_day.step_s(SimpleNamespace(simulate=False, transport="pyusb"), Robot,
                                      clock=lambda: next(ticks), pause=lambda _: None)
        self.assertIn("shoulder, elbow, pitch, roll", problems[0])


if __name__ == "__main__":
    unittest.main()
