"""User configuration file that overrides CLI defaults.

Precedence is: command-line flag, then the config file, then the built-in
default. The file is TOML and holds only the settings the user changed.
"""

from __future__ import annotations

import math
import os
import re
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from posterize.errors import UserFacingError

CONFIG_ENV_VAR = "POSTERIZE_CONFIG"

ConfigValue = str | int | float | bool
ConfigValues = dict[str, ConfigValue]


@dataclass(frozen=True)
class Setting:
    """One config key. ``key`` is also the argparse destination."""

    key: str
    kind: type[ConfigValue]
    section: str
    label: str
    help: str = ""
    minimum: float | None = None
    maximum: float | None = None
    exclusive_minimum: bool = False


SETTINGS: tuple[Setting, ...] = (
    Setting(
        "default_width",
        float,
        "Size and plates",
        "Print width in mm",
        "Used when a run sets neither --width nor --height.",
        minimum=0,
        exclusive_minimum=True,
    ),
    Setting(
        "plate_size",
        float,
        "Size and plates",
        "Plate size in mm",
        "Set this to always split prints into plates. --single-plate turns it off.",
        minimum=0,
        exclusive_minimum=True,
    ),
    Setting(
        "optimize_plate_offset",
        bool,
        "Size and plates",
        "Search for a seam shift that needs fewer parts",
    ),
    Setting(
        "per_plate_palette",
        bool,
        "Size and plates",
        "Pick filaments for each plate",
    ),
    Setting(
        "dowel_holes",
        bool,
        "Size and plates",
        "Dowel alignment holes",
        "6 mm holes on shared plate edges.",
    ),
    Setting(
        "force_dowel_holes",
        bool,
        "Size and plates",
        "Keep dowel holes where the base is thin",
    ),
    Setting(
        "max_filaments",
        int,
        "Colors",
        "Most filaments to use",
        minimum=1,
    ),
    Setting(
        "color_distance",
        float,
        "Colors",
        "Color merge distance",
        "Colors closer than this RGB distance count as one color.",
        minimum=0,
    ),
    Setting(
        "alpha_threshold",
        int,
        "Colors",
        "Opacity cut-off",
        "Pixels with alpha below this value are empty. Use 0 to 255.",
        minimum=0,
        maximum=255,
    ),
    Setting(
        "base_height",
        float,
        "Base",
        "Base thickness in mm",
        minimum=0,
        exclusive_minimum=True,
    ),
    Setting(
        "base_color",
        str,
        "Base",
        "Base color",
        "A hex color, such as #1A1A1A.",
    ),
    Setting(
        "raised_height",
        float,
        "Base",
        "Color layer height in mm",
        minimum=0,
        exclusive_minimum=True,
    ),
    Setting(
        "keyhole_inset",
        float,
        "Wall mount",
        "Keyhole inset, in percent of plate width",
        minimum=0,
    ),
    Setting(
        "keyhole_offset",
        float,
        "Wall mount",
        "Keyhole sideways shift in mm",
    ),
    Setting(
        "keyhole_edge_clearance",
        float,
        "Wall mount",
        "Keyhole edge clearance in mm",
        minimum=0,
    ),
    Setting("output_dir", str, "Files and tools", "Output folder"),
    Setting("build_dir", str, "Files and tools", "Build folder"),
    Setting("openscad", str, "Files and tools", "OpenSCAD program"),
    Setting("potrace", str, "Files and tools", "potrace program"),
    Setting(
        "include_command_metadata",
        bool,
        "Files and tools",
        "Save the command in the 3MF",
        "Off by default, because arguments can contain secrets.",
    ),
    Setting(
        "filaments",
        str,
        "Filaments",
        "Filament library file",
        "Leave empty to use filaments.json next to the config file. "
        "Use a full path so it works from any folder.",
    ),
)
SETTINGS_BY_KEY = {setting.key: setting for setting in SETTINGS}
PATH_KEYS = frozenset({"filaments", "output_dir", "build_dir", "openscad", "potrace"})
_HEX_COLOR = re.compile(r"#?[0-9a-fA-F]{6}")


def config_path() -> Path:
    """Return the config file location for this platform."""

    override = os.environ.get(CONFIG_ENV_VAR)
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        base = Path(os.environ["APPDATA"])
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "posterize" / "config.toml"


def default_filaments_path() -> Path:
    """Return the filament library location used when no path is configured."""

    return config_path().parent / "filaments.json"


def load_config(path: Path | None = None) -> ConfigValues:
    """Read and validate the config file. A missing file means no overrides."""

    path = config_path() if path is None else path
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as exc:
        raise UserFacingError(f"cannot read config file {path}: {exc}") from None
    except tomllib.TOMLDecodeError as exc:
        raise UserFacingError(f"config file {path} is not valid TOML: {exc}") from None

    values: ConfigValues = {}
    for key, value in raw.items():
        setting = SETTINGS_BY_KEY.get(key)
        if setting is None:
            raise UserFacingError(f"config file {path}: unknown setting {key!r}")
        problem = setting_problem(setting, value)
        if problem:
            raise UserFacingError(f"config file {path}: {key} {problem}")
        values[key] = _normalized(setting, value)
    return values


def setting_problem(setting: Setting, value: object) -> str | None:
    """Return why ``value`` is not valid for ``setting``, or None."""

    if setting.kind is bool:
        return None if isinstance(value, bool) else "must be true or false"
    if setting.kind is str:
        if not isinstance(value, str) or not value.strip():
            return "must be a non-empty string"
        if setting.key == "base_color" and not _HEX_COLOR.fullmatch(value.strip()):
            return "must be a hex color such as #1A1A1A"
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return "must be a number"
    if setting.kind is int and not isinstance(value, int):
        return "must be a whole number"
    if not math.isfinite(value):
        return "must be finite"
    if setting.minimum is not None:
        if setting.exclusive_minimum and value <= setting.minimum:
            return f"must be greater than {setting.minimum:g}"
        if value < setting.minimum:
            return f"must be at least {setting.minimum:g}"
    if setting.maximum is not None and value > setting.maximum:
        return f"must be at most {setting.maximum:g}"
    return None


def _normalized(setting: Setting, value: ConfigValue) -> ConfigValue:
    if setting.kind is float:
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        return os.path.expanduser(text) if setting.key in PATH_KEYS else text
    return value


def format_config(values: Mapping[str, ConfigValue]) -> str:
    """Return TOML text for ``values``, grouped by section in schema order."""

    lines: list[str] = []
    section = ""
    for setting in SETTINGS:
        if setting.key not in values:
            continue
        if setting.section != section:
            if lines:
                lines.append("")
            lines.append(f"# {setting.section}")
            section = setting.section
        lines.append(f"{setting.key} = {_toml_value(values[setting.key])}")
    return "\n".join(lines) + "\n" if lines else ""


def _toml_value(value: ConfigValue) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def save_config(values: Mapping[str, ConfigValue], path: Path | None = None) -> Path:
    path = config_path() if path is None else path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(format_config(values), encoding="utf-8")
    return path
