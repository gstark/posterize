"""Behavior tests for the typed color and filament module."""

import math

import pytest
from PIL import Image

from posterize.color_filament import (
    ColorFilamentError,
    DetectedColor,
    DuplicateFilamentAssignmentError,
    Filament,
    RGBColor,
    detect_colors,
    load_filaments,
    match_filaments,
    posterize_image,
    rgb_to_lab,
    select_filaments,
)


def test_rgb_to_lab_uses_srgb_d65_reference_white():
    lab = rgb_to_lab(RGBColor(255, 255, 255))

    assert lab.lightness == pytest.approx(100.0, abs=1e-4)
    assert lab.a == pytest.approx(0.0, abs=1e-3)
    assert lab.b == pytest.approx(0.0, abs=1e-3)


def test_match_filaments_rejects_duplicate_image_color_assignments_by_default():
    filaments = [Filament(name="Red", hex="FF0000", rgb=RGBColor(255, 0, 0))]
    colors = [
        DetectedColor(rgb=RGBColor(255, 0, 0), hex="FF0000", pixel_count=3),
        DetectedColor(rgb=RGBColor(254, 0, 0), hex="FE0000", pixel_count=2),
    ]

    with pytest.raises(DuplicateFilamentAssignmentError) as excinfo:
        match_filaments(colors, filaments)

    message = str(excinfo.value)
    assert "#FE0000" in message
    assert "#FF0000" in message
    assert "Red" in message


def test_match_filaments_allows_duplicate_assignments_when_explicitly_requested():
    filaments = [Filament(name="Red", hex="FF0000", rgb=RGBColor(255, 0, 0))]
    colors = [
        DetectedColor(rgb=RGBColor(255, 0, 0), hex="FF0000", pixel_count=3),
        DetectedColor(rgb=RGBColor(254, 0, 0), hex="FE0000", pixel_count=2),
    ]

    matches = match_filaments(colors, filaments, allow_duplicate_assignments=True)

    assert [match.filament.name for match in matches] == ["Red", "Red"]


def test_load_filaments_returns_typed_filaments_and_rejects_invalid_json(tmp_path):
    filament_json = tmp_path / "filaments.json"
    filament_json.write_text(
        '[{"name":"Apple Green","hex":"C2E189","rgb":[194,225,137]}]',
        encoding="utf-8",
    )

    filaments = load_filaments(filament_json)

    assert filaments == [
        Filament(name="Apple Green", hex="C2E189", rgb=RGBColor(194, 225, 137))
    ]

    bad_json = tmp_path / "bad.json"
    bad_json.write_text('{"name":"not an array"}', encoding="utf-8")

    with pytest.raises(ColorFilamentError, match="non-empty JSON array"):
        load_filaments(bad_json)


def test_load_filaments_rejects_hex_rgb_mismatches(tmp_path):
    filament_json = tmp_path / "filaments.json"
    filament_json.write_text(
        '[{"name":"Not Red","hex":"FF0000","rgb":[0,255,0]}]',
        encoding="utf-8",
    )

    with pytest.raises(ColorFilamentError, match="hex must match rgb"):
        load_filaments(filament_json)


def test_select_filaments_rejects_duplicate_names_without_silently_merging_counts(
    tmp_path,
):
    image_path = tmp_path / "red.png"
    Image.new("RGBA", (1, 1), (255, 0, 0, 255)).save(image_path)
    filaments = [
        Filament(name="Red", hex="FF0000", rgb=RGBColor(255, 0, 0)),
        Filament(name="RED", hex="FE0000", rgb=RGBColor(254, 0, 0)),
    ]

    with pytest.raises(ColorFilamentError, match="duplicates"):
        select_filaments(str(image_path), filaments, max_count=1)


def test_select_filaments_returns_typed_selection_ranked_by_perceptual_distance(
    tmp_path,
):
    image_path = tmp_path / "gold.png"
    Image.new("RGBA", (2, 2), (214, 185, 98, 255)).save(image_path)
    filaments = [
        Filament(name="Apple Green", hex="C2E189", rgb=RGBColor(194, 225, 137)),
        Filament(name="Sunflower Yellow", hex="FEC600", rgb=RGBColor(254, 198, 0)),
    ]

    selection = select_filaments(str(image_path), filaments, max_count=1)

    assert selection.selected == [filaments[1]]
    assert selection.ranked[0].filament_name == "Sunflower Yellow"
    assert selection.ranked[0].pixel_count == 4


def test_color_operations_reject_invalid_alpha_thresholds(tmp_path):
    image_path = tmp_path / "red.png"
    output_path = tmp_path / "posterized.png"
    Image.new("RGBA", (1, 1), (255, 0, 0, 255)).save(image_path)
    filaments = [Filament(name="Red", hex="FF0000", rgb=RGBColor(255, 0, 0))]

    with pytest.raises(ColorFilamentError, match="alpha_threshold"):
        select_filaments(image_path, filaments, max_count=1, alpha_threshold=math.nan)
    with pytest.raises(ColorFilamentError, match="alpha_threshold"):
        posterize_image(image_path, filaments, output_path, alpha_threshold=256)
    with pytest.raises(ColorFilamentError, match="alpha_threshold"):
        detect_colors(image_path, alpha_threshold=True)


def test_detect_colors_rejects_invalid_color_distance_before_silently_splitting(
    tmp_path,
):
    image_path = tmp_path / "red.png"
    Image.new("RGBA", (1, 1), (255, 0, 0, 255)).save(image_path)

    with pytest.raises(ColorFilamentError, match="color_distance"):
        detect_colors(image_path, color_distance=math.nan)


@pytest.mark.parametrize("max_count", [0, 1.5, True])
def test_select_filaments_rejects_invalid_max_count(tmp_path, max_count):
    image_path = tmp_path / "red.png"
    Image.new("RGBA", (1, 1), (255, 0, 0, 255)).save(image_path)
    filaments = [Filament(name="Red", hex="FF0000", rgb=RGBColor(255, 0, 0))]

    with pytest.raises(ColorFilamentError, match="max_filaments"):
        select_filaments(image_path, filaments, max_count=max_count)
