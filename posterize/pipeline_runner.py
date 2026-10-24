"""Pipeline runner orchestration for posterize."""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from posterize.color_filament import (
    DEFAULT_ALPHA_THRESHOLD,
    DEFAULT_COLOR_DISTANCE,
    Filament,
    FilamentMatch,
    detect_colors,
    match_filaments,
    posterize_image,
    select_filaments,
)
from posterize.errors import InputValidationError
from posterize.external_tools import ExternalTools
from posterize.image_io import _crop_image_checked, read_image_size
from posterize.formatting import format_plate_label, select_keyhole_outline_extruder
from posterize.dowels import compute_dowel_specs
from posterize.keyholes import (
    DEFAULT_KEYHOLE_EDGE_CLEARANCE_MM,
    DEFAULT_KEYHOLE_OFFSET_MM,
    KEYHOLE_EDGE_MARGIN_PCT,
    KEYHOLE_OUTLINE_PART,
    _translate_keyhole_position,
    format_keyhole_map,
    format_keyhole_positions,
    format_keyhole_selection_diagnostics,
    save_keyhole_attempt_debug_image,
    select_keyhole_positions,
)
from posterize.masks import generate_masks, load_opaque_mask, mask_image_to_pbm
from posterize.project_builder import (
    add_plate_checked,
    add_stl_group_checked,
    build_dowel_3mf,
    create_project_checked,
    require_bambu3mf,
    save_project_checked,
)
from posterize.paths import Pathish, as_path as _as_path
from posterize.plate_grid import DEFAULT_WIDTH_MM, PlateGrid
from posterize.scad import (
    SCAD_BASE_PART,
    SCAD_UNCLIPPED_PLATE_HEIGHT,
    SCAD_UNCLIPPED_PLATE_WIDTH,
    SCAD_UNCLIPPED_PLATE_X,
    SCAD_UNCLIPPED_PLATE_Y,
)
from posterize.scad_rendering import (
    DEFAULT_BASE_HEIGHT_MM,
    generate_scad,
    generate_svgs,
    render_stl,
)
from posterize.threemf import (
    BAMBU_FILAMENT_SETTINGS_ID,
    ColorSlot,
    assign_color_slots,
)

DEFAULT_BASE_COLOR = "#000000"
DEFAULT_DOWEL_HOLES = True
EXACT_COLOR_MATCH = 0


@dataclass(frozen=True)
class PipelineConfig:
    """Typed request for the image-to-SCAD/color-slot pipeline."""

    image_path: Pathish
    filaments: list[Filament]
    build_dir: Pathish
    max_filaments: int = 4
    alpha_threshold: int = DEFAULT_ALPHA_THRESHOLD
    color_distance: float = DEFAULT_COLOR_DISTANCE
    width: float = DEFAULT_WIDTH_MM
    base_height: float = DEFAULT_BASE_HEIGHT_MM
    raised_height: float = 2.0
    base_color: str = DEFAULT_BASE_COLOR
    no_base: bool = False
    base_mask: Pathish | None = None
    tools: ExternalTools | None = None


@dataclass(frozen=True)
class PipelineResult:
    """Typed result from posterization, vectorization, and SCAD generation."""

    scad_path: str
    color_slots: list[ColorSlot]
    filament_slots: dict[int, Filament]
    img_w: int
    img_h: int


