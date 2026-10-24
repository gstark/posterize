"""Vectorization, OpenSCAD generation, and STL rendering."""

from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from collections.abc import Iterable, Sequence

from posterize.dowels import (
    DOWEL_CHAMFER_MM,
    DOWEL_DIAMETER_MM,
    DOWEL_HOLE_CHAMFER_MM,
    DOWEL_HOLE_DEPTH_MM,
    DOWEL_HOLE_DIAMETER_MM,
    DOWEL_LENGTH_MM,
    DOWEL_MIN_WALL_MM,
    DOWEL_PRINT_FLAT_DEPTH_MM,
    DOWEL_RIB_HEIGHT_MM,
    DOWEL_RIB_SPACING_MM,
    DOWEL_RIB_WIDTH_MM,
    DowelSpec,
    _empty_dowel_spec,
)
from posterize.errors import ExternalToolError
from posterize.external_tools import (
    OPENSCAD,
    OPENSCAD_ENV_VAR,
    POTRACE,
    POTRACE_ENV_VAR,
    ExternalTools,
    _external_tool_message,
    _resolve_tool,
    _run_external_command,
)
from posterize.keyholes import (
    KEYHOLE_HEAD_DIAMETER_MM,
    KEYHOLE_MARGIN_MM,
    KEYHOLE_NECK_LENGTH_MM,
    KEYHOLE_NECK_WIDTH_MM,
    KEYHOLE_OUTLINE_PART,
    KEYHOLE_OUTLINE_THICKNESS_MM,
    KEYHOLE_OUTLINE_WIDTH_MM,
    KEYHOLE_TOP_MARGIN_MM,
    KeyholePosition,
    compute_keyhole_depth,
)
from posterize.paths import Pathish, as_path as _as_path
from posterize.plate_grid import DEFAULT_WIDTH_MM
from posterize.scad import (
    SCAD_DOWEL_PART,
    SCAD_MODEL_FILENAME,
    SCAD_UNCLIPPED_PLATE_HEIGHT,
    SCAD_UNCLIPPED_PLATE_WIDTH,
    SCAD_UNCLIPPED_PLATE_X,
    SCAD_UNCLIPPED_PLATE_Y,
    ScadRenderRequest,
    render_fragment as _render_scad_fragment,
    render_template as _render_scad_template,
    scad_runtime_constants,
)
from posterize.threemf import PackageMetadata

DEFAULT_BASE_HEIGHT_MM = 10.0


def generate_svgs(
    pbm_paths: Iterable[Pathish], build_dir: Pathish, tools: ExternalTools | None = None
) -> list[str]:
    """Run potrace on each PBM → SVG. Returns list of SVG paths."""
    tools = tools or ExternalTools(
        openscad=OPENSCAD,
        potrace=_resolve_tool(POTRACE, "--potrace", POTRACE_ENV_VAR),
    )
    build_path = _as_path(build_dir)
    svg_paths = []
    for pbm in pbm_paths:
        pbm_path = _as_path(pbm)
        svg = build_path / f"{pbm_path.stem}.svg"
        cmd = [tools.potrace, "-s", "-o", os.fspath(svg), os.fspath(pbm_path)]
        result = _run_external_command("potrace", cmd, svg)
        if not svg.exists():
            raise ExternalToolError(
                _external_tool_message(
                    f"potrace completed but did not create expected SVG: {svg}",
                    cmd,
                    result,
                )
            )
        # Import in pixel coordinates. Potrace's default pt units introduce a
        # fractional mm conversion that OpenSCAD quantizes before model scaling,
        # pushing otherwise aligned edges across plate seams.
        try:
            tree = ET.parse(svg)
            root = tree.getroot()
            _, _, width, height = root.attrib["viewBox"].split()
            root.set("width", f"{width}mm")
            root.set("height", f"{height}mm")
            ET.register_namespace("", "http://www.w3.org/2000/svg")
            tree.write(svg, encoding="unicode")
        except (ET.ParseError, KeyError, ValueError, OSError) as exc:
            raise ExternalToolError(
                _external_tool_message(
                    f"cannot set pixel units for potrace SVG {svg}: {exc}", cmd, result
                )
            ) from None
        svg_paths.append(os.fspath(svg))
    return svg_paths


