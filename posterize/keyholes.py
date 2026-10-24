"""Keyhole wall-mount placement, coverage checks, and diagnostics."""

from __future__ import annotations

import math
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, overload

from PIL import Image, ImageDraw

from posterize.lateral_depth import Mask, _coverage_indices
from posterize.plate_grid import PlateGrid

DEFAULT_KEYHOLE_OFFSET_MM = 0.0
DEFAULT_KEYHOLE_EDGE_CLEARANCE_MM = 3.0
KEYHOLE_OUTLINE_PART = 100
KEYHOLE_OUTLINE_THICKNESS_MM = 0.4
KEYHOLE_OUTLINE_WIDTH_MM = 2.5
KEYHOLE_EDGE_MARGIN_PCT = 0.25
STUD_SPACING_MM = 406.4
DEFAULT_KEYHOLE_MIN_SPACING_MM = 40
KEYHOLE_TOP_MARGIN_MM = 8.0
KEYHOLE_HEAD_DIAMETER_MM = 10.0
KEYHOLE_NECK_WIDTH_MM = 5.0
KEYHOLE_NECK_LENGTH_MM = 10.0
KEYHOLE_MARGIN_MM = 1.0

KeyholePosition = float | list[float] | tuple[float, ...]
KeyholeMap = dict[tuple[int, int], list[KeyholePosition]]
KeyholeAttempt = dict[str, Any]


def _keyhole_position_x(position: Any) -> float:
    """Return the x coordinate from a scalar or [x, slot_top_y] keyhole position."""
    if isinstance(position, (list, tuple)):
        return float(position[0])
    return float(position)


def _translate_keyhole_position(
    position: KeyholePosition, dx: float = 0.0, dy: float = 0.0
) -> KeyholePosition:
    """Translate a scalar x or [x, slot_top_y] keyhole position."""
    x = _keyhole_position_x(position) + dx
    if isinstance(position, (list, tuple)):
        return [x, position[1] + dy]
    return x


def _keyhole_map_for_positions(
    positions: Sequence[KeyholePosition], grid: PlateGrid, row: int
) -> KeyholeMap:
    """Assign global keyhole positions to the plate(s) that contain them."""
    if not 0 <= row < grid.rows:
        raise IndexError(f"row {row} out of range for {grid.rows} rows")

    keyhole_map: KeyholeMap = {}
    for position in positions:
        x = _keyhole_position_x(position)
        col = grid.column_for_model_x(x)
        keyhole_map.setdefault((row, col), []).append(position)
    return keyhole_map


def _candidate_keyhole_pairs(
    model_width: float,
    plate_size: float,
    edge_margin_pct: float = KEYHOLE_EDGE_MARGIN_PCT,
    stud_spacing: bool = False,
    offset: float = DEFAULT_KEYHOLE_OFFSET_MM,
) -> list[dict[str, Any]]:
    """Return candidate symmetric keyhole pairs ordered by preference."""
    half_w = model_width / 2
    head_r = KEYHOLE_HEAD_DIAMETER_MM / 2

    if stud_spacing:
        return [
            {
                "positions": [
                    -STUD_SPACING_MM / 2 + offset,
                    STUD_SPACING_MM / 2 + offset,
                ],
                "strategy": (
                    f"standard stud spacing ({STUD_SPACING_MM:.1f}mm) "
                    f"with {offset:.1f}mm offset"
                ),
                "mode": "stud_spacing",
                "requested_offset": offset,
            }
        ]

    edge_margin = plate_size * edge_margin_pct
    edge_positions = [
        -half_w + edge_margin + head_r + offset,
        half_w - edge_margin - head_r + offset,
    ]
    initial_half_spacing = max(
        abs(edge_positions[0] - offset), abs(edge_positions[1] - offset)
    )
    min_half_spacing = DEFAULT_KEYHOLE_MIN_SPACING_MM / 2
    if initial_half_spacing < min_half_spacing:
        first_positions = [offset - min_half_spacing, offset + min_half_spacing]
        first_strategy = (
            f"minimum default spacing ({DEFAULT_KEYHOLE_MIN_SPACING_MM:.1f}mm apart) "
            f"with {offset:.1f}mm offset"
        )
        first_mode = "minimum_spacing"
        initial_half_spacing = min_half_spacing
    else:
        first_positions = edge_positions
        first_strategy = (
            f"edge margin ({edge_margin_pct * 100:.1f}% of {plate_size:.1f}mm"
            f" plate) with {offset:.1f}mm offset"
        )
        first_mode = "edge_margin"
    candidates = [
        {
            "positions": first_positions,
            "strategy": first_strategy,
            "mode": first_mode,
            "requested_offset": offset,
        }
    ]

    step_mm = 1.0
    seen = {tuple(round(x, 6) for x in first_positions)}
    step_count = int(math.floor((initial_half_spacing - min_half_spacing) / step_mm))
    for step in range(1, step_count + 1):
        half_spacing = initial_half_spacing - step * step_mm
        positions = [offset - half_spacing, offset + half_spacing]
        key = tuple(round(x, 6) for x in positions)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "positions": positions,
                "strategy": (
                    f"center-spaced fallback ({half_spacing * 2:.1f}mm apart) "
                    f"with {offset:.1f}mm offset"
                ),
                "mode": "center_spaced",
                "requested_offset": offset,
            }
        )

    return candidates


