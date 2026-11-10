"""Real Stack 2 workers on device_file's fake API; never a native handle or USB."""

import json
import io
from pathlib import Path
import time
from unittest import mock
from types import SimpleNamespace

import pytest

from scripts import stream_soak as soak
from scorbot.transport import device_file


@pytest.mark.parametrize("args", [
    ["--online"],
    ["--device-path", r"\\.\ERUSBDevice0"],
    ["--device-path", "//./erusbdevice0"],
    ["--device-path", "other-fake"],
])
def test_refuses_hardware_before_connect(args):
    with mock.patch.object(device_file, "Win32Api", side_effect=AssertionError("native API")), \
            mock.patch.object(soak.DeskScorbot, "connect", side_effect=AssertionError("connect")):
        with pytest.raises(SystemExit) as error:
            soak.main(args)
    assert error.value.code == 2


def test_device_file_uses_only_fake_api(tmp_path):
    with (tmp_path / "wire.jsonl").open("x", encoding="utf-8") as log, \
            mock.patch.object(device_file, "Win32Api", side_effect=AssertionError("native API")):
        api = soak.FaultWire(log)
        link = device_file.DeviceLink(soak.FAKE_PATH, api=api)
        link.endpoint_out.write(bytes(64))
        buffer = bytearray(64)
        assert link.endpoint_in.read(buffer) == 64
        link.close()
    assert api.closed
    with pytest.raises(ValueError, match="desk fake"):
        api.create_file(r"\\.\ERUSBDevice0")


def test_targets_are_repeatable_and_bounded():
    bounds = {"base": (-50, 100), "shoulder": (-200, 30), "elbow": (-80, 80)}
    first, second = soak.Targets(bounds, seed=17), soak.Targets(bounds, seed=17)
    values = [first.at(t / 20) for t in range(400)]
    assert values == [second.at(t / 20) for t in range(400)]
    assert len({v["base"] for v in values}) > 30
    assert all(low <= value[motor] <= high for value in values
               for motor, (low, high) in bounds.items())


def test_expired_reply_boundary_excludes_its_already_transmitted_write():
    wire = soak.FaultWire(io.StringIO())
    wire.watch = True
    wire.fault = "stale"
    wire.injected.set()
    wire.stale_deadline = 10.5
    wire.stale_until = 10.6
    with mock.patch.object(soak, "time", SimpleNamespace(monotonic=lambda: 10.501,
                                                       monotonic_ns=lambda: 10_501_000_000)):
        wire.write_file(1, bytes(64))  # already handed to the controller before the expired reply
        assert wire.boundary is None
        wire.read_file(1, 64)
    assert wire.boundary == 1


def test_each_fault_prevents_further_motion(tmp_path):
    folder = tmp_path
    started = time.monotonic()
    with mock.patch.object(device_file, "Win32Api", side_effect=AssertionError("native API")):
        code = soak.main(["--minutes", "0.05", "--logs", str(folder), "--seed", "17"])
    summary = next(folder.glob("*/results.jsonl"))
    rows = [json.loads(line) for line in summary.read_text(encoding="utf-8").splitlines()]
    elapsed, folder = time.monotonic() - started, summary.parent
    results = [row for row in rows if row["event"] == "result"]
    assert [row["fault"] for row in results] == list(soak.FAULTS)
    assert elapsed < 60, f"short soak took {elapsed:.1f} s"
    for result in results:
        assert result["injected"], result
        assert result["healthy_moving_packets"] > 0, result
        assert result["checks"], result
        assert result["cleanup_error"] is None, result
        controller = Path(folder / result["controller_log"])
        controller_rows = [json.loads(line) for line in controller.read_text().splitlines()]
        assert all(row["data_source"] == "simulated" and row["simulated"] for row in controller_rows)
        events = [row["event"] for row in controller_rows]
        assert events.index("home_start") < events.index("home_complete") < events.index("stream_start")
        wire_rows = [json.loads(line) for line in (folder / result["wire_log"]).read_text().splitlines()]
        assert any(row["event"] == "injected" for row in wire_rows)
        assert any(row["event"] == "write" for row in wire_rows)
        assert any(row["event"] == "read" for row in wire_rows)
    assert rows[0]["seed"] == 17
    assert all(row["data_source"] == "simulated" and row["simulated"] for row in rows)
    assert rows[-1]["event"] == "summary"
    assert code == (0 if all(row["passed"] for row in results) else 1)
    assert rows[-1]["passed"] == (code == 0)
    failed = [r for r in results if not r["passed"]]
    assert not failed, json.dumps(failed, indent=2)
