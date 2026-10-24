"""Typed OpenSCAD rendering boundary for posterize models.

This module owns the names and sentinel values shared by SCAD generation and
OpenSCAD rendering.  Keeping them here prevents the Python pipeline and Jinja
SCAD templates from quietly drifting apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

SCAD_ASSETS_DIR = Path(__file__).parent / "assets" / "scad"
SCAD_MODEL_FILENAME = "model.scad"
SCAD_MODEL_TEMPLATE = "model.scad.j2"
SCAD_FRAGMENT_DIR = "fragments"
SCAD_BASE_MODULES_FRAGMENT = "base_modules.scad.j2"
SCAD_BASE_MASK_FOOTPRINT_FRAGMENT = "base_mask_footprint.scad.j2"
SCAD_NO_BASE_COLOR_MODULE_FRAGMENT = "no_base_color_module.scad.j2"

SCAD_ALL_PARTS = 0
SCAD_BASE_PART = 1
SCAD_FIRST_COLOR_PART = 2
SCAD_DOWEL_PART = 8000

# OpenSCAD clips to a tile only when plate_w is positive.  Width/height values
# at or below this sentinel mean "render the whole model" for local per-tile
# SCAD files and direct render_stl callers.
SCAD_UNCLIPPED_PLATE_X = -1
SCAD_UNCLIPPED_PLATE_Y = 0
SCAD_UNCLIPPED_PLATE_WIDTH = 0
SCAD_UNCLIPPED_PLATE_HEIGHT = 0

# The generated SCAD uses a little extra Z height on clipping cubes so boolean
# intersections do not leave coincident faces exactly on the model top.
SCAD_CLIP_Z_SLOP_MM = 1.0

# Preview defaults used only when opening the SCAD directly in OpenSCAD.  Actual
# CLI renders pass explicit plate bounds or the unclipped sentinel above.
SCAD_PREVIEW_PLATE_WIDTH_MM = 200
SCAD_PREVIEW_PLATE_HEIGHT_MM = 200

SCAD_TEMPLATE_ENV = Environment(
    loader=FileSystemLoader(SCAD_ASSETS_DIR),
    undefined=StrictUndefined,
    # SCAD is code, not HTML/XML. Escaping quotes and operators would corrupt
    # generated OpenSCAD expressions, so keep Jinja autoescape disabled here.
    autoescape=False,
    trim_blocks=False,
    lstrip_blocks=False,
)


@dataclass(frozen=True)
class ScadTemplateNames:
    """Template and fragment names needed to render a model SCAD file."""

    model: str = SCAD_MODEL_TEMPLATE
    base_modules: str = SCAD_BASE_MODULES_FRAGMENT
    base_mask_footprint: str = SCAD_BASE_MASK_FOOTPRINT_FRAGMENT
    no_base_color_module: str = SCAD_NO_BASE_COLOR_MODULE_FRAGMENT


@dataclass(frozen=True)
class ScadRenderRequest:
    """Typed input for the SCAD file generation boundary."""

    color_svg_paths: list[str]
    base_svg_path: str
    image_width_px: int
    image_height_px: int
    build_dir: str
    base_height_mm: float
    raised_height_mm: float
    default_width_mm: float
    no_base: bool = False
    keyhole_outline_part: int = 9000
    template_names: ScadTemplateNames = ScadTemplateNames()


def render_template(template_name: str, context: dict[str, Any] | None = None) -> str:
    return SCAD_TEMPLATE_ENV.get_template(template_name).render(**(context or {}))


def render_fragment(fragment_name: str, context: dict[str, Any] | None = None) -> str:
    return render_template(f"{SCAD_FRAGMENT_DIR}/{fragment_name}", context)


def scad_runtime_constants() -> dict[str, float | int]:
    """Constants injected into templates and OpenSCAD command defaults."""

    return {
        "all_parts": SCAD_ALL_PARTS,
        "base_part": SCAD_BASE_PART,
        "first_color_part": SCAD_FIRST_COLOR_PART,
        "unclipped_plate_x": SCAD_UNCLIPPED_PLATE_X,
        "unclipped_plate_y": SCAD_UNCLIPPED_PLATE_Y,
        "unclipped_plate_w": SCAD_UNCLIPPED_PLATE_WIDTH,
        "unclipped_plate_h": SCAD_UNCLIPPED_PLATE_HEIGHT,
        "preview_plate_w": SCAD_PREVIEW_PLATE_WIDTH_MM,
        "preview_plate_h": SCAD_PREVIEW_PLATE_HEIGHT_MM,
        "clip_z_slop_mm": SCAD_CLIP_Z_SLOP_MM,
    }
