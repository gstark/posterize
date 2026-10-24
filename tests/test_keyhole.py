"""Tests for keyhole wall-mount support."""

import os
import subprocess
import sys
from unittest.mock import patch

import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from posterize.external_tools import ExternalTools
from posterize.keyholes import (
    _keyhole_map_for_positions,
    _keyhole_position_coverage_failure,
    _translate_keyhole_position,
    compute_keyhole_depth,
    format_keyhole_map,
    format_keyhole_positions,
    save_keyhole_attempt_debug_image,
    select_keyhole_positions,
    validate_keyhole_positions,
)
from posterize.scad_rendering import generate_scad, render_stl
from posterize.plate_grid import PlateGrid


CLI = [sys.executable, "-m", "posterize"]


def run_cli(*args):
    result = subprocess.run(
        [*CLI, *args],
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stderr


def keyhole_x(position):
    return position[0] if isinstance(position, list) else position


def test_format_keyhole_map_rounds_coordinates_to_one_decimal_place():
    keyhole_map = {
        (0, 0): [[-20.491506228765573, 28.00000000000003], 0.00000000001],
        (0, 1): [(20.491506228765573, -0.00000000001)],
    }

    assert format_keyhole_map(keyhole_map) == (
        "{(0, 0): [[-20.5, 28.0], 0.0], (0, 1): [(20.5, 0.0)]}"
    )
    assert format_keyhole_positions(keyhole_map[(0, 0)]) == "[[-20.5, 28.0], 0.0]"


def test_keyhole_map_uses_actual_plate_bounds_for_uneven_grids():
    grid = PlateGrid.from_dimensions(
        image_width_px=101, image_height_px=61, target_width_mm=505, plate_size_mm=200
    )

    keyhole_map = _keyhole_map_for_positions([101.6], grid, row=0)

    assert grid.rows == 2
    assert keyhole_map == {(0, 2): [101.6]}


# --- select_keyhole_positions ---


def test_select_keyhole_positions_falls_back_to_center_spaced_pair_before_next_row(
    tmp_path,
):
    image_path = tmp_path / "keyhole_row_fallback.png"
    img = Image.new("RGBA", (400, 400), (255, 0, 0, 255))
    pixels = img.load()

    for y in range(8, 29):
        for x in range(49, 62):
            pixels[x, y] = (0, 0, 0, 0)
        for x in range(339, 352):
            pixels[x, y] = (0, 0, 0, 0)

    img.save(image_path)

    mask, img_w, img_h = Image.open(image_path).convert("RGBA"), 400, 400
    mask_pixels = mask.load()
    mask_matrix = [
        [mask_pixels[x, y][3] >= 128 for x in range(img_w)] for y in range(img_h)
    ]
    grid = PlateGrid.from_dimensions(
        img_w, img_h, target_width_mm=400, plate_size_mm=200
    )

    keyhole_map = select_keyhole_positions(
        mask_matrix,
        grid,
    )

    assert set(keyhole_map) == {(0, 0), (0, 1)}
    positions = sorted(x for xs in keyhole_map.values() for x in xs)
    assert positions[0] > -145.0
    assert positions[1] < 145.0
    assert positions[0] == pytest.approx(-positions[1])


def test_select_keyhole_positions_uses_smaller_stud_pair_when_outer_pair_lacks_coverage(
    tmp_path,
):
    image_path = tmp_path / "keyhole_pair_fallback.png"
    img = Image.new("RGBA", (900, 120), (255, 0, 0, 255))
    pixels = img.load()

    for y in range(8, 29):
        for x in range(38, 50):
            pixels[x, y] = (0, 0, 0, 0)
        for x in range(850, 862):
            pixels[x, y] = (0, 0, 0, 0)

    img.save(image_path)

    mask, img_w, img_h = Image.open(image_path).convert("RGBA"), 900, 120
    mask_pixels = mask.load()
    mask_matrix = [
        [mask_pixels[x, y][3] >= 128 for x in range(img_w)] for y in range(img_h)
    ]
    grid = PlateGrid.from_dimensions(
        img_w, img_h, target_width_mm=900, plate_size_mm=300
    )

    keyhole_map = select_keyhole_positions(
        mask_matrix,
        grid,
        stud_spacing=True,
    )

    positions = sorted(keyhole_x(x) for xs in keyhole_map.values() for x in xs)
    assert positions == pytest.approx([-203.2, 203.2])


def test_stud_spacing_uses_standard_spacing_even_below_old_threshold():
    img_w, img_h = 430, 80
    mask_matrix = [[True for _ in range(img_w)] for _ in range(img_h)]
    grid = PlateGrid.from_dimensions(
        img_w, img_h, target_width_mm=430, plate_size_mm=430
    )

    keyhole_map = select_keyhole_positions(
        mask_matrix,
        grid,
        stud_spacing=True,
    )

    positions = sorted(keyhole_x(x) for xs in keyhole_map.values() for x in xs)
    assert positions == pytest.approx([-203.2, 203.2])


def test_default_keyhole_spacing_uses_edge_margin_when_above_minimum():
    img_w, img_h = 300, 80
    mask_matrix = [[True for _ in range(img_w)] for _ in range(img_h)]
    grid = PlateGrid.from_dimensions(
        img_w, img_h, target_width_mm=300, plate_size_mm=300
    )

    keyhole_map = select_keyhole_positions(
        mask_matrix,
        grid,
    )

    positions = sorted(keyhole_x(x) for xs in keyhole_map.values() for x in xs)
    assert positions == pytest.approx([-70.0, 70.0])


def test_keyhole_selection_includes_outline_width_in_material_check():
    img_w, img_h = 100, 80
    mask_matrix = [[True for _ in range(img_w)] for _ in range(img_h)]
    for y in range(img_h):
        mask_matrix[y][23] = False

    grid = PlateGrid.from_dimensions(
        img_w, img_h, target_width_mm=100, plate_size_mm=100
    )

    keyhole_map = select_keyhole_positions(
        mask_matrix,
        grid,
        edge_clearance_mm=0,
    )

    assert keyhole_map is None


def test_keyhole_material_check_rejects_transparency_in_bounding_corner():
    mask_matrix = [[True for _ in range(300)] for _ in range(80)]
    mask_matrix[30][41] = False

    grid = PlateGrid.from_dimensions(
        image_width_px=300, image_height_px=80, target_width_mm=300, plate_size_mm=300
    )

    failure = _keyhole_position_coverage_failure(
        mask_matrix,
        grid,
        row=0,
        col=0,
        x_center_mm=-101.6,
        edge_clearance_mm=3,
    )

    assert failure is not None
    assert "bounding box" in failure


def test_select_keyhole_positions_moves_down_within_row_before_next_row():
    img_w, img_h = 400, 400
    img = Image.new("RGBA", (img_w, img_h), (255, 0, 0, 255))
    pixels = img.load()
    for y in range(5, 31):
        for x in range(img_w):
            pixels[x, y] = (0, 0, 0, 0)

    mask_pixels = img.load()
    mask_matrix = [
        [mask_pixels[x, y][3] >= 128 for x in range(img_w)] for y in range(img_h)
    ]
    grid = PlateGrid.from_dimensions(
        img_w, img_h, target_width_mm=400, plate_size_mm=200
    )

    keyhole_map, attempts = select_keyhole_positions(
        mask_matrix,
        grid,
        include_diagnostics=True,
    )

    assert set(keyhole_map) == {(0, 0), (0, 1)}
    selected = attempts[-1]
    assert selected["failures"] == []
    assert selected["row"] == 0
    assert selected["slot_top_y"] > 31
    positions = [position for xs in keyhole_map.values() for position in xs]
    assert all(isinstance(position, list) for position in positions)
    assert sorted(keyhole_x(position) for position in positions) == pytest.approx(
        [-145, 145]
    )


def test_select_keyhole_positions_auto_shifts_stud_spacing_off_seams():
    img_w, img_h = 1268, 1957
    mask_matrix = [[True for _ in range(img_w)] for _ in range(img_h)]
    grid = PlateGrid.from_dimensions(
        img_w, img_h, target_width_mm=800, plate_size_mm=200
    )

    keyhole_map, attempts = select_keyhole_positions(
        mask_matrix,
        grid,
        stud_spacing=True,
        include_diagnostics=True,
    )

    assert keyhole_map is not None
    positions = sorted(x for xs in keyhole_map.values() for x in xs)
    assert positions[0] > -203.2
    assert positions[1] > 203.2
    assert any("auto seam shift" in attempt["strategy"] for attempt in attempts)
    assert attempts[-1]["failures"] == []


def test_select_keyhole_positions_respects_edge_clearance():
    img_w, img_h = 1268, 1957
    mask_matrix = [[True for _ in range(img_w)] for _ in range(img_h)]
    grid = PlateGrid.from_dimensions(
        img_w, img_h, target_width_mm=800, plate_size_mm=200
    )

    keyhole_map, attempts = select_keyhole_positions(
        mask_matrix,
        grid,
        stud_spacing=True,
        edge_clearance_mm=3.0,
        include_diagnostics=True,
    )

    assert keyhole_map is not None
    positions = sorted(x for xs in keyhole_map.values() for x in xs)
    assert positions[0] > -195.2
    assert positions[1] > 211.2
    assert any(
        "3.0mm clearance" in failure[2]
        for attempt in attempts
        if isinstance(attempt["failures"], list)
        for failure in attempt["failures"]
    )


def test_select_keyhole_positions_reports_all_considered_failures(tmp_path):
    image_path = tmp_path / "keyhole_failure_details.png"
    img = Image.new("RGBA", (400, 400), (255, 0, 0, 255))
    pixels = img.load()

    for y in range(8, 29):
        for x in range(49, 62):
            pixels[x, y] = (0, 0, 0, 0)
        for x in range(339, 352):
            pixels[x, y] = (0, 0, 0, 0)
    for y in range(208, 229):
        for x in range(49, 62):
            pixels[x, y] = (0, 0, 0, 0)
        for x in range(339, 352):
            pixels[x, y] = (0, 0, 0, 0)

    img.save(image_path)

    mask, img_w, img_h = Image.open(image_path).convert("RGBA"), 400, 400
    mask_pixels = mask.load()
    mask_matrix = [
        [mask_pixels[x, y][3] >= 128 for x in range(img_w)] for y in range(img_h)
    ]
    grid = PlateGrid.from_dimensions(
        img_w, img_h, target_width_mm=400, plate_size_mm=200
    )

    keyhole_map, attempts = select_keyhole_positions(
        mask_matrix,
        grid,
        include_diagnostics=True,
    )

    assert keyhole_map is not None
    assert attempts[0]["row"] == 0
    assert attempts[0]["strategy"].startswith("edge margin")
    assert attempts[0]["positions"] == pytest.approx([-145.0, 145.0])
    assert "transparent pixel" in attempts[0]["failures"][0][2]
    assert attempts[0]["failures"][0][0] == pytest.approx(-145.0)
    assert attempts[0]["failures"][0][1] == 0
    assert any(
        attempt["strategy"].startswith("center-spaced fallback") for attempt in attempts
    )
    assert attempts[-1]["failures"] == []


def test_save_keyhole_attempt_debug_image_writes_overlay(tmp_path):
    image_path = tmp_path / "keyhole_input.png"
    output_path = tmp_path / "keyhole_attempts.png"
    Image.new("RGBA", (100, 60), (255, 255, 255, 255)).save(image_path)

    attempts = [
        {
            "row": 0,
            "positions": [-20.0, 20.0],
            "strategy": "edge margin (demo)",
            "failures": [
                (-20.0, 0, "transparent pixel(s)"),
                (20.0, 1, "transparent pixel(s)"),
            ],
        }
    ]
    grid = PlateGrid.from_dimensions(
        image_width_px=100, image_height_px=60, target_width_mm=100, plate_size_mm=100
    )

    save_keyhole_attempt_debug_image(
        str(image_path),
        str(output_path),
        attempts,
        grid,
    )

    assert output_path.exists()
    rendered = Image.open(output_path).convert("RGBA")
    assert rendered.getbbox() is not None
    assert rendered.getpixel((30, 8)) != (255, 255, 255, 255)


def test_save_keyhole_attempt_debug_image_accepts_current_directory_output(
    tmp_path, monkeypatch
):
    image_path = tmp_path / "keyhole_input.png"
    Image.new("RGBA", (100, 60), (255, 255, 255, 255)).save(image_path)
    monkeypatch.chdir(tmp_path)
    grid = PlateGrid.from_dimensions(
        image_width_px=100, image_height_px=60, target_width_mm=100, plate_size_mm=100
    )

    save_keyhole_attempt_debug_image(
        str(image_path),
        "keyhole_attempts.png",
        [{"row": 0, "positions": [-20.0, 20.0], "strategy": "demo", "failures": []}],
        grid,
    )

    assert (tmp_path / "keyhole_attempts.png").exists()


# --- validate_keyhole_positions ---


def test_validate_positions_ok():
    err = validate_keyhole_positions([-100, 100], model_width=300, tile_h=50)
    assert err is None


def test_validate_positions_past_edge():
    err = validate_keyhole_positions([-150], model_width=300, tile_h=50)
    assert err is not None
    assert "left edge" in err


def test_validate_positions_overlap():
    err = validate_keyhole_positions([-3, 3], model_width=300, tile_h=50)
    assert err is not None
    assert "overlap" in err


def test_validate_positions_too_tall():
    err = validate_keyhole_positions([0], model_width=300, tile_h=10)
    assert err is not None
    assert "height" in err


# --- depths ---


def test_compute_keyhole_depth():
    assert compute_keyhole_depth(4.0) == pytest.approx(2.0)
    assert compute_keyhole_depth(2.0) == pytest.approx(0.0)
    assert compute_keyhole_depth(6.0) == pytest.approx(4.0)


def test_compute_keyhole_depth_rejects_negative_base_height():
    with pytest.raises(ValueError, match="base_height"):
        compute_keyhole_depth(-1.0)


def test_translate_keyhole_position_preserves_tuple_slot_positions():
    assert _translate_keyhole_position((10.0, 20.0), dx=1.5, dy=-2.0) == [
        11.5,
        18.0,
    ]


# --- SCAD generation ---


def test_generate_scad_includes_keyhole_modules(tmp_path):
    scad_path = generate_scad(
        ["color_red.svg"],
        "base.svg",
        img_w=40,
        img_h=20,
        build_dir=str(tmp_path),
    )

    with open(scad_path) as f:
        scad = f.read()

    assert "module keyhole_cutouts()" in scad
    assert "keyhole_positions = [];" in scad
    assert "function keyhole_position_slot_top(position)" in scad
    assert "tile_y0() + tile_h() - keyhole_top_margin" in scad
    assert "module keyhole_outline()" in scad
    assert "keyhole_margin" in scad
    assert "keyhole_cutouts();" in scad


# --- render_stl ---


def test_render_stl_passes_keyhole_positions_to_openscad(tmp_path):
    outpath = tmp_path / "out.stl"

    def fake_run(cmd, **kwargs):
        outpath.write_bytes(b"0" * 100)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with patch(
        "posterize.external_tools.subprocess.run", side_effect=fake_run
    ) as mock_run:
        ok = render_stl(
            "model.scad",
            part=1,
            target_width=500,
            outpath=outpath,
            keyhole_positions=[-203.2, 203.2],
            tools=ExternalTools(openscad="openscad", potrace="potrace"),
        )

    assert ok is True
    cmd = mock_run.call_args.args[0]
    assert "keyhole_enabled=true" in cmd
    assert "keyhole_positions=[-203.2, 203.2]" in cmd


def test_render_stl_no_keyholes(tmp_path):
    outpath = tmp_path / "out.stl"

    def fake_run(cmd, **kwargs):
        outpath.write_bytes(b"0" * 100)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with patch(
        "posterize.external_tools.subprocess.run", side_effect=fake_run
    ) as mock_run:
        render_stl(
            "model.scad",
            part=1,
            target_width=500,
            outpath=outpath,
            tools=ExternalTools(openscad="openscad", potrace="potrace"),
        )

    cmd = mock_run.call_args.args[0]
    assert "keyhole_enabled=false" in cmd
    assert "keyhole_positions=[]" in cmd


# --- CLI ---


def test_keyhole_rejected_with_no_base():
    rc, stderr = run_cli("dummy.png", "--keyhole", "--no-base")

    assert rc != 0
    assert "--keyhole is only available when the base layer is enabled" in stderr


def test_keyhole_rejects_base_height_without_recess_depth(tmp_path):
    image = tmp_path / "image.png"
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(image)

    rc, stderr = run_cli(
        str(image),
        "--keyhole",
        "--no-dowel-holes",
        "--base-height",
        "2.0",
    )

    assert rc != 0
    assert "--keyhole requires --base-height > 2.0mm" in stderr


def test_help_shows_keyhole_flag():
    result = subprocess.run(
        [*CLI, "--help"],
        capture_output=True,
        text=True,
    )

    assert "--keyhole" in result.stdout