def _keyhole_slot_geometry(
    grid: PlateGrid,
    row: int,
    x_center_mm: float,
    slot_top_y_px: float | None = None,
) -> dict[str, float]:
    """Return pixel-space dimensions for a keyhole candidate on one row.

    The caller supplies the center in global model millimeters. The returned
    dictionary contains row bounds, the converted x center, and vertical extents
    used by both coverage checks and debug rendering.
    """

    head_r_px = (KEYHOLE_HEAD_DIAMETER_MM / 2) / grid.mm_per_px
    top_margin_px = KEYHOLE_TOP_MARGIN_MM / grid.mm_per_px
    neck_length_px = KEYHOLE_NECK_LENGTH_MM / grid.mm_per_px

    row_y0, row_y1 = grid.row_bounds()[row]
    slot_top_y = row_y0 + top_margin_px if slot_top_y_px is None else slot_top_y_px
    circle_center_y = slot_top_y + neck_length_px + head_r_px
    footprint_bottom = circle_center_y + head_r_px
    center_x = (x_center_mm + grid.model_width_mm / 2) / grid.mm_per_px
    return {
        "row_y0": row_y0,
        "row_y1": row_y1,
        "slot_top_y": slot_top_y,
        "circle_center_y": circle_center_y,
        "footprint_bottom": footprint_bottom,
        "center_x": center_x,
        "head_r_px": head_r_px,
    }


def _keyhole_position_coverage_failure(
    mask: Mask,
    grid: PlateGrid,
    row: int,
    col: int,
    x_center_mm: float,
    edge_clearance_mm: float = DEFAULT_KEYHOLE_EDGE_CLEARANCE_MM,
    slot_top_y_px: float | None = None,
) -> str | None:
    """Return why a keyhole candidate is unsafe, or ``None`` when covered.

    Coverage requires the expanded keyhole bounding box to fit inside the target
    plate cell and land entirely on opaque/base material in ``mask``.
    """

    geometry = _keyhole_slot_geometry(
        grid,
        row,
        x_center_mm,
        slot_top_y_px=slot_top_y_px,
    )
    head_r_px = geometry["head_r_px"]
    slot_top_y = geometry["slot_top_y"]
    footprint_bottom = geometry["footprint_bottom"]
    center_x = geometry["center_x"]
    row_y0 = geometry["row_y0"]
    row_y1 = geometry["row_y1"]
    clearance_px = edge_clearance_mm / grid.mm_per_px
    expanded_top = slot_top_y - clearance_px
    expanded_bottom = footprint_bottom + clearance_px
    if expanded_top < row_y0:
        overflow_mm = (row_y0 - expanded_top) * grid.mm_per_px
        return (
            f"needs {overflow_mm:.1f}mm more top clearance "
            f"for {edge_clearance_mm:.1f}mm edge clearance"
        )
    if expanded_bottom > row_y1:
        overflow_mm = (expanded_bottom - row_y1) * grid.mm_per_px
        return (
            f"needs {overflow_mm:.1f}mm more bottom clearance "
            f"for {edge_clearance_mm:.1f}mm edge clearance"
        )
    footprint_left = center_x - head_r_px
    footprint_right = center_x + head_r_px
    col_bounds = grid.pixel_bounds(row, col)
    expanded_left = footprint_left - clearance_px
    expanded_right = footprint_right + clearance_px
    if expanded_left < col_bounds.x0 or expanded_right > col_bounds.x1:
        return (
            f"needs {edge_clearance_mm:.1f}mm clearance from plate boundary "
            f"[{col_bounds.x0 * grid.mm_per_px:.1f}, "
            f"{col_bounds.x1 * grid.mm_per_px:.1f}]mm"
        )

    width = len(mask[0]) if mask else 0
    height = len(mask)
    x0, x1 = _coverage_indices(expanded_left, expanded_right, width)
    y0, y1 = _coverage_indices(expanded_top, expanded_bottom, height)
    if x0 >= x1 or y0 >= y1:
        return "footprint does not overlap the sampled mask area"

    uncovered = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            if not mask[y][x]:
                uncovered += 1

    if uncovered:
        return (
            f"footprint bounding box plus {edge_clearance_mm:.1f}mm "
            f"clearance includes {uncovered} transparent pixel(s)"
        )
    return None


