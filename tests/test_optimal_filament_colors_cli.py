"""Tests for --optimal-filament-colors reporting mode."""

import subprocess
import sys
from pathlib import Path

from PIL import Image

CLI = [sys.executable, "-m", "posterize"]


def run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*CLI, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_optimal_filament_colors_prints_image_colors_without_filament_json_or_outputs(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "sample.png"
    image = Image.new("RGBA", (3, 2), (255, 0, 0, 255))
    image.putpixel((2, 0), (0, 0, 255, 255))
    image.putpixel((2, 1), (0, 0, 255, 255))
    image.save(image_path)

    result = run_cli(
        str(image_path),
        "--optimal-filament-colors",
        "--max-filaments",
        "1",
        "--filaments",
        str(tmp_path / "does-not-exist.json"),
        "--build-dir",
        str(tmp_path / "build"),
        "--output-dir",
        str(tmp_path / "output"),
    )

    assert result.returncode == 0, result.stderr
    assert "Optimal filament colors" in result.stdout
    assert "#FF0000" in result.stdout
    assert "4 px" in result.stdout
    assert "#0000FF" not in result.stdout
    assert "1 more detected color" in result.stdout
    assert result.stderr == ""
    assert not (tmp_path / "build").exists()
    assert not (tmp_path / "output").exists()


def test_help_shows_optimal_filament_colors_flag() -> None:
    result = run_cli("--help")

    assert result.returncode == 0
    assert "--optimal-filament-colors" in result.stdout
