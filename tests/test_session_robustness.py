"""Recorder failure injection only: no USB, controller, or arm."""

import builtins
from concurrent.futures import ThreadPoolExecutor
import json
import threading
from unittest.mock import Mock

from mcap.reader import make_reader
import pytest

from scorbot.session import record


def new_writer(root):
    return record.SessionWriter.create(root, data_source="synthetic", robot_id="test-arm")


def metadata(writer):
    return json.loads((writer.path / "metadata.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("stage", ["finish", "fsync", "stream_close"])
def test_close_failure_records_end_metadata_and_reraises(tmp_path, monkeypatch, stage):
    writer = new_writer(tmp_path)
    writer.log_note("already recorded")
    error = OSError(f"{stage} failed")
    with monkeypatch.context() as patch:
        if stage == "finish":
            patch.setattr(writer._writer, "finish", Mock(side_effect=error))
        elif stage == "fsync":
            real_fsync = record.os.fsync
            mcap_fd = writer._stream.fileno()

            def fsync(fd):
                if fd == mcap_fd and not writer._stream.closed:
                    raise error
                return real_fsync(fd)

            patch.setattr(record.os, "fsync", fsync)
        else:
            stream = writer._stream

            def close():
                stream.close()
                raise error

            patch.setattr(writer, "_stream", Mock(wraps=stream, close=close))
        with pytest.raises(OSError) as caught:
            writer.close()
        assert caught.value is error
    assert writer._stream.closed
    saved = metadata(writer)
    assert saved["ended_utc"]
    assert saved["event_count"] == 1
    assert saved["closed_cleanly"] is False
    assert str(error) in saved["write_error"]
    writer.close()
    assert metadata(writer) == saved


@pytest.mark.parametrize("finish_fails", [False, True])
def test_close_retries_metadata_without_finishing_twice(tmp_path, monkeypatch, finish_fails):
    writer = new_writer(tmp_path)
    writer.log_note("one event")
    finish_error = OSError("finish failed first")
    metadata_error = OSError("metadata temporarily locked")
    finish = Mock(side_effect=finish_error) if finish_fails else Mock(wraps=writer._writer.finish)
    monkeypatch.setattr(writer._writer, "finish", finish)
    with monkeypatch.context() as patch:
        save = Mock(side_effect=metadata_error)
        patch.setattr(record, "_write_json_atomic", save)
        with pytest.raises(OSError) as caught:
            writer.close()
        assert caught.value is (finish_error if finish_fails else metadata_error)
        assert save.call_count == 1
    writer.close()
    assert finish.call_count == 1
    saved = metadata(writer)
    assert saved["ended_utc"]
    assert saved["event_count"] == 1
    assert saved["closed_cleanly"] is (not finish_fails)


def test_close_preserves_finish_error_when_stream_close_also_fails(tmp_path, monkeypatch):
    writer = new_writer(tmp_path)
    error = OSError("finish failed first")
    monkeypatch.setattr(writer._writer, "finish", Mock(side_effect=error))
    stream = writer._stream

    def close():
        stream.close()
        raise OSError("secondary close failure")

    monkeypatch.setattr(writer, "_stream", Mock(wraps=stream, close=close))
    with pytest.raises(OSError) as caught:
        writer.close()
    assert caught.value is error
    assert str(error) in metadata(writer)["write_error"]


@pytest.mark.parametrize("stage", ["start", "add_metadata", "metadata", "identity", "init"])
def test_create_failure_closes_handle_and_removes_new_folder(tmp_path, monkeypatch, stage):
    streams = []

    def track_open(path, mode, *args, **kwargs):
        stream = builtins.open(path, mode, *args, **kwargs)
        if mode == "xb":
            streams.append(stream)
        return stream

    monkeypatch.setattr(record, "open", track_open, raising=False)
    error = OSError(f"{stage} failed")
    fail = Mock(side_effect=error)
    if stage in ("start", "add_metadata"):
        monkeypatch.setattr(record.Writer, stage, fail)
    elif stage == "metadata":
        monkeypatch.setattr(record, "_write_json_atomic", fail)
    elif stage == "identity":
        monkeypatch.setattr(record, "_code_identity", fail)
    else:
        monkeypatch.setattr(record.SessionWriter, "__init__", fail)
    try:
        with pytest.raises(OSError) as caught:
            new_writer(tmp_path)
        assert caught.value is error
        assert all(stream.closed for stream in streams)
        assert list(tmp_path.iterdir()) == []
    finally:
        # Also release leaked baseline handles so Windows can clean up tmp_path.
        for stream in streams:
            stream.close()


def test_create_collision_preserves_existing_session(tmp_path, monkeypatch):
    monkeypatch.setattr(record.time, "time_ns", lambda: 1_000_000_000)
    monkeypatch.setattr(record.secrets, "token_hex", lambda _: "fixed")
    writer = new_writer(tmp_path)
    writer.close()
    before = {path.name: path.read_bytes() for path in writer.path.iterdir()}
    with pytest.raises(FileExistsError):
        new_writer(tmp_path)
    assert {path.name: path.read_bytes() for path in writer.path.iterdir()} == before


def test_periodic_fsync_failure_keeps_recording_and_reports_durability(tmp_path, monkeypatch):
    writer = new_writer(tmp_path)
    try:
        with monkeypatch.context() as patch:
            start = writer._last_fsync_ns + record.FSYNC_INTERVAL_NS
            times = iter([start, start + 1])
            patch.setattr(record.time, "monotonic_ns", lambda: next(times))
            fsync = Mock(side_effect=OSError("periodic sync failed"))
            patch.setattr(record.os, "fsync", fsync)
            assert writer.log_note("on disk") == 0
            assert writer.log_note("still recording") == 1
            assert fsync.call_count == 1
        assert writer._broken is None
    finally:
        writer.close()
    saved = metadata(writer)
    assert saved["closed_cleanly"] is True
    assert saved["event_count"] == 2
    assert "periodic sync failed" in saved["fsync_error"]
    assert "write_error" not in saved
    with writer.mcap_path.open("rb") as stream:
        rows = [json.loads(message.data) for _, _, message in make_reader(stream).iter_messages()]
    assert [row["text"] for row in rows] == ["on disk", "still recording"]
    assert [row["_rec"]["seq"] for row in rows] == [0, 1]


def test_caller_rec_field_is_rejected_without_consuming_sequence(tmp_path):
    payload = {"encoder_counts": {}, "home_switch_bits": 0, "connected": True,
               "_rec": {"seq": 999}}
    with new_writer(tmp_path) as writer:
        with pytest.raises(record.SessionError, match="_rec"):
            writer.log_state(payload)
        assert writer.log_note("valid") == 0
    assert payload["_rec"] == {"seq": 999}
    assert metadata(writer)["event_count"] == 1


@pytest.mark.parametrize("prior_failure", [False, True])
def test_best_effort_close_never_raises_and_preserves_first_failure(
        tmp_path, monkeypatch, prior_failure):
    writer = new_writer(tmp_path)
    warnings = []
    recorder = record.BestEffortRecorder(writer, warn=warnings.append)
    log_error = OSError("logging failed")
    if prior_failure:
        monkeypatch.setattr(writer, "log_note", Mock(side_effect=log_error))
        assert recorder.log_note("failure") is None
    close_error = OSError("finish failed")
    finish = Mock(side_effect=close_error)
    monkeypatch.setattr(writer._writer, "finish", finish)
    recorder.close()
    assert writer._stream.closed
    assert finish.call_count == 1
    assert recorder.failure is (log_error if prior_failure else close_error)
    assert len(warnings) == 1
    recorder.close()
    assert finish.call_count == 1


def test_best_effort_concurrent_failures_warn_once_and_keep_first_error():
    ready = threading.Barrier(2)

    def fail(text):
        ready.wait(timeout=5)
        raise OSError(text)

    writer = Mock(log_note=fail)
    warnings = []
    recorder = record.BestEffortRecorder(writer, warn=warnings.append)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(recorder.log_note, ["failure A", "failure B"]))
    assert results == [None, None]
    assert len(warnings) == 1
    assert str(recorder.failure) in warnings[0]
    first = recorder.failure
    assert recorder.log_note("dropped") is None
    assert recorder.failure is first


def test_two_creates_run_each_git_subprocess_only_once(tmp_path, monkeypatch):
    cache = getattr(record, "_code_identity_once", None)
    if cache is not None:
        cache.cache_clear()
    git = Mock(return_value=Mock(returncode=0, stdout="test-commit\n"))
    monkeypatch.setattr(record.subprocess, "run", git)
    try:
        with new_writer(tmp_path):
            pass
        with new_writer(tmp_path):
            pass
        assert [call.args[0] for call in git.call_args_list] == [
            ["git", "rev-parse", "HEAD"], ["git", "status", "--porcelain"]]
    finally:
        if cache is not None:
            cache.cache_clear()


def test_cached_code_identity_cannot_be_mutated_by_session_callers(tmp_path):
    with new_writer(tmp_path) as first:
        expected = dict(first.metadata["code"])
        first.metadata["code"].clear()
        first.metadata["code"]["git_commit"] = "caller mutation"
        with new_writer(tmp_path) as second:
            assert second.metadata["code"] == expected
        assert record._code_identity() == expected
