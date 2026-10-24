"""Tests for the authoritative PlateGrid geometry core."""

import math
import os
import sys
from unittest.mock import patch

import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from posterize.project_builder import build_3mf
from posterize.plate_grid import PlateGrid
from posterize.threemf import ColorSlot


def test_plate_grid_owns_model_dimensions_and_pixel_bounds_for_uneven_images():
    grid = PlateGrid.from_dimensions(
        image_width_px=101,
        image_height_px=61,
        target_width_mm=505,
        plate_size_mm=200,
    )

    assert grid.cols == 3
    assert grid.rows == 2
    assert grid.model_width_mm == pytest.approx(505)
    assert grid.model_height_mm == pytest.approx(305)
    assert grid.mm_per_px == pytest.approx(5)
    assert grid.x_bounds_px == (0.0, 33.0, 67.0, 101.0)
    assert grid.y_bounds_px == (0.0, 30.0, 61.0)


def test_plate_grid_crops_using_the_same_bounds_it_reports(tmp_path):
    image = Image.new("RGBA", (101, 61), (255, 255, 255, 255))
    image_path = tmp_path / "source.png"
    image.save(image_path)
    grid = PlateGrid.from_dimensions(101, 61, target_width_mm=505, plate_size_mm=200)

    crop_path = grid.crop_image(str(image_path), row=1, col=2, output_dir=str(tmp_path))

    assert os.path.basename(crop_path) == "plate_r2_c3.png"
    assert grid.crop_box(1, 2) == (67, 30, 101, 61)
    assert Image.open(crop_path).size == (34, 31)


def test_plate_grid_converts_pixel_cells_to_model_space_for_scad_placement():
    grid = PlateGrid.from_dimensions(101, 61, target_width_mm=505, plate_size_mm=200)

    placement = grid.scad_plate(row=0, col=2)

    assert placement.x == pytest.approx(82.5)
    assert placement.y == pytest.approx(2.5)
    assert placement.width == pytest.approx(170.0)
    assert placement.height == pytest.approx(150.0)
    assert placement.row == 0
    assert placement.col == 2
    assert placement.label == "r1_c3"


def test_plate_grid_reports_plate_center_from_authoritative_pixel_bounds():
    grid = PlateGrid.from_dimensions(101, 61, target_width_mm=505, plate_size_mm=200)

    center_x, center_y = grid.plate_center_mm(row=0, col=2)

    assert center_x == pytest.approx(167.5)
    assert center_y == pytest.approx(77.5)


def test_plate_grid_assigns_model_x_using_actual_pixel_bounds():
    grid = PlateGrid.from_dimensions(101, 61, target_width_mm=505, plate_size_mm=200)

    assert grid.column_for_model_x(-101.6) == 0
    assert grid.column_for_model_x(101.6) == 2


def test_plate_grid_chooses_enough_cells_to_keep_integer_crops_within_plate_size():
    grid = PlateGrid.from_dimensions(
        image_width_px=5,
        image_height_px=5,
        target_width_mm=10,
        plate_size_mm=3,
    )

    assert grid.cols == 5
    assert grid.rows == 5
    assert max(grid.column_widths_mm()) <= 3
    assert max(grid.row_heights_mm()) <= 3


def test_plate_grid_rejects_invalid_explicit_plate_size():
    with pytest.raises(ValueError, match="plate_size_mm must be positive"):
        PlateGrid.from_grid_dimensions(
            101, 61, target_width_mm=505, rows=2, cols=3, plate_size_mm=0
        )


@pytest.mark.parametrize("bad_value", [math.nan, math.inf])
def test_plate_grid_rejects_non_finite_physical_dimensions(bad_value):
    with pytest.raises(ValueError, match="target_width_mm must be positive and finite"):
        PlateGrid.from_dimensions(10, 10, target_width_mm=bad_value, plate_size_mm=10)

    with pytest.raises(ValueError, match="plate_size_mm must be positive and finite"):
        PlateGrid.from_grid_dimensions(
            10, 10, target_width_mm=10, rows=1, cols=1, plate_size_mm=bad_value
        )


