"""The browser page: Viser widgets wired to a ``Panel``. The only Viser import in the SDK.

Everything the page decides is in ``panel.py``; this file only draws
``Panel.view()`` and passes slider, handle and button events on. Simulator
only. Needs the ``ui`` extra.

Design: docs/specs/2026-10-05-three-front-doors-design.md.
"""

from __future__ import annotations

import time

import numpy as np

from .. import arm_chain, arm_view
from ..robot import ScorbotError
from .panel import JOINTS, STOP_LABEL, STOP_NOTE, Panel

HOST = "127.0.0.1"                 # this machine only
ARM_COLOR, GHOST_COLOR = (70, 130, 220), (240, 160, 40)
HANDS_OFF_S = 0.6                  # leave a control alone this long after the user touched it
DRAG_STUCK_S = 5.0                 # a drag with no event for this long has ended, whatever we heard
REFRESH_S = 0.05
KEY_BINDINGS = (("base", -1.0, "A"), ("base", 1.0, "D"),
                ("shoulder", 1.0, "W"), ("shoulder", -1.0, "S"),
                ("elbow", 1.0, "E"), ("elbow", -1.0, "Q"))


class ViserUnavailable(RuntimeError):
    """viser is not installed."""


def require_viser():
    try:
        import viser
    except ImportError as exc:
        raise ViserUnavailable("The browser page needs the ui extra: "
                               "python -m pip install -e \".[ui]\"") from exc
    return viser


def _segments(points) -> np.ndarray:
    """A line through ``points`` as the (N, 2, 3) array Viser draws."""
    line = np.asarray(points, dtype=float)
    return np.stack([line[:-1], line[1:]], axis=1)