def generate_scad(
    color_svgs: Iterable[Pathish],
    base_svg: Pathish,
    img_w: int,
    img_h: int,
    build_dir: Pathish,
    base_height: float = DEFAULT_BASE_HEIGHT_MM,
    raised_height: float = 2.0,
    no_base: bool = False,
) -> str:
    """Generate a parametric OpenSCAD file. Returns path to .scad.

    SVGs must use the one-mm-per-pixel units written by generate_svgs. The base
    is generated as a full solid support under the colors from z=0 to z=base_height.
    """
    request = ScadRenderRequest(
        color_svg_paths=[os.fspath(svg) for svg in color_svgs],
        base_svg_path=os.fspath(base_svg),
        image_width_px=img_w,
        image_height_px=img_h,
        build_dir=os.fspath(build_dir),
        base_height_mm=base_height,
        raised_height_mm=raised_height,
        default_width_mm=DEFAULT_WIDTH_MM,
        no_base=no_base,
        keyhole_outline_part=KEYHOLE_OUTLINE_PART,
    )
    build_path = _as_path(request.build_dir)
    # generate_svgs writes one mm per source pixel, matching PlateGrid's axes.
    raw_w = request.image_width_px
    raw_h = request.image_height_px
    raw_cx = raw_w / 2
    raw_cy = raw_h / 2
    keyhole_depth = compute_keyhole_depth(request.base_height_mm)

    color_svg_names = [Path(svg).name for svg in request.color_svg_paths]
    runtime_constants = scad_runtime_constants()

    if request.no_base:
        base_modules = ""
        color_module = _render_scad_fragment(
            request.template_names.no_base_color_module,
            runtime_constants,
        )
    else:
        base_footprint_module = _render_scad_fragment(
            request.template_names.base_mask_footprint,
            {"base_svg_name": Path(request.base_svg_path).name},
        )
        base_modules = _render_scad_fragment(
            request.template_names.base_modules,
            {"base_footprint_module": base_footprint_module, **runtime_constants},
        )
        color_module = ""

    scad = _render_scad_template(
        request.template_names.model,
        {
            "default_width_mm": request.default_width_mm,
            "base_height": request.base_height_mm,
            "raised_height": request.raised_height_mm,
            "raw_cx": raw_cx,
            "raw_cy": raw_cy,
            "raw_width": raw_w,
            "raw_height": raw_h,
            "keyhole_top_margin_mm": KEYHOLE_TOP_MARGIN_MM,
            "keyhole_head_diameter_mm": KEYHOLE_HEAD_DIAMETER_MM,
            "keyhole_neck_width_mm": KEYHOLE_NECK_WIDTH_MM,
            "keyhole_neck_length_mm": KEYHOLE_NECK_LENGTH_MM,
            "keyhole_depth": keyhole_depth,
            "keyhole_margin_mm": KEYHOLE_MARGIN_MM,
            "keyhole_outline_part": request.keyhole_outline_part,
            "keyhole_outline_width_mm": KEYHOLE_OUTLINE_WIDTH_MM,
            "keyhole_outline_thickness_mm": KEYHOLE_OUTLINE_THICKNESS_MM,
            "dowel_hole_depth_mm": DOWEL_HOLE_DEPTH_MM,
            "dowel_hole_diameter_mm": DOWEL_HOLE_DIAMETER_MM,
            "dowel_hole_chamfer_mm": DOWEL_HOLE_CHAMFER_MM,
            "dowel_min_wall_mm": DOWEL_MIN_WALL_MM,
            "dowel_part": SCAD_DOWEL_PART,
            "dowel_diameter_mm": DOWEL_DIAMETER_MM,
            "dowel_length_mm": DOWEL_LENGTH_MM,
            "dowel_chamfer_mm": DOWEL_CHAMFER_MM,
            "dowel_rib_height_mm": DOWEL_RIB_HEIGHT_MM,
            "dowel_rib_width_mm": DOWEL_RIB_WIDTH_MM,
            "dowel_rib_spacing_mm": DOWEL_RIB_SPACING_MM,
            "dowel_print_flat_depth_mm": DOWEL_PRINT_FLAT_DEPTH_MM,
            "base_modules": base_modules,
            "color_module": color_module,
            "no_base": request.no_base,
            **runtime_constants,
            "color_svg_names": color_svg_names,
        },
    )
    scad_path = build_path / SCAD_MODEL_FILENAME
    scad_path.write_text(scad)
    return os.fspath(scad_path)


