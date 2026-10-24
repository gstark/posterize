"""Tests for dowel alignment holes between plates."""

import os
import subprocess
import sys
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from posterize.external_tools import ExternalTools
from posterize.scad_rendering import render_stl
from posterize.dowels import (
    compute_dowel_specs,
    dowel_hole_offsets,
    minimum_dowel_base_height,
)
from posterize.plate_grid import PlateGrid

CLI = [sys.executable, "-m", "posterize"]


def grid_from_edges(edge_widths, edge_heights, mm_per_px=1.0):
    x_bounds = [0.0]
    for width in edge_widths:
        x_bounds.append(x_bounds[-1] + width / mm_per_px)
    y_bounds = [0.0]
    for height in edge_heights:
        y_bounds.append(y_bounds[-1] + height / mm_per_px)

    return PlateGrid(
        image_width_px=round(x_bounds[-1]),
        image_height_px=round(y_bounds[-1]),
        target_width_mm=sum(edge_widths),
        plate_size_mm=max([*edge_widths, *edge_heights]),
        rows=len(edge_heights),
        cols=len(edge_widths),
        model_width_mm=sum(edge_widths),
        model_height_mm=sum(edge_heights),
        mm_per_px=mm_per_px,
        x_bounds_px=tuple(x_bounds),
        y_bounds_px=tuple(y_bounds),
    )


def test_help_shows_force_dowel_holes_flag():
    result = subprocess.run(
        [*CLI, "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert "--force-dowel-holes" in result.stdout


def test_dowel_hole_offsets_for_full_200mm_edge():
    offsets = dowel_hole_offsets(200)

    assert len(offsets) == 18
    assert offsets[:3] == [23.5, 32.5, 41.5]
    assert offsets[-3:] == [158.5, 167.5, 176.5]


def test_dowel_hole_offsets_leave_one_radius_between_adjacent_holes():
    offsets = dowel_hole_offsets(80)

    assert offsets == [22.0, 31.0, 40.0, 49.0, 58.0]


def test_dowel_hole_offsets_leave_corner_clearance_to_prevent_intersections():
    assert dowel_hole_offsets(39.9) == []
    assert dowel_hole_offsets(40.0) == [20.0]


def test_minimum_dowel_base_height_leaves_two_mm_walls():
    assert minimum_dowel_base_height() == 10.0


def test_compute_dowel_specs_mirrors_shared_edges():
    specs = compute_dowel_specs(grid_from_edges([200, 100], [200, 80]))

    assert specs[(0, 0)]["east"] == dowel_hole_offsets(200)
    assert specs[(0, 1)]["west"] == dowel_hole_offsets(200)
    assert specs[(0, 1)]["south"] == dowel_hole_offsets(100)
    assert specs[(1, 1)]["north"] == dowel_hole_offsets(100)


def test_compute_dowel_specs_skips_vertical_seam_holes_without_lateral_depth():
    mask = [[False for _ in range(40)] for _ in range(40)]
    for y in range(18, 23):
        mask[y][19] = True
        mask[y][20] = True

    specs = compute_dowel_specs(grid_from_edges([20, 20], [40]), mask=mask)

    assert specs[(0, 0)]["east"] == []
    assert specs[(0, 1)]["west"] == []


def test_compute_dowel_specs_rejects_hole_when_radius_edge_lacks_depth():
    mask = [[False for _ in range(40)] for _ in range(40)]
    for x in range(5, 35):
        mask[24][x] = True

    specs = compute_dowel_specs(grid_from_edges([20, 20], [40]), mask=mask)

    assert specs[(0, 0)]["east"] == []
    assert specs[(0, 1)]["west"] == []


def test_compute_dowel_specs_can_force_vertical_seam_holes_without_lateral_depth(
    capsys,
):
    mask = [[False for _ in range(40)] for _ in range(40)]
    for y in range(18, 23):
        mask[y][19] = True
        mask[y][20] = True

    specs = compute_dowel_specs(
        grid_from_edges([20, 20], [40]),
        mask=mask,
        force_dowel_holes=True,
    )

    expected_offsets = [20.0]
    assert specs[(0, 0)]["east"] == expected_offsets
    assert specs[(0, 1)]["west"] == expected_offsets
    out = capsys.readouterr().out
    assert (
        "Adding a dowel hole at r1_c1 east / r1_c2 west offset 20mm "
        "even though the depth is only 0mm and we need 15mm"
    ) in out


def test_compute_dowel_specs_keeps_vertical_seam_holes_with_lateral_depth_on_both_sides():
    mask = [[False for _ in range(40)] for _ in range(40)]
    for y in range(40):
        for x in range(5, 35):
            mask[y][x] = True

    specs = compute_dowel_specs(grid_from_edges([20, 20], [40]), mask=mask)

    expected_offsets = [20.0]
    assert specs[(0, 0)]["east"] == expected_offsets
    assert specs[(0, 1)]["west"] == expected_offsets


def test_compute_dowel_specs_skips_horizontal_seam_holes_without_lateral_depth():
    mask = [[False for _ in range(40)] for _ in range(40)]
    for x in range(18, 23):
        mask[19][x] = True
        mask[20][x] = True

    specs = compute_dowel_specs(grid_from_edges([40], [20, 20]), mask=mask)

    assert specs[(0, 0)]["south"] == []
    assert specs[(1, 0)]["north"] == []


def test_compute_dowel_specs_measures_vertical_edge_offsets_from_bottom_for_scad():
    mask = [[False for _ in range(40)] for _ in range(120)]
    for y in range(120):
        for x in range(5, 35):
            mask[y][x] = True
    mask[78][19] = False
    mask[78][20] = False

    specs = compute_dowel_specs(grid_from_edges([20, 20], [120]), mask=mask)

    expected_offsets = [24.0, 33.0, 51.0, 60.0, 69.0, 78.0, 87.0, 96.0]
    assert specs[(0, 0)]["east"] == expected_offsets
    assert specs[(0, 1)]["west"] == expected_offsets


def test_render_stl_passes_dowel_positions_to_openscad(tmp_path):
    out = tmp_path / "out.stl"
    out.write_bytes(b"\0" * 100)

    def fake_openscad(*args, **kwargs):
        out.write_bytes(b"\0" * 100)
        result = subprocess.CompletedProcess(args[0], 0, "", "")
        return result

    with patch(
        "posterize.external_tools.subprocess.run", side_effect=fake_openscad
    ) as run:
        ok = render_stl(
            "model.scad",
            1,
            200,
            str(out),
            dowel_spec={
                "north": [],
                "south": [50.0],
                "east": [50.0, 100.0, 150.0],
                "west": [],
            },
            tools=ExternalTools(openscad="openscad", potrace="potrace"),
        )

    assert ok is True
    cmd = " ".join(run.call_args.args[0])
    assert "dowel_enabled=true" in cmd
    assert "dowel_east_positions=[50.0, 100.0, 150.0]" in cmd
    assert "dowel_south_positions=[50.0]" in cmd


def _run_optimal_colors_cli(tmp_path, *args):
    image = tmp_path / "image.png"
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(image)
    return subprocess.run(
        [*CLI, str(image), "--optimal-filament-colors", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_cli_rejects_low_base_height_when_plates_get_dowels(tmp_path):
    result = _run_optimal_colors_cli(
        tmp_path, "--plate-size", "100", "--base-height", "5"
    )

    assert result.returncode != 0
    assert "dowel holes require --base-height" in result.stderr


def test_cli_allows_low_base_height_without_plate_size(tmp_path):
    result = _run_optimal_colors_cli(tmp_path, "--base-height", "5")

    assert result.returncode == 0, result.stderr
