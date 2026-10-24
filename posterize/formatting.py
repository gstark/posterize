"""Small formatting and color-conversion helpers shared by pipeline modules."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeAlias, cast

from posterize import color_filament
from posterize.color_filament import LabColor, RGBColor
from posterize.errors import InputValidationError
from posterize.plate_grid import PlateGrid
from posterize.threemf import ColorSlot

RGBTuple: TypeAlias = tuple[int, int, int]


def rgb_to_lab(r: int, g: int, b: int) -> LabColor:
    """Convert sRGB (0-255) to CIELAB using the color/filament module."""
    return color_filament.rgb_to_lab(RGBColor(r, g, b))


def ciede2000(lab1: LabColor, lab2: LabColor) -> float:
    """Return the perceptual CIEDE2000 color difference between two Lab colors."""
    return color_filament.ciede2000(lab1, lab2)


def _hex_payload(
    value: object,
    field_name: str,
    allow_hash: bool = False,
    allow_alpha: bool = False,
) -> str:
    if not isinstance(value, str):
        raise InputValidationError(f"{field_name} must be a string")
    payload = value[1:] if allow_hash and value.startswith("#") else value
    valid_lengths = (6, 8) if allow_alpha else (6,)
    if len(payload) not in valid_lengths or any(
        digit not in "0123456789abcdefABCDEF" for digit in payload
    ):
        expected = "#RRGGBB or RRGGBB" if allow_hash else "RRGGBB"
        if allow_alpha:
            expected += " with optional AA alpha"
        raise InputValidationError(f"{field_name} must be {expected} hexadecimal")
    return payload[:6]


def hex_to_rgb(hex_color: str) -> RGBTuple:
    """Convert a #RRGGBB or #RRGGBBAA string to an (r, g, b) tuple."""
    value = _hex_payload(
        hex_color,
        "hex color",
        allow_hash=True,
        allow_alpha=True,
    )
    return cast(RGBTuple, tuple(int(value[i : i + 2], 16) for i in (0, 2, 4)))


def select_keyhole_outline_extruder(
    color_slots: Sequence[ColorSlot],
) -> int | None:
    """Pick a consistent non-base color with the strongest contrast to the base."""
    if len(color_slots) < 2:
        return None
    base_lab = rgb_to_lab(*hex_to_rgb(color_slots[0].rgba_hex))
    return max(
        color_slots[1:],
        key=lambda slot: ciede2000(base_lab, rgb_to_lab(*hex_to_rgb(slot.rgba_hex))),
    ).slot


def format_plate_label(row: int, col: int) -> str:
    """Return a human-facing 1-based plate label like ``r1_c2``."""
    return PlateGrid.label_for(row, col)
