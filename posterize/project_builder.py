"""Checked 3MF project assembly helpers."""

from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from typing import Any, TypeAlias

from posterize.color_filament import Filament
from posterize.dowels import DowelSpecMap
from posterize.errors import OutputAssemblyError
from posterize.external_tools import ExternalTools
from posterize.formatting import format_plate_label, select_keyhole_outline_extruder
from posterize.keyholes import KEYHOLE_OUTLINE_PART, KeyholeMap
from posterize.paths import Pathish, as_path as _as_path
from posterize.scad import SCAD_BASE_PART, SCAD_DOWEL_PART
from posterize.scad_rendering import render_stl
from posterize.threemf import (
    BambuProjectOptions,
    ColorSlot,
    BAMBU_FILAMENT_SETTINGS_ID,
    PackageMetadata,
    bambu_filament_payloads,
    project_class_or_fallback,
)

try:
    from bambu3mf import BambuProject
except ModuleNotFoundError:  # pragma: no cover - exercised in CLI validation tests
    BambuProject = None

StlEntry: TypeAlias = tuple[str, int]
PlateBuildTuple: TypeAlias = tuple[float, float, float, float, int, int]
StlRenderer: TypeAlias = Callable[..., bool]


def require_bambu3mf() -> Any:
    """Return the BambuProject class, falling back at the 3MF boundary."""
    return project_class_or_fallback(BambuProject)


def stl_entry_paths(stl_entries: Iterable[StlEntry]) -> str:
    return ", ".join(path for path, _ in stl_entries)


def create_project_checked(
    project_cls: Any,
    filaments: list[dict[str, str] | None],
    command_line: list[str] | None,
    include_command_metadata: bool = False,
) -> Any:
    options = BambuProjectOptions(
        filaments=filaments,
        metadata=PackageMetadata(
            command_line=command_line,
            include_command_metadata=include_command_metadata,
        ),
    )
    try:
        return project_cls(**options.constructor_kwargs())
    except Exception as exc:
        raise OutputAssemblyError(f"failed to initialize 3MF project: {exc}") from None


def add_plate_checked(project: Any, plate_name: str) -> Any:
    try:
        return project.add_plate(name=plate_name)
    except Exception as exc:
        raise OutputAssemblyError(
            f"failed to create 3MF plate {plate_name}: {exc}"
        ) from None


def add_stl_group_checked(
    bambu_plate: Any, stl_entries: Sequence[StlEntry], plate_name: str
) -> None:
    try:
        bambu_plate.add_stl_group(stl_entries)
    except Exception as exc:
        raise OutputAssemblyError(
            f"failed to add rendered STL files to plate {plate_name}: "
            f"{stl_entry_paths(stl_entries)}; {exc}"
        ) from None


def save_project_checked(project: Any, output_path: Pathish) -> None:
    output_file = _as_path(output_path)
    try:
        project.save(os.fspath(output_file))
    except Exception as exc:
        raise OutputAssemblyError(f"failed to write 3MF {output_file}: {exc}") from None


def build_dowel_3mf(
    scad_path: Pathish,
    output_path: Pathish,
    base_color: str,
    target_width: float,
    tmpdir: Pathish,
    command_line: list[str] | None = None,
    tools: ExternalTools | None = None,
    include_command_metadata: bool = False,
    stl_renderer: StlRenderer | None = None,
) -> int:
    """Build a companion 3MF containing a single printable dowel in base color."""
    tmp_path = _as_path(tmpdir)
    tmp_path.mkdir(parents=True, exist_ok=True)
    output_file = _as_path(output_path)
    dowel_slot = ColorSlot("dowel", base_color, 1)
    filaments: list[dict[str, str] | None] = [
        {
            "settings_id": BAMBU_FILAMENT_SETTINGS_ID,
            "colour": dowel_slot.bambu_colour,
        }
    ]

    project_cls = require_bambu3mf()
    project = create_project_checked(
        project_cls,
        filaments,
        command_line,
        include_command_metadata=include_command_metadata,
    )
    bambu_plate = add_plate_checked(project, "dowel")
    stl_path = tmp_path / "dowel.stl"
    renderer = stl_renderer or render_stl
    ok = renderer(
        scad_path,
        SCAD_DOWEL_PART,
        target_width,
        stl_path,
        tools=tools,
    )
    if not ok:
        return 0

    add_stl_group_checked(
        bambu_plate,
        [(os.fspath(stl_path), dowel_slot.slot)],
        "dowel",
    )
    save_project_checked(project, output_file)
    return 1


