"""Tests for the plate-offset search that reduces the printed plate count."""

import os
import sys

import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from posterize.cli_impl import CliRunConfig, _apply_plate_offset
from posterize.errors import ExternalToolError
from posterize.external_tools import ExternalTools, discover_external_tools
from posterize.image_io import pad_image_checked
from posterize.masks import mask_image_to_pbm
from posterize.plate_grid import PlateGrid
from posterize.plate_offset import find_best_plate_offset, layout_for_offset
from posterize.scad_rendering import generate_scad, generate_svgs, render_stl


def _mask(width, height, boxes):
    """Return an opaque mask with the given (x0, y0, x1, y1) boxes set."""
    mask = [[False] * width for _ in range(height)]
    for x0, y0, x1, y1 in boxes:
        for y in range(y0, y1):
            for x in range(x0, x1):
                mask[y][x] = True
    return mask


def test_offset_removes_a_column_of_sliver_plates():
    # 100x40 artwork sitting 90 px from the left edge of a 200x40 image.
    # Without padding the artwork straddles the seam at x=100.
    mask = _mask(200, 40, [(90, 0, 190, 40)])

    best, current = find_best_plate_offset(mask, mm_per_px=1.0, plate_size_mm=100.0)

    assert current.occupied_plates == 2
    assert best.occupied_plates == 1
    assert best.pad_x_px == 10
    assert best.canvas_width_px == 300


def test_current_layout_is_kept_when_it_is_already_best():
    mask = _mask(200, 40, [(0, 0, 90, 40)])

    best, current = find_best_plate_offset(mask, mm_per_px=1.0, plate_size_mm=100.0)

    assert current.occupied_plates == 1
    assert not best.is_better_than(current)


def test_search_never_returns_a_worse_layout_than_today():
    # Two blocks that always need two plates; the seam leaves a thin sliver.
    mask = _mask(200, 40, [(95, 0, 105, 40), (150, 0, 200, 40)])

    best, current = find_best_plate_offset(mask, mm_per_px=1.0, plate_size_mm=100.0)

    assert best.quality <= current.quality


def test_offset_removes_a_sliver_without_removing_a_plate():
    # The seam at x=100 shaves a 4x20 px sliver off the left block. Both
    # layouts need two plates, so only the sliver can improve.
    mask = _mask(300, 100, [(20, 20, 104, 40), (110, 20, 190, 80)])

    best, current = find_best_plate_offset(mask, mm_per_px=1.0, plate_size_mm=100.0)

    assert current.occupied_plates == 2
    assert current.parts == 3
    assert current.small_parts == 1
    assert best.occupied_plates == 2
    assert best.parts == 2
    assert best.small_parts == 0


def test_explicit_offset_reports_the_resulting_layout():
    mask = _mask(200, 40, [(90, 0, 190, 40)])

    layout = layout_for_offset(mask, 1.0, 100.0, pad_x_px=10, pad_y_px=0)

    assert layout.occupied_plates == 1
    assert (layout.canvas_width_px, layout.canvas_height_px) == (300, 100)


def test_search_rejects_invalid_inputs():
    mask = _mask(10, 10, [(0, 0, 5, 5)])

    with pytest.raises(ValueError):
        find_best_plate_offset(mask, mm_per_px=0.0, plate_size_mm=100.0)
    with pytest.raises(ValueError):
        find_best_plate_offset(mask, mm_per_px=1.0, plate_size_mm=0.0)
    with pytest.raises(ValueError):
        find_best_plate_offset([], mm_per_px=1.0, plate_size_mm=100.0)


def _cli_config(image, **overrides):
    defaults = dict(
        image=str(image),
        build_dir="build",
        output_dir="output",
        plate_size=100.0,
        target_width=200.0,
        target_height=None,
        base_height=10.0,
        raised_height=2.0,
        base_color="#000000",
        no_base=False,
        base_mask=None,
        alpha_threshold=128,
        color_distance=30.0,
        max_filaments=4,
        filaments=[],
        optimal_filament_colors=False,
        per_plate_palette=True,
        dowel_holes=True,
        force_dowel_holes=False,
        keyhole=False,
        keyhole_stud_spacing=False,
        keyhole_edge_margin_pct=0.25,
        keyhole_offset=0.0,
        keyhole_edge_clearance=3.0,
        tools=ExternalTools(openscad="", potrace=""),
        include_command_metadata=False,
        optimize_plate_offset=True,
        plate_offset=None,
    )
    return CliRunConfig(**{**defaults, **overrides})


