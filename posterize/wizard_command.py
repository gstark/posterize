"""Wizard choices and the posterize command line they produce.

Numeric fields stay as the text the user typed. The command builder only
emits a flag when its value differs from the effective default (the config
file value, or the built-in default), and the real CLI parser validates the
result before anything runs.
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

Mode = Literal["model", "colors"]
SizeBy = Literal["width", "height"]
OffsetMode = Literal["none", "optimize", "manual"]
Mount = Literal["none", "edge", "stud"]
Defaults = Mapping[str, object]

FALLBACK_PLATE_SIZE_MM = 256.0


def number_text(value: object) -> str:
    return f"{value:g}" if isinstance(value, int | float) else ""


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


@dataclass
class WizardChoices:
    defaults: Defaults = field(repr=False)
    image: str = ""
    mode: Mode = "model"
    size_by: SizeBy = "width"
    size_mm: str = ""
    split: bool = False
    plate_size: str = ""
    offset_mode: OffsetMode = "none"
    plate_offset: str = "0,0"
    per_plate_palette: bool = False
    dowel_holes: bool = True
    force_dowel_holes: bool = False
    max_filaments: str = ""
    filaments: str = ""
    color_distance: str = ""
    alpha_threshold: str = ""
    base: bool = True
    base_height: str = ""
    base_color: str = ""
    base_mask: str = ""
    raised_height: str = ""
    mount: Mount = "none"
    keyhole_inset: str = ""
    keyhole_offset: str = ""
    keyhole_edge_clearance: str = ""
    build_dir: str = ""
    output_dir: str = ""
    openscad: str = ""
    potrace: str = ""
    command_metadata: bool = False

    @classmethod
    def from_defaults(cls, defaults: Defaults) -> WizardChoices:
        """Start every field at its effective default."""

        d = defaults
        plate_size = d["plate_size"]
        return cls(
            defaults=d,
            split=plate_size is not None,
            plate_size=number_text(plate_size or FALLBACK_PLATE_SIZE_MM),
            offset_mode="optimize" if d["optimize_plate_offset"] else "none",
            per_plate_palette=bool(d["per_plate_palette"]),
            dowel_holes=bool(d["dowel_holes"]),
            force_dowel_holes=bool(d["force_dowel_holes"]),
            max_filaments=number_text(d["max_filaments"]),
            filaments=_text(d["filaments"]),
            color_distance=number_text(d["color_distance"]),
            alpha_threshold=number_text(d["alpha_threshold"]),
            base=not d["no_base"],
            base_height=number_text(d["base_height"]),
            base_color=_text(d["base_color"]),
            raised_height=number_text(d["raised_height"]),
            keyhole_inset=number_text(d["keyhole_inset"]),
            keyhole_offset=number_text(d["keyhole_offset"]),
            keyhole_edge_clearance=number_text(d["keyhole_edge_clearance"]),
            build_dir=_text(d["build_dir"]),
            output_dir=_text(d["output_dir"]),
            openscad=_text(d["openscad"]),
            potrace=_text(d["potrace"]),
            command_metadata=bool(d["include_command_metadata"]),
        )

    @property
    def uses_plates(self) -> bool:
        return self.mode == "model" and self.split

    def to_argv(self) -> list[str]:
        """Return CLI arguments, without the program name."""

        argv = [self.image.strip()]
        if self.mode == "colors":
            argv.append("--optimal-filament-colors")
            self._number(argv, "--max-filaments", self.max_filaments)
            self._number(argv, "--alpha-threshold", self.alpha_threshold)
            self._number(argv, "--color-distance", self.color_distance)
            return argv

        if self.size_mm.strip():
            argv += [f"--{self.size_by}", self.size_mm.strip()]
        if self.uses_plates:
            self._number(argv, "--plate-size", self.plate_size)
            if self.offset_mode == "manual":
                argv += ["--plate-offset", self.plate_offset.replace(" ", "")]
            else:
                self._switch(
                    argv, "--optimize-plate-offset", self.offset_mode == "optimize"
                )
            self._switch(argv, "--per-plate-palette", self.per_plate_palette)
        elif self.defaults["plate_size"] is not None:
            argv.append("--single-plate")

        self._number(argv, "--max-filaments", self.max_filaments)
        self._text(argv, "--filaments", self.filaments)
        self._number(argv, "--color-distance", self.color_distance)
        self._number(argv, "--alpha-threshold", self.alpha_threshold)

        if self.base:
            self._number(argv, "--base-height", self.base_height)
            if _hex_key(self.base_color) != _hex_key(
                _text(self.defaults["base_color"])
            ):
                argv += ["--base-color", self.base_color.strip()]
            self._text(argv, "--base-mask", self.base_mask)
        else:
            argv.append("--no-base")
        self._number(argv, "--raised-height", self.raised_height)

        if self.uses_plates and self.base:
            self._switch(argv, "--dowel-holes", self.dowel_holes)
            if self.dowel_holes:
                self._switch(argv, "--force-dowel-holes", self.force_dowel_holes)

        if self.base and self.mount != "none":
            if self.mount == "stud":
                argv.append("--wall")
            else:
                argv.append("--keyhole")
                self._number(argv, "--keyhole-inset", self.keyhole_inset)
            self._number(argv, "--keyhole-offset", self.keyhole_offset)
            self._number(argv, "--keyhole-edge-clearance", self.keyhole_edge_clearance)

        self._text(argv, "--build-dir", self.build_dir)
        self._text(argv, "--output-dir", self.output_dir)
        self._text(argv, "--openscad", self.openscad)
        self._text(argv, "--potrace", self.potrace)
        self._switch(
            argv,
            "--command-metadata",
            self.command_metadata,
            "include_command_metadata",
        )
        return argv

    def command_line(self) -> str:
        return format_command(self.to_argv())

    def _default(self, flag: str) -> object:
        return self.defaults[flag.removeprefix("--").replace("-", "_")]

    def _number(self, argv: list[str], flag: str, raw: str) -> None:
        """Append ``flag raw`` unless ``raw`` is blank or equals the default.

        Text that is not a number is passed through so the CLI reports it.
        """

        text = raw.strip()
        if not text:
            return
        default = self._default(flag)
        try:
            if isinstance(default, int | float) and float(text) == default:
                return
        except ValueError:
            pass
        argv += [flag, text]

    def _text(self, argv: list[str], flag: str, raw: str) -> None:
        text = raw.strip()
        if text and text != self._default(flag):
            argv += [flag, text]

    def _switch(
        self, argv: list[str], flag: str, value: bool, dest: str | None = None
    ) -> None:
        default = self.defaults[dest] if dest else self._default(flag)
        if value != bool(default):
            argv.append(flag if value else f"--no-{flag.removeprefix('--')}")


def _hex_key(value: str) -> str:
    return value.strip().lstrip("#").upper()


def format_command(argv: list[str]) -> str:
    return shlex.join(["posterize", *argv])
