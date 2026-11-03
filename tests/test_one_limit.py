"""Vendor travel bounds, desk only: simulator and pure argument parsing."""

import argparse
from types import SimpleNamespace
from unittest import mock

import pytest

from examples import bench_stream, policy_loop
from scorbot import Scorbot, ScorbotError, SimulatedScorbot
from scorbot import limits, nominal, vendor_model, vendor_move


@pytest.fixture
def robot():
    with SimulatedScorbot() as arm:
        arm.enable()
        arm.vendor_home(operator_at_stop=True, jaws_empty=True, wrist_clear=True)
        yield arm


def test_jog_beyond_source_window_succeeds(robot):
    # The source elbow window stopped before +5196 counts; the vendor allows +5676.
    for _ in range(10):
        after = robot.jog_joint("elbow", -5).signed_encoder_counts["elbow"]
    assert after > 5196


def test_jog_past_enclimit_queues_nothing(robot):
    robot.sim.counts["shoulder"] = 1490
    queued = list(robot.sim.commands)
    with mock.patch.object(robot._commands, "put", wraps=robot._commands.put) as put:
        with pytest.raises(ValueError, match=r"vendor limit \(USBC.dll EncLimit / ROB_4u.INI\)"):
            robot.jog_joint("shoulder", 1)
    put.assert_not_called()
    assert robot.sim.commands == queued
    assert robot._fault is None


def test_jog_back_from_past_enclimit(robot):
    robot.sim.counts["shoulder"] = 1700
    after = robot.jog_joint("shoulder", -1).signed_encoder_counts["shoulder"]
    assert 1500 < after < 1700  # still outside, but closer
    with pytest.raises(ValueError):
        robot.jog_joint("shoulder", 1)


def test_recovery_must_not_worsen_another_vendor_window(robot):
    robot.sim.counts.update(shoulder=1700, elbow=-25500)
    queued = list(robot.sim.commands)
    with pytest.raises(ValueError, match="vendor limit"):
        robot.jog_joint("shoulder", -1)
    assert robot.sim.commands == queued


def test_vendor_counts_include_the_session_home_offset(robot):
    robot._home_counts["shoulder"] = 1000
    robot.sim.counts["shoulder"] = 400
    with pytest.raises(ValueError, match="ROB_4u"):
        robot.jog_joint("shoulder", 1)


def test_stream_optional_cap_has_no_ceiling_and_keeps_vendor_windows(robot):
    from scorbot import StreamRefused

    with robot.start_stream(travel_cap_deg=1000) as stream:
        with pytest.raises(StreamRefused, match="vendor limit"):
            stream.set_target({"shoulder": 600})


@pytest.mark.parametrize("home", [None, {m: 32767 if m == "base" else 0 for m in limits.RECORDED_MOTORS}])
def test_unplaceable_jog_latches(robot, home):
    robot._home_counts = home
    with pytest.raises(ScorbotError):
        robot._refuse_past_joint_limit(robot.get_state(), {"base": 10})
    assert robot._fault
    assert not robot._homed


def test_legacy_home_is_not_vendor_zero():
    with SimulatedScorbot() as robot:
        robot.enable()
        robot.home(start_position_confirmed=True)
        queued = list(robot.sim.commands)
        with pytest.raises(ScorbotError, match="vendor Home"):
            robot.jog_joint("base", 1)
        with pytest.raises(ScorbotError, match="vendor Home"):
            robot.start_stream(travel_cap_deg=2)
        assert robot.sim.commands == queued


def test_vendor_move_target_outside_vendor_window(robot):
    target = list(vendor_move.home_joints())
    target[0] = 4.0
    queued = list(robot.sim.commands)
    with pytest.raises(ValueError, match="vendor limit"):
        robot.move_joint_vendor(target, total_ms=3000)
    assert robot.sim.commands == queued


def test_vendor_move_setpoint_enclimit():
    # Source reference angles accept this pose, but the elbow exceeds EncLimit.
    counts = (0, -16000, 20001, 0, 0)
    first = SimpleNamespace(emergency=False, counts=counts)
    run = mock.Mock(reply=first, setp=[])
    run.send.return_value = iter(())
    run.cycle.return_value = iter(())
    with mock.patch.object(vendor_move.vh, "_Run", return_value=run):
        with pytest.raises(vendor_move.vh.HomeError, match="vendor limit"):
            list(vendor_move.run_move(first, [counts], lead_limit_deg=5))
    run.cycle.assert_not_called()


@pytest.mark.parametrize("module, required", [
    (bench_stream, ["--motor", "base", "--delta", "1"]),
    (policy_loop, ["--policy", "reach:base=5"]),
])
@pytest.mark.parametrize("extra, expected", [([], None), (["--travel-cap-deg", "1000"], 1000)])
def test_example_parsing_no_cap(module, required, extra, expected):
    # Stop immediately after parse: no preflight, files, prompts or device access.
    parse = argparse.ArgumentParser.parse_args
    parsed = []

    class Parsed(Exception):
        pass

    def capture(parser, *args, **kwargs):
        parsed.append(parse(parser, *args, **kwargs))
        raise Parsed

    labels = ["--output", "unused.jsonl", "--robot-id", "desk", "--arm-label", "desk",
              "--controller-label", "desk", "--driver", "simulated", "--operator", "test",
              "--start-pose-note", "desk only"]
    with mock.patch("sys.argv", [module.__name__, *labels, *required, *extra]), \
            mock.patch.object(argparse.ArgumentParser, "parse_args", capture):
        with pytest.raises(Parsed):
            module.main()
    assert parsed[0].travel_cap_deg == expected


def test_default_cap_is_none():
    assert limits.TRAVEL_CAP_DEG is None
    assert Scorbot.STREAM_TRAVEL_CAP_MAX_DEG is None


def test_vendor_predicate_uses_absolute_counts():
    counts = dict.fromkeys(vendor_model.MOTORS, 0)
    assert nominal.vendor_limit_problem(counts) is None
    assert nominal.vendor_limit_problem({**counts, "shoulder": 1501})
