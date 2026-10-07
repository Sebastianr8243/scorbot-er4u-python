"""The browser page's logic (``scorbot.ui.panel``) with no browser. No USB.

Design: docs/specs/2026-10-05-three-front-doors-design.md.
"""

from importlib.util import find_spec
import time
import unittest

from scorbot import source_model
from scorbot.mover import Mover
from scorbot.ui import panel as panel_module
from scorbot.ui.panel import Panel

try:
    from tests.sim_support import SimulatedRobotCase
    from tests.test_mover import Clock
except ImportError:      # discovered with -s tests: the folder itself is on sys.path
    from sim_support import SimulatedRobotCase
    from test_mover import Clock

HOME = source_model.HOME_ANGLES


@unittest.skipUnless(find_spec("ruckig"), "ruckig not installed (pip install .[planning])")
class PanelTests(SimulatedRobotCase):
    CONTROLLER_DEFAULTS = {"step_delay_s": 0.001}

    def panel(self, **controller_kwargs):
        robot = self.robot(**controller_kwargs)
        self.clock = Clock()
        panel = Panel(Mover(robot, now=self.clock), robot, now=self.clock)
        panel.clients(1)
        self.addCleanup(panel.close)
        return panel, robot

    def run_until(self, panel, done, seconds=8.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            panel.tick()
            if done():
                return True
            time.sleep(0.005)
        return False

    def test_the_view_at_home_shows_the_home_pose_and_no_ghost(self):
        panel, _robot = self.panel()
        view = panel.view()
        self.assertAlmostEqual(view.angles["shoulder"], HOME["shoulder"], delta=0.01)
        self.assertAlmostEqual(view.xyz[0], 169.3, delta=0.5)
        self.assertIsNone(view.ghost)
        self.assertIsNone(view.busy)
        self.assertIsNone(view.fault)
        self.assertIn("SIMULATED", view.status)
        self.assertIn("not measured", view.status)
        self.assertIn("forearm_link", view.actual.poses)

    def test_a_slider_moves_the_arm_and_the_ghost_leads_it(self):
        panel, robot = self.panel(step_delay_s=0.004)
        self.assertIsNone(panel.set_joint("base", 8.0))
        view = panel.view()
        self.assertIsNotNone(view.ghost)
        self.assertNotEqual(view.ghost.skeleton[-1], view.actual.skeleton[-1])
        self.assertTrue(self.run_until(panel, panel.mover.arrived))
        self.assertAlmostEqual(panel.view().angles["base"], 8.0, delta=0.1)
        self.assertIsNone(panel.view().ghost, "no ghost once the arm is there")
        self.assertIsNone(robot._fault)

    def test_slider_limits_are_the_joint_limits_in_degrees(self):
        # Each joint alone from home, by the source model's whole-pose limits.
        limits = Panel.slider_limits()
        self.assertEqual(set(limits), {"base", "shoulder", "elbow"})
        self.assertAlmostEqual(limits["base"][0], -132.0, delta=0.05)
        self.assertAlmostEqual(limits["base"][1], 174.0, delta=0.05)
        self.assertAlmostEqual(limits["shoulder"][1] - HOME["shoulder"], 3.72, delta=0.05)
        # Down, the elbow's -5.16 limit stops the shoulder 89.9 degrees from home.
        self.assertAlmostEqual(HOME["shoulder"] - limits["shoulder"][0], 89.9, delta=0.1)
        # Up (counts negative) the wrist pitch stops the elbow after 20.8 degrees,
        # down it is the elbow's own -140.8 limit, 45.8 degrees from home.
        self.assertAlmostEqual(limits["elbow"][1] - HOME["elbow"], 20.8, delta=0.1)
        self.assertAlmostEqual(HOME["elbow"] - limits["elbow"][0], 45.8, delta=0.1)
        for joint, (low, high) in limits.items():
            self.assertLess(low, HOME[joint])
            self.assertGreater(high, HOME[joint])

    def test_a_refused_slider_or_drag_says_why_and_changes_nothing(self):
        panel, robot = self.panel()
        reason = panel.set_joint("base", 175.0)           # the base ends at 174 degrees
        self.assertIn("travel cap", reason)
        self.assertEqual(panel.message, reason)
        self.assertIn("reach", panel.drag_tool(900.0, 0.0, 349.0))
        self.assertIsNone(panel.mover.target_angles())
        self.assertFalse(panel.mover.moving())
        self.assertIsNone(robot._fault)
        with self.assertRaises(ValueError):
            panel.set_joint("pitch", 1.0)

    def test_dragging_the_tool_moves_it_in_millimetres(self):
        panel, _robot = self.panel()
        x, y, z = panel.view().xyz[:3]
        self.assertIsNone(panel.drag_tool(x + 15.0, y, z - 10.0))
        self.assertTrue(self.run_until(panel, panel.mover.arrived))
        here = panel.view().xyz
        self.assertAlmostEqual(here[0], x + 15.0, delta=1.0)
        self.assertAlmostEqual(here[2], z - 10.0, delta=1.0)

    def test_the_home_button_brings_the_arm_back(self):
        panel, _robot = self.panel()
        panel.set_joint("base", 5.0)
        self.assertTrue(self.run_until(panel, panel.mover.arrived))
        self.assertIsNone(panel.press("home"))
        self.assertTrue(self.run_until(panel, panel.mover.arrived))
        self.assertAlmostEqual(panel.view().angles["base"], 0.0, delta=0.1)

    def test_the_gripper_runs_off_the_pages_thread_and_a_second_press_is_busy(self):
        panel, robot = self.panel(step_delay_s=0.004)
        started = time.monotonic()
        self.assertIsNone(panel.press("close"))
        self.assertLess(time.monotonic() - started, 0.5, "the press returns at once")
        self.assertEqual(panel.view().busy, "gripper")
        self.assertIn("Busy", panel.press("open"))
        self.assertIn("Busy", panel.set_joint("base", 2.0))
        panel.tick()                                         # must not block or raise
        self.assertTrue(self.run_until(panel, lambda: panel.view().busy is None, seconds=30))
        self.assertIn("gripper_complete", self.events())
        self.assertIsNone(panel.set_joint("base", 2.0))
        self.assertIsNone(robot._fault)

    def test_stop_during_a_gripper_move_ends_it_without_waiting(self):
        panel, robot = self.panel(step_delay_s=0.01)
        panel.press("close")
        time.sleep(0.2)
        started = time.monotonic()
        panel.press("stop")
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertTrue(self.run_until(panel, lambda: panel.view().busy is None, seconds=30))
        self.assertIn("gripper_stopped", self.events())
        self.assertIsNone(robot._fault)

    def test_stop_ends_a_move_and_it_does_not_start_again(self):
        panel, robot = self.panel(step_delay_s=0.004)
        panel.set_joint("base", 9.0)
        time.sleep(0.15)
        self.assertIsNone(panel.press("stop"))
        self.assertEqual(panel.message, "Stopped")
        for _ in range(5):
            panel.tick()
        self.assertFalse(panel.mover.moving())
        self.assertLess(panel.view().angles["base"], 8.5)
        self.assertIsNone(robot._fault)

    def test_slider_and_handle_events_already_in_flight_cannot_restart_the_arm_after_stop(self):
        panel, robot = self.panel(step_delay_s=0.004)
        panel.set_joint("base", 9.0)
        time.sleep(0.1)
        panel.press("stop")
        self.assertTrue(panel.set_joint("base", 9.0))          # a late slider event: refused
        self.assertTrue(panel.drag_tool(170.0, 10.0, 500.0))   # a late handle event: refused
        self.assertEqual(panel.message, "Stopped")
        self.assertFalse(panel.mover.moving())
        self.clock.added += panel_module.STOP_HOLD_S + 0.1
        self.assertIsNone(panel.set_joint("base", 2.0))        # a fresh gesture works
        self.assertTrue(panel.mover.moving())
        self.assertIsNone(robot._fault)

    def test_late_home_and_gripper_button_events_cannot_restart_after_stop(self):
        panel, _robot = self.panel()
        panel.press("stop")
        self.assertEqual(panel.press("home"), "Stopped")
        self.assertEqual(panel.press("close"), "Stopped")
        self.assertFalse(panel.mover.moving())
        self.assertIsNone(panel.view().busy)

    def test_closing_the_last_tab_during_a_gripper_move_stops_it(self):
        panel, robot = self.panel(step_delay_s=0.01)
        panel.press("close")
        time.sleep(0.2)
        panel.clients(0)
        panel.tick()
        panel.tick()                                           # asked once, not every tick
        self.assertTrue(self.run_until(panel, lambda: panel.view().busy is None, seconds=30))
        self.assertIn("gripper_stopped", self.events())
        self.assertIsNone(robot._fault)

    def test_when_the_browser_goes_the_move_is_given_up_and_coming_back_shows_the_arm(self):
        panel, robot = self.panel(step_delay_s=0.004)
        panel.set_joint("base", 9.0)
        panel.tick()
        panel.clients(0)
        panel.tick()
        self.assertTrue(panel.mover.moving(), "a moment's grace")
        self.clock.added += Mover.IDLE_CLOSE_S + 0.1
        panel.tick()
        self.assertFalse(panel.mover.moving())
        self.assertIn("stream_complete", self.events())
        panel.clients(1)
        for _ in range(5):
            panel.tick()
        self.assertFalse(panel.mover.moving(), "reconnecting does not resume the old move")
        view = panel.view()
        self.assertLess(view.angles["base"], 8.5)
        self.assertIsNone(view.ghost)
        self.assertIsNone(robot._fault)

    def test_a_fault_shows_on_the_page_and_controls_say_so(self):
        panel, robot = self.panel()
        robot.sim.stream_stuck = True
        panel.set_joint("base", 9.0)
        self.assertTrue(self.run_until(panel, lambda: robot._fault is not None))
        panel.tick()
        self.assertIn("not following", panel.view().fault)
        self.assertIn("fault", panel.set_joint("base", 1.0).lower())

    def test_the_stop_is_never_called_an_emergency_stop(self):
        self.assertNotIn("emergency", panel_module.STOP_LABEL.lower())
        self.assertIn("not an emergency stop", panel_module.STOP_NOTE)
        self.assertIn("physical stop", panel_module.STOP_NOTE)

    def test_the_panel_does_not_import_viser(self):
        # In a fresh process: another test in this one may already have imported it.
        import subprocess
        import sys
        code = "import sys, scorbot.ui.panel; sys.exit('viser' in sys.modules)"
        self.assertEqual(subprocess.run([sys.executable, "-c", code]).returncode, 0)


if __name__ == "__main__":
    unittest.main()
