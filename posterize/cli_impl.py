"""Command-line interface for the posterize pipeline."""

from __future__ import annotations

import argparse
import math
import os
import sys
from dataclasses import dataclass, replace
from collections.abc import Mapping
from typing import NoReturn

from posterize.color_filament import (
    DEFAULT_ALPHA_THRESHOLD,
    DEFAULT_COLOR_DISTANCE,
    ColorFilamentError,
    DetectedColor,
    Filament,
    detect_colors,
    load_filaments,
)
from posterize.dowels import (
    DOWEL_MIN_WALL_MM,
    compute_dowel_specs,
    minimum_dowel_base_height,
)
from posterize.config import (
    ConfigValue,
    config_path,
    default_filaments_path,
    load_config,
)
from posterize.errors import InputValidationError, UserFacingError
from posterize.external_tools import (
    OPENSCAD_ENV_VAR,
    POTRACE_ENV_VAR,
    ExternalTools,
    ToolOverrides,
    discover_external_tools,
)
from posterize.image_io import (
    pad_image_checked,
    read_image_size,
    validate_base_mask_size,
)
from posterize.keyholes import (
    DEFAULT_KEYHOLE_EDGE_CLEARANCE_MM,
    DEFAULT_KEYHOLE_OFFSET_MM,
    KEYHOLE_EDGE_MARGIN_PCT,
    KEYHOLE_MARGIN_MM,
    format_keyhole_selection_diagnostics,
    save_keyhole_attempt_debug_image,
    select_keyhole_positions,
)
from posterize.formatting import _hex_payload
from posterize.masks import load_opaque_mask
from posterize.paths import Pathish, as_path as _as_path
from posterize.plate_grid import DEFAULT_WIDTH_MM, PlateGrid, resolve_target_width
from posterize.plate_offset import (
    PlateLayout,
    find_best_plate_offset,
    layout_for_offset,
)

from posterize.project_builder import build_3mf, build_dowel_3mf
from posterize.scad_rendering import DEFAULT_BASE_HEIGHT_MM
from posterize.pipeline_runner import (
    DEFAULT_BASE_COLOR,
    DEFAULT_DOWEL_HOLES,
    PipelineConfig,
    PipelineRunner,
)


@dataclass(frozen=True)
class CliRunConfig:
    """Validated, non-mutating view of parsed CLI options."""

    image: str
    build_dir: str
    output_dir: str
    plate_size: float | None
    target_width: float
    target_height: float | None
    base_height: float
    raised_height: float
    base_color: str
    no_base: bool
    base_mask: str | None
    alpha_threshold: int
    color_distance: float
    max_filaments: int
    filaments: list[Filament]
    optimal_filament_colors: bool
    per_plate_palette: bool
    dowel_holes: bool
    force_dowel_holes: bool
    keyhole: bool
    keyhole_stud_spacing: bool
    keyhole_edge_margin_pct: float
    keyhole_offset: float
    keyhole_edge_clearance: float
    tools: ExternalTools
    include_command_metadata: bool
    optimize_plate_offset: bool
    plate_offset: tuple[int, int] | None


