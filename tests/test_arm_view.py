"""scorbot.arm_view: the 3D picture of the arm. No USB, no meshes and no Rerun
needed: a fake recording stands in for the viewer."""

import contextlib
from importlib.util import find_spec
import io
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np

from scorbot import arm_chain, arm_view
from scorbot.arm_view import LINK_MESHES, chain_angles_from_counts, find_meshes, visual_offset

MOTORS = ("base", "shoulder", "elbow", "wrist_motor_1", "wrist_motor_2")
HOME = dict.fromkeys(MOTORS, 0)


class MeshPlacementTests(unittest.TestCase):
    def test_every_offset_is_a_rigid_transform(self):
        for link in LINK_MESHES:
            rotation = visual_offset(link)[:3, :3]
            np.testing.assert_allclose(rotation @ rotation.T, np.eye(3), atol=1e-12,
                                       err_msg=link)
            self.assertAlmostEqual(float(np.linalg.det(rotation)), 1.0, places=12)

    def test_the_arm_meshes_long_axis_ends_up_along_the_link(self):
        # The upper arm and forearm files run along their own -y; the link runs along +x.
        for link in ("upper_arm_link", "forearm_link"):
            np.testing.assert_allclose(visual_offset(link)[:3, :3] @ (0, -1, 0), (1, 0, 0),
                                       atol=1e-9)
        # The wrist and gripper files run along +z, fingers at the -z end... which is +x here.
        for link in ("wrist_link", "flange_link"):
            np.testing.assert_allclose(visual_offset(link)[:3, :3] @ (0, 0, -1), (1, 0, 0),
                                       atol=1e-6)

    def test_there_is_a_mesh_for_every_link_and_only_for_links(self):
        self.assertEqual(tuple(LINK_MESHES), arm_chain.LINKS)


class FindMeshesTests(unittest.TestCase):
    def folder(self, *names):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for name in names:
            (Path(directory.name) / name).write_bytes(b"solid")
        return Path(directory.name)

    def test_files_are_matched_whatever_their_case(self):
        folder = self.folder("BASE_LINK.stl", "elbow_Link.STL", "notes.txt")
        self.assertEqual(sorted(find_meshes(folder)), ["base_link", "upper_arm_link"])

    def test_a_missing_folder_means_no_meshes_not_an_error(self):
        self.assertEqual(find_meshes(self.folder() / "absent"), {})

    def test_the_environment_variable_names_the_folder(self):
        folder = self.folder("roll_Link.STL")
        with mock.patch.dict(os.environ, {arm_view.MESH_DIR_ENV: str(folder)}):
            self.assertEqual(list(find_meshes()), ["wrist_link"])

    def test_the_repository_never_ships_the_meshes(self):
        # Their licence is unclear. Only the README in that folder is tracked.
        root = Path(arm_view.__file__).resolve().parents[1]
        tracked = subprocess.run(["git", "ls-files", "models/er4u_meshes"], cwd=root,
                                 capture_output=True, text=True)
        if tracked.returncode != 0:
            self.skipTest("not a git checkout")
        others = [name for name in tracked.stdout.splitlines()
                  if name != "models/er4u_meshes/README.md"]
        self.assertEqual(others, [])


class CountsToAnglesTests(unittest.TestCase):
    def test_zero_counts_is_the_home_pose_the_usna_toolbox_publishes(self):
        # kutzer/ScorBotToolbox ScorGoHome.m: XYZPRhome = [169.300, 0.000, 504.328,
        # -1.10912, 0]. Counts -> vendor formula -> this chain with the vendor's
        # dimensions gives the same point. Evidence about the vendor's model only.
        q = chain_angles_from_counts(HOME)
        tool = arm_chain.link_poses(q, arm_chain.VENDOR_INI)["tool"][:3, 3]
        np.testing.assert_allclose(tool, (169.300, 0.000, 504.328), atol=0.3)
        self.assertAlmostEqual(math.radians(q[1] + q[2] + q[3]), -1.10912, places=4)
        self.assertEqual((round(q[0], 6), round(q[4], 6)), (0.0, 0.0))

    def test_the_base_motor_moves_only_the_base_angle(self):
        home, turned = chain_angles_from_counts(HOME), chain_angles_from_counts(
            {**HOME, "base": 1419})
        self.assertNotAlmostEqual(turned[0], home[0])
        np.testing.assert_allclose(turned[1:], home[1:], atol=1e-9)
        self.assertAlmostEqual(abs(turned[0]), 10.0, places=1)

    def test_the_shoulder_motor_alone_leaves_the_forearm_at_the_same_angle_to_the_floor(self):
        # The vendor formula's own property (vendor_model.py); the picture must show it.
        home, raised = chain_angles_from_counts(HOME), chain_angles_from_counts(
            {**HOME, "shoulder": -1135})
        self.assertAlmostEqual(abs(raised[1] - home[1]), 10.0, places=1)
        self.assertAlmostEqual(raised[1] + raised[2], home[1] + home[2], places=6)

    def test_extra_names_such_as_the_gripper_are_ignored(self):
        self.assertEqual(chain_angles_from_counts({**HOME, "gripper": 55}),
                         chain_angles_from_counts(HOME))


