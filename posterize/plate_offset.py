"""Search for an image offset that reduces the number of printed parts.

The plate grid always starts at the top-left corner of the source image and
divides it into equal cells. Every seam that crosses the artwork splits it into
more pieces, and a seam that clips the edge of a shape leaves a thin sliver
part. Padding the image with transparent pixels moves the seams without moving
the artwork in model space, so a better origin can remove whole plates and can
remove slivers.

A part is one connected island of base material on one plate. That is what
comes off the printer, so the search counts parts, not pixels.

Each candidate canvas is padded to a whole number of full-size cells. This
keeps every seam at a fixed pixel period, so a candidate offset moves the seams
instead of resizing the cells.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import accumulate
from operator import add

from posterize.plate_grid import PlateGrid

DEFAULT_SMALL_PART_FRACTION = 0.01
DEFAULT_SEARCH_STEPS = 16
MAX_CANDIDATES_PER_AXIS = 48
MAX_MEASURED_CANDIDATES = 24

Mask = Sequence[Sequence[bool]]
Runs = list[list[tuple[int, int]]]


@dataclass(frozen=True)
class PlateLayout:
    """Part statistics for one candidate image placement."""

    pad_x_px: int
    pad_y_px: int
    canvas_width_px: int
    canvas_height_px: int
    rows: int
    cols: int
    occupied_plates: int
    parts: int
    small_parts: int
    min_part_mm2: float

    @property
    def is_origin(self) -> bool:
        """Return True when the artwork stays at the top-left grid corner."""

        return self.pad_x_px == 0 and self.pad_y_px == 0

    @property
    def quality(self) -> tuple[int, int, int, float]:
        """Return the comparison key: fewer plates, fewer slivers, fewer parts."""

        return (
            self.occupied_plates,
            self.small_parts,
            self.parts,
            -self.min_part_mm2,
        )

    def is_better_than(self, other: PlateLayout) -> bool:
        """Return True when this layout prints better than another one."""

        return self.quality < other.quality

    def describe(self) -> str:
        """Return a one-line human-readable summary."""

        return (
            f"{self.occupied_plates} printed plate(s) of {self.cols}x{self.rows}, "
            f"{self.parts} part(s), {self.small_parts} small, "
            f"smallest part {self.min_part_mm2:.0f} mm2"
        )


# ---------------------------------------------------------------------------
# Opaque-pixel lookups
# ---------------------------------------------------------------------------


def _integral_image(mask: Mask) -> list[list[int]]:
    """Return a (h+1)x(w+1) summed-area table of opaque pixel counts."""

    height = len(mask)
    width = len(mask[0]) if height else 0
    integral = [[0] * (width + 1)]
    previous = integral[0]
    for row in mask:
        row_prefix = [0, *accumulate(map(int, row))]
        current = list(map(add, previous, row_prefix))
        integral.append(current)
        previous = current
    return integral


def _region_sum(integral: list[list[int]], x0: int, y0: int, x1: int, y1: int) -> int:
    """Return the opaque pixel count inside a half-open pixel box."""

    if x1 <= x0 or y1 <= y0:
        return 0
    return integral[y1][x1] - integral[y0][x1] - integral[y1][x0] + integral[y0][x0]


def _mask_runs(mask: Mask) -> Runs:
    """Return the half-open opaque pixel spans of every image row."""

    runs: Runs = []
    for row in mask:
        data = bytes(bytearray(1 if pixel else 0 for pixel in row))
        row_runs = []
        start = data.find(b"\x01")
        while start != -1:
            end = data.find(b"\x00", start)
            if end == -1:
                end = len(data)
            row_runs.append((start, end))
            start = data.find(b"\x01", end)
        runs.append(row_runs)
    return runs


# ---------------------------------------------------------------------------
# Part counting
# ---------------------------------------------------------------------------


def _component_areas(runs: Runs, x0: int, y0: int, x1: int, y1: int) -> list[int]:
    """Return the pixel area of every 4-connected island inside a pixel box.

    The box is clipped image space, so each island is one printed part of one
    plate.
    """

    parent: list[int] = []
    areas: list[int] = []

    def find(index: int) -> int:
        root = index
        while parent[root] != root:
            root = parent[root]
        while parent[index] != root:
            parent[index], index = root, parent[index]
        return root

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    previous: list[tuple[int, int, int]] = []
    for y in range(y0, y1):
        current: list[tuple[int, int, int]] = []
        for start, end in runs[y]:
            span_start = max(start, x0)
            span_end = min(end, x1)
            if span_start >= span_end:
                continue
            index = len(parent)
            parent.append(index)
            areas.append(span_end - span_start)
            current.append((span_start, span_end, index))

        above = 0
        below = 0
        while above < len(previous) and below < len(current):
            prev_start, prev_end, prev_index = previous[above]
            curr_start, curr_end, curr_index = current[below]
            if prev_start < curr_end and curr_start < prev_end:
                union(prev_index, curr_index)
            if prev_end <= curr_end:
                above += 1
            else:
                below += 1
        previous = current

    totals: dict[int, int] = defaultdict(int)
    for index, area in enumerate(areas):
        totals[find(index)] += area
    return list(totals.values())


# ---------------------------------------------------------------------------
# Grid candidates
# ---------------------------------------------------------------------------


def _cell_size_px(mm_per_px: float, plate_size_mm: float) -> int:
    """Return the largest whole pixel cell that fits one plate."""

    cell_px = math.floor(plate_size_mm / mm_per_px + 1e-9)
    if cell_px < 1:
        raise ValueError("plate_size_mm is too small for image resolution")
    return cell_px


def _occupancy_profile(
    integral: list[list[int]], length_px: int, other_length_px: int, is_x_axis: bool
) -> list[bool]:
    """Return, for each line along one axis, whether it holds opaque pixels."""

    profile = []
    for index in range(length_px):
        if is_x_axis:
            total = _region_sum(integral, index, 0, index + 1, other_length_px)
        else:
            total = _region_sum(integral, 0, index, other_length_px, index + 1)
        profile.append(total > 0)
    return profile


def _edge_positions(profile: Sequence[bool]) -> list[int]:
    """Return the positions where the artwork starts or stops along one axis."""

    edges = []
    previous = False
    for index, occupied in enumerate(profile):
        if occupied != previous:
            edges.append(index)
            previous = occupied
    if previous:
        edges.append(len(profile))
    return edges


def _thin(values: Sequence[int], limit: int) -> list[int]:
    """Return at most ``limit`` values, spread evenly through the input."""

    if len(values) <= limit:
        return list(values)
    step = len(values) / limit
    return [values[int(index * step)] for index in range(limit)]


def _candidate_offsets(profile: Sequence[bool], cell_px: int, steps: int) -> list[int]:
    """Return the padding amounts to try along one axis.

    Padding by a whole cell only prepends an empty row or column, so the search
    covers exactly one cell period. The candidates are the paddings that put a
    seam on an edge of the artwork, plus a coarse sweep as a fallback.
    """

    if cell_px <= 1:
        return [0]
    step = max(1, cell_px // max(1, steps))
    sweep = set(range(0, cell_px, step))
    aligned = {(-position) % cell_px for position in _edge_positions(profile)}
    return _thin(sorted(sweep | aligned), MAX_CANDIDATES_PER_AXIS)


def _canvas_length(length_px: int, pad_px: int, cell_px: int) -> int:
    """Return the padded canvas length rounded up to whole cells."""

    return math.ceil((length_px + pad_px) / cell_px) * cell_px


def _grid_for_offset(
    image_width_px: int,
    image_height_px: int,
    pad_x_px: int,
    pad_y_px: int,
    mm_per_px: float,
    plate_size_mm: float,
) -> PlateGrid:
    """Return the plate grid of the canvas that holds one padded placement."""

    cell_px = _cell_size_px(mm_per_px, plate_size_mm)
    canvas_w = _canvas_length(image_width_px, pad_x_px, cell_px)
    canvas_h = _canvas_length(image_height_px, pad_y_px, cell_px)
    return PlateGrid.from_dimensions(
        canvas_w, canvas_h, canvas_w * mm_per_px, plate_size_mm
    )


def _cell_in_image(
    grid: PlateGrid,
    row: int,
    col: int,
    image_width_px: int,
    image_height_px: int,
    pad_x_px: int,
    pad_y_px: int,
) -> tuple[int, int, int, int]:
    """Return one plate cell as a pixel box clipped to the source image."""

    x0, y0, x1, y1 = grid.crop_box(row, col)
    return (
        max(0, min(image_width_px, x0 - pad_x_px)),
        max(0, min(image_height_px, y0 - pad_y_px)),
        max(0, min(image_width_px, x1 - pad_x_px)),
        max(0, min(image_height_px, y1 - pad_y_px)),
    )


# ---------------------------------------------------------------------------
# Layout measurement
# ---------------------------------------------------------------------------


def _count_occupied_plates(
    integral: list[list[int]],
    grid: PlateGrid,
    image_width_px: int,
    image_height_px: int,
    pad_x_px: int,
    pad_y_px: int,
) -> int:
    """Return how many plates hold artwork. This is the cheap search filter."""

    occupied = 0
    for row in range(grid.rows):
        for col in range(grid.cols):
            box = _cell_in_image(
                grid, row, col, image_width_px, image_height_px, pad_x_px, pad_y_px
            )
            if _region_sum(integral, *box) > 0:
                occupied += 1
    return occupied


def _measure(
    runs: Runs,
    integral: list[list[int]],
    grid: PlateGrid,
    image_width_px: int,
    image_height_px: int,
    pad_x_px: int,
    pad_y_px: int,
    mm_per_px: float,
    plate_size_mm: float,
    small_part_fraction: float,
) -> PlateLayout:
    """Return the full part statistics for one grid and image placement."""

    px_area_mm2 = mm_per_px * mm_per_px
    small_part_mm2 = plate_size_mm * plate_size_mm * small_part_fraction
    occupied = 0
    parts = 0
    small_parts = 0
    min_part_mm2 = math.inf
    for row in range(grid.rows):
        for col in range(grid.cols):
            box = _cell_in_image(
                grid, row, col, image_width_px, image_height_px, pad_x_px, pad_y_px
            )
            if _region_sum(integral, *box) == 0:
                continue
            occupied += 1
            for area_px in _component_areas(runs, *box):
                area_mm2 = area_px * px_area_mm2
                parts += 1
                min_part_mm2 = min(min_part_mm2, area_mm2)
                if area_mm2 < small_part_mm2:
                    small_parts += 1
    return PlateLayout(
        pad_x_px=pad_x_px,
        pad_y_px=pad_y_px,
        canvas_width_px=grid.image_width_px,
        canvas_height_px=grid.image_height_px,
        rows=grid.rows,
        cols=grid.cols,
        occupied_plates=occupied,
        parts=parts,
        small_parts=small_parts,
        min_part_mm2=0.0 if math.isinf(min_part_mm2) else min_part_mm2,
    )


def _validate_search_inputs(mask: Mask, mm_per_px: float, plate_size_mm: float) -> None:
    if not mask or not mask[0]:
        raise ValueError("mask must not be empty")
    if not math.isfinite(mm_per_px) or mm_per_px <= 0:
        raise ValueError("mm_per_px must be positive and finite")
    if not math.isfinite(plate_size_mm) or plate_size_mm <= 0:
        raise ValueError("plate_size_mm must be positive and finite")


def layout_for_offset(
    mask: Mask,
    mm_per_px: float,
    plate_size_mm: float,
    pad_x_px: int,
    pad_y_px: int,
    small_part_fraction: float = DEFAULT_SMALL_PART_FRACTION,
) -> PlateLayout:
    """Return the part statistics for one explicit padding offset."""

    _validate_search_inputs(mask, mm_per_px, plate_size_mm)
    if pad_x_px < 0 or pad_y_px < 0:
        raise ValueError("padding must be non-negative")
    image_height_px = len(mask)
    image_width_px = len(mask[0])
    grid = _grid_for_offset(
        image_width_px, image_height_px, pad_x_px, pad_y_px, mm_per_px, plate_size_mm
    )
    return _measure(
        _mask_runs(mask),
        _integral_image(mask),
        grid,
        image_width_px,
        image_height_px,
        pad_x_px,
        pad_y_px,
        mm_per_px,
        plate_size_mm,
        small_part_fraction,
    )


def current_layout(
    mask: Mask,
    mm_per_px: float,
    plate_size_mm: float,
    small_part_fraction: float = DEFAULT_SMALL_PART_FRACTION,
) -> PlateLayout:
    """Return the part statistics of the unpadded layout used today."""

    _validate_search_inputs(mask, mm_per_px, plate_size_mm)
    image_height_px = len(mask)
    image_width_px = len(mask[0])
    grid = PlateGrid.from_dimensions(
        image_width_px, image_height_px, image_width_px * mm_per_px, plate_size_mm
    )
    return _measure(
        _mask_runs(mask),
        _integral_image(mask),
        grid,
        image_width_px,
        image_height_px,
        0,
        0,
        mm_per_px,
        plate_size_mm,
        small_part_fraction,
    )


def _ranking_key(candidate: PlateLayout) -> tuple[int, int, int, float, int]:
    """Rank layouts by quality, then prefer the smallest padding."""

    return (*candidate.quality, candidate.pad_x_px + candidate.pad_y_px)


def find_best_plate_offset(
    mask: Mask,
    mm_per_px: float,
    plate_size_mm: float,
    small_part_fraction: float = DEFAULT_SMALL_PART_FRACTION,
    search_steps: int = DEFAULT_SEARCH_STEPS,
) -> tuple[PlateLayout, PlateLayout]:
    """Return the best padded layout and the layout used today.

    The search runs in two passes. The first pass counts occupied plates for
    every candidate offset, which is cheap. The second pass counts parts for
    the candidates that share the lowest plate count, which is not.
    """

    _validate_search_inputs(mask, mm_per_px, plate_size_mm)
    if search_steps <= 0:
        raise ValueError("search_steps must be positive")

    image_height_px = len(mask)
    image_width_px = len(mask[0])
    integral = _integral_image(mask)
    runs = _mask_runs(mask)
    cell_px = _cell_size_px(mm_per_px, plate_size_mm)

    x_profile = _occupancy_profile(integral, image_width_px, image_height_px, True)
    y_profile = _occupancy_profile(integral, image_height_px, image_width_px, False)

    counted: list[tuple[int, int, int, PlateGrid]] = []
    for pad_y in _candidate_offsets(y_profile, cell_px, search_steps):
        for pad_x in _candidate_offsets(x_profile, cell_px, search_steps):
            grid = _grid_for_offset(
                image_width_px,
                image_height_px,
                pad_x,
                pad_y,
                mm_per_px,
                plate_size_mm,
            )
            occupied = _count_occupied_plates(
                integral, grid, image_width_px, image_height_px, pad_x, pad_y
            )
            counted.append((occupied, pad_x, pad_y, grid))

    fewest = min(entry[0] for entry in counted)
    shortlist = [entry for entry in counted if entry[0] == fewest]
    if len(shortlist) > MAX_MEASURED_CANDIDATES:
        step = len(shortlist) / MAX_MEASURED_CANDIDATES
        shortlist = [
            shortlist[int(index * step)] for index in range(MAX_MEASURED_CANDIDATES)
        ]

    best: PlateLayout | None = None
    for _, pad_x, pad_y, grid in shortlist:
        candidate = _measure(
            runs,
            integral,
            grid,
            image_width_px,
            image_height_px,
            pad_x,
            pad_y,
            mm_per_px,
            plate_size_mm,
            small_part_fraction,
        )
        if best is None or _ranking_key(candidate) < _ranking_key(best):
            best = candidate
    if best is None:
        raise RuntimeError("internal error: offset search produced no candidates")
    return best, current_layout(mask, mm_per_px, plate_size_mm, small_part_fraction)
