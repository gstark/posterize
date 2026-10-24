"""Dowel-hole placement and lateral-depth filtering for PlateGrid seams."""

from __future__ import annotations

from collections.abc import Sequence

from posterize.lateral_depth import Mask, compute_lateral_depth_at
from posterize.plate_grid import PlateGrid

DOWEL_HOLE_DEPTH_MM = 15.0
DOWEL_HOLE_DIAMETER_MM = 6.0
DOWEL_HOLE_CHAMFER_MM = 1.0
DOWEL_MIN_WALL_MM = 2.0

DOWEL_CLEARANCE_MM = 0.12
DOWEL_DIAMETER_MM = DOWEL_HOLE_DIAMETER_MM - DOWEL_CLEARANCE_MM
DOWEL_LENGTH_MM = 28.0
DOWEL_CHAMFER_MM = 0.75
DOWEL_RIB_HEIGHT_MM = 0.07
DOWEL_RIB_WIDTH_MM = 0.8
DOWEL_RIB_SPACING_MM = 3.0
DOWEL_PRINT_FLAT_DEPTH_MM = 0.25

DowelSpec = dict[str, list[float]]
DowelSpecMap = dict[tuple[int, int], DowelSpec]
DowelDepthCheck = tuple[int, int, str, tuple[float, float, float, float]]


def minimum_dowel_base_height() -> float:
    """Return the minimum base height that leaves sturdy walls around dowel holes."""
    return DOWEL_HOLE_DIAMETER_MM + 2 * DOWEL_MIN_WALL_MM