class SceneTests(unittest.TestCase):
    def test_the_scene_is_the_chain_in_metres(self):
        q = (20, 40, -30, 10, 5)
        drawn = arm_view.scene(q)
        chain = arm_chain.link_poses(q)
        for name in chain:
            np.testing.assert_allclose(drawn.poses[name][:3, 3] * 1000, chain[name][:3, 3])
            np.testing.assert_allclose(drawn.poses[name][:3, :3], chain[name][:3, :3])
        np.testing.assert_allclose(np.array(drawn.skeleton) * 1000, arm_chain.skeleton(q))


class _Recording:
    def __init__(self):
        self.logged, self.times, self.flushed = [], [], 0

    def log(self, path, value, static=False):
        self.logged.append((path, value, static))

    def set_time(self, name, **kwargs):
        self.times.append((name, kwargs))

    def flush(self):
        self.flushed += 1

    def paths(self, kind=None):
        return [path for path, value, _ in self.logged if kind is None or value[0] == kind]


def _fake_rerun():
    """Just enough of the Rerun API: each archetype becomes a tagged tuple."""
    def tagged(kind):
        return lambda *args, **kwargs: (kind, args, kwargs)
    return SimpleNamespace(
        Transform3D=tagged("transform"), Asset3D=tagged("asset"),
        LineStrips3D=tagged("lines"), Scalars=tagged("scalar"),
        TextDocument=tagged("text"), ViewCoordinates=SimpleNamespace(RIGHT_HAND_Z_UP="z-up"),
        MediaType=SimpleNamespace(MARKDOWN="md", STL="stl"))


class ArmViewTests(unittest.TestCase):
    def view(self, *names, **kwargs):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for name in names:
            (Path(directory.name) / name).write_bytes(b"solid " + name.encode())
        self.recording = _Recording()
        return arm_view.ArmView(mesh_dir=directory.name, recording=self.recording,
                                rerun=_fake_rerun(), **kwargs)

    def test_without_meshes_the_arm_is_still_drawn_as_a_line_and_the_notice_says_so(self):
        view = self.view()
        view.show((0, 0, 0, 0, 0))
        self.assertEqual(self.recording.paths("asset"), [])
        self.assertEqual(self.recording.paths("lines"), ["skeleton"])
        [notice] = [value for path, value, _ in self.recording.logged if path == "notice"]
        self.assertIn("UNVALIDATED", notice[1][0])
        self.assertIn("SIMULATED", notice[1][0])
        self.assertIn("drawn as a line", notice[1][0])

    def test_meshes_are_sent_once_and_only_their_links_move_after_that(self):
        view = self.view("base_Link.STL", "elbow_Link.STL")
        assets = [(path, static) for path, value, static in self.recording.logged
                  if value[0] == "asset"]
        self.assertEqual(assets, [("arm/base_link/mesh", True), ("arm/upper_arm_link/mesh", True)])
        before = len(self.recording.logged)
        view.show((0, 10, 0, 0, 0), time_s=1.5)
        view.show((0, 20, 0, 0, 0), time_s=1.6)
        later = self.recording.logged[before:]
        self.assertFalse([path for path, value, _ in later if value[0] == "asset"])
        moved = [path for path, value, static in later if value[0] == "transform"]
        self.assertEqual(moved, [f"arm/{link}" for link in arm_chain.LINKS] * 2)
        self.assertEqual(self.recording.times, [("time", {"duration": 1.5}),
                                                ("time", {"duration": 1.6})])

    def test_each_link_is_placed_where_the_chain_says(self):
        view = self.view()
        q = (30, 45, -20, 10, 0)
        view.show(q)
        placed = {path: value[2] for path, value, _ in self.recording.logged
                  if value[0] == "transform"}
        expected = arm_view.scene(q).poses
        for link in arm_chain.LINKS:
            np.testing.assert_allclose(placed[f"arm/{link}"]["translation"],
                                       expected[link][:3, 3])
            np.testing.assert_allclose(placed[f"arm/{link}"]["mat3x3"], expected[link][:3, :3])

    def test_a_target_is_drawn_as_its_own_line(self):
        view = self.view()
        view.show((0, 0, 0, 0, 0), target_q=(10, 0, 0, 0, 0))
        self.assertEqual(self.recording.paths("lines"), ["skeleton", "target"])
        target = next(value for path, value, _ in self.recording.logged if path == "target")
        np.testing.assert_allclose(target[1][0][0], arm_view.scene((10, 0, 0, 0, 0)).skeleton)

    def test_a_real_arm_view_is_not_headed_simulated_but_stays_unvalidated(self):
        self.view(simulated=False)
        [notice] = [value for path, value, _ in self.recording.logged if path == "notice"]
        self.assertNotIn("SIMULATED", notice[1][0])
        self.assertIn("UNVALIDATED", notice[1][0])


