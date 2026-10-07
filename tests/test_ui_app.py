"""The browser page's wiring (``scorbot.ui.app``) and command line. No USB, no browser.

Design: docs/specs/2026-10-05-three-front-doors-design.md.
"""

import contextlib
from importlib.util import find_spec
import io
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from scorbot.mover import Mover
from scorbot.robot import ScorbotError
from scorbot.ui import __main__ as ui_main
from scorbot.ui.panel import STOP_LABEL, Panel

try:
    from tests.sim_support import SimulatedRobotCase
except ImportError:      # discovered with -s tests: the folder itself is on sys.path
    from sim_support import SimulatedRobotCase

HAS_BOTH = find_spec("viser") is not None and find_spec("ruckig") is not None


def run_main(*args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = ui_main.main(list(args))
    return code, out.getvalue()


class CommandTests(unittest.TestCase):
    def test_without_simulate_it_refuses_and_says_why(self):
        code, text = run_main()
        self.assertEqual(code, ui_main.EXIT_REFUSED)
        self.assertIn("--simulate", text)

    def test_a_missing_extra_is_named(self):
        with mock.patch.object(ui_main, "find_spec", return_value=None):
            code, text = run_main("--simulate")
        self.assertEqual(code, ui_main.EXIT_REFUSED)
        self.assertIn("ui extra", text)


@unittest.skipUnless(HAS_BOTH, "viser or ruckig not installed (pip install .[ui,planning])")
class AppTests(SimulatedRobotCase):
    CONTROLLER_DEFAULTS = {"step_delay_s": 0.001}

    def app(self):
        from scorbot.ui.app import App
        robot = self.robot()
        panel = Panel(Mover(robot), robot)
        self.addCleanup(panel.close)
        app = App(panel, port=0, mesh_dir=self.id())       # no such folder: drawn as lines
        self.addCleanup(app.close)
        return app, panel, robot

    @staticmethod
    def from_browser(target):
        return SimpleNamespace(client=object(), target=target)

    def test_the_page_is_local_only_and_has_the_controls(self):
        from scorbot.ui import app as app_module
        app, _panel, _robot = self.app()
        self.assertEqual(app_module.HOST, "127.0.0.1")
        self.assertEqual(app.server.get_host(), "127.0.0.1")
        self.assertEqual(set(app.sliders), {"base", "shoulder", "elbow"})
        self.assertEqual(set(app.buttons), {"home", "open", "close", "stop"})
        self.assertEqual(app.buttons["stop"].label, STOP_LABEL)
        self.assertIn("deg", app.readout.content)

    def test_a_slider_from_the_browser_moves_the_arm_and_the_page_follows(self):
        app, panel, robot = self.app()
        panel.clients(1)
        slider = app.sliders["base"]
        slider.value = 6.0                                   # server-side set: must not move
        self.assertIsNone(panel.mover.target_angles())
        app._slid("base", self.from_browser(slider))
        self.assertAlmostEqual(panel.mover.target_angles()["base"], 6.0)
        deadline = time.monotonic() + 10
        while not panel.mover.arrived() and time.monotonic() < deadline:
            app.refresh()
            time.sleep(0.005)
        self.assertAlmostEqual(panel.view().angles["base"], 6.0, delta=0.1)
        panel.mover.close()                                  # settled
        app.refresh()
        self.assertIn("base 6.0", app.readout.content)
        self.assertIsNone(robot._fault)

    def test_a_refused_drag_shows_the_reason_and_the_handle_goes_back(self):
        from scorbot.ui import app as app_module
        app, panel, _robot = self.app()
        panel.clients(1)
        home = tuple(app.handle.position)
        app.handle.position = (0.9, 0.0, 0.349)
        app._dragged(self.from_browser(app.handle))
        self.assertIn("reach", panel.message)
        with mock.patch.object(app_module, "HANDS_OFF_S", 0.0):
            app.refresh()
        self.assertIn("reach", app.readout.content)
        for got, was in zip(app.handle.position, home):
            self.assertAlmostEqual(got, was, places=3)

    def test_a_handle_that_is_held_is_not_moved_until_it_is_released(self):
        from scorbot.ui import app as app_module
        app, panel, _robot = self.app()
        panel.clients(1)
        held = tuple(app.handle.position)
        # Three metres sideways: out of reach, so releasing the handle is refused
        # and it goes back to the arm (a small nudge is a legal move since the
        # travel cap was lifted on 2026-10-06, and the arm would follow it).
        app.handle.position = (held[0], held[1] + 3.0, held[2])
        moved = tuple(app.handle.position)
        app._dragged(SimpleNamespace(client=object(), phase="start", target=app.handle))
        with mock.patch.object(app_module, "HANDS_OFF_S", 0.0):
            app.refresh()
            app._count_clients()                           # a second tab opening clears the timers
            app.refresh()
            self.assertEqual(tuple(app.handle.position), moved, "held: the page leaves it alone")
            app._dragged(SimpleNamespace(client=object(), phase="end", target=app.handle))
            panel.drag_tool(900.0, 0.0, 349.0)             # refused (out of reach): back to the arm
            app._touched.clear()
            app.refresh()
        for got, was in zip(app.handle.position, held):
            self.assertAlmostEqual(got, was, places=2)

    def test_a_failed_read_of_the_arm_is_shown_and_does_not_end_the_page(self):
        app, panel, robot = self.app()
        panel.clients(1)
        with mock.patch.object(panel, "view", side_effect=ScorbotError("No fresh reading")):
            app.refresh()                                  # must not raise
        self.assertIn("Cannot read the arm: No fresh reading", app.readout.content)
        self.assertTrue(app.buttons["home"].disabled)
        self.assertFalse(app.buttons["stop"].disabled)
        app.refresh()                                      # the next good reading recovers
        self.assertNotIn("Cannot read", app.readout.content)
        self.assertFalse(app.buttons["home"].disabled)
        self.assertIsNone(robot._fault)

    def test_a_fault_is_shown_and_the_move_buttons_are_disabled(self):
        app, panel, robot = self.app()
        panel.clients(1)
        robot._latch_fault("made up for the test")
        app.refresh()
        self.assertIn("FAULT: made up for the test", app.readout.content)
        self.assertTrue(app.buttons["home"].disabled)
        self.assertFalse(app.buttons["stop"].disabled)


if __name__ == "__main__":
    unittest.main()
