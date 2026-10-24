"""Tests for the run_pipeline() function."""

import os
import sys
from unittest.mock import patch

import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from posterize.color_filament import Filament, RGBColor
from posterize.pipeline_runner import PipelineConfig, PipelineResult, PipelineRunner


def run_pipeline(image_path, filaments, build_dir, **kwargs):
    return PipelineRunner().run_pipeline(
        PipelineConfig(
            image_path=image_path, filaments=filaments, build_dir=build_dir, **kwargs
        )
    )


def fake_generate_svgs(pbm_paths, build_dir):
    return [
        os.path.join(
            build_dir, f"{os.path.splitext(os.path.basename(pbm_path))[0]}.svg"
        )
        for pbm_path in pbm_paths
    ]


@pytest.fixture
def filaments():
    return [
        Filament("Red", "FF0000", RGBColor(255, 0, 0)),
        Filament("Green", "00FF00", RGBColor(0, 255, 0)),
        Filament("Blue", "0000FF", RGBColor(0, 0, 255)),
        Filament("White", "FFFFFF", RGBColor(255, 255, 255)),
    ]


@pytest.fixture
def test_image(tmp_path):
    """Create a 20x20 image: left half red, right half green."""
    img = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    pixels = img.load()
    for y in range(20):
        for x in range(10):
            pixels[x, y] = (255, 0, 0, 255)
        for x in range(10, 20):
            pixels[x, y] = (0, 255, 0, 255)
    path = str(tmp_path / "test.png")
    img.save(path)
    return path


def test_run_pipeline_returns_typed_result(test_image, filaments, tmp_path):
    build_dir = str(tmp_path / "build")
    result = run_pipeline(test_image, filaments, build_dir, max_filaments=2)

    assert isinstance(result, PipelineResult)
    assert result.scad_path.endswith("model.scad")
    assert result.color_slots
    assert result.filament_slots
    assert result.img_w == 20
    assert result.img_h == 20


def test_run_pipeline_creates_scad(test_image, filaments, tmp_path):
    build_dir = str(tmp_path / "build")
    result = run_pipeline(test_image, filaments, build_dir, max_filaments=2)

    assert os.path.exists(result.scad_path)
    assert result.scad_path.endswith("model.scad")


def test_run_pipeline_creates_intermediate_files(test_image, filaments, tmp_path):
    build_dir = str(tmp_path / "build")
    run_pipeline(test_image, filaments, build_dir, max_filaments=2)

    # Posterized image should exist
    assert any(f.endswith("_posterized.png") for f in os.listdir(build_dir))
    # SVG files should exist
    svg_files = [f for f in os.listdir(build_dir) if f.endswith(".svg")]
    assert len(svg_files) >= 2  # at least one color + base


def test_run_pipeline_color_slots_include_base(test_image, filaments, tmp_path):
    build_dir = str(tmp_path / "build")
    result = run_pipeline(test_image, filaments, build_dir, max_filaments=2)

    names = [slot.name for slot in result.color_slots]
    assert "base" in names


def test_run_pipeline_no_base(test_image, filaments, tmp_path):
    build_dir = str(tmp_path / "build")
    result = run_pipeline(
        test_image, filaments, build_dir, max_filaments=2, no_base=True
    )

    names = [slot.name for slot in result.color_slots]
    assert "base" not in names


def test_run_pipeline_respects_max_filaments(test_image, filaments, tmp_path):
    build_dir = str(tmp_path / "build")
    result = run_pipeline(test_image, filaments, build_dir, max_filaments=1)

    assert len(result.color_slots) == 2  # base + 1 color


def test_run_pipeline_reports_only_selected_color_match_distance(
    test_image, filaments, tmp_path, capsys
):
    build_dir = str(tmp_path / "build")

    run_pipeline(test_image, filaments, build_dir, max_filaments=1)

    output = capsys.readouterr().out
    assert "  Selected colors:" in output
    assert "    #FF0000 → Red #FF0000 (ΔE=0.0; avg ΔE=" in output
    assert "Matching original colors to selected filaments:" not in output
    assert "#00FF00 (200 px) → Red" not in output
    assert "best: Green ΔE=0.0" not in output


def test_run_pipeline_isolated_build_dirs(test_image, filaments, tmp_path):
    """Two pipeline runs with different build dirs don't interfere."""
    build_a = str(tmp_path / "build_a")
    build_b = str(tmp_path / "build_b")

    result_a = run_pipeline(test_image, filaments, build_a, max_filaments=2)
    result_b = run_pipeline(test_image, filaments, build_b, max_filaments=2)

    # Each has its own scad file in its own directory
    assert build_a in result_a.scad_path
    assert build_b in result_b.scad_path
    assert result_a.scad_path != result_b.scad_path

    # Both files exist independently
    assert os.path.exists(result_a.scad_path)
    assert os.path.exists(result_b.scad_path)


def test_run_pipeline_uses_generated_base_svg(test_image, filaments, tmp_path):
    build_dir = str(tmp_path / "build")

    with patch(
        "posterize.pipeline_runner.generate_svgs", side_effect=fake_generate_svgs
    ):
        result = run_pipeline(
            test_image,
            filaments,
            build_dir,
            max_filaments=2,
        )

    scad = open(result.scad_path).read()
    base_footprint = scad.split("module base_footprint()", 1)[1].split(
        "module base_shape_raw()", 1
    )[0]
    assert 'aligned_svg("base.svg")' in base_footprint
    assert "hull()" not in base_footprint


def test_run_pipeline_keeps_selected_filaments_with_close_colors(tmp_path):
    close_filaments = [
        Filament("Red", "FF0000", RGBColor(255, 0, 0)),
        Filament("Dark Red", "EB0000", RGBColor(235, 0, 0)),
    ]
    img = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    pixels = img.load()
    for y in range(20):
        for x in range(20):
            pixels[x, y] = (255, 0, 0, 255) if x < 10 else (235, 0, 0, 255)
    image_path = tmp_path / "close.png"
    img.save(image_path)

    result = run_pipeline(
        image_path, close_filaments, tmp_path / "build", max_filaments=2
    )

    names = {filament.name for filament in result.filament_slots.values()}
    assert {"Red", "Dark Red"} <= names


def test_run_pipeline_custom_base_mask_uses_alpha_threshold(
    test_image, filaments, tmp_path
):
    mask_path = tmp_path / "mask.png"
    Image.new("RGBA", (20, 20), (0, 0, 0, 100)).save(mask_path)
    build_dir = tmp_path / "build"

    run_pipeline(
        test_image,
        filaments,
        build_dir,
        max_filaments=2,
        alpha_threshold=50,
        base_mask=mask_path,
    )

    base = Image.open(build_dir / "base.pbm")
    assert base.getextrema() == (0, 0)
