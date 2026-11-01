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


def mjcf(geometry: ChainGeometry = MESH_MODEL) -> str:
    """The scene as MJCF text (metres, degrees)."""
    g = {k: v / 1000.0 for k, v in vars(geometry).items() if k.endswith("_mm")}
    j = {name: f'<joint name="{name}" axis="{_AXES[name]}" range="{_range(name)}"/>'
         for name in JOINTS}
    act = "".join(f'<position name="{n}" joint="{n}" kp="400" forcerange="-200 200"/>' for n in JOINTS)
    return f"""<mujoco model="scorbot_er4u_twin">
  <compiler angle="degree"/>
  <option timestep="0.002"/>
  <default>
    <joint damping="8" armature="0.05" limited="true"/>
    <geom rgba="0.13 0.36 0.66 1" density="1200"/>
  </default>
  <visual><global offwidth="640" offheight="480"/></visual>
  <worldbody>
    <light pos="0.3 -0.6 1.4" dir="-0.2 0.4 -1"/>
    <geom name="table" type="plane" size="0.8 0.8 0.01" rgba="0.82 0.84 0.86 1"/>
    <geom name="target" type="box" pos="0.30 0.22 0.0005" size="0.05 0.05 0.0005" rgba="0.35 0.7 0.4 1" contype="0" conaffinity="0"/>
    <body name="block" pos="0.36 -0.05 0.021">
      <freejoint/>
      <geom type="box" size="0.02 0.02 0.02" rgba="0.9 0.6 0.2 1" density="600"/>
    </body>
    <camera name="front" pos="0.95 -0.55 0.62" xyaxes="0.5 0.866 0 -0.32 0.18 0.93" fovy="55"/>
    <body name="base_link">
      <geom type="cylinder" pos="0 0 {g['base_to_turret_mm'] / 2}" size="0.11 {g['base_to_turret_mm'] / 2}" rgba="0.25 0.27 0.3 1"/>
      <body name="turret_link" pos="0 0 {g['base_to_turret_mm']}">
        {j['base']}
        <geom type="box" pos="0 0 {g['turret_to_shoulder_mm'] / 2}" size="0.07 0.09 {g['turret_to_shoulder_mm'] / 2}"/>
        <body name="upper_arm_link" pos="{g['shoulder_forward_mm']} 0 {g['turret_to_shoulder_mm']}">
          {j['shoulder']}
          <geom type="capsule" fromto="0 0 0 {g['upper_arm_mm']} 0 0" size="0.035"/>
          <body name="forearm_link" pos="{g['upper_arm_mm']} 0 0">
            {j['elbow']}
            <geom type="capsule" fromto="0 0 0 {g['forearm_mm']} 0 0" size="0.03"/>
            <body name="wrist_link" pos="{g['forearm_mm']} 0 0">
              {j['wrist_pitch']}
              <geom type="sphere" size="0.03" rgba="0.2 0.22 0.25 1"/>
              <body name="flange_link">
                {j['wrist_roll']}
                <geom type="box" pos="0.05 0 0" size="0.05 0.035 0.025" rgba="0.2 0.22 0.25 1"/>
                <geom type="box" pos="{g['tool_length_mm'] - 0.02} 0.025 0" size="0.02 0.005 0.012" rgba="0.6 0.62 0.65 1"/>
                <geom type="box" pos="{g['tool_length_mm'] - 0.02} -0.025 0" size="0.02 0.005 0.012" rgba="0.6 0.62 0.65 1"/>
                <site name="tool" pos="{g['tool_length_mm']} 0 0" size="0.006"/>
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
