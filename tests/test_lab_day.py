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


def idle_packet(gripper=0):
    data = bytearray(64)
    for offset in (19, 24, 29, 34, 39):
        data[offset + 2] = 128
    data[44:47] = (gripper % 65535).to_bytes(2, "little") + bytes([128 if gripper >= 0 else 127])
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
        self.assertEqual(lab_day.probe_counts(zeroed), {"base": -2, "shoulder": 3})
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
        code, out = self.run_main(["--online"], ["GO", "go"], steps)
        self.assertEqual((code, ran), (3, ["T", "S"]))      # T connects to nothing, so it needs no GO
        self.assertIn("Stopped by you before P", out)

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