class App:
    def __init__(self, panel: Panel, *, port: int = 8080, mesh_dir=None):
        viser = require_viser()
        self._so3 = viser.transforms.SO3
        self.panel = panel
        self.server = viser.ViserServer(host=HOST, port=port, verbose=False)
        self._touched: dict[str, float] = {}
        self._dragging = False                 # the handle is held; its events arrive on a thread pool
        self._drag_seen = 0.0                  # when the last drag event came (a new tab does not clear it)
        self._framed: set[int] = set()         # browser tabs whose camera we have placed
        self._ticks = 0
        scene, gui = self.server.scene, self.server.gui
        scene.set_up_direction("+z")
        scene.add_grid("floor", width=1.5, height=1.5, cell_size=0.1, section_size=0.5)

        self.links = {link: scene.add_frame(f"arm/{link}", show_axes=False)
                      for link in arm_chain.LINKS}
        self.meshes = arm_view.find_meshes(mesh_dir)
        for link, path in self.meshes.items():
            import trimesh                       # comes with viser
            mesh = trimesh.load(path, force="mesh")
            offset = arm_view.visual_offset(link)
            scene.add_mesh_simple(f"arm/{link}/mesh", np.asarray(mesh.vertices),
                                  np.asarray(mesh.faces), color=(200, 205, 215),
                                  wxyz=self._wxyz(offset), position=offset[:3, 3])

        view = panel.view()
        self.handle = scene.add_transform_controls(
            "tool", scale=0.12, disable_rotations=True, disable_sliders=True,
            position=self._metres(view.xyz))
        self.handle.on_update(self._dragged)

        gui.add_markdown("## SIMULATED\n\nNo arm is connected. Angles and positions are from "
                         "the source model, not measured.")
        gui.add_markdown("**Keyboard:** A/D base, W/S shoulder, E/Q elbow. "
                         "Each key requests a 1° step; the simulator enforces its limits.")
        self.sliders = {}
        self.step_buttons = {}
        for joint, (low, high) in panel.slider_limits().items():
            slider = gui.add_slider(f"{joint} (deg)", min=round(low, 2), max=round(high, 2),
                                    step=0.1, initial_value=round(view.angles[joint], 1))
            slider.on_update(lambda event, joint=joint: self._slid(joint, event))
            self.sliders[joint] = slider
            self.step_buttons[joint] = {}
            for delta, label in ((-1.0, f"{joint} −1°"), (1.0, f"{joint} +1°")):
                button = gui.add_button(label)
                button.on_click(lambda _event, joint=joint, delta=delta:
                                self.panel.nudge_joint(joint, delta))
                self.step_buttons[joint][delta] = button
        self.buttons = {}
        for name, label in (("home", "Home"), ("open", "Open gripper"),
                            ("close", "Close gripper"), ("stop", STOP_LABEL)):
            button = gui.add_button(label, color="red" if name == "stop" else None)
            button.on_click(lambda _event, name=name: self.panel.press(name))
            self.buttons[name] = button
        gui.add_markdown(STOP_NOTE)
        self.readout = gui.add_markdown("")

        self.server.on_client_connect(self._client_connected)
        self.server.on_client_disconnect(lambda _client: self._count_clients())
        self.refresh()

    # -- events from the browser -----------------------------------------------

    def _client_connected(self, client) -> None:
        """Install keyboard commands for this browser tab only."""
        self._count_clients()
        for joint, delta, hotkey in KEY_BINDINGS:
            command = client.gui.add_command(
                f"Jog {joint} {delta:+.0f} degree",
                description="One degree simulator step; travel limits still apply.",
                hotkey=hotkey,
            )
            command.on_trigger(lambda _event, joint=joint, delta=delta:
                               self.panel.nudge_joint(joint, delta))

    def _count_clients(self) -> None:
        self.panel.clients(len(self.server.get_clients()))
        self._touched.clear()          # a page that has just opened shows the arm as it is

    def _slid(self, joint: str, event) -> None:
        if event.client is None:       # we set the value ourselves in refresh()
            return
        self._touched[joint] = time.monotonic()
        self.panel.set_joint(joint, float(event.target.value))

    def _dragged(self, event) -> None:
        if getattr(event, "client", None) is None:
            return
        self._touched["tool"] = self._drag_seen = time.monotonic()
        phase = getattr(event, "phase", "update")
        if phase == "start":
            self._dragging = True
        elif phase == "end":
            self._dragging = False
        x, y, z = (float(value) * 1000.0 for value in self.handle.position)
        self.panel.drag_tool(x, y, z)

    # -- drawing ---------------------------------------------------------------

    def _wxyz(self, pose) -> np.ndarray:
        return self._so3.from_matrix(np.asarray(pose)[:3, :3]).wxyz

    @staticmethod
    def _metres(xyz) -> tuple[float, float, float]:
        return tuple(float(value) / 1000.0 for value in xyz[:3])

    def _free(self, control: str, now: float) -> bool:
        since = now - self._touched.get(control, 0.0)
        if control == "tool" and self._dragging:
            return time.monotonic() - self._drag_seen > DRAG_STUCK_S
        return since > HANDS_OFF_S

    def _frame_new_tabs(self) -> None:
        # Viser's default camera is metres away. A tab's camera can only be
        # moved once the tab has reported it, so this is done here, not on connect.
        clients = self.server.get_clients()
        self._framed &= set(clients)
        for client_id, client in clients.items():
            if client_id not in self._framed and client.camera.update_timestamp != 0.0:
                client.camera.position = (0.9, -0.9, 0.8)
                client.camera.look_at = (0.1, 0.0, 0.3)
                self._framed.add(client_id)

    def refresh(self) -> None:
        """Draw the panel's view. Call about twenty times a second."""
        self._ticks += 1
        if self._ticks % 2 == 0:
            self.panel.tick()
        self._frame_new_tabs()
        try:
            view = self.panel.view()
        except ScorbotError as exc:        # a failed read must not end the page; show it
            for name in ("home", "open", "close"):
                self.buttons[name].disabled = True
            self.readout.content = (f"**Cannot read the arm: {exc}** The drawing is the last one "
                                    "read. Software stop still works.")
            return
        now = time.monotonic()
        scene = self.server.scene
        with self.server.atomic():
            for link, frame in self.links.items():
                pose = view.actual.poses[link]
                frame.position, frame.wxyz = pose[:3, 3], self._wxyz(pose)
            scene.add_line_segments("skeleton", _segments(view.actual.skeleton), ARM_COLOR,
                                    thickness=0.006, visible=not self.meshes)
            scene.add_line_segments(
                "ghost", _segments((view.ghost or view.actual).skeleton), GHOST_COLOR,
                thickness=0.004, visible=view.ghost is not None)
        target = self.panel.mover.target_angles() if view.ghost is not None else None
        for joint in JOINTS:
            if self._free(joint, now):
                shown = round((target or view.angles)[joint], 1)
                slider = self.sliders[joint]
                shown = min(max(shown, slider.min), slider.max)
                if slider.value != shown:
                    slider.value = shown
        if self._free("tool", now) and view.ghost is None:
            self.handle.position = self._metres(view.xyz)
        for name in ("home", "open", "close"):
            self.buttons[name].disabled = view.busy is not None or view.fault is not None
        for buttons in self.step_buttons.values():
            for button in buttons.values():
                button.disabled = view.busy is not None or view.fault is not None
        x, y, z, pitch, _roll = view.xyz
        lines = ["**Current:** " + " | ".join(f"{joint} {view.angles[joint]:.1f} deg"
                                               for joint in JOINTS)]
        if target is not None:
            lines.append("**Target:** " + " | ".join(f"{joint} {target[joint]:.1f} deg"
                                                      for joint in JOINTS))
        lines.append(f"Tool ({x:.0f}, {y:.0f}, {z:.0f}) mm; pitch {pitch:.1f} deg")
        if view.busy:
            lines.append(f"**Busy: {view.busy}**")
        if view.fault:
            lines.append(f"**FAULT: {view.fault}** Restart the page's program to go on.")
        elif self.panel.message:
            lines.append(self.panel.message)
        self.readout.content = "\n\n".join(lines)

    def close(self) -> None:
        self.server.stop()


def run(panel: Panel, *, port: int = 8080, mesh_dir=None) -> None:
    """Serve the page until Ctrl+C."""
    app = App(panel, port=port, mesh_dir=mesh_dir)
    print(f"SIMULATED arm page: http://{HOST}:{app.server.get_port()}  (Ctrl+C to end)")
    try:
        while True:
            app.refresh()
            time.sleep(REFRESH_S)
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