def build_parser(
    parser_class: type[argparse.ArgumentParser] = argparse.ArgumentParser,
    config: Mapping[str, ConfigValue] | None = None,
) -> argparse.ArgumentParser:
    """Build the CLI parser. ``config`` values replace the built-in defaults."""

    config = config or {}
    default_width = float(config.get("default_width", DEFAULT_WIDTH_MM))
    parser = parser_class(
        description=(
            "Convert a flat-color PNG to multi-color 3MF files. "
            "Run with no arguments to open the interactive wizard. "
            f"Run 'posterize config' to change the defaults in {config_path()}."
        )
    )
    parser.add_argument("image", help="Input PNG file")
    size_group = parser.add_mutually_exclusive_group()
    size_group.add_argument(
        "--width",
        type=float,
        default=None,
        help=(
            f"Target width in mm (default: {default_width:g} when neither "
            "--width nor --height is set)"
        ),
    )
    size_group.add_argument(
        "--height", type=float, default=None, help="Target height in mm"
    )
    plate_group = parser.add_mutually_exclusive_group()
    plate_group.add_argument(
        "--plate-size",
        type=float,
        default=None,
        help=(
            "Plate size in mm (default: "
            f"{_plate_size_default_text(config.get('plate_size'))})"
        ),
    )
    plate_group.add_argument(
        "--single-plate",
        action="store_true",
        help="Print one plate, ignoring a plate size set in the config file",
    )
    parser.add_argument(
        "--base-height",
        type=float,
        default=DEFAULT_BASE_HEIGHT_MM,
        help="Base plate thickness in mm (default: %(default).0f)",
    )
    parser.add_argument(
        "--raised-height",
        type=float,
        default=2.0,
        help=(
            "Color layer height above the supported top surface in mm "
            "(default: %(default)g)"
        ),
    )
    parser.add_argument(
        "--base-color",
        default=DEFAULT_BASE_COLOR,
        help="Base plate hex color (default: %(default)s)",
    )
    parser.add_argument(
        "--no-base", action="store_true", help="Omit the base plate (colors only)"
    )
    parser.add_argument(
        "--base-mask",
        help="Image mask for base layer shape "
        "(opaque pixels = base, transparent = no base)",
    )
    parser.add_argument(
        "--alpha-threshold",
        type=int,
        default=DEFAULT_ALPHA_THRESHOLD,
        help="Min alpha to consider a pixel opaque (default: %(default)s)",
    )
    parser.add_argument(
        "--color-distance",
        type=float,
        default=DEFAULT_COLOR_DISTANCE,
        help="Max RGB distance for color clustering (default: %(default)s)",
    )
    parser.add_argument(
        "--max-filaments",
        type=int,
        default=4,
        help="Max filament colors to use (default: %(default)s)",
    )
    parser.add_argument(
        "--filaments",
        default=str(default_filaments_path()),
        help="Filament library JSON (default: %(default)s)",
    )
    parser.add_argument(
        "--optimal-filament-colors",
        action="store_true",
        help=(
            "Print the best source image colors to buy as filament and exit; "
            "does not read --filaments or generate model files"
        ),
    )
    parser.add_argument(
        "--per-plate-palette",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Select filaments per plate instead of globally (requires --plate-size)",
    )
    offset_group = parser.add_mutually_exclusive_group()
    offset_group.add_argument(
        "--optimize-plate-offset",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Search for a transparent padding offset that prints fewer plates "
            "and fewer small parts (requires --plate-size)"
        ),
    )
    offset_group.add_argument(
        "--plate-offset",
        default=None,
        help=(
            "Shift the image by X,Y pixels of transparent padding before "
            "splitting it into plates (requires --plate-size)"
        ),
    )
    parser.add_argument(
        "--dowel-holes",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_DOWEL_HOLES,
        help="Add 6mm dowel alignment holes on shared plate edges",
    )
    parser.add_argument(
        "--force-dowel-holes",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Keep candidate dowel holes even when the base "
        "mask depth check says there is not enough "
        "material, printing a warning for each forced "
        "hole",
    )
    parser.add_argument(
        "--keyhole",
        action="store_true",
        help="Add back-side wall-mount keyholes to the top-left and top-right plates",
    )
    parser.add_argument(
        "--wall",
        action="store_true",
        help="Wall-mount preset: enables --keyhole and --keyhole-stud-spacing",
    )
    parser.add_argument(
        "--keyhole-inset",
        type=float,
        default=KEYHOLE_EDGE_MARGIN_PCT * 100,
        help="Keyhole inset from plate edge as a percentage "
        "of plate width; only used without "
        "--keyhole-stud-spacing (default: %(default).0f)",
    )
    parser.add_argument(
        "--keyhole-stud-spacing",
        action="store_true",
        help="Place keyholes at 16-inch stud spacing "
        "instead of edge-margin positioning",
    )
    parser.add_argument(
        "--keyhole-offset",
        type=float,
        default=DEFAULT_KEYHOLE_OFFSET_MM,
        help="Shift keyhole positions in mm (positive = "
        "rightward) to move off plate seams",
    )
    parser.add_argument(
        "--keyhole-edge-clearance",
        type=float,
        default=DEFAULT_KEYHOLE_EDGE_CLEARANCE_MM,
        help="Minimum extra base material to preserve around "
        "the keyhole footprint in mm (default: %(default).1f)",
    )
    parser.add_argument(
        "--build-dir",
        default="build",
        help="Directory for intermediate files (default: %(default)s)",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory for 3MF output (default: %(default)s)",
    )
    parser.add_argument(
        "--openscad",
        help=f"OpenSCAD executable override (or set {OPENSCAD_ENV_VAR})",
    )
    parser.add_argument(
        "--potrace",
        help=f"potrace executable override (or set {POTRACE_ENV_VAR})",
    )
    parser.add_argument(
        "--command-metadata",
        dest="include_command_metadata",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Store the full invoking command line in 3MF description metadata; "
            "off by default because arguments may contain secrets"
        ),
    )
    parser.set_defaults(**{**config, "default_width": default_width})
    return parser


