"""Tests for crop_plate_region()."""

import os
import sys

import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from posterize.errors import InputValidationError
from posterize.masks import crop_plate_region


@pytest.fixture
def sample_image(tmp_path):
    """Create a 100x60 RGBA image with unique pixel colors per position."""
    img = Image.new("RGBA", (100, 60))
    pixels = img.load()
    for y in range(60):
        for x in range(100):
            pixels[x, y] = (x % 256, y % 256, (x + y) % 256, 255)
    path = str(tmp_path / "sample.png")
    img.save(path)
    return path


class TestCropDimensions:
    def test_malformed_image_raises_user_facing_error(self, tmp_path):
        bad_image = tmp_path / "bad.png"
        bad_image.write_text("not an image")

        with pytest.raises(InputValidationError, match="cannot read input image"):
            crop_plate_region(str(bad_image), 0, 0, 1, 1, str(tmp_path))

    def test_1x1_grid_returns_original_dimensions(self, sample_image, tmp_path):
        result = crop_plate_region(sample_image, 0, 0, 1, 1, str(tmp_path))
        cropped = Image.open(result)
        assert cropped.size == (100, 60)

    def test_2x2_even_split(self, sample_image, tmp_path):
        crop = crop_plate_region(sample_image, 0, 0, 2, 2, str(tmp_path))
        assert Image.open(crop).size == (50, 30)

    def test_3x3_non_divisible_interior_plate(self, sample_image, tmp_path):
        # 100/3 = 33 per col, 60/3 = 20 per row
        crop = crop_plate_region(sample_image, 0, 0, 3, 3, str(tmp_path))
        assert Image.open(crop).size == (33, 20)

    def test_3x3_last_col_absorbs_remainder(self, sample_image, tmp_path):
        # last col: 100 - 33*2 = 34
        crop = crop_plate_region(sample_image, 0, 2, 3, 3, str(tmp_path))
        assert Image.open(crop).width == 34

    def test_3x3_last_row_absorbs_remainder(self, sample_image, tmp_path):
        # last row: 60 - 20*2 = 20 (happens to be even here)
        crop = crop_plate_region(sample_image, 2, 0, 3, 3, str(tmp_path))
        assert Image.open(crop).height == 20

    def test_non_divisible_last_row_and_col(self, tmp_path):
        """7x5 image in 2x3 grid: last row absorbs remainder height, last col remainder width."""
        img = Image.new("RGBA", (7, 5), (128, 128, 128, 255))
        path = str(tmp_path / "small.png")
        img.save(path)

        # col_width = 7//3 = 2, last col = 7 - 2*2 = 3
        # row_height = 5//2 = 2, last row = 5 - 2*1 = 3
        crop = crop_plate_region(path, 1, 2, 2, 3, str(tmp_path))
        assert Image.open(crop).size == (3, 3)


class TestFullCoverage:
    def test_all_crops_cover_full_image_even(self, tmp_path):
        """Sum of all crop pixel areas equals original for evenly divisible image."""
        img = Image.new("RGBA", (80, 40), (100, 100, 100, 255))
        path = str(tmp_path / "even.png")
        img.save(path)

        rows, cols = 2, 4
        total_pixels = 0
        for r in range(rows):
            for c in range(cols):
                crop = crop_plate_region(path, r, c, rows, cols, str(tmp_path))
                w, h = Image.open(crop).size
                total_pixels += w * h
        assert total_pixels == 80 * 40

    def test_all_crops_cover_full_image_uneven(self, sample_image, tmp_path):
        """Sum of all crop pixel areas equals original for non-divisible image (100x60, 3x3)."""
        rows, cols = 3, 3
        total_pixels = 0
        for r in range(rows):
            for c in range(cols):
                crop = crop_plate_region(sample_image, r, c, rows, cols, str(tmp_path))
                w, h = Image.open(crop).size
                total_pixels += w * h
        assert total_pixels == 100 * 60


class TestPixelContent:
    def test_crop_contains_correct_pixels(self, sample_image, tmp_path):
        """Verify the top-left pixel of plate (1,1) in a 2x2 grid matches source."""
        crop_path = crop_plate_region(sample_image, 1, 1, 2, 2, str(tmp_path))
        cropped = Image.open(crop_path)
        original = Image.open(sample_image)

        # plate (1,1) starts at x=50, y=30
        expected = original.getpixel((50, 30))
        actual = cropped.getpixel((0, 0))
        assert actual == expected


class TestOutputFile:
    def test_returns_valid_path(self, sample_image, tmp_path):
        result = crop_plate_region(sample_image, 0, 0, 2, 2, str(tmp_path))
        assert os.path.isfile(result)

    def test_output_path_uses_one_based_plate_label(self, sample_image, tmp_path):
        result = crop_plate_region(sample_image, 0, 0, 2, 2, str(tmp_path))
        assert os.path.basename(result) == "plate_r1_c1.png"

    def test_output_is_png(self, sample_image, tmp_path):
        result = crop_plate_region(sample_image, 0, 0, 2, 2, str(tmp_path))
        img = Image.open(result)
        assert img.format == "PNG"
