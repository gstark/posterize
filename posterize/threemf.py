"""Typed 3MF assembly boundary for Bambu Studio projects."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from posterize.color_filament import Filament, FilamentMatch, hex_payload

BAMBU_PRINTER_SETTINGS_ID = "Bambu Lab P1S 0.4 nozzle"
BAMBU_PRINT_SETTINGS_ID = "0.20mm Standard @BBL X1C"
BAMBU_FILAMENT_SETTINGS_ID = "Bambu PLA Matte @BBL P1S 0.4 nozzle"
BASE_PART_NAME = "base"

try:  # pragma: no cover - import availability varies by environment
    from bambu3mf import BambuProject as ImportedBambuProject
except ModuleNotFoundError:  # pragma: no cover - exercised through fallback use
    ImportedBambuProject = None


class _FallbackBambuPlate:
    """Minimal stand-in used when bambu3mf is unavailable."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.stl_groups: list[list[tuple[str, int]]] = []

    def add_stl_group(self, stl_entries: Iterable[tuple[str, int]]) -> None:
        self.stl_groups.append(list(stl_entries))


class _FallbackBambuProject:
    """Minimal stand-in used by tests and CLI validation paths."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.plates: list[_FallbackBambuPlate] = []

    def add_plate(self, name: str) -> _FallbackBambuPlate:
        plate = _FallbackBambuPlate(name)
        self.plates.append(plate)
        return plate

    def save(self, output_path: str | os.PathLike[str]) -> None:
        output_file = Path(output_path)
        if output_file.parent != Path(""):
            output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_bytes(b"fallback 3mf placeholder")


@dataclass(frozen=True)
class ColorSlot:
    """One printable color part and the 1-based Bambu AMS/extruder slot it uses."""

    name: str
    rgba_hex: str
    slot: int

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("color slot name must be a non-empty string")
        payload = hex_payload(
            self.rgba_hex,
            "color slot hex",
            allow_hash=True,
            allow_alpha=True,
        )
        if type(self.slot) is not int or self.slot <= 0:
            raise ValueError("color slot number must be a positive integer")
        object.__setattr__(self, "rgba_hex", f"#{payload}FF")

    @property
    def bambu_colour(self) -> str:
        """Return the #RRGGBB color expected by bambu3mf filament metadata."""

        return self.rgba_hex[:7]


@dataclass(frozen=True)
class BambuFilamentSlot:
    """Bambu filament payload assigned to a 1-based slot."""

    slot: int
    colour: str
    settings_id: str = BAMBU_FILAMENT_SETTINGS_ID

    def as_payload(self) -> dict[str, str]:
        return {"settings_id": self.settings_id, "colour": self.colour}


@dataclass(frozen=True)
class PackageMetadata:
    """User-visible metadata stored in the Bambu project."""

    command_line: list[str] | None = None
    include_command_metadata: bool = False

    def description(self) -> str | None:
        if not self.include_command_metadata or not self.command_line:
            return None
        return " ".join(self.command_line)


@dataclass(frozen=True)
class BambuProjectOptions:
    """Options passed across the 3MF project construction boundary."""

    filaments: list[dict[str, str] | None]
    metadata: PackageMetadata = PackageMetadata()
    printer_settings_id: str = BAMBU_PRINTER_SETTINGS_ID
    print_settings_id: str = BAMBU_PRINT_SETTINGS_ID

    def constructor_kwargs(self) -> dict[str, Any]:
        return {
            "printer_settings_id": self.printer_settings_id,
            "print_settings_id": self.print_settings_id,
            "filaments": self.filaments,
            "description": self.metadata.description(),
        }


def project_class_or_fallback(project_cls: Any | None = None) -> Any:
    """Return a BambuProject class, using the local fallback only at this boundary."""

    return project_cls or ImportedBambuProject or _FallbackBambuProject


def assign_color_slots(
    matched: Iterable[FilamentMatch],
    *,
    base_color: str,
    no_base: bool,
) -> tuple[list[ColorSlot], dict[int, Filament]]:
    """Assign sequential Bambu slots to the base and matched filament colors."""

    color_slots: list[ColorSlot] = []
    filament_slots: dict[int, Filament] = {}
    next_slot = 1
    if not no_base:
        base_hex = hex_payload(base_color, "base color", allow_hash=True)
        color_slots.append(ColorSlot(BASE_PART_NAME, f"#{base_hex}FF", next_slot))
        next_slot += 1
    for match in matched:
        slot = next_slot
        next_slot += 1
        color_slots.append(
            ColorSlot(match.filament.name, f"#{match.filament.hex}FF", slot)
        )
        filament_slots[slot] = match.filament
    return color_slots, filament_slots


def bambu_filament_payloads(
    color_slots: Iterable[ColorSlot],
    filament_slots: dict[int, Filament] | None = None,
) -> list[dict[str, str] | None]:
    """Build the ordered bambu3mf filament payload list from color slots."""

    normalized = list(color_slots)
    if not normalized:
        raise ValueError("at least one color slot is required to build a 3MF")
    filament_slots = filament_slots or {}
    num_slots = max(slot.slot for slot in normalized)
    filaments: list[dict[str, str] | None] = [None] * num_slots
    for color_slot in normalized:
        colour = color_slot.bambu_colour
        if color_slot.slot in filament_slots:
            colour = f"#{filament_slots[color_slot.slot].hex}"
        filaments[color_slot.slot - 1] = BambuFilamentSlot(
            color_slot.slot,
            colour,
        ).as_payload()
    return filaments