def _plate_size_default_text(value: object) -> str:
    return f"{value:g}" if isinstance(value, float) else "single plate"


def effective_defaults(config: Mapping[str, ConfigValue]) -> dict[str, object]:
    """Return every option's default after applying ``config``."""

    return vars(build_parser(config=config).parse_args(["image.png"]))


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def main(argv: list[str] | None = None) -> None:
    raw_args = sys.argv[1:] if argv is None else argv
    if raw_args[:1] == ["config"]:
        _config_command(raw_args[1:])
        return
    try:
        config = load_config()
    except UserFacingError as exc:
        sys.exit(f"error: {exc}")

    if not raw_args and _interactive():
        from posterize.wizard import run_wizard

        chosen = run_wizard(config)
        if chosen is None:
            return
        raw_args = chosen

    parser = build_parser(config=config)
    args = parser.parse_args(raw_args)
    command_line = [sys.argv[0], *raw_args]
    try:
        _run_cli(parser, args, command_line)
    except (UserFacingError, ColorFilamentError) as exc:
        parser.exit(2, f"error: {exc}\n")


class _RaisingParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise InputValidationError(message)


def _config_command(args: list[str]) -> None:
    """Edit the config file, or print it when there is no terminal."""

    if args:
        sys.exit("usage: posterize config")
    path = config_path()
    if _interactive():
        from posterize.config_tui import run_config_editor

        run_config_editor(path)
        return
    print(f"# {path}")
    try:
        print(path.read_text(encoding="utf-8"), end="")
    except FileNotFoundError:
        print("# No config file. Built-in defaults apply.")


def check_arguments(
    argv: list[str], config: Mapping[str, ConfigValue] | None = None
) -> str | None:
    """Return the first validation error for ``argv``, or None when it is valid.

    This runs the same checks as a real run (files, filament library, tools)
    without generating any output.
    """

    parser = build_parser(_RaisingParser, config)
    try:
        _validated_cli_config(parser, parser.parse_args(argv))
    except (UserFacingError, ColorFilamentError) as exc:
        return str(exc)
    return None


