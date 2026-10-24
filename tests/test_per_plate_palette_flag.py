"""Tests for --per-plate-palette flag parsing and validation."""

import sys
import subprocess

CLI = [sys.executable, "-m", "posterize"]


def run_cli(*args):
    """Run the CLI and return (returncode, stderr)."""
    result = subprocess.run(
        [*CLI, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.returncode, result.stderr


def test_width_and_height_options_are_mutually_exclusive():
    rc, stderr = run_cli("dummy.png", "--width", "100", "--height", "200")
    assert rc != 0
    assert "not allowed with argument" in stderr


def test_help_shows_height_option():
    result = subprocess.run(
        [*CLI, "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0
    assert "--height" in result.stdout


def test_plate_size_zero_is_rejected_before_pipeline_fallbacks():
    rc, stderr = run_cli("dummy.png", "--plate-size", "0")

    assert rc != 0
    assert "--plate-size must be positive" in stderr


def test_non_finite_dimensions_are_rejected_before_pipeline_fallbacks():
    rc, stderr = run_cli("dummy.png", "--width", "nan")
    assert rc != 0
    assert "--width must be positive and finite" in stderr

    rc, stderr = run_cli("dummy.png", "--plate-size", "inf")
    assert rc != 0
    assert "--plate-size must be positive and finite" in stderr


def test_subpixel_plate_size_is_rejected_before_pipeline_fallbacks(tmp_path):
    from PIL import Image

    image_path = tmp_path / "tiny.png"
    Image.new("RGBA", (2, 2), (255, 0, 0, 255)).save(image_path)

    rc, stderr = run_cli(str(image_path), "--width", "100", "--plate-size", "49")

    assert rc != 0
    assert "--plate-size invalid" in stderr
    assert "too small" in stderr


class TestPerPlatePaletteFlag:
    def test_flag_accepted_with_plate_size(self, tmp_path):
        """--per-plate-palette with --plate-size should not produce a validation error."""
        # Will fail later in the pipeline (no real image), but should pass arg validation
        img = tmp_path / "test.png"
        from PIL import Image

        Image.new("RGBA", (10, 10), (255, 0, 0, 255)).save(str(img))

        rc, stderr = run_cli(
            str(img),
            "--per-plate-palette",
            "--plate-size",
            "100",
            "--filaments",
            "filaments.json",
        )
        # Should NOT contain the validation error about requiring --plate-size
        assert "--plate-size" not in stderr or "requires" not in stderr

    def test_flag_rejected_without_plate_size(self):
        """--per-plate-palette without --plate-size should error with a clear message."""
        rc, stderr = run_cli("dummy.png", "--per-plate-palette")
        assert rc != 0
        assert "--plate-size" in stderr

    def test_flag_absent_no_error(self, tmp_path):
        """Without --per-plate-palette, no validation error even without --plate-size."""
        img = tmp_path / "test.png"
        from PIL import Image

        Image.new("RGBA", (10, 10), (255, 0, 0, 255)).save(str(img))

        rc, stderr = run_cli(
            str(img),
            "--filaments",
            "filaments.json",
        )
        # Should not mention per-plate-palette validation
        assert "requires" not in stderr or "--plate-size" not in stderr

    def test_help_shows_flag(self):
        """--per-plate-palette should appear in --help output."""
        result = subprocess.run(
            [*CLI, "--help"],
            capture_output=True,
            text=True,
        )
        assert "--per-plate-palette" in result.stdout

    def test_help_omits_info_plate_flag(self):
        """--info-plate should not appear in --help output."""
        result = subprocess.run(
            [*CLI, "--help"],
            capture_output=True,
            text=True,
        )
        assert "--info-plate" not in result.stdout
