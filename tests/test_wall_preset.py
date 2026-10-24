"""Tests for the --wall CLI preset."""

import subprocess
import sys

CLI = [sys.executable, "-m", "posterize"]


def run_cli(*args):
    return subprocess.run(
        [*CLI, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_help_shows_wall_flag():
    result = run_cli("--help")

    assert result.returncode == 0
    assert "--wall" in result.stdout


def test_wall_implies_keyhole_validation_with_no_base():
    result = run_cli("dummy.png", "--wall", "--no-base", "--plate-size", "200")

    assert result.returncode != 0
    assert "--keyhole is only available when the base layer is enabled" in result.stderr


def test_wall_implies_keyhole_stud_spacing_validation_path():
    result = run_cli("dummy.png", "--wall", "--per-plate-palette")

    assert result.returncode != 0
    assert "--per-plate-palette requires --plate-size" in result.stderr