def _suggest_keyhole_boundary_shifts(
    positions: Sequence[float],
    grid: PlateGrid,
    edge_clearance_mm: float = DEFAULT_KEYHOLE_EDGE_CLEARANCE_MM,
) -> list[float]:
    """Suggest minimal x shifts that move stud-spacing holes off plate seams."""

    half_w = grid.model_width_mm / 2
    head_r = KEYHOLE_HEAD_DIAMETER_MM / 2 + edge_clearance_mm
    shifts = {0.0}

    for x in positions:
        center_from_left = x + half_w
        col = grid.column_for_model_x(x)
        candidate_cols = [col]
        if col > 0:
            candidate_cols.append(col - 1)
        if col + 1 < grid.cols:
            candidate_cols.append(col + 1)

        for candidate_col in candidate_cols:
            left_mm = grid.x_bounds_px[candidate_col] * grid.mm_per_px
            right_mm = grid.x_bounds_px[candidate_col + 1] * grid.mm_per_px
            min_center = left_mm + head_r
            max_center = right_mm - head_r
            if min_center > max_center:
                continue
            if center_from_left < min_center:
                shifts.add(min_center - center_from_left)
            elif center_from_left > max_center:
                shifts.add(max_center - center_from_left)
            else:
                shifts.add(0.0)

    return sorted(shifts, key=lambda shift: (abs(shift), shift < 0, shift))


def _expand_keyhole_candidate_variants(
    candidate: dict[str, Any],
    grid: PlateGrid,
    edge_clearance_mm: float = DEFAULT_KEYHOLE_EDGE_CLEARANCE_MM,
) -> list[dict[str, Any]]:
    if candidate.get("mode") != "stud_spacing":
        return [candidate]

    variants = []
    seen = set()
    for shift in _suggest_keyhole_boundary_shifts(
        candidate["positions"],
        grid,
        edge_clearance_mm=edge_clearance_mm,
    ):
        positions = [x + shift for x in candidate["positions"]]
        key = tuple(round(x, 6) for x in positions)
        if key in seen:
            continue
        seen.add(key)
        strategy = candidate["strategy"]
        if abs(shift) > 1e-9:
            strategy += f"; auto seam shift {shift:+.1f}mm"
        variants.append(
            {
                **candidate,
                "positions": positions,
                "strategy": strategy,
            }
        )
    return variants