def dowel_hole_offsets(edge_length_mm: float) -> list[float]:
    """Return dense dowel center offsets with corner-safe margins.

    Adjacent candidate holes are separated by one dowel radius of material, so
    center-to-center spacing is one diameter plus one radius. End margins also
    keep side-entering dowel holes from intersecting perpendicular dowel holes
    near plate corners.
    """
    radius = DOWEL_HOLE_DIAMETER_MM / 2
    min_end_margin = DOWEL_HOLE_DEPTH_MM + radius + DOWEL_MIN_WALL_MM
    if edge_length_mm < min_end_margin * 2:
        return []

    usable_length = edge_length_mm - min_end_margin * 2
    center_spacing = DOWEL_HOLE_DIAMETER_MM + radius
    hole_count = int(usable_length // center_spacing) + 1
    if hole_count == 1:
        return [round(edge_length_mm / 2, 3)]

    used_span = (hole_count - 1) * center_spacing
    first_offset = min_end_margin + (usable_length - used_span) / 2
    return [round(first_offset + center_spacing * idx, 3) for idx in range(hole_count)]


def empty_dowel_spec() -> DowelSpec:
    return {"north": [], "south": [], "east": [], "west": []}


# Private alias retained for existing package internals that serialize SCAD args.
_empty_dowel_spec = empty_dowel_spec


def _plate_bounds_for(
    grid: PlateGrid, row: int, col: int
) -> tuple[float, float, float, float]:
    bounds = grid.pixel_bounds(row, col)
    return (bounds.x0, bounds.y0, bounds.x1, bounds.y1)


def _dowel_lateral_depth(
    mask: Mask,
    edge: str,
    offset_mm: float,
    plate_bounds: tuple[float, float, float, float],
    mm_per_px: float,
) -> float:
    return compute_lateral_depth_at(
        mask,
        edge,
        offset_mm,
        mm_per_px=mm_per_px,
        plate_bounds=plate_bounds,
    )


def _measurement_offset_for_edge(
    edge: str, offset_mm: float, plate_width_mm: float, plate_height_mm: float
) -> float:
    """Map a SCAD dowel offset to the top-left image-space offset.

    OpenSCAD places east/west dowels at ``tile_y0() + offset`` which is measured
    up from the bottom of the plate, while source mask rows are measured down
    from the top. North/south offsets are x offsets and do not need a flip.
    """
    if edge in ("east", "west"):
        edge_length = plate_height_mm
        return max(0.0, min(edge_length - 1e-9, edge_length - offset_mm))
    edge_length = plate_width_mm
    return max(0.0, min(edge_length - 1e-9, offset_mm))


def _format_mm(value: float) -> str:
    formatted = f"{value:.3f}".rstrip("0").rstrip(".")
    if formatted == "-0":
        formatted = "0"
    return f"{formatted}mm"


def _format_forced_dowel_location(
    checks: Sequence[DowelDepthCheck], offset_mm: float
) -> str:
    seam = " / ".join(
        f"{PlateGrid.label_for(row, col)} {edge}" for row, col, edge, _ in checks
    )
    return f"{seam} offset {_format_mm(offset_mm)}"


def _dowel_edge_sample_offsets(offset_mm: float) -> tuple[float, float, float]:
    radius = DOWEL_HOLE_DIAMETER_MM / 2
    return (offset_mm, offset_mm - radius, offset_mm + radius)


def _filter_dowel_offsets_by_lateral_depth(
    offsets: Sequence[float],
    mask: Mask | None,
    checks: Sequence[DowelDepthCheck],
    mm_per_px: float,
    min_lateral_depth_mm: float,
    force_dowel_holes: bool = False,
) -> list[float]:
    """Keep seam dowel offsets with enough material on every adjacent plate.

    ``checks`` contains the two plate-edge samples that share a candidate seam.
    The function samples the center and both radius extremes of each candidate
    hole so a dowel is not kept when only a thin line of material exists.
    """

    if mask is None:
        return list(offsets)

    filtered: list[float] = []
    for offset in offsets:
        depths: list[float] = []
        for _, _, edge, bounds in checks:
            x0, y0, x1, y1 = bounds
            plate_width_mm = (x1 - x0) * mm_per_px
            plate_height_mm = (y1 - y0) * mm_per_px
            sample_depths = []
            for sample_offset in _dowel_edge_sample_offsets(offset):
                measurement_offset = _measurement_offset_for_edge(
                    edge, sample_offset, plate_width_mm, plate_height_mm
                )
                sample_depths.append(
                    _dowel_lateral_depth(
                        mask, edge, measurement_offset, bounds, mm_per_px
                    )
                )
            depths.append(min(sample_depths))
        if all(depth >= min_lateral_depth_mm for depth in depths):
            filtered.append(offset)
        elif force_dowel_holes:
            filtered.append(offset)
            print(
                "Adding a dowel hole at "
                f"{_format_forced_dowel_location(checks, offset)} "
                "even though the depth is only "
                f"{_format_mm(min(depths))} and we need "
                f"{_format_mm(min_lateral_depth_mm)}"
            )
    return filtered


def compute_dowel_specs(
    grid: PlateGrid,
    mask: Mask | None = None,
    min_lateral_depth_mm: float = DOWEL_HOLE_DEPTH_MM,
    force_dowel_holes: bool = False,
) -> DowelSpecMap:
    """Build matching per-plate dowel-hole positions for shared seams.

    Each candidate on a shared seam is kept only if both adjacent plates have
    enough contiguous material inward from the authoritative ``PlateGrid`` seam
    for a blind dowel hole of ``min_lateral_depth_mm``. When
    ``force_dowel_holes`` is true, depth-failing candidate holes are kept and a
    warning is printed with the measured depth.
    """
    specs = {
        (row, col): empty_dowel_spec()
        for row in range(grid.rows)
        for col in range(grid.cols)
    }

    row_heights = grid.row_heights_mm()
    column_widths = grid.column_widths_mm()
    mm_per_px = grid.mm_per_px

    for row in range(grid.rows):
        edge_length = row_heights[row]
        base_offsets = dowel_hole_offsets(edge_length)
        for col in range(grid.cols - 1):
            offsets = _filter_dowel_offsets_by_lateral_depth(
                base_offsets,
                mask,
                [
                    (row, col, "east", _plate_bounds_for(grid, row, col)),
                    (row, col + 1, "west", _plate_bounds_for(grid, row, col + 1)),
                ],
                mm_per_px,
                min_lateral_depth_mm,
                force_dowel_holes=force_dowel_holes,
            )
            specs[(row, col)]["east"] = offsets
            specs[(row, col + 1)]["west"] = offsets

    for row in range(grid.rows - 1):
        for col in range(grid.cols):
            edge_length = column_widths[col]
            base_offsets = dowel_hole_offsets(edge_length)
            offsets = _filter_dowel_offsets_by_lateral_depth(
                base_offsets,
                mask,
                [
                    (row, col, "south", _plate_bounds_for(grid, row, col)),
                    (row + 1, col, "north", _plate_bounds_for(grid, row + 1, col)),
                ],
                mm_per_px,
                min_lateral_depth_mm,
                force_dowel_holes=force_dowel_holes,
            )
            specs[(row, col)]["south"] = offsets
            specs[(row + 1, col)]["north"] = offsets

    return specs