class PipelineRunner:
    """Orchestrate normal and per-plate posterize pipeline runs."""

    # ---------------------------------------------------------------------------
    # Pipeline runner
    # ---------------------------------------------------------------------------

    def run_pipeline(self, config: PipelineConfig) -> PipelineResult:
        """Execute posterization, vectorization, SCAD rendering, and slot assignment."""

        image_path = _as_path(config.image_path)
        filaments = config.filaments
        build_dir = _as_path(config.build_dir)
        max_filaments = config.max_filaments
        alpha_threshold = config.alpha_threshold
        color_distance = config.color_distance
        base_height = config.base_height
        raised_height = config.raised_height
        base_color = config.base_color
        no_base = config.no_base
        base_mask = _as_path(config.base_mask) if config.base_mask is not None else None
        tools = config.tools
        model_name = image_path.stem
        build_dir.mkdir(parents=True, exist_ok=True)

        # 1. Select best filaments and posterize image
        print(f"Selecting best {max_filaments} filaments for {image_path}...")
        selection = select_filaments(
            image_path, filaments, max_filaments, alpha_threshold
        )
        original_detected = detect_colors(image_path, alpha_threshold, color_distance)
        original_matches = match_filaments(
            original_detected.colors,
            selection.selected,
            allow_duplicate_assignments=True,
        )
        selected_delta_totals = {filament.name: 0.0 for filament in selection.selected}
        selected_pixel_totals = {filament.name: 0 for filament in selection.selected}
        dominant_matches: dict[str, FilamentMatch] = {}
        for match in original_matches:
            selected_delta_totals[match.filament.name] += (
                match.distance * match.image_color.pixel_count
            )
            selected_pixel_totals[match.filament.name] += match.image_color.pixel_count
            previous_dominant = dominant_matches.get(match.filament.name)
            if (
                previous_dominant is None
                or match.image_color.pixel_count
                > previous_dominant.image_color.pixel_count
            ):
                dominant_matches[match.filament.name] = match

        print("  Selected colors:")
        for filament in selection.selected:
            pixel_total = selected_pixel_totals[filament.name]
            average_delta = (
                selected_delta_totals[filament.name] / pixel_total
                if pixel_total
                else 0.0
            )
            dominant_match = dominant_matches.get(filament.name)
            if dominant_match is None:
                print(f"    (unmatched) → {filament.name} #{filament.hex}")
                continue
            print(
                f"    #{dominant_match.image_color.hex} → {filament.name} #{filament.hex} "
                f"(ΔE={dominant_match.distance:.1f}; avg ΔE={average_delta:.1f})"
            )

        posterized_path = build_dir / f"{model_name}_posterized.png"
        posterize_image(
            image_path, selection.selected, posterized_path, alpha_threshold
        )

        # 2. Detect colors from posterized image. It holds only exact filament
        # colors, so match exactly to keep close filaments as separate colors.
        detected = detect_colors(posterized_path, alpha_threshold, EXACT_COLOR_MATCH)
        colors = detected.colors
        img_w = detected.width
        img_h = detected.height

        matched = match_filaments(colors, selection.selected)

        # 3. Generate masks
        print("Generating masks...")
        color_pbms, base_pbm = generate_masks(
            posterized_path, colors, build_dir, alpha_threshold, EXACT_COLOR_MATCH
        )

        # 3b. Choose the base footprint source.
        if base_mask:
            custom_pbm = build_dir / "base.pbm"
            mask_image_to_pbm(base_mask, custom_pbm, alpha_threshold)
            base_pbm = os.fspath(custom_pbm)
            print(f"  Using custom base mask: {base_mask}")
        else:
            print("  Using opaque pixels as base mask")

        # 3c. Vectorize with potrace
        print("Vectorizing masks...")
        if tools is None:
            color_svgs = generate_svgs(color_pbms, build_dir)
            base_svg = generate_svgs([base_pbm], build_dir)[0]
        else:
            color_svgs = generate_svgs(color_pbms, build_dir, tools=tools)
            base_svg = generate_svgs([base_pbm], build_dir, tools=tools)[0]

        # 4. Generate SCAD
        scad_path = generate_scad(
            color_svgs,
            base_svg,
            img_w,
            img_h,
            build_dir,
            base_height,
            raised_height,
            no_base=no_base,
        )
        print(f"  Generated {scad_path}")

        # 5. Build typed color slots for 3MF assembly.
        color_slots, filament_slots = assign_color_slots(
            matched,
            base_color=base_color,
            no_base=no_base,
        )

        return PipelineResult(
            scad_path=scad_path,
            color_slots=color_slots,
            filament_slots=filament_slots,
            img_w=img_w,
            img_h=img_h,
        )

    # ---------------------------------------------------------------------------
    # Per-plate orchestrator
    # ---------------------------------------------------------------------------

    def run_per_plate_pipeline(
        self,
        image_path: Pathish,
        filaments: list[Filament],
        build_dir: Pathish,
        output_dir: Pathish,
        plate_size: float,
        max_filaments: int = 4,
        alpha_threshold: int = DEFAULT_ALPHA_THRESHOLD,
        color_distance: float = DEFAULT_COLOR_DISTANCE,
        width: float = DEFAULT_WIDTH_MM,
        base_height: float = DEFAULT_BASE_HEIGHT_MM,
        raised_height: float = 2.0,
        base_color: str = DEFAULT_BASE_COLOR,
        no_base: bool = False,
        base_mask: Pathish | None = None,
        dowel_holes: bool = DEFAULT_DOWEL_HOLES,
        keyhole: bool = False,
        keyhole_edge_margin_pct: float = KEYHOLE_EDGE_MARGIN_PCT,
        keyhole_stud_spacing: bool = False,
        keyhole_offset: float = DEFAULT_KEYHOLE_OFFSET_MM,
        keyhole_edge_clearance_mm: float = DEFAULT_KEYHOLE_EDGE_CLEARANCE_MM,
        command_line: list[str] | None = None,
        force_dowel_holes: bool = False,
        tools: ExternalTools | None = None,
        include_command_metadata: bool = False,
    ) -> None:
        """Run the full pipeline independently per plate, producing one .3mf per row.

        Computes the plate grid from image dimensions and plate_size, then gives
        each plate its own cropped image, independent filament selection, and
        isolated build directory. Final output is grouped by row, so row 1 is
        written to ``<model>-row-1.3mf``, row 2 to ``<model>-row-2.3mf``, etc.
        """
        image_path = _as_path(image_path)
        build_dir = _as_path(build_dir)
        output_dir = _as_path(output_dir)
        base_mask = _as_path(base_mask) if base_mask is not None else None
        model_name = image_path.stem
        img_w, img_h = read_image_size(image_path, "input image")
        if base_mask:
            mask_w, mask_h = read_image_size(base_mask, "base mask")
            if (mask_w, mask_h) != (img_w, img_h):
                raise InputValidationError(
                    f"base mask size {mask_w}x{mask_h} "
                    f"does not match image size {img_w}x{img_h}"
                )
        grid = PlateGrid.from_dimensions(img_w, img_h, width, plate_size)
        rows, cols = grid.rows, grid.cols
        print(f"Per-plate palette mode: {cols}x{rows} grid ({cols * rows} plates)")
        output_dir.mkdir(parents=True, exist_ok=True)

        global_keyhole_map = None
        dowel_map = None
        mask = None
        if (dowel_holes and not no_base) or keyhole:
            mask_source = base_mask or image_path
            mask, _, _ = load_opaque_mask(mask_source, alpha_threshold)
        if dowel_holes and not no_base:
            dowel_map = compute_dowel_specs(
                grid,
                mask=mask,
                force_dowel_holes=force_dowel_holes,
            )
        if keyhole:
            if mask is None:
                raise RuntimeError(
                    "internal error: keyhole placement requires an opaque mask"
                )
            global_keyhole_map, keyhole_attempts = select_keyhole_positions(
                mask,
                grid,
                edge_margin_pct=keyhole_edge_margin_pct,
                stud_spacing=keyhole_stud_spacing,
                offset=keyhole_offset,
                edge_clearance_mm=keyhole_edge_clearance_mm,
                include_diagnostics=True,
            )
            keyhole_debug_image = output_dir / f"{model_name}_keyhole_attempts.png"
            save_keyhole_attempt_debug_image(
                image_path,
                keyhole_debug_image,
                keyhole_attempts,
                grid,
                edge_clearance_mm=keyhole_edge_clearance_mm,
            )
            if not global_keyhole_map:
                raise SystemExit(
                    "error: no fully-covered keyhole pair found\n"
                    f"{format_keyhole_selection_diagnostics(keyhole_attempts)}\n"
                    f"keyhole attempt image: {keyhole_debug_image}"
                )
            print(f"Keyholes: {format_keyhole_map(global_keyhole_map)}")
            print(f"Keyhole attempt image: {keyhole_debug_image}")

        # Pass 1: run pipeline per tile and collect raw results.
        tile_results_by_row = defaultdict(list)

        for row in range(rows):
            for col in range(cols):
                plate_label = format_plate_label(row, col)
                print(f"\n{'=' * 60}")
                print(f"Plate {plate_label} ({row + 1},{col + 1}) of {rows}x{cols}")
                print(f"{'=' * 60}")

                plate_build_dir = build_dir / plate_label
                plate_build_dir.mkdir(parents=True, exist_ok=True)

                cropped_path = os.fspath(
                    _crop_image_checked(
                        image_path,
                        grid.crop_box(row, col),
                        plate_build_dir / f"plate_{plate_label}.png",
                        "input image",
                    )
                )
                plate_mask, _, _ = load_opaque_mask(cropped_path, alpha_threshold)
                if not any(any(mask_row) for mask_row in plate_mask):
                    print("  Skipping transparent plate")
                    continue

                plate_width = grid.scad_plate(row, col).width

                plate_base_mask = None
                if base_mask:
                    plate_mask_path = plate_build_dir / f"mask_{plate_label}.png"
                    _crop_image_checked(
                        base_mask,
                        grid.crop_box(row, col),
                        plate_mask_path,
                        "base mask",
                    )
                    plate_base_mask = plate_mask_path

                result = self.run_pipeline(
                    PipelineConfig(
                        image_path=cropped_path,
                        filaments=filaments,
                        build_dir=plate_build_dir,
                        max_filaments=max_filaments,
                        alpha_threshold=alpha_threshold,
                        color_distance=color_distance,
                        width=plate_width,
                        base_height=base_height,
                        raised_height=raised_height,
                        base_color=base_color,
                        no_base=no_base,
                        base_mask=plate_base_mask,
                        tools=tools,
                    )
                )

                tile_results_by_row[row].append((row, col, plate_width, result))

        for row_tiles in tile_results_by_row.values():
            row_tiles.sort(key=lambda tile_result: tile_result[1])

        total_plate_count = 0
        for row in range(rows):
            row_tiles = tile_results_by_row.get(row, [])
            if not row_tiles:
                continue

            row_filaments: dict[str, tuple[int, dict[str, str]]] = {}
            next_row_slot = 1
            if not no_base:
                base_hex = base_color.lstrip("#")
                row_filaments["base"] = (
                    next_row_slot,
                    {
                        "settings_id": BAMBU_FILAMENT_SETTINGS_ID,
                        "colour": f"#{base_hex}",
                    },
                )
                next_row_slot += 1

            row_slot_remaps: dict[int, dict[int, int]] = {}
            for _, col, _, result in row_tiles:
                slot_remap = {}
                for color_slot in result.color_slots:
                    color_name, old_slot = color_slot.name, color_slot.slot
                    if color_name in row_filaments:
                        slot_remap[old_slot] = row_filaments[color_name][0]
                        continue

                    fil = result.filament_slots.get(old_slot)
                    if fil:
                        colour = f"#{fil.hex}"
                    else:
                        colour = color_slot.bambu_colour
                    row_filaments[color_name] = (
                        next_row_slot,
                        {
                            "settings_id": BAMBU_FILAMENT_SETTINGS_ID,
                            "colour": colour,
                        },
                    )
                    slot_remap[old_slot] = next_row_slot
                    next_row_slot += 1
                row_slot_remaps[col] = slot_remap

            num_slots = max(slot for slot, _ in row_filaments.values())
            bambu_filaments: list[dict[str, str] | None] = [None] * num_slots
            for _, (slot, fil_dict) in row_filaments.items():
                bambu_filaments[slot - 1] = fil_dict

            project_cls = require_bambu3mf()
            project = create_project_checked(
                project_cls,
                bambu_filaments,
                command_line,
                include_command_metadata=include_command_metadata,
            )

            row_plate_count = 0
            print(f"\nBuilding row {row + 1} of {rows}...")
            for _, col, plate_width, result in row_tiles:
                plate_label = format_plate_label(row, col)
                print(f"  Plate {plate_label}...", end="", flush=True)

                tmpdir = build_dir / plate_label / "stl_tmp"
                tmpdir.mkdir(parents=True, exist_ok=True)

                plate_keyhole_map = None
                if global_keyhole_map and (row, col) in global_keyhole_map:
                    plate_center, plate_center_y = grid.plate_center_mm(row, col)
                    local_pos = [
                        _translate_keyhole_position(
                            position, dx=-plate_center, dy=-plate_center_y
                        )
                        for position in global_keyhole_map[(row, col)]
                    ]
                    plate_keyhole_map = {(row, col): local_pos}
                    print(
                        f" keyhole: global={format_keyhole_positions(global_keyhole_map[(row, col)])}"
                        f" plate_center={plate_center:.1f}"
                        f" local={format_keyhole_positions(local_pos)}",
                        end="",
                    )

                bambu_plate = add_plate_checked(project, plate_label)
                stl_entries = []
                slot_remap = row_slot_remaps[col]
                plate_keyhole_pos = (
                    plate_keyhole_map.get((row, col), []) if plate_keyhole_map else []
                )
                for part_idx, color_slot in enumerate(result.color_slots):
                    color_name, old_slot = color_slot.name, color_slot.slot
                    part_num = SCAD_BASE_PART + part_idx
                    stl_path = tmpdir / f"{color_name}_{plate_label}.stl"
                    keyhole_pos = (
                        plate_keyhole_pos if part_num == SCAD_BASE_PART else []
                    )
                    ok = render_stl(
                        result.scad_path,
                        part_num,
                        plate_width,
                        stl_path,
                        plate_x=SCAD_UNCLIPPED_PLATE_X,
                        plate_y=SCAD_UNCLIPPED_PLATE_Y,
                        plate_w=SCAD_UNCLIPPED_PLATE_WIDTH,
                        plate_h=SCAD_UNCLIPPED_PLATE_HEIGHT,
                        keyhole_positions=keyhole_pos,
                        dowel_spec=(
                            dowel_map.get((row, col))
                            if dowel_map and part_num == SCAD_BASE_PART
                            else None
                        ),
                        tools=tools,
                    )
                    if not ok:
                        continue
                    stl_entries.append((os.fspath(stl_path), slot_remap[old_slot]))

                keyhole_outline_old_slot = select_keyhole_outline_extruder(
                    result.color_slots
                )
                if plate_keyhole_pos and keyhole_outline_old_slot is not None:
                    outline_path = tmpdir / f"keyhole_outline_{plate_label}.stl"
                    ok = render_stl(
                        result.scad_path,
                        part=KEYHOLE_OUTLINE_PART,
                        target_width=plate_width,
                        outpath=outline_path,
                        plate_x=SCAD_UNCLIPPED_PLATE_X,
                        plate_y=SCAD_UNCLIPPED_PLATE_Y,
                        plate_w=SCAD_UNCLIPPED_PLATE_WIDTH,
                        plate_h=SCAD_UNCLIPPED_PLATE_HEIGHT,
                        keyhole_positions=plate_keyhole_pos,
                        tools=tools,
                    )
                    if ok:
                        stl_entries.append(
                            (
                                os.fspath(outline_path),
                                slot_remap[keyhole_outline_old_slot],
                            )
                        )

                if not stl_entries:
                    print(" (empty)")
                    continue

                add_stl_group_checked(bambu_plate, stl_entries, plate_label)
                has_keyholes = bool(
                    plate_keyhole_map and plate_keyhole_map.get((row, col))
                )
                suffix = " [keyholes]" if has_keyholes else ""
                print(f" done{suffix}")
                row_plate_count += 1
                total_plate_count += 1

            output_path = output_dir / f"{model_name}-row-{row + 1}.3mf"
            if row_plate_count > 0:
                save_project_checked(project, output_path)
            print(f"Wrote {output_path} ({row_plate_count} plate(s))")

        if dowel_holes and not no_base and total_plate_count > 0:
            first_result = next(
                (
                    tile_result[3]
                    for row_tiles in tile_results_by_row.values()
                    for tile_result in row_tiles
                ),
                None,
            )
            if first_result is not None:
                dowel_output_path = output_dir / f"{model_name}-dowel.3mf"
                dowel_count = build_dowel_3mf(
                    first_result.scad_path,
                    dowel_output_path,
                    base_color,
                    width,
                    build_dir / "dowel_tmp",
                    command_line=command_line,
                    tools=tools,
                    include_command_metadata=include_command_metadata,
                    stl_renderer=render_stl,
                )
                if dowel_count > 0:
                    print(f"Wrote {dowel_output_path} (1 dowel)")

        print(f"\nWrote {rows} row file(s) covering {total_plate_count} plate(s)")