def _format_keyhole_candidate_attempt(attempt: KeyholeAttempt) -> str:
    joined_positions = ", ".join(
        f"{_keyhole_position_x(position):.1f}" for position in attempt["positions"]
    )
    slot_detail = ""
    if "slot_top_y" in attempt:
        slot_detail = f", slot top {attempt['slot_top_y']:.1f}px"
    prefix = (
        f"plate row {attempt['row'] + 1}, strategy {attempt['strategy']}, "
        f"positions [{joined_positions}]mm{slot_detail}"
    )
    failures = attempt["failures"]
    if isinstance(failures, str):
        return f"{prefix}: {failures}"
    if not failures:
        return f"{prefix}: selected"

    detail = "; ".join(
        f"x={x:.1f}mm on plate ({attempt['row'] + 1},{col + 1}): {reason}"
        for x, col, reason in failures
    )
    return f"{prefix}: {detail}"


def format_keyhole_selection_diagnostics(attempts: Sequence[KeyholeAttempt]) -> str:
    if not attempts:
        return "considered options: none"
    return "considered options:\n" + "\n".join(
        f"- {_format_keyhole_candidate_attempt(attempt)}" for attempt in attempts
    )


def _rounded_keyhole_coordinate(coordinate: float) -> float:
    rounded = round(float(coordinate), 1)
    if rounded == 0:
        return 0.0
    return rounded


def _display_keyhole_position(position: KeyholePosition) -> KeyholePosition:
    if isinstance(position, list):
        return [_rounded_keyhole_coordinate(coordinate) for coordinate in position]
    if isinstance(position, tuple):
        return tuple(_rounded_keyhole_coordinate(coordinate) for coordinate in position)
    return _rounded_keyhole_coordinate(position)


def format_keyhole_positions(positions: Sequence[KeyholePosition]) -> str:
    """Return keyhole positions formatted for human-facing diagnostics."""
    return repr([_display_keyhole_position(position) for position in positions])


def format_keyhole_map(keyhole_map: KeyholeMap) -> str:
    """Return a keyhole map formatted with concise coordinate precision."""
    display_map = {
        plate: [_display_keyhole_position(position) for position in positions]
        for plate, positions in keyhole_map.items()
    }
    return repr(display_map)


def _candidate_keyhole_slot_tops(grid: PlateGrid, row: int) -> list[float]:
    """Yield keyhole slot-top y positions from highest to lowest in pixel space."""
    row_y0, row_y1 = grid.row_bounds()[row]
    top_margin_px = KEYHOLE_TOP_MARGIN_MM / grid.mm_per_px
    footprint_below_slot_px = (
        KEYHOLE_NECK_LENGTH_MM + KEYHOLE_HEAD_DIAMETER_MM
    ) / grid.mm_per_px
    first_slot_top = row_y0 + top_margin_px
    last_slot_top = row_y1 - footprint_below_slot_px
    if first_slot_top > last_slot_top + 1e-9:
        return []

    slot_tops = []
    slot_top_y = first_slot_top
    step_px = max(1.0, 1.0 / grid.mm_per_px)
    while slot_top_y <= last_slot_top + 1e-9:
        slot_tops.append(slot_top_y)
        slot_top_y += step_px
    return slot_tops


def _positions_for_slot_top(
    positions: Sequence[float], grid: PlateGrid, row: int, slot_top_y_px: float
) -> list[float] | list[list[float]]:
    """Attach a model-space slot-top y coordinate when not using the default."""
    default_slot_top_y = (
        grid.row_bounds()[row][0] + KEYHOLE_TOP_MARGIN_MM / grid.mm_per_px
    )
    if abs(slot_top_y_px - default_slot_top_y) < 1e-9:
        return list(positions)

    slot_top_model_y = grid.model_height_mm / 2 - slot_top_y_px * grid.mm_per_px
    return [[x, slot_top_model_y] for x in positions]