def _validated_cli_config(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> CliRunConfig:
    """Validate argparse output and return derived options without mutating it."""

    keyhole = bool(args.keyhole or args.wall)
    keyhole_stud_spacing = bool(args.keyhole_stud_spacing or args.wall)
    optimal_filament_colors = bool(getattr(args, "optimal_filament_colors", False))

    if args.width is not None and (not math.isfinite(args.width) or args.width <= 0):
        parser.error("--width must be positive and finite")
    if args.height is not None and (not math.isfinite(args.height) or args.height <= 0):
        parser.error("--height must be positive and finite")
    if args.plate_size is not None and (
        not math.isfinite(args.plate_size) or args.plate_size <= 0
    ):
        parser.error("--plate-size must be positive and finite")
    if not math.isfinite(args.base_height) or args.base_height <= 0:
        parser.error("--base-height must be positive and finite")
    if not math.isfinite(args.raised_height) or args.raised_height <= 0:
        parser.error("--raised-height must be positive and finite")
    if not 0 <= args.alpha_threshold <= 255:
        parser.error("--alpha-threshold must be between 0 and 255")
    if not math.isfinite(args.color_distance) or args.color_distance < 0:
        parser.error("--color-distance must be non-negative and finite")
    if args.max_filaments <= 0:
        parser.error("--max-filaments must be positive")
    try:
        base_color = (
            f"#{_hex_payload(args.base_color, '--base-color', allow_hash=True)}"
        )
    except InputValidationError as exc:
        parser.error(str(exc))
    if not math.isfinite(args.keyhole_inset) or args.keyhole_inset < 0:
        parser.error("--keyhole-inset must be non-negative and finite")
    if not math.isfinite(args.keyhole_offset):
        parser.error("--keyhole-offset must be finite")
    if (
        not math.isfinite(args.keyhole_edge_clearance)
        or args.keyhole_edge_clearance < 0
    ):
        parser.error("--keyhole-edge-clearance must be non-negative and finite")

    plate_size = None if args.single_plate else args.plate_size
    # Plate-only switches turned on in the config file do not apply to a
    # single-plate run; the same switches given on the command line do.
    per_plate_palette = args.per_plate_palette and not (
        plate_size is None and parser.get_default("per_plate_palette")
    )
    optimize_plate_offset = args.optimize_plate_offset and not (
        plate_size is None and parser.get_default("optimize_plate_offset")
    )
    if per_plate_palette and plate_size is None:
        parser.error("--per-plate-palette requires --plate-size")
    plate_offset = _parse_plate_offset(parser, args.plate_offset)
    if (optimize_plate_offset or plate_offset) and plate_size is None:
        parser.error("--optimize-plate-offset and --plate-offset require --plate-size")
    if (
        args.dowel_holes
        and not args.no_base
        and plate_size is not None
        and args.base_height < minimum_dowel_base_height()
    ):
        parser.error(
            f"dowel holes require --base-height >= {minimum_dowel_base_height():.1f}mm "
            f"to leave {DOWEL_MIN_WALL_MM:.1f}mm walls around each hole"
        )
    if keyhole and args.no_base:
        parser.error("--keyhole is only available when the base layer is enabled")
    min_keyhole_base_height = 2 * KEYHOLE_MARGIN_MM
    if keyhole and args.base_height <= min_keyhole_base_height:
        parser.error(
            f"--keyhole requires --base-height > {min_keyhole_base_height:.1f}mm "
            f"to leave {KEYHOLE_MARGIN_MM:.1f}mm of material behind the recess"
        )

    img_size = read_image_size(args.image, "input image")
    if args.base_mask:
        validate_base_mask_size(args.image, args.base_mask)
    target_width = resolve_target_width(
        img_size[0],
        img_size[1],
        width=args.width,
        height=args.height,
        default_width=args.default_width,
    )
    if plate_size is not None:
        try:
            PlateGrid.from_dimensions(
                img_size[0], img_size[1], target_width, plate_size
            )
        except ValueError as exc:
            parser.error(f"--plate-size invalid: {exc}")

    return CliRunConfig(
        image=args.image,
        build_dir=args.build_dir,
        output_dir=args.output_dir,
        plate_size=plate_size,
        target_width=target_width,
        target_height=args.height,
        base_height=args.base_height,
        raised_height=args.raised_height,
        base_color=base_color,
        no_base=args.no_base,
        base_mask=args.base_mask,
        alpha_threshold=args.alpha_threshold,
        color_distance=args.color_distance,
        max_filaments=args.max_filaments,
        filaments=[] if optimal_filament_colors else _load_library(args.filaments),
        optimal_filament_colors=optimal_filament_colors,
        per_plate_palette=per_plate_palette,
        dowel_holes=args.dowel_holes,
        force_dowel_holes=args.force_dowel_holes,
        keyhole=keyhole,
        keyhole_stud_spacing=keyhole_stud_spacing,
        keyhole_edge_margin_pct=args.keyhole_inset / 100.0,
        keyhole_offset=args.keyhole_offset,
        keyhole_edge_clearance=args.keyhole_edge_clearance,
        tools=(
            ExternalTools(openscad="", potrace="")
            if optimal_filament_colors
            else discover_external_tools(ToolOverrides(args.openscad, args.potrace))
        ),
        include_command_metadata=args.include_command_metadata,
        optimize_plate_offset=optimize_plate_offset,
        plate_offset=plate_offset,
    )


def _load_library(path: str) -> list[Filament]:
    """Load the filament library, pointing at the editor when it is missing."""

    if not os.path.exists(path):
        raise ColorFilamentError(
            f"no filament library at {path}. Run 'posterize config' to create "
            "one, or pass --filaments"
        )
    return load_filaments(path)


def _parse_plate_offset(
    parser: argparse.ArgumentParser, raw: str | None
) -> tuple[int, int] | None:
    """Parse an ``X,Y`` pixel offset argument."""

    if raw is None:
        return None
    parts = raw.split(",")
    if len(parts) != 2:
        parser.error("--plate-offset must be given as X,Y in pixels")
    try:
        pad_x, pad_y = (int(part.strip()) for part in parts)
    except ValueError:
        parser.error("--plate-offset must contain two whole numbers of pixels")
    if pad_x < 0 or pad_y < 0:
        parser.error("--plate-offset values must be non-negative")
    return (pad_x, pad_y)


def _resolve_plate_layout(
    config: CliRunConfig, plate_size: float, mm_per_px: float
) -> PlateLayout | None:
    """Return the padded layout to apply, searching for it when asked to."""

    mask_source = config.base_mask or config.image
    mask, img_w, img_h = load_opaque_mask(mask_source, config.alpha_threshold)

    if config.plate_offset is not None:
        pad_x, pad_y = config.plate_offset
        chosen = layout_for_offset(mask, mm_per_px, plate_size, pad_x, pad_y)
        print(f"Plate offset ({pad_x}, {pad_y}) px: {chosen.describe()}")
        return chosen

    print("Searching for a plate offset that prints fewer parts...")
    best, current = find_best_plate_offset(mask, mm_per_px, plate_size)
    print(f"  Current: {current.describe()}")
    if not best.is_better_than(current):
        print("  No better offset found; keeping the current layout")
        return None
    print(f"  Offset ({best.pad_x_px}, {best.pad_y_px}) px: {best.describe()}")
    _print_offset_gain(best, current)
    return best


def _print_offset_gain(best: PlateLayout, current: PlateLayout) -> None:
    """Print what the chosen offset saves against the current layout."""

    saved_plates = current.occupied_plates - best.occupied_plates
    saved_parts = current.parts - best.parts
    saved_small = current.small_parts - best.small_parts
    if saved_plates > 0:
        print(f"  Saves {saved_plates} plate(s)")
    if saved_parts > 0:
        print(f"  Saves {saved_parts} part(s)")
    if saved_small > 0:
        print(f"  Removes {saved_small} small part(s)")


def _apply_plate_offset(config: CliRunConfig, build_dir: Pathish) -> CliRunConfig:
    """Return a config whose image and mask are padded to a better grid origin.

    The padding is transparent and the target width grows with the canvas, so
    the artwork keeps its physical size and only the plate seams move.
    """

    if config.plate_size is None:
        return config
    if not config.optimize_plate_offset and config.plate_offset is None:
        return config

    img_w, _ = read_image_size(config.image, "input image")
    mm_per_px = config.target_width / img_w
    layout = _resolve_plate_layout(config, config.plate_size, mm_per_px)
    if layout is None:
        return config

    canvas = (layout.canvas_width_px, layout.canvas_height_px)
    offset_dir = _as_path(build_dir) / "offset"
    artwork_dir = offset_dir / "artwork"
    artwork_dir.mkdir(parents=True, exist_ok=True)
    image_path = _as_path(config.image)
    padded_image = pad_image_checked(
        image_path,
        layout.pad_x_px,
        layout.pad_y_px,
        canvas,
        artwork_dir / f"{image_path.stem}.png",
        "input image",
    )
    padded_mask = None
    if config.base_mask:
        mask_path = _as_path(config.base_mask)
        mask_dir = offset_dir / "masks"
        mask_dir.mkdir(parents=True, exist_ok=True)
        padded_mask = pad_image_checked(
            mask_path,
            layout.pad_x_px,
            layout.pad_y_px,
            canvas,
            mask_dir / f"{mask_path.stem}.png",
            "base mask",
        )
    print(
        f"Padded canvas: {canvas[0]}x{canvas[1]} px "
        f"at offset ({layout.pad_x_px}, {layout.pad_y_px}) -> {padded_image}"
    )
    return replace(
        config,
        image=os.fspath(padded_image),
        base_mask=os.fspath(padded_mask) if padded_mask else None,
        target_width=layout.canvas_width_px * mm_per_px,
        target_height=None,
    )


def _format_optimal_color_line(
    index: int, color: DetectedColor, total_pixels: int
) -> str:
    percentage = 100 * color.pixel_count / total_pixels
    return f"{index:>2}. #{color.hex}  {color.pixel_count:,} px  {percentage:.1f}%"


def _print_optimal_filament_colors(config: CliRunConfig) -> None:
    detected = detect_colors(
        config.image,
        alpha_threshold=config.alpha_threshold,
        color_distance=config.color_distance,
    )
    if not detected.colors:
        raise ColorFilamentError(
            "input image contains no pixels at or above "
            f"--alpha-threshold ({config.alpha_threshold})"
        )

    total_pixels = sum(color.pixel_count for color in detected.colors)
    recommended = detected.colors[: config.max_filaments]
    print(
        f"Optimal filament colors for {config.image} "
        f"(top {len(recommended)} of {len(detected.colors)} detected colors):"
    )
    for index, color in enumerate(recommended, start=1):
        print(_format_optimal_color_line(index, color, total_pixels))
    if len(detected.colors) > len(recommended):
        remaining_pixels = sum(
            color.pixel_count for color in detected.colors[len(recommended) :]
        )
        remaining_percent = 100 * remaining_pixels / total_pixels
        print(
            f"... {len(detected.colors) - len(recommended)} more detected color(s) "
            f"cover {remaining_pixels:,} px ({remaining_percent:.1f}%)"
        )


def _run_cli(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    command_line: list[str],
) -> None:
    config = _validated_cli_config(parser, args)
    build_dir = _as_path(config.build_dir)
    output_dir = _as_path(config.output_dir)

    if config.optimal_filament_colors:
        _print_optimal_filament_colors(config)
        return

    build_dir.mkdir(parents=True, exist_ok=True)
    config = _apply_plate_offset(config, build_dir)
    image_path = _as_path(config.image)
    model_name = image_path.stem

    if config.per_plate_palette:
        if config.plate_size is None:
            raise RuntimeError("internal error: per-plate palette requires plate_size")
        PipelineRunner().run_per_plate_pipeline(
            image_path=image_path,
            filaments=config.filaments,
            build_dir=build_dir,
            output_dir=output_dir,
            plate_size=config.plate_size,
            max_filaments=config.max_filaments,
            alpha_threshold=config.alpha_threshold,
            color_distance=config.color_distance,
            width=config.target_width,
            base_height=config.base_height,
            raised_height=config.raised_height,
            base_color=config.base_color,
            no_base=config.no_base,
            base_mask=config.base_mask,
            dowel_holes=config.dowel_holes,
            keyhole=config.keyhole,
            keyhole_edge_margin_pct=config.keyhole_edge_margin_pct,
            keyhole_stud_spacing=config.keyhole_stud_spacing,
            keyhole_offset=config.keyhole_offset,
            keyhole_edge_clearance_mm=config.keyhole_edge_clearance,
            command_line=command_line,
            force_dowel_holes=config.force_dowel_holes,
            tools=config.tools,
            include_command_metadata=config.include_command_metadata,
        )
        print("\nDone. Open plates in Bambu Studio.")
        return

    result = PipelineRunner().run_pipeline(
        PipelineConfig(
            image_path=image_path,
            filaments=config.filaments,
            build_dir=build_dir,
            max_filaments=config.max_filaments,
            alpha_threshold=config.alpha_threshold,
            color_distance=config.color_distance,
            width=config.target_width,
            base_height=config.base_height,
            raised_height=config.raised_height,
            base_color=config.base_color,
            no_base=config.no_base,
            base_mask=config.base_mask,
            tools=config.tools,
        )
    )

    # 6. Compute plate grid
    plate_size = (
        config.plate_size
        if config.plate_size is not None
        else max(config.target_width, config.target_width * result.img_h / result.img_w)
    )
    grid = PlateGrid.from_dimensions(
        result.img_w,
        result.img_h,
        config.target_width,
        plate_size,
    )
    rows, cols = grid.rows, grid.cols
    model_w, model_h = grid.model_width_mm, grid.model_height_mm
    if config.target_height is not None:
        print(
            f"\nTarget height: {config.target_height} mm "
            f"(computed width: {config.target_width:.0f} mm), "
            f"plate size: {plate_size:.0f} mm"
        )
    else:
        print(
            f"\nTarget width: {config.target_width} mm, plate size: {plate_size:.0f} mm"
        )
    print(f"Model: {model_w:.0f} x {model_h:.0f} mm")
    num_plates = cols * rows
    print(f"Plates: {cols} x {rows} = {num_plates}\n")

    # 7. Render plates into single 3MF
    output_dir.mkdir(parents=True, exist_ok=True)
    tmpdir = build_dir / "stl_tmp"
    tmpdir.mkdir(parents=True, exist_ok=True)

    plates = [plate.as_build_tuple() for plate in grid.scad_plates()]

    keyhole_map = None
    dowel_map = None
    mask = None
    if (
        config.dowel_holes and not config.no_base and config.plate_size is not None
    ) or config.keyhole:
        mask_source = _as_path(config.base_mask) if config.base_mask else image_path
        mask, _, _ = load_opaque_mask(mask_source, config.alpha_threshold)
    if config.dowel_holes and not config.no_base and config.plate_size is not None:
        dowel_map = compute_dowel_specs(
            grid,
            mask=mask,
            force_dowel_holes=config.force_dowel_holes,
        )
    if config.keyhole:
        if mask is None:
            raise RuntimeError(
                "internal error: keyhole placement requires an opaque mask"
            )
        keyhole_map, keyhole_attempts = select_keyhole_positions(
            mask,
            grid,
            edge_margin_pct=config.keyhole_edge_margin_pct,
            stud_spacing=config.keyhole_stud_spacing,
            offset=config.keyhole_offset,
            edge_clearance_mm=config.keyhole_edge_clearance,
            include_diagnostics=True,
        )
        keyhole_debug_image = output_dir / f"{model_name}_keyhole_attempts.png"
        save_keyhole_attempt_debug_image(
            image_path,
            keyhole_debug_image,
            keyhole_attempts,
            grid,
            edge_clearance_mm=config.keyhole_edge_clearance,
        )
        if not keyhole_map:
            raise SystemExit(
                "error: no fully-covered keyhole pair found\n"
                f"{format_keyhole_selection_diagnostics(keyhole_attempts)}\n"
                f"keyhole attempt image: {keyhole_debug_image}"
            )
        print(f"Keyhole attempt image: {keyhole_debug_image}")

    output_path = output_dir / f"{model_name}.3mf"
    plate_count = build_3mf(
        result.scad_path,
        output_path,
        result.color_slots,
        config.target_width,
        plates,
        tmpdir,
        result.filament_slots,
        keyhole_map=keyhole_map,
        dowel_map=dowel_map,
        command_line=command_line,
        tools=config.tools,
        include_command_metadata=config.include_command_metadata,
    )

    print(f"\nWrote {output_path} ({plate_count} plate(s))")
    if (
        config.dowel_holes
        and not config.no_base
        and config.plate_size is not None
        and plate_count > 0
    ):
        dowel_output_path = output_dir / f"{model_name}-dowel.3mf"
        dowel_count = build_dowel_3mf(
            result.scad_path,
            dowel_output_path,
            config.base_color,
            config.target_width,
            build_dir / "dowel_tmp",
            command_line=command_line,
            tools=config.tools,
            include_command_metadata=config.include_command_metadata,
        )
        if dowel_count > 0:
            print(f"Wrote {dowel_output_path} (1 dowel)")
    print("Open in Bambu Studio.")
