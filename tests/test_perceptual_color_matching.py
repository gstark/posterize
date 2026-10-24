"""Tests for perceptual filament color matching."""

import os
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from posterize.color_filament import DetectedColor, Filament, RGBColor
from posterize.color_filament import match_filaments, posterize_image, select_filaments


def test_match_filaments_uses_ciede2000_not_lab_euclidean():
    filaments = [
        Filament("Apple Green", "C2E189", RGBColor(194, 225, 137)),
        Filament("Sunflower Yellow", "FEC600", RGBColor(254, 198, 0)),
        Filament("Desert Tan", "E8DBB7", RGBColor(232, 219, 183)),
    ]
    colors = [DetectedColor(RGBColor(214, 185, 98), "D6B962", 1)]

    matched = match_filaments(colors, filaments)

    assert matched[0].filament.name == "Sunflower Yellow"


def test_select_filaments_uses_perceptual_distance(tmp_path):
    filaments = [
        Filament("Apple Green", "C2E189", RGBColor(194, 225, 137)),
        Filament("Sunflower Yellow", "FEC600", RGBColor(254, 198, 0)),
    ]
    image_path = tmp_path / "gold.png"
    Image.new("RGBA", (2, 2), (214, 185, 98, 255)).save(image_path)

    selection = select_filaments(str(image_path), filaments, max_count=1)

    assert selection.selected[0].name == "Sunflower Yellow"
    assert selection.ranked[0].filament_name == "Sunflower Yellow"


def test_posterize_image_uses_perceptual_distance(tmp_path):
    filaments = [
        Filament("Apple Green", "C2E189", RGBColor(194, 225, 137)),
        Filament("Sunflower Yellow", "FEC600", RGBColor(254, 198, 0)),
    ]
    image_path = tmp_path / "gold.png"
    output_path = tmp_path / "posterized.png"
    Image.new("RGBA", (1, 1), (214, 185, 98, 255)).save(image_path)

    posterize_image(str(image_path), filaments, str(output_path))

    pixel = Image.open(output_path).convert("RGBA").getpixel((0, 0))
    assert pixel == (254, 198, 0, 255)