def save_keyhole_attempt_debug_image(
    image_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    attempts: Sequence[KeyholeAttempt],
    grid: PlateGrid,
    edge_clearance_mm: float = DEFAULT_KEYHOLE_EDGE_CLEARANCE_MM,
) -> None:
    edge_clearance_mm = max(edge_clearance_mm, KEYHOLE_OUTLINE_WIDTH_MM)
    with Image.open(image_path) as source_image:
        image = source_image.convert("RGBA")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for row_y0, row_y1 in grid.row_bounds():
        draw.rectangle(
            [(0, row_y0), (image.width - 1, row_y1 - 1)],
            outline=(255, 255, 255, 96),
            width=1,
        )

    for attempt in attempts:
        color = (64, 200, 96, 220) if not attempt["failures"] else (255, 64, 64, 220)
        for position in attempt["positions"]:
            x_center_mm = _keyhole_position_x(position)
            slot_top_y_px = attempt.get("slot_top_y")
            if slot_top_y_px is None and isinstance(position, (list, tuple)):
                slot_top_y_px = image.height / 2 - position[1] / grid.mm_per_px
            geometry = _keyhole_slot_geometry(
                grid,
                attempt["row"],
                x_center_mm,
                slot_top_y_px=slot_top_y_px,
            )
            center_x = geometry["center_x"]
            slot_top_y = geometry["slot_top_y"]
            circle_center_y = geometry["circle_center_y"]
            head_r_px = geometry["head_r_px"]
            clearance_px = edge_clearance_mm / grid.mm_per_px

            draw.line(
                [
                    (center_x, slot_top_y - clearance_px),
                    (center_x, circle_center_y + clearance_px),
                ],
                fill=(255, 200, 200, 80)
                if attempt["failures"]
                else (200, 255, 200, 80),
                width=max(1, int(round((head_r_px + clearance_px) * 0.6))),
            )
            draw.ellipse(
                [
                    (
                        center_x - head_r_px - clearance_px,
                        circle_center_y - head_r_px - clearance_px,
                    ),
                    (
                        center_x + head_r_px + clearance_px,
                        circle_center_y + head_r_px + clearance_px,
                    ),
                ],
                outline=(255, 200, 200, 120)
                if attempt["failures"]
                else (200, 255, 200, 120),
                width=1,
            )

            draw.line(
                [(center_x, slot_top_y), (center_x, circle_center_y)],
                fill=color,
                width=max(1, int(round(head_r_px * 0.6))),
            )
            draw.ellipse(
                [
                    (center_x - head_r_px, circle_center_y - head_r_px),
                    (center_x + head_r_px, circle_center_y + head_r_px),
                ],
                outline=color,
                width=2,
            )
            draw.line(
                [
                    (center_x - head_r_px - 4, slot_top_y),
                    (center_x + head_r_px + 4, slot_top_y),
                ],
                fill=color,
                width=2,
            )

    annotated = Image.alpha_composite(image, overlay)
    output_file = Path(output_path)
    if output_file.parent != Path(""):
        output_file.parent.mkdir(parents=True, exist_ok=True)
    annotated.save(output_file)


@overload
def select_keyhole_positions(
    mask: Mask,
    grid: PlateGrid,
    edge_margin_pct: float = KEYHOLE_EDGE_MARGIN_PCT,
    stud_spacing: bool = False,
    offset: float = DEFAULT_KEYHOLE_OFFSET_MM,
    edge_clearance_mm: float = DEFAULT_KEYHOLE_EDGE_CLEARANCE_MM,
    row_order: Sequence[int] | None = None,
    include_diagnostics: Literal[False] = False,
) -> KeyholeMap | None: ...


@overload
def select_keyhole_positions(
    mask: Mask,
    grid: PlateGrid,
    edge_margin_pct: float = KEYHOLE_EDGE_MARGIN_PCT,
    stud_spacing: bool = False,
    offset: float = DEFAULT_KEYHOLE_OFFSET_MM,
    edge_clearance_mm: float = DEFAULT_KEYHOLE_EDGE_CLEARANCE_MM,
    row_order: Sequence[int] | None = None,
    *,
    include_diagnostics: Literal[True],
) -> tuple[KeyholeMap | None, list[KeyholeAttempt]]: ...


