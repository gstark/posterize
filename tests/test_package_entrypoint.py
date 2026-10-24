"""Tests for the packaged posterize console entrypoint."""

import os
import subprocess
import tarfile
from pathlib import Path

import posterize_build_backend


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_posterize_console_script_shows_existing_cli_help():
    env = os.environ.copy()
    env["UV_PROJECT_ENVIRONMENT"] = str(PROJECT_ROOT / ".venv")

    result = subprocess.run(
        [
            "uv",
            "run",
            "--locked",
            "--project",
            str(PROJECT_ROOT),
            "posterize",
            "--help",
        ],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Convert a flat-color PNG to multi-color 3MF files." in result.stdout
    assert "--plate-size" in result.stdout


def test_build_backend_can_create_sdist(tmp_path):
    archive_name = posterize_build_backend.build_sdist(tmp_path)
    archive_path = tmp_path / archive_name

    assert archive_path.exists()
    with tarfile.open(archive_path, "r:gz") as sdist:
        names = set(sdist.getnames())

    assert "posterize-0.1.0/pyproject.toml" in names
    assert "posterize-0.1.0/PKG-INFO" in names
    assert "posterize-0.1.0/posterize/__main__.py" in names
    assert "posterize-0.1.0/posterize/assets/scad/model.scad.j2" in names
    assert "posterize-0.1.0/docs/architecture.md" in names
