"""Tests for target-size resolution and PlateGrid dimensions."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from posterize.plate_grid import DEFAULT_WIDTH_MM, PlateGrid, resolve_target_width


def test_resolve_target_width_defaults_to_project_default():
    assert resolve_target_width(100, 50) == pytest.approx(DEFAULT_WIDTH_MM)


def test_resolve_target_width_uses_explicit_width():
    assert resolve_target_width(100, 50, width=300) == pytest.approx(300)


def test_resolve_target_width_converts_target_height_to_width():
    assert resolve_target_width(100, 200, height=300) == pytest.approx(150)


def test_resolve_target_width_rejects_width_and_height_together():
    with pytest.raises(ValueError, match="mutually exclusive"):
        resolve_target_width(100, 200, width=300, height=300)


class TestPlateGridDimensions:
    def test_exact_multiple(self):
        """500mm width, 200mm plates on a square image → 3x3 grid."""
        grid = PlateGrid.from_dimensions(
            100, 100, target_width_mm=500, plate_size_mm=200
        )
        assert grid.cols == 3
        assert grid.rows == 3

    def test_single_plate_when_plate_larger_than_model(self):
        """plate_size bigger than model → 1x1."""
        grid = PlateGrid.from_dimensions(
            100, 50, target_width_mm=500, plate_size_mm=1000
        )
        assert grid.rows == 1
        assert grid.cols == 1

    def test_non_square_image(self):
        """100x50 px image at 500mm width → model is 500x250mm."""
        grid = PlateGrid.from_dimensions(
            100, 50, target_width_mm=500, plate_size_mm=200
        )
        # 500/200=2.5 → ceil=3 cols, 250/200=1.25 → ceil=2 rows
        assert grid.cols == 3
        assert grid.rows == 2

    def test_model_dimensions_correct(self):
        """Model width should equal target_width, height proportional."""
        grid = PlateGrid.from_dimensions(
            200, 100, target_width_mm=500, plate_size_mm=200
        )
        assert grid.model_width_mm == pytest.approx(500.0)
        assert grid.model_height_mm == pytest.approx(250.0)

    def test_plate_size_equals_model(self):
        """When plate_size matches model, 1x1 grid."""
        grid = PlateGrid.from_dimensions(
            100, 100, target_width_mm=500, plate_size_mm=500
        )
        assert grid.rows == 1
        assert grid.cols == 1