def select_keyhole_positions(
    mask: Mask,
    grid: PlateGrid,
    edge_margin_pct: float = KEYHOLE_EDGE_MARGIN_PCT,
    stud_spacing: bool = False,
    offset: float = DEFAULT_KEYHOLE_OFFSET_MM,
    edge_clearance_mm: float = DEFAULT_KEYHOLE_EDGE_CLEARANCE_MM,
    row_order: Sequence[int] | None = None,
    include_diagnostics: bool = False,
) -> KeyholeMap | tuple[KeyholeMap | None, list[KeyholeAttempt]] | None:
    """Choose the highest fully-covered keyhole pair across preferred rows."""
    edge_clearance_mm = max(edge_clearance_mm, KEYHOLE_OUTLINE_WIDTH_MM)
    candidate_pairs = _candidate_keyhole_pairs(
        grid.model_width_mm,
        grid.plate_size_mm,
        edge_margin_pct=edge_margin_pct,
        stud_spacing=stud_spacing,
        offset=offset,
    )
    attempts: list[KeyholeAttempt] = []

    expanded_candidates = []
    for candidate in candidate_pairs:
        expanded_candidates.extend(
            _expand_keyhole_candidate_variants(
                candidate,
                grid,
                edge_clearance_mm=edge_clearance_mm,
            )
        )

    rows = row_order if row_order is not None else range(grid.rows)
    for row in rows:
        row_height_mm = grid.row_heights_mm()[row]
        row_candidates = []
        for candidate in expanded_candidates:
            positions = candidate["positions"]
            strategy = candidate["strategy"]
            validation_error = validate_keyhole_positions(
                positions, grid.model_width_mm, row_height_mm
            )
            if validation_error:
                attempts.append(
                    {
                        "row": row,
                        "positions": positions,
                        "strategy": strategy,
                        "failures": validation_error,
                    }
                )
                continue
            row_candidates.append(candidate)

        for slot_top_y in _candidate_keyhole_slot_tops(grid, row):
            for candidate in row_candidates:
                positions = candidate["positions"]
                strategy = candidate["strategy"]
                attempt_positions = _positions_for_slot_top(
                    positions, grid, row, slot_top_y
                )

                failures = []
                for x in positions:
                    col = grid.column_for_model_x(x)
                    failure = _keyhole_position_coverage_failure(
                        mask,
                        grid,
                        row,
                        col,
                        x,
                        edge_clearance_mm=edge_clearance_mm,
                        slot_top_y_px=slot_top_y,
                    )
                    if failure:
                        failures.append((x, col, failure))
                attempt = {
                    "row": row,
                    "positions": attempt_positions,
                    "strategy": strategy,
                    "slot_top_y": slot_top_y,
                    "failures": failures,
                }
                attempts.append(attempt)
                if not failures:
                    result = _keyhole_map_for_positions(attempt_positions, grid, row)
                    if include_diagnostics:
                        return result, attempts
                    return result

    if include_diagnostics:
        return None, attempts
    return None


def validate_keyhole_positions(
    positions: Sequence[KeyholePosition], model_width: float, tile_h: float
) -> str | None:
    """Check that keyhole positions fit. Returns error string or None."""
    half_w = model_width / 2
    head_r = KEYHOLE_HEAD_DIAMETER_MM / 2
    x_positions = [_keyhole_position_x(position) for position in positions]
    for x in x_positions:
        if x - head_r < -half_w:
            return (
                f"keyhole at x={x:.1f}mm extends past left edge "
                f"(model width {model_width:.1f}mm)"
            )
        if x + head_r > half_w:
            return (
                f"keyhole at x={x:.1f}mm extends past right edge "
                f"(model width {model_width:.1f}mm)"
            )
    sorted_pos = sorted(x_positions)
    for i in range(len(sorted_pos) - 1):
        gap = sorted_pos[i + 1] - sorted_pos[i] - KEYHOLE_HEAD_DIAMETER_MM
        if gap < 0:
            return f"keyholes overlap by {-gap:.1f}mm"
    needed_h = KEYHOLE_TOP_MARGIN_MM + KEYHOLE_NECK_LENGTH_MM + KEYHOLE_HEAD_DIAMETER_MM
    if needed_h > tile_h:
        return (
            f"keyhole needs {needed_h:.1f}mm height "
            f"but plate is only {tile_h:.1f}mm tall"
        )
    return None


def compute_keyhole_depth(base_height: float) -> float:
    """Return a blind keyhole recess depth with 1mm margin on each side."""
    if not math.isfinite(base_height) or base_height < 0:
        raise ValueError("base_height must be non-negative and finite")
    return max(0.0, base_height - 2 * KEYHOLE_MARGIN_MM)
