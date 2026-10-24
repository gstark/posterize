"""Tests for model lateral-depth sampling from plate edges."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from posterize.lateral_depth import (
    compute_lateral_depth_at,
    compute_lateral_depth_profile,
)


def test_south_edge_depth_counts_contiguous_material_from_edge():
    mask = [
        [False, True, True, False],
        [False, True, True, False],
        [True, True, False, False],
        [True, True, False, False],
    ]

    assert compute_lateral_depth_at(mask, "south", 0.5) == pytest.approx(2.0)
    assert compute_lateral_depth_at(mask, "south", 1.5) == pytest.approx(4.0)
    assert compute_lateral_depth_at(mask, "south", 2.5) == pytest.approx(0.0)


def test_north_west_and_east_edges_use_image_space_directions():
    mask = [
        [True, True, False, False],
        [True, True, True, False],
        [False, True, True, True],
    ]

    assert compute_lateral_depth_at(mask, "north", 0.5) == pytest.approx(2.0)
    assert compute_lateral_depth_at(mask, "west", 1.5) == pytest.approx(3.0)
    assert compute_lateral_depth_at(mask, "east", 2.5) == pytest.approx(3.0)


def test_depth_can_skip_empty_margin_before_model_silhouette():
    mask = [
        [False, False, False],
        [False, True, False],
        [False, True, False],
    ]

    assert compute_lateral_depth_at(mask, "north", 1.5) == pytest.approx(0.0)
    assert compute_lateral_depth_at(
        mask, "north", 1.5, require_edge_contact=False
    ) == pytest.approx(2.0)


def test_plate_bounds_limit_depth_to_one_plate_region():
    mask = [
        [False, False, False, False],
        [True, True, True, True],
        [True, True, True, True],
        [True, True, True, True],
    ]

    assert compute_lateral_depth_at(
        mask, "south", 0.5, plate_bounds=(1, 1, 3, 4)
    ) == pytest.approx(3.0)


def test_profile_samples_requested_offsets_in_millimeters():
    mask = [
        [False, True, False],
        [True, True, False],
        [True, True, False],
    ]

    profile = compute_lateral_depth_profile(
        mask,
        "bottom",
        mm_per_px=2.0,
        sample_offsets_mm=[1.0, 3.0, 5.0],
    )

    assert profile == pytest.approx([(1.0, 4.0), (3.0, 6.0), (5.0, 0.0)])


def test_profile_defaults_to_pixel_center_samples():
    mask = [[True, False, True]]

    profile = compute_lateral_depth_profile(mask, "top")

    assert profile == pytest.approx([(0.5, 1.0), (1.5, 0.0), (2.5, 1.0)])
