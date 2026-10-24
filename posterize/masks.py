"""Raster mask creation and image cropping helpers."""

from __future__ import annotations

import math
import os
from collections.abc import Sequence
from typing import Any, cast

from PIL import Image

from posterize.color_filament import (
    DEFAULT_ALPHA_THRESHOLD,
    DEFAULT_COLOR_DISTANCE,
    DetectedColor,
)
from posterize.image_io import _crop_image_checked, _open_rgba_image, read_image_size
from posterize.paths import Pathish, as_path
from posterize.plate_grid import PlateGrid


def mask_image_to_pbm(
    mask_image_path: Pathish,
    output_pbm_path: Pathish,
    alpha_threshold: int = DEFAULT_ALPHA_THRESHOLD,
) -> Pathish:
    """Convert a mask image to a 1-bit PBM for potrace."""
    img = _open_rgba_image(mask_image_path, "base mask")
    bw = Image.new("1", img.size, 1)
    px_in = cast(Any, img.load())
    px_out = cast(Any, bw.load())
    w, h = img.size
    for y in range(h):
        for x in range(w):
            if px_in[x, y][3] >= alpha_threshold:
                px_out[x, y] = 0
    bw.save(output_pbm_path, "PPM")
    return output_pbm_path


def crop_plate_region(
    image_path: Pathish, row: int, col: int, rows: int, cols: int, output_dir: Pathish
) -> str:
    """Crop the pixel region for grid cell (row, col) from the source image."""
    w, h = read_image_size(image_path, "input image")
    grid = PlateGrid.from_grid_dimensions(
        image_width_px=w,
        image_height_px=h,
        target_width_mm=float(w),
        rows=rows,
        cols=cols,
    )
    output_path = as_path(output_dir) / f"plate_{grid.label(row, col)}.png"
    return os.fspath(
        _crop_image_checked(
            image_path,
            grid.crop_box(row, col),
            output_path,
            "input image",
        )
    )


def load_opaque_mask(
    image_path: Pathish, alpha_threshold: int = DEFAULT_ALPHA_THRESHOLD
) -> tuple[list[list[bool]], int, int]:
    """Load an image as a boolean opaque-mask matrix."""
    img = _open_rgba_image(image_path, "mask image")
    pixels = cast(Any, img.load())
    w, h = img.size
    mask = []
    for y in range(h):
        row = []
        for x in range(w):
            row.append(pixels[x, y][3] >= alpha_threshold)
        mask.append(row)
    return mask, w, h


def generate_masks(
    image_path: Pathish,
    colors: Sequence[DetectedColor],
    build_dir: Pathish,
    alpha_threshold: int = DEFAULT_ALPHA_THRESHOLD,
    color_distance: float = DEFAULT_COLOR_DISTANCE,
) -> tuple[list[str], str]:
    """Create PBM masks for each color + a base (union) mask.

    Returns (color_pbm_paths, base_pbm_path).
    """
    img = _open_rgba_image(image_path, "input image")
    pixels = cast(Any, img.load())
    w, h = img.size

    # Pre-build mask images (mode '1': 1-bit)
    # Potrace traces BLACK (0) regions, so: init white, set matching to black.
    masks = [Image.new("1", (w, h), 1) for _ in colors]
    mask_pixels = [cast(Any, m.load()) for m in masks]
    base_mask = Image.new("1", (w, h), 1)
    base_px = cast(Any, base_mask.load())

    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            if a < alpha_threshold:
                continue
            base_px[x, y] = 0
            for i, color in enumerate(colors):
                cr, cg, cb = color.rgb.as_tuple()
                dist = math.sqrt((r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2)
                if dist <= color_distance:
                    mask_pixels[i][x, y] = 0
                    break

    build_path = as_path(build_dir)
    color_paths = []
    for i, color in enumerate(colors):
        path = build_path / f"color_{color.hex}.pbm"
        masks[i].save(path, "PPM")
        color_paths.append(os.fspath(path))

    base_path = build_path / "base.pbm"
    base_mask.save(base_path, "PPM")

    return color_paths, os.fspath(base_path)
