"""scripts/summarize_log.py on hand-made server logs: no server, no DLL, no arm."""

import ast
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "summarize_log.py"
spec = importlib.util.spec_from_file_location("summarize_log", SCRIPT)
summarize_log = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summarize_log)


def command(t, **fields):
    return json.dumps({"t": t, "event": "command", "source": "vendor_dll_simulated", "line": json.dumps(fields)})


def reply(t, **fields):
    return json.dumps({"t": t, "event": "reply", "source": "vendor_dll_simulated", **fields})


LOG = [
    json.dumps({"t": 0.0, "event": "start", "source": "vendor_dll_simulated"}),
    command(1.0, op="move_by", dx=5, id=1),
    json.dumps({"t": 1.1, "event": "teach", "source": "vendor_dll_simulated"}),      # the server logs its own steps between the two
    json.dumps({"t": 3.4, "event": "move_done", "source": "vendor_dll_simulated"}),
    reply(3.5, id=1, ok=True, code=0, xyz=[1, 2, 3, 4, 5], timing_s={"read": 0.1, "move": 2.4}),
    command(4.0, op="move_by", dx=40, id=2),
    reply(4.0, id=2, ok=False, error="outside the box"),
    command(5.0, op="where", id=3),
    reply(5.0, id=3, ok=True, xyz=[1, 2, 3, 4, 5]),
    command(6.0, op="move_by", dx=1, id=4),
    reply(6.5, id=4, ok=False, code=911, error="move failed"),
]


def run(lines, ending="\n"):
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "server.jsonl"
        path.write_text("\n".join(lines) + ending, encoding="utf-8")
        said = []
        code = summarize_log.main([str(path)], said.append)
    return code, "\n".join(said)


class SummaryTests(unittest.TestCase):
    def test_table_and_totals(self):
        code, text = run(LOG)
        self.assertEqual(code, 0)
        rows = [line for line in text.splitlines() if "move_by" in line or "where" in line]
        self.assertEqual(len(rows), 4)
        self.assertRegex(rows[0], r"1\.0.*move_by.*ok.*2\.5")           # timing_s summed
        self.assertRegex(rows[1], r"4\.0.*move_by.*refused: outside the box")
        self.assertRegex(rows[2], r"5\.0.*where.*ok")
        self.assertRegex(rows[3], r"6\.0.*move_by.*dll code 911")
        self.assertIn("4 commands: 2 ok, 1 refused, 1 failed", text)
        self.assertNotIn("ended early", text)

    def test_a_failed_call_with_no_reason_is_failed_not_refused(self):
        _, text = run([command(1.0, op="speed", percent=20, id=1), reply(1.0, id=1, ok=False)])
        self.assertRegex(text, r"speed.*dll call failed")
        self.assertIn("1 commands: 0 ok, 0 refused, 1 failed", text)

    def test_a_damaged_line_stops_pairing(self):
        lines = [command(1.0, op="move_by", dx=1, id=1), "{damaged", "{damaged", reply(2.0, id=2, ok=True, xyz=[1, 2, 3, 4, 5])]
        _, text = run(lines)
        self.assertRegex(text, r"1\.0.*move_by.*no reply")       # reply 2 must not answer command 1
        self.assertIn("1 commands: 0 ok, 0 refused, 1 failed", text)
        self.assertIn("2 damaged lines", text)

    def test_a_reply_with_another_id_is_not_the_answer(self):
        _, text = run([command(1.0, op="where", id=1), reply(1.5, id=2, ok=True)])
        self.assertRegex(text, r"where.*no reply")

    def test_a_long_reason_is_shortened(self):
        long = "x" * 200
        _, text = run([command(1.0, op="move_by", dx=1, id=1), reply(1.0, id=1, ok=False, error=long)])
        self.assertIn("refused: " + "x" * 57 + "...", text)
        self.assertNotIn("x" * 100, text)

    def test_truncated_last_line(self):
        lines = [*LOG[:5], command(9.0, op="move_by", dx=2, id=9), '{"t": 9.5, "event": "rep']
        code, text = run(lines, ending="")
        self.assertEqual(code, 0)
        self.assertRegex(text, r"9\.0.*move_by.*no reply")
        self.assertIn("2 commands: 1 ok, 0 refused, 1 failed", text)
        self.assertIn("log ended early", text)

    def test_log_is_untouched_and_no_dll(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "server.jsonl"
            path.write_text("\n".join(LOG) + "\n", encoding="utf-8")
            before = (path.read_bytes(), path.stat().st_mtime_ns)
            summarize_log.main([str(path)], lambda _: None)
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
        imported = set()
        for node in ast.walk(ast.parse(SCRIPT.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module.split(".")[0])
        self.assertLessEqual(imported, {"__future__", "argparse", "json", "pathlib", "sys"})


if __name__ == "__main__":
    unittest.main()
