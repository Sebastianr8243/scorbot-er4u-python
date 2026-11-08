"""Setup planning only: never run pip or create an environment."""

from pathlib import Path
from unittest import mock
import subprocess

from scripts import setup_lerobot as setup


def test_platform_python_paths():
    assert setup.venv_python(Path("env"), "win32") == Path("env/Scripts/python.exe")
    assert setup.venv_python(Path("env"), "linux") == Path("env/bin/python")


def test_dry_run_prints_every_command_without_side_effects(tmp_path, capsys):
    with mock.patch.object(setup.subprocess, "run") as run, \
            mock.patch.object(setup, "check_symlinks") as symlinks:
        assert setup.main(["--venv", str(tmp_path / "env"), "--dry-run"]) == 0
    run.assert_not_called()
    symlinks.assert_not_called()
    output = capsys.readouterr().out
    for command in ("-m venv", "lerobot[dataset,training]==0.6.1", "-e .",
                    "-e plugins/lerobot_robot_scorbot", "examples/lerobot_sim_check.py"):
        assert command in output
    assert not (tmp_path / "env").exists()
    assert "check: skipped" in output


def test_failure_stops_and_reports_remaining_steps(tmp_path, capsys):
    with mock.patch.object(setup.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "pip")) as run, \
            mock.patch.object(setup, "check_symlinks"):
        assert setup.main(["--venv", str(tmp_path / "env")]) == 1
    assert run.call_count == 1
    output = capsys.readouterr().out
    assert "venv: failed" in output
    assert "lerobot: skipped" in output
    assert "check: skipped" in output


def test_existing_environment_and_skip_check(tmp_path, capsys):
    executable = setup.venv_python(tmp_path)
    executable.parent.mkdir(parents=True)
    executable.touch()
    with mock.patch.object(setup.subprocess, "run") as run, \
            mock.patch.object(setup, "check_symlinks"):
        assert setup.main(["--venv", str(tmp_path), "--skip-check"]) == 0
    assert run.call_count == 3
    assert all(call.kwargs["cwd"] == setup.ROOT for call in run.call_args_list)
    assert "venv: skipped" in capsys.readouterr().out


def test_symlink_warning(capsys):
    with mock.patch.object(setup.os, "symlink", side_effect=OSError("not permitted")):
        assert not setup.check_symlinks()
    output = capsys.readouterr().out
    assert "huggingface/lerobot#4059" in output
    assert "Developer Mode" in output


def test_python_version_warning(capsys):
    with mock.patch.object(setup.sys, "version_info", (3, 13)), \
            mock.patch.object(setup.sys, "platform", "win32"):
        assert setup.main(["--dry-run", "--skip-check"]) == 0
    assert "Python 3.12" in capsys.readouterr().out