def render_stl(
    scad_path: Pathish,
    part: int,
    target_width: float,
    outpath: Pathish,
    plate_x: float = SCAD_UNCLIPPED_PLATE_X,
    plate_y: float = SCAD_UNCLIPPED_PLATE_Y,
    plate_w: float = SCAD_UNCLIPPED_PLATE_WIDTH,
    plate_h: float = SCAD_UNCLIPPED_PLATE_HEIGHT,
    keyhole_positions: Sequence[KeyholePosition] | None = None,
    dowel_spec: DowelSpec | None = None,
    tools: ExternalTools | None = None,
) -> bool:
    """Render one SCAD part to binary STL with explicit clipping parameters.

    ``part`` follows the SCAD template convention: 1 is the base, 2+ are color
    layers in slot order, and ``KEYHOLE_OUTLINE_PART`` renders the optional
    printed outline. ``plate_x``/``plate_y``/``plate_w``/``plate_h`` are model
    coordinates in millimeters; a non-positive width/height is the documented
    unclipped sentinel used for per-plate local SCAD files.
    """

    tools = tools or ExternalTools(
        openscad=_resolve_tool(OPENSCAD, "--openscad", OPENSCAD_ENV_VAR),
        potrace=POTRACE,
    )
    scad_file = _as_path(scad_path)
    output_file = _as_path(outpath)
    positions = keyhole_positions or []
    dowels = dowel_spec or _empty_dowel_spec()
    cmd = [
        tools.openscad,
        os.fspath(scad_file),
        "-o",
        os.fspath(output_file),
        "--export-format",
        "binstl",
        "-D",
        f"part={part}",
        "-D",
        f"target_width={target_width}",
        "-D",
        f"plate_x={plate_x}",
        "-D",
        f"plate_y={plate_y}",
        "-D",
        f"plate_w={plate_w}",
        "-D",
        f"plate_h={plate_h}",
        "-D",
        f"keyhole_enabled={json.dumps(len(positions) > 0)}",
        "-D",
        f"keyhole_positions={json.dumps(positions)}",
        "-D",
        f"dowel_enabled={json.dumps(any(dowels[edge] for edge in ('north', 'south', 'east', 'west')))}",
        "-D",
        f"dowel_north_positions={json.dumps(dowels['north'])}",
        "-D",
        f"dowel_south_positions={json.dumps(dowels['south'])}",
        "-D",
        f"dowel_east_positions={json.dumps(dowels['east'])}",
        "-D",
        f"dowel_west_positions={json.dumps(dowels['west'])}",
    ]
    result = _run_external_command("openscad", cmd, output_file, empty_output_ok=True)
    stderr = getattr(result, "stderr", "") or ""
    if "empty" in stderr.lower():
        return False
    if not output_file.exists():
        raise ExternalToolError(
            _external_tool_message(
                f"openscad completed but did not create expected STL: {output_file}",
                cmd,
                result,
            )
        )
    try:
        output_size = output_file.stat().st_size
    except OSError as exc:
        raise ExternalToolError(
            f"cannot inspect openscad output {output_file}: {exc}"
        ) from None
    return output_size > 84


def format_3mf_description(
    command_line: list[str] | None,
    include_command_metadata: bool = False,
) -> str | None:
    """Convert argv into 3MF description text only after explicit opt-in."""
    return PackageMetadata(
        command_line=command_line,
        include_command_metadata=include_command_metadata,
    ).description()