def _straddling_image(path):
    """Write a 200x40 image whose artwork straddles the 100 px seam."""
    image = Image.new("RGBA", (200, 40), (0, 0, 0, 0))
    for y in range(40):
        for x in range(90, 190):
            image.putpixel((x, y), (255, 0, 0, 255))
    image.save(path)
    return path


def test_apply_plate_offset_pads_the_image_and_grows_the_target_width(tmp_path):
    image = _straddling_image(tmp_path / "art.png")

    padded = _apply_plate_offset(_cli_config(image), tmp_path / "build")

    assert padded.image != str(image)
    assert Image.open(padded.image).size == (300, 100)
    # 1 mm per pixel is preserved, so the artwork keeps its physical size.
    assert padded.target_width == 300.0


@pytest.mark.parametrize("image_name", ["art_mask.png", "art.png"])
def test_padded_artwork_and_base_mask_keep_distinct_pixels(tmp_path, image_name):
    image = _straddling_image(tmp_path / image_name)
    mask_dir = tmp_path / "masks"
    mask_dir.mkdir()
    mask = mask_dir / "art.png"
    Image.new("RGBA", (200, 40), (0, 255, 0, 255)).save(mask)
    config = _cli_config(image, base_mask=str(mask), plate_offset=(10, 5))

    padded = _apply_plate_offset(config, tmp_path / "build")

    assert padded.image != padded.base_mask
    for source_path, padded_path in [(image, padded.image), (mask, padded.base_mask)]:
        with Image.open(source_path) as source, Image.open(padded_path) as output:
            assert output.size == (300, 100)
            assert output.crop((10, 5, 210, 45)).tobytes() == source.tobytes()
            assert output.getpixel((0, 0))[3] == 0


@pytest.mark.parametrize("transpose", [False, True], ids=["x-seam", "y-seam"])
@pytest.mark.parametrize("mm_per_px", [1.0, 0.37])
def test_edge_aligned_offset_renders_only_the_predicted_plate(
    tmp_path, transpose, mm_per_px
):
    try:
        tools = discover_external_tools()
    except ExternalToolError as exc:
        pytest.skip(str(exc))
    image_path = _straddling_image(tmp_path / "art.png")
    if transpose:
        with Image.open(image_path) as image:
            image.transpose(Image.Transpose.TRANSPOSE).save(image_path)
    width = 40 if transpose else 200
    config = _cli_config(
        image_path,
        target_width=width * mm_per_px,
        plate_size=100 * mm_per_px,
        per_plate_palette=False,
    )
    padded = _apply_plate_offset(config, tmp_path / "build")
    with Image.open(padded.image) as image:
        canvas_w, canvas_h = image.size
    pbm = mask_image_to_pbm(padded.image, tmp_path / "art.pbm")
    svg = generate_svgs([pbm], tmp_path, tools=tools)[0]
    scad = generate_scad([svg], svg, canvas_w, canvas_h, tmp_path)
    grid = PlateGrid.from_dimensions(
        canvas_w, canvas_h, padded.target_width, config.plate_size
    )

    # Exercise the global-palette renderer for both the base and color layer.
    # Even a microscopic sliver in a neighboring cell would create a plate.
    for part in (1, 2):
        occupied = []
        for plate in grid.scad_plates():
            if render_stl(
                scad,
                part,
                padded.target_width,
                tmp_path / f"{part}_{plate.label}.stl",
                plate_x=plate.x,
                plate_y=plate.y,
                plate_w=plate.width,
                plate_h=plate.height,
                tools=tools,
            ):
                occupied.append((plate.row, plate.col))
        assert occupied == ([(1, 0)] if transpose else [(0, 1)])


def test_apply_plate_offset_is_a_no_op_without_plate_size(tmp_path):
    image = _straddling_image(tmp_path / "art.png")
    config = _cli_config(image, plate_size=None)

    assert _apply_plate_offset(config, tmp_path / "build") is config


def test_apply_plate_offset_is_a_no_op_when_not_requested(tmp_path):
    image = _straddling_image(tmp_path / "art.png")
    config = _cli_config(image, optimize_plate_offset=False)

    assert _apply_plate_offset(config, tmp_path / "build") is config


def test_pad_image_shifts_pixels_and_keeps_padding_transparent(tmp_path):
    source = tmp_path / "art.png"
    image = Image.new("RGBA", (4, 3), (255, 0, 0, 255))
    image.save(source)

    output = pad_image_checked(
        source, 5, 2, (12, 8), tmp_path / "padded.png", "input image"
    )

    padded = Image.open(output).convert("RGBA")
    assert padded.size == (12, 8)
    assert padded.getpixel((0, 0))[3] == 0
    assert padded.getpixel((5, 2)) == (255, 0, 0, 255)
