"""The arm as a MuJoCo scene: a table, a block, a target square and two cameras. UNVALIDATED.

``mjcf()`` builds the scene text from ``arm_chain`` geometry, so the simulator and the 3D
drawings share one set of numbers (manual, vendor INI and community CAD placements; none measured
on our arm). It imports nothing beyond the standard library; only ``load()`` needs ``mujoco``
(``pip install mujoco``, not a dependency of this package). Wired into no motion command.
Joint angles are ``arm_chain``'s convention (degrees, zero = arm stretched out level), not
degrees from home. The gripper is fixed open: driving it is a later step.
"""

from __future__ import annotations

from .arm_chain import JOINTS, MESH_MODEL, ChainGeometry
from .nominal import AXIS_RANGES

CAMERAS = ("front", "wrist")
_AXES = {"base": "0 0 1", "shoulder": "0 -1 0", "elbow": "0 -1 0", "wrist_pitch": "0 -1 0",
         "wrist_roll": "1 0 0"}


def _range(joint: str) -> str:
    r = AXIS_RANGES[joint]
    if r.signed_min_deg is None:        # the manual gives base as a 310 degree span only
        return f"{-r.span_deg / 2:g} {r.span_deg / 2:g}"
    return f"{r.signed_min_deg:g} {r.signed_max_deg:g}"


def _taper(name: str, x0: float, x1: float, w0: float, h0: float, w1: float, h1: float,
           zc: float = 0.0) -> str:
    """A box along x whose cross-section shrinks from (w0, h0) to (w1, h1), as a mesh (mm in, m out)."""
    pts = [(x, s * w / 2, zc + t * h / 2) for x, w, h in ((x0, w0, h0), (x1, w1, h1))
           for s, t in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    return f'<mesh name="{name}" vertex="{" ".join(f"{v / 1000:g}" for p in pts for v in p)}"/>'


def mjcf(geometry: ChainGeometry = MESH_MODEL) -> str:
    """The scene as MJCF text (metres, degrees).

    Part shapes (pedestal, turret, motor housings, tapered links, gripper) are simple nominal
    solids sized by eye from photos, the same ones the lab app draws; not CAD, not measured.
    """
    G = vars(geometry)
    g = {k: v / 1000.0 for k, v in G.items() if k.endswith("_mm")}
    j = {name: f'<joint name="{name}" axis="{_AXES[name]}" range="{_range(name)}"/>'
         for name in JOINTS}
    act = "".join(f'<position name="{n}" joint="{n}" kp="400" forcerange="-200 200"/>' for n in JOINTS)
    meshes = (_taper("turret", -78, 92, 150, 140, 120, 140, zc=70)
              + _taper("upper_arm", -24, G["upper_arm_mm"] + 10, 66, 76, 54, 58)
              + _taper("forearm", -12, G["forearm_mm"] - 6, 54, 50, 42, 38))
    t, sf, ts = g["tool_length_mm"], g["shoulder_forward_mm"], g["turret_to_shoulder_mm"]
    return f"""<mujoco model="scorbot_er4u_twin">
  <compiler angle="degree"/>
  <option timestep="0.002"/>
  <asset>
    <texture name="sky" type="skybox" builtin="gradient" rgb1="0.93 0.94 0.95" rgb2="0.78 0.80 0.83" width="64" height="64"/>
    <texture name="grid" type="2d" builtin="checker" rgb1="0.90 0.89 0.86" rgb2="0.86 0.85 0.82" mark="edge" markrgb="0.72 0.72 0.70" width="200" height="200"/>
    <material name="table" texture="grid" texrepeat="16 16"/>
    <material name="link" rgba="0.80 0.81 0.83 1" specular="0.4" shininess="0.5"/>
    <material name="motor" rgba="0.20 0.21 0.23 1" specular="0.3"/>
    <material name="plate" rgba="0.52 0.53 0.55 1" specular="0.3"/>
    {meshes}
  </asset>
  <default>
    <joint damping="8" armature="0.05" limited="true"/>
    <geom material="link" density="1200"/>
  </default>
  <visual><global offwidth="640" offheight="480"/><quality shadowsize="4096"/></visual>
  <worldbody>
    <light pos="0.3 -0.6 1.4" dir="-0.2 0.4 -1" diffuse="0.7 0.7 0.7"/>
    <light pos="-0.6 0.4 1.2" dir="0.4 -0.3 -1" diffuse="0.3 0.3 0.3" castshadow="false"/>
    <geom name="table" type="plane" size="0.8 0.8 0.01" material="table"/>
    <geom name="target" type="box" pos="0.30 0.22 0.0005" size="0.05 0.05 0.0005" rgba="0.35 0.7 0.4 1" contype="0" conaffinity="0"/>
    <body name="block" pos="0.36 -0.05 0.021">
      <freejoint/>
      <geom type="box" size="0.02 0.02 0.02" rgba="0.75 0.45 0.3 1" density="600"/>
    </body>
    <camera name="front" pos="0.95 -0.55 0.62" xyaxes="0.5 0.866 0 -0.32 0.18 0.93" fovy="55"/>
    <body name="base_link">
      <geom type="box" pos="0 0 0.006" size="0.15 0.13 0.006" material="plate"/>
      <geom type="cylinder" fromto="0 0 0.012 0 0 {g['base_to_turret_mm']}" size="0.1" material="motor"/>
      <body name="turret_link" pos="0 0 {g['base_to_turret_mm']}">
        {j['base']}
        <geom type="mesh" mesh="turret"/>
        <geom type="cylinder" fromto="{sf} -0.082 {ts} {sf} 0.082 {ts}" size="0.054" material="motor"/>
        <body name="upper_arm_link" pos="{sf} 0 {ts}">
          {j['shoulder']}
          <geom type="mesh" mesh="upper_arm"/>
          <geom type="box" pos="0.095 0 0.048" size="0.055 0.03 0.012" material="motor"/>
          <body name="forearm_link" pos="{g['upper_arm_mm']} 0 0">
            {j['elbow']}
            <geom type="cylinder" fromto="0 -0.04 0 0 0.04 0" size="0.042" material="motor"/>
            <geom type="mesh" mesh="forearm"/>
            <body name="wrist_link" pos="{g['forearm_mm']} 0 0">
              {j['wrist_pitch']}
              <geom type="cylinder" fromto="0 -0.031 0 0 0.031 0" size="0.028" material="motor"/>
              <body name="flange_link">
                {j['wrist_roll']}
                <geom type="cylinder" fromto="0.012 0 0 0.046 0 0" size="0.024"/>
                <geom type="box" pos="0.069 0 0" size="0.023 0.034 0.015" material="motor"/>
                <geom type="box" pos="{(0.092 + t) / 2} 0.024 0" size="{(t - 0.092) / 2} 0.004 0.011" material="plate"/>
                <geom type="box" pos="{(0.092 + t) / 2} -0.024 0" size="{(t - 0.092) / 2} 0.004 0.011" material="plate"/>
                <site name="tool" pos="{t} 0 0" size="0.006" group="4"/>
                <camera name="wrist" pos="0.105 0 0.035" xyaxes="0 -1 0 0.5 0 0.866" fovy="70"/>
              </body>
            </body>
          </body>
        </body>
      </body>
    </body>
  </worldbody>
  <actuator>{act}</actuator>
</mujoco>
"""


def load(geometry: ChainGeometry = MESH_MODEL):
    """(model, data) for the scene. Needs ``mujoco``."""
    import mujoco
    model = mujoco.MjModel.from_xml_string(mjcf(geometry))
    return model, mujoco.MjData(model)