def build_3mf(
    scad_path: Pathish,
    output_path: Pathish,
    color_slots: Sequence[ColorSlot],
    target_width: float,
    plates: Iterable[PlateBuildTuple],
    tmpdir: Pathish,
    filament_slots: dict[int, Filament] | None = None,
    keyhole_map: KeyholeMap | None = None,
    dowel_map: DowelSpecMap | None = None,
    command_line: list[str] | None = None,
    tools: ExternalTools | None = None,
    include_command_metadata: bool = False,
) -> int:
    """Build a single 3MF with each model tile on its own Bambu build plate.

    ``plates`` contains SCAD clipping
    rectangles as ``(plate_x, plate_y, plate_w, plate_h, row, col)``. ``keyhole_map``
    and ``dowel_map`` are keyed by zero-based ``(row, col)`` plate coordinates.
    The return value is the number of non-empty plates written into the project.
    """
    filament_slots = filament_slots or {}
    filaments = bambu_filament_payloads(color_slots, filament_slots)
    tmp_path = _as_path(tmpdir)
    output_file = _as_path(output_path)

    project_cls = require_bambu3mf()
    project = create_project_checked(
        project_cls,
        filaments,
        command_line,
        include_command_metadata=include_command_metadata,
    )

    plate_count = 0
    plates_by_row = defaultdict(list)
    for plate in plates:
        plates_by_row[plate[4]].append(plate)
    for row in plates_by_row:
        plates_by_row[row].sort(key=lambda plate: plate[5])

    keyhole_outline_extruder = select_keyhole_outline_extruder(color_slots)

    for row in sorted(plates_by_row):
        for plate_x, plate_y, plate_w, plate_h, plate_row, col in plates_by_row[row]:
            if plate_row != row:
                raise OutputAssemblyError("internal plate row grouping mismatch")
            plate_name = format_plate_label(row, col)
            print(f"  Plate {plate_name}...", end="", flush=True)
            bambu_plate = add_plate_checked(project, plate_name)
            stl_entries = []
            plate_keyhole_pos = keyhole_map.get((row, col), []) if keyhole_map else []
            for part_idx, color_slot in enumerate(color_slots):
                part_num = SCAD_BASE_PART + part_idx
                stl_path = tmp_path / f"{color_slot.name}_{plate_name}.stl"
                keyhole_pos = plate_keyhole_pos if part_num == SCAD_BASE_PART else []
                plate_dowels = dowel_map.get((row, col)) if dowel_map else None
                ok = render_stl(
                    scad_path,
                    part_num,
                    target_width,
                    stl_path,
                    plate_x=plate_x,
                    plate_y=plate_y,
                    plate_w=plate_w,
                    plate_h=plate_h,
                    keyhole_positions=keyhole_pos,
                    dowel_spec=plate_dowels if part_num == SCAD_BASE_PART else None,
                    tools=tools,
                )
                if not ok:
                    continue
                stl_entries.append((os.fspath(stl_path), color_slot.slot))

            if plate_keyhole_pos and keyhole_outline_extruder is not None:
                outline_path = tmp_path / f"keyhole_outline_{plate_name}.stl"
                ok = render_stl(
                    scad_path,
                    KEYHOLE_OUTLINE_PART,
                    target_width,
                    outline_path,
                    plate_x=plate_x,
                    plate_y=plate_y,
                    plate_w=plate_w,
                    plate_h=plate_h,
                    keyhole_positions=plate_keyhole_pos,
                    tools=tools,
                )
                if ok:
                    stl_entries.append(
                        (os.fspath(outline_path), keyhole_outline_extruder)
                    )

            if not stl_entries:
                print(" (empty)")
                continue

            add_stl_group_checked(bambu_plate, stl_entries, plate_name)
            has_keyholes = bool(keyhole_map and keyhole_map.get((row, col)))
            suffix = " [keyholes]" if has_keyholes else ""
            print(f" done{suffix}")
            plate_count += 1

    if plate_count == 0:
        return 0

    save_project_checked(project, output_file)
    return plate_count