class CommandTests(unittest.TestCase):
    def run_main(self, *argv, view=None):
        out = io.StringIO()
        made = view or mock.MagicMock(meshes={})
        with mock.patch.object(arm_view, "ArmView", return_value=made) as factory, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                code = arm_view.main(list(argv))
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), factory, made

    def test_one_pose_is_drawn_and_the_notice_is_printed(self):
        code, out, factory, view = self.run_main("--pose", "0", "120", "-95", "-90", "0")
        self.assertEqual(code, 0)
        view.show.assert_called_once_with([0.0, 120.0, -95.0, -90.0, 0.0])
        view.close.assert_called_once()
        self.assertIn("UNVALIDATED", out)
        self.assertNotIn("SIMULATED", out)
        self.assertEqual(factory.call_args.kwargs["simulated"], False)

    def test_it_needs_to_be_told_what_to_draw(self):
        code, out, factory, _ = self.run_main()
        self.assertEqual(code, 2)
        factory.assert_not_called()

    def test_an_existing_recording_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            existing = Path(directory) / "arm.rrd"
            existing.write_bytes(b"keep")
            code, out, factory, _ = self.run_main("--pose", "0", "0", "0", "0", "0",
                                                  "--save", str(existing))
            self.assertEqual(code, 2)
            self.assertEqual(existing.read_bytes(), b"keep")
        factory.assert_not_called()

    def test_a_missing_viewer_gives_an_install_hint_not_a_traceback(self):
        out = io.StringIO()
        with mock.patch.object(arm_view, "require_rerun",
                               side_effect=arm_view.RerunUnavailable("needs the viz extra")), \
                contextlib.redirect_stderr(out), contextlib.redirect_stdout(out):
            code = arm_view.main(["--pose", "0", "0", "0", "0", "0"])
        self.assertEqual(code, 2)
        self.assertIn("viz extra", out.getvalue())

    @unittest.skipUnless(find_spec("ruckig"), "ruckig not installed (pip install .[planning])")
    def test_the_simulated_demo_draws_the_arm_following_its_targets(self):
        shown = []
        view = mock.MagicMock(meshes={})
        view.show.side_effect = lambda q, **kwargs: shown.append((q, kwargs))
        code, out, factory, _ = self.run_main("--simulate", "--seconds", "1.5", view=view)
        self.assertEqual(code, 0)
        self.assertIn("SIMULATED", out)
        self.assertEqual(factory.call_args.kwargs["simulated"], True)
        self.assertGreater(len(shown), 5)
        view.close.assert_called_once()
        first, last = shown[0][0], shown[-1][0]
        self.assertNotAlmostEqual(first[0], last[0], places=2, msg="the base angle moved")
        self.assertTrue(all("target_q" in kwargs and "time_s" in kwargs for _, kwargs in shown))
        # ten degrees of travel at most: the stream's own cap, seen through the picture
        home = chain_angles_from_counts(HOME)
        self.assertLess(max(abs(q[0] - home[0]) for q, _ in shown), 10.5)

    @unittest.skipUnless(find_spec("rerun"), "rerun-sdk not installed (pip install .[viz])")
    def test_a_real_recording_is_written(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "arm.rrd"
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = arm_view.main(["--pose", "0", "120", "-95", "-90", "0", "--save",
                                      str(target), "--mesh-dir", str(Path(directory) / "none")])
            self.assertEqual(code, 0)
            self.assertGreater(target.stat().st_size, 1000)
            self.assertIn("0 of 6 found", out.getvalue())


class ImportTests(unittest.TestCase):
    def test_importing_the_view_loads_no_usb_and_no_viewer(self):
        code = ("import sys; import scorbot.arm_view; "
                "print(any(m.split('.')[0] in ('usb', 'rerun', 'libdef', 'libcomm') "
                "for m in sys.modules))")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             check=True)
        self.assertEqual(out.stdout.strip(), "False")


if __name__ == "__main__":
    unittest.main()
