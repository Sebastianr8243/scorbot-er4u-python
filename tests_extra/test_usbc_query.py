"""tools/usbc_analysis/query.py against a made-up dump. No vendor code involved."""

import contextlib
import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path

QUERY = Path(__file__).resolve().parent.parent / "tools" / "usbc_analysis" / "query.py"
spec = importlib.util.spec_from_file_location("usbc_query", QUERY)
query = importlib.util.module_from_spec(spec)
spec.loader.exec_module(query)

DUMP = """// ===== 10001000 first

int first(void)
{
  return second(1);
}

// ===== 10002000 FUN_10002000

int second(int x)
{
  return x - 0x7fffff;
}
"""


class QueryTest(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".c")
        os.close(handle)
        self.addCleanup(os.remove, self.path)
        Path(self.path).write_text(DUMP, encoding="utf-8")
        self.functions = query.load(self.path)

    def run_main(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = query.main(["query.py", self.path, *args])
        return code, out.getvalue()

    def test_load_keeps_the_first_function(self):
        self.assertEqual(list(self.functions), ["first", "FUN_10002000"])
        self.assertEqual(self.functions["first"][0], "10001000")

    def test_show_by_name_and_by_address(self):
        _, by_name = self.run_main("show", "first")
        self.assertIn("return second(1);", by_name)
        _, by_address = self.run_main("show", "FUN_10002000")
        self.assertIn("0x7fffff", by_address)

    def test_callers_lists_users_but_not_the_function_itself(self):
        _, out = self.run_main("callers", "second")
        self.assertEqual(out.split(), ["10001000", "first"])

    def test_grep_reports_the_matching_function(self):
        _, out = self.run_main("grep", "0x7fffff;")
        self.assertTrue(out.startswith("10002000 FUN_10002000 |"))

    def test_bad_usage_returns_2(self):
        code, out = self.run_main("nope", "x")
        self.assertEqual(code, 2)
        self.assertIn("show NAME_OR_ADDRESS", out)


if __name__ == "__main__":
    unittest.main()