def test_plate_grid_rejects_subpixel_plate_cells_from_plate_size():
    with pytest.raises(ValueError, match="plate_size_mm is too small"):
        PlateGrid.from_dimensions(2, 2, target_width_mm=100, plate_size_mm=49)


def test_plate_grid_uses_actual_integer_cell_size_as_implicit_plate_size():
    grid = PlateGrid.from_grid_dimensions(
        101,
        61,
        target_width_mm=505,
        rows=2,
        cols=3,
    )

    assert grid.plate_size_mm == pytest.approx(170)


def test_plate_grid_rejects_explicit_plate_size_smaller_than_integer_cells():
    with pytest.raises(ValueError, match="plate cells must not exceed plate_size_mm"):
        PlateGrid.from_grid_dimensions(
            101,
            61,
            target_width_mm=505,
            rows=2,
            cols=3,
            plate_size_mm=169,
        )


def test_plate_grid_rejects_more_grid_cells_than_source_pixels():
    with pytest.raises(ValueError, match="must not exceed image dimensions"):
        PlateGrid.from_grid_dimensions(2, 2, target_width_mm=100, rows=1, cols=3)


def test_plate_grid_direct_constructor_rejects_more_cells_than_source_pixels():
    with pytest.raises(ValueError, match="must not exceed image dimensions"):
        PlateGrid(
            image_width_px=1,
            image_height_px=1,
            target_width_mm=10,
            plate_size_mm=10,
            rows=1,
            cols=2,
            model_width_mm=10,
            model_height_mm=10,
            mm_per_px=10,
            x_bounds_px=(0.0, 0.5, 1.0),
            y_bounds_px=(0.0, 1.0),
        )


def test_plate_grid_direct_constructor_rejects_inconsistent_model_scale():
    with pytest.raises(ValueError, match="model_width_mm must equal"):
        PlateGrid(
            image_width_px=10,
            image_height_px=10,
            target_width_mm=20,
            plate_size_mm=20,
            rows=1,
            cols=1,
            model_width_mm=10,
            model_height_mm=20,
            mm_per_px=2,
            x_bounds_px=(0.0, 10.0),
            y_bounds_px=(0.0, 10.0),
        )


def test_plate_grid_direct_constructor_rejects_fractional_pixel_bounds():
    with pytest.raises(ValueError, match="integer pixel boundaries"):
        PlateGrid(
            image_width_px=10,
            image_height_px=10,
            target_width_mm=10,
            plate_size_mm=10,
            rows=1,
            cols=2,
            model_width_mm=10,
            model_height_mm=10,
            mm_per_px=1,
            x_bounds_px=(0.0, 4.5, 10.0),
            y_bounds_px=(0.0, 10.0),
        )


class RecordingPlate:
    def add_stl_group(self, entries):
        self.entries = entries


class RecordingProject:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def add_plate(self, name):
        return RecordingPlate()

    def save(self, output_path):
        self.output_path = output_path


def fake_render_stl(scad_path, part, target_width, outpath, **kwargs):
    return True


def test_build_3mf_uses_plate_grid_model_bounds_for_rendering(tmp_path):
    grid = PlateGrid.from_dimensions(101, 61, target_width_mm=505, plate_size_mm=200)
    plate = grid.scad_plate(row=0, col=2)

    with (
        patch("posterize.project_builder.BambuProject", RecordingProject),
        patch(
            "posterize.project_builder.render_stl", side_effect=fake_render_stl
        ) as render,
    ):
        count = build_3mf(
            scad_path="model.scad",
            output_path=str(tmp_path / "out.3mf"),
            color_slots=[ColorSlot("base", "#000000FF", 1)],
            target_width=grid.model_width_mm,
            plates=[plate.as_build_tuple()],
            tmpdir=str(tmp_path),
        )

    assert count == 1
    render_kwargs = render.call_args.kwargs
    assert render_kwargs["plate_x"] == pytest.approx(plate.x)
    assert render_kwargs["plate_y"] == pytest.approx(plate.y)
    assert render_kwargs["plate_w"] == pytest.approx(plate.width)
    assert render_kwargs["plate_h"] == pytest.approx(plate.height)
