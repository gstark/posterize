"""Lateral-depth sampling against source-image plate regions."""

from __future__ import annotations

import math
from collections.abc import Sequence


Mask = Sequence[Sequence[bool]]
PixelBoundsTuple = tuple[float, float, float, float]


def _coverage_indices(start_px: float, end_px: float, limit: int) -> tuple[int, int]:
    """Convert float half-open pixel bounds to clamped integer sample indices."""

    start = max(0, min(limit, math.floor(start_px)))
    end = max(0, min(limit, math.ceil(end_px)))
    return start, end


_EDGE_ALIASES = {
    "north": "north",
    "top": "north",
    "south": "south",
    "bottom": "south",
    "east": "east",
    "right": "east",
    "west": "west",
    "left": "west",
}


def _normalise_edge(edge: str) -> str:
    try:
        return _EDGE_ALIASES[edge.lower()]
    except AttributeError, KeyError:
        valid = ", ".join(sorted(_EDGE_ALIASES))
        raise ValueError(f"edge must be one of: {valid}") from None


def _normalise_plate_bounds(
    mask: Mask, plate_bounds: PixelBoundsTuple | None
) -> tuple[int, int, int, int]:
    height = len(mask)
    width = len(mask[0]) if height else 0
    if plate_bounds is None:
        return 0, 0, width, height

    x0, y0, x1, y1 = plate_bounds
    x0, x1 = _coverage_indices(x0, x1, width)
    y0, y1 = _coverage_indices(y0, y1, height)
    if x0 > x1:
        x0, x1 = x1, x0
    if y0 > y1:
        y0, y1 = y1, y0
    return x0, y0, x1, y1


def _edge_length_px(edge: str, bounds: tuple[int, int, int, int]) -> int:
    x0, y0, x1, y1 = bounds
    return (x1 - x0) if edge in ("north", "south") else (y1 - y0)


def _edge_ray_points(
    edge: str,
    bounds: tuple[int, int, int, int],
    offset_mm: float,
    mm_per_px: float,
) -> list[tuple[int, int]]:
    if mm_per_px <= 0:
        raise ValueError("mm_per_px must be positive")

    edge_length_px = _edge_length_px(edge, bounds)
    edge_length_mm = edge_length_px * mm_per_px
    if edge_length_px <= 0:
        return []
    if not 0 <= offset_mm < edge_length_mm:
        raise ValueError(
            f"offset_mm must be in [0, {edge_length_mm:.3f}) for this edge"
        )

    offset_px = min(edge_length_px - 1, int(offset_mm / mm_per_px))
    x0, y0, x1, y1 = bounds
    if edge == "north":
        x = x0 + offset_px
        return [(x, y) for y in range(y0, y1)]
    if edge == "south":
        x = x0 + offset_px
        return [(x, y) for y in range(y1 - 1, y0 - 1, -1)]
    if edge == "west":
        y = y0 + offset_px
        return [(x, y) for x in range(x0, x1)]
    if edge == "east":
        y = y0 + offset_px
        return [(x, y) for x in range(x1 - 1, x0 - 1, -1)]
    raise AssertionError(f"unsupported normalized edge: {edge}")


def compute_lateral_depth_at(
    mask: Mask,
    edge: str,
    offset_mm: float,
    mm_per_px: float = 1.0,
    plate_bounds: PixelBoundsTuple | None = None,
    require_edge_contact: bool = True,
) -> float:
    """Return model depth, in mm, along one inward ray from a plate edge.

    ``mask`` is a rectangular matrix where ``True`` means the generated base has
    material at that source pixel. ``edge`` is image-space north/south/east/west
    (top/bottom/right/left aliases are accepted). ``offset_mm`` is the distance
    along that edge from its top-left corner in image coordinates.

    By default the ray must start in material; if the edge pixel is transparent,
    the depth is zero. Set ``require_edge_contact=False`` to skip transparent
    pixels until the first material run, useful when measuring an external model
    silhouette rather than a cut plate edge. Internal transparent gaps terminate
    the measured depth.
    """
    normalized_edge = _normalise_edge(edge)
    bounds = _normalise_plate_bounds(mask, plate_bounds)
    ray_points = _edge_ray_points(normalized_edge, bounds, offset_mm, mm_per_px)
    if not ray_points:
        return 0.0

    material_started = False
    depth_px = 0
    for x, y in ray_points:
        has_material = mask[y][x]
        if not material_started:
            if has_material:
                material_started = True
                depth_px += 1
                continue
            if require_edge_contact:
                break
            continue
        if not has_material:
            break
        depth_px += 1

    return depth_px * mm_per_px


def compute_lateral_depth_profile(
    mask: Mask,
    edge: str,
    mm_per_px: float = 1.0,
    plate_bounds: PixelBoundsTuple | None = None,
    sample_offsets_mm: Sequence[float] | None = None,
    sample_spacing_mm: float | None = None,
    require_edge_contact: bool = True,
) -> list[tuple[float, float]]:
    """Return ``[(offset_mm, depth_mm), ...]`` samples for a plate edge.

    If ``sample_offsets_mm`` is omitted, the routine samples each source pixel
    along the edge at that pixel's center. Use ``sample_spacing_mm`` to request a
    coarser regular spacing, e.g. drawing diagnostic lines every 10mm.
    """
    normalized_edge = _normalise_edge(edge)
    bounds = _normalise_plate_bounds(mask, plate_bounds)
    edge_length_px = _edge_length_px(normalized_edge, bounds)
    edge_length_mm = edge_length_px * mm_per_px
    if edge_length_px <= 0:
        return []

    if sample_offsets_mm is None:
        spacing = sample_spacing_mm or mm_per_px
        if spacing <= 0:
            raise ValueError("sample_spacing_mm must be positive")
        sample_offsets_mm = []
        offset = spacing / 2
        while offset < edge_length_mm:
            sample_offsets_mm.append(offset)
            offset += spacing

    return [
        (
            offset,
            compute_lateral_depth_at(
                mask,
                normalized_edge,
                offset,
                mm_per_px=mm_per_px,
                plate_bounds=bounds,
                require_edge_contact=require_edge_contact,
            ),
        )
        for offset in sample_offsets_mm
    ]
