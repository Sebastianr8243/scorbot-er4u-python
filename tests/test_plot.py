"""Plots (optional matplotlib extra) and the shipped Foxglove/Lichtblick layout."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import matplotlib  # noqa: F401
    HAVE_MATPLOTLIB = True
except ImportError:
    HAVE_MATPLOTLIB = False


def png(path):
    return Path(path).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


@unittest.skipUnless(HAVE_MATPLOTLIB, "install the plot extra: pip install -e .[plot]")
class PlotTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_counts_plot_has_one_panel_per_motor_that_moved_and_names_the_source(self):
        from test_analysis import record_sim_session
        from scorbot.session import load_session
        from scorbot.session.plot import plot_counts
        session = load_session(record_sim_session(self.root / "s"))
        info = plot_counts(session, self.root / "counts.png")
        self.assertTrue(png(info["path"]))
        self.assertEqual(info["panels"], ["base"])  # only the base motor changed
        self.assertTrue(info["title"].startswith("SIMULATED"))
        self.assertEqual(info["command_spans"], 3)  # home + two jogs

    def test_cross_run_plots_pool_one_source(self):
        from test_analysis import record_sim_session
        from scorbot.session import load_session
        from scorbot.session.analysis import command_records
        from scorbot.session.plot import plot_count_error, plot_durations
        runs = []
        for name in ("a", "b"):
            session = load_session(record_sim_session(self.root / name))
            runs.append((session.metadata["session_id"], command_records(session)))
        error = plot_count_error(runs, "simulated", self.root / "err.png")
        self.assertTrue(png(error["path"]))
        self.assertEqual(error["points"], 4 * 6)  # 4 jogs x 6 motors read
        self.assertEqual(error["max_abs"], 0)
        self.assertTrue(error["title"].startswith("SIMULATED"))
        durations = plot_durations(runs, "simulated", self.root / "dur.png")
        self.assertEqual(durations["kinds"], ["home", "jog_joint"])
        self.assertIn("script overhead", durations["note"])

    def test_dark_theme_renders(self):
        from test_analysis import record_sim_session
        from scorbot.session import load_session
        from scorbot.session.plot import plot_counts
        session = load_session(record_sim_session(self.root / "s"))
        self.assertTrue(png(plot_counts(session, self.root / "d.png", theme="dark")["path"]))

    def test_cli_writes_per_run_and_cross_run_plots(self):
        from test_analysis import cli, record_sim_session
        a = record_sim_session(self.root / "runs")
        b = record_sim_session(self.root / "runs")
        out = self.root / "plots"
        result = cli("plot", self.root / "runs", "--out", out)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        names = sorted(p.name for p in out.glob("*.png"))
        self.assertIn(f"counts_{a.name}.png", names)
        self.assertIn(f"counts_{b.name}.png", names)
        self.assertIn("count_error.png", names)
        self.assertIn("durations.png", names)

    def test_cli_refuses_to_pool_mixed_sources_but_still_plots_each_run(self):
        from test_analysis import cli, record_sim_session, state, writer
        sim = record_sim_session(self.root / "runs")
        with writer(self.root / "runs", data_source="real") as rec:
            rec.log_state(state(0))
            cid = rec.log_command("jog_joint", {"motor_count_deltas": {"base": 1}})
            rec.log_command_result(cid, "completed")
            rec.log_state(state(1))
        out = self.root / "plots"
        result = cli("plot", self.root / "runs", "--out", out)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Mixed data sources", result.stdout)
        self.assertFalse((out / "count_error.png").exists())
        self.assertTrue((out / f"counts_{sim.name}.png").exists())
        self.assertTrue((out / f"counts_{rec.path.name}.png").exists())


class MissingMatplotlibTests(unittest.TestCase):
    def test_a_clear_install_hint_instead_of_a_traceback(self):
        from unittest.mock import patch
        import builtins
        from scorbot.session.plot import PlotUnavailable, require_matplotlib
        real_import = builtins.__import__

        def no_matplotlib(name, *args, **kwargs):
            if name == "matplotlib" or name.startswith("matplotlib."):
                raise ImportError("No module named 'matplotlib'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", no_matplotlib):
            with self.assertRaises(PlotUnavailable) as caught:
                require_matplotlib()
        self.assertIn('pip install -e ".[plot]"', str(caught.exception))


class LayoutTests(unittest.TestCase):
    def test_shipped_viewer_layout_is_valid_and_uses_real_topics(self):
        from scorbot.session.schemas import TOPICS
        layout = json.loads((REPO_ROOT / "layouts" / "session_review.json").read_text())
        panels = layout["configById"]
        self.assertTrue(any(key.startswith("Plot!") for key in panels))
        self.assertTrue(any(key.startswith("Image!") for key in panels))
        known = set(TOPICS) | {"/camera/cam0/image", "/camera/cam0/detections"}
        text = json.dumps(panels)
        import re
        for topic in set(re.findall(r'"(/[a-z_]+/[a-z_0-9/]+?)(?:\.[a-z_.]+)?"', text)):
            self.assertIn(topic, known, topic)
        referenced = set(re.findall(r'"([A-Za-z]+![a-z0-9]+)"', json.dumps(layout["layout"])))
        self.assertEqual(referenced, set(panels))


if __name__ == "__main__":
    unittest.main()
