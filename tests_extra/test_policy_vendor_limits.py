"""Vendor travel guards, offline frames and a simulated vendor Home only."""

from contextlib import ExitStack
from importlib.util import find_spec
import math
from types import SimpleNamespace
from unittest import mock

import pytest

from scorbot import ScorbotError, SimulatedScorbot, nominal, policy_arm, vendor_model
from scorbot.calibration import signed_count_delta
from scorbot.policy_arm import JOINTS, PolicyArm, clamp, wrist_counts


def arm_frames(home=None):
    # Same policy signs/scales as preview_jog; no worker, USB or arm.
    arm = PolicyArm(SimpleNamespace(_fault=None, _homed=True, _gripper_homed=True))
    arm._stack = ExitStack()
    arm._home = dict.fromkeys((*vendor_model.MOTORS, "gripper"), 0)
    arm._home.update({m: v % 65535 for m, v in (home or {}).items()})
    arm._per_degree = {"base": 142, "shoulder": 115, "elbow": -113}
    arm.joints = JOINTS
    arm.wrist = True
    arm.target = dict.fromkeys(JOINTS, 0.0)
    arm._stream = mock.Mock()
    arm.observe = lambda: setattr(arm, "_seen", dict(arm.target))
    return arm


def counts(arm):
    out = {m: round(arm.target[m] * arm._per_degree[m]) for m in policy_arm.MOTORS}
    out.update(wrist_counts(arm.target["pitch"], arm.target["roll"]))
    return {m: signed_count_delta(arm._home[m], 0) + out[m] for m in vendor_model.MOTORS}


def vendor_degrees(arm):
    return {j: math.degrees(v) for j, v in
            vendor_model.counts_to_joints(counts(arm).values()).items()}


def walk(arm, goal, frames=1200):
    for _ in range(frames):
        before = dict(arm.target)
        arm.act(goal)
        assert all(abs(arm.target[j] - before[j]) <= policy_arm.STEP_MAX_DEG for j in JOINTS)
    return arm.target


def test_base_reaches_vendor_plus_170_beyond_two_degrees():
    arm = arm_frames()
    # Policy's legacy positive base is negative in the vendor joint convention.
    goal = -170 * abs(vendor_model.ER4U.counts_per_degree(0)) / arm._per_degree["base"]
    assert walk(arm, {"base": goal})["base"] == goal
    assert vendor_degrees(arm)["base"] == pytest.approx(170, abs=0.01)


def test_base_past_174_holds_the_frame_at_the_last_allowed_step():
    arm = arm_frames()
    walk(arm, {"base": -180})
    assert 173 < vendor_degrees(arm)["base"] <= 174
    before = dict(arm.target)
    assert arm.act({"base": -180, "roll": 0.1}) == before


def test_encoder_box_binds_even_when_joint_angles_allow_more():
    arm = arm_frames()
    walk(arm, {"roll": 570})
    assert 14980 <= counts(arm)["wrist_motor_1"] <= 15000
    assert 360 < vendor_degrees(arm)["roll"] < 570


def test_roll_vendor_window_binds_independently_of_encoder_box():
    arm = arm_frames()
    walk(arm, {"pitch": 45, "roll": 600}, frames=1300)
    assert 569 < vendor_degrees(arm)["roll"] <= 570
    assert counts(arm)["wrist_motor_1"] < 15000
    assert abs(vendor_degrees(arm)["pitch"]) < 113


def test_shoulder_upward_room_uses_vendor_window():
    arm = arm_frames()
    walk(arm, {"shoulder": 20})
    assert -124 <= vendor_degrees(arm)["shoulder"] < -123.5
    assert arm.target["shoulder"] > 3
    # The opposite side admits the vendor's +31, beyond the USNA +28.28.
    arm = arm_frames()
    walk(arm, {"shoulder": -160})
    assert 30.5 < vendor_degrees(arm)["shoulder"] <= 31


def test_absolute_session_home_counts_are_included():
    arm = arm_frames({"base": -24000})
    walk(arm, {"base": -20})
    assert 173 < vendor_degrees(arm)["base"] <= 174
    assert abs(arm.target["base"]) < 6


def test_coupled_pose_and_clamp_reporting_hold_entire_frame():
    arm = arm_frames()
    walk(arm, {"elbow": -100})
    assert 159 < vendor_degrees(arm)["elbow"] <= 160
    before = dict(arm.target)
    new, held = clamp({"elbow": -0.5, "base": 0.1}, before, before,
                      count_guard=arm._vendor_target_problem)
    assert new == before
    assert set(held) == {"elbow", "base"}


def test_act_refuses_before_connect_or_after_home_is_invalidated():
    arm = PolicyArm(SimpleNamespace())
    with pytest.raises(ConnectionError):
        arm.act({"base": 1})
    arm = arm_frames()
    arm.robot._homed = False
    with pytest.raises(ScorbotError, match="[Hh]ome"):
        arm.act({"base": 1})
    arm._stream.set_target.assert_not_called()


@pytest.mark.skipif(not find_spec("ruckig"), reason="needs the planning extra")
@pytest.mark.parametrize("wrist", [False, True])
def test_policy_stream_checks_vendor_windows_on_targets_and_setpoints(tmp_path, wrist):
    arm = PolicyArm(SimulatedScorbot(log_path=tmp_path / "sim.jsonl"), wrist=wrist)
    arm.connect(operator_at_stop=True, jaws_empty=True, wrist_clear=True)
    try:
        core = arm._stream.core
        target = dict.fromkeys(core.motors, 0)
        # +170 vendor base and roll over 360, including the held wrist on arm-only streams.
        target["base"] = -24122
        if wrist:
            target.update(wrist_motor_1=12000, wrist_motor_2=12000)
        assert core._pose_check(target) is None
        assert core.bounds["base"] == (-25000, 20000)
        assert core._pose_check({**target, "base": -24800}) is not None
        assert core._pose_check({**target, "base": -25001}) is not None
        # Use the core's public refusal path without submitting any large valid target to the worker.
        from scorbot.streaming import StreamRefused
        with pytest.raises(StreamRefused):
            arm._stream.set_target({"base": -24800})
        assert core._target == dict.fromkeys(core.motors, 0)
    finally:
        arm.close()


def test_all_encoder_axes_and_coupled_pitch_use_vendor_conversion():
    zero = dict.fromkeys((*vendor_model.MOTORS, "gripper"), 0)
    for motor, limit in nominal.VENDOR_ENCODER_SOFT_LIMITS.items():
        for outside in (limit.minimum - 1, limit.maximum + 1):
            assert "EncLimit" in nominal.vendor_limit_problem({**zero, motor: outside})
    # Each count is in the encoder box, but their differential exceeds the joint pitch window.
    assert "pitch" in nominal.vendor_limit_problem({**zero, "wrist_motor_1": 14000,
                                                    "wrist_motor_2": -14000})
