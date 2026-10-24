"""Typed color detection, posterization, and filament matching.

This module owns the color science and filament-library schema. Public values use
small dataclasses instead of positional tuples or untyped dictionaries.
"""

from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, cast

from PIL import Image, UnidentifiedImageError


class ColorFilamentError(ValueError):
    """Color/filament input or matching failed with a user-facing message."""


class DuplicateFilamentAssignmentError(ColorFilamentError):
    """Two detected image colors were assigned to the same filament."""


@dataclass(frozen=True)
class RGBColor:
    """An sRGB color with 8-bit channels."""

    red: int
    green: int
    blue: int

    def __post_init__(self) -> None:
        for channel_name, channel in (
            ("red", self.red),
            ("green", self.green),
            ("blue", self.blue),
        ):
            if type(channel) is not int or not 0 <= channel <= 255:
                raise ColorFilamentError(
                    f"RGB {channel_name} channel must be an integer between 0 and 255"
                )

    @classmethod
    def from_sequence(cls, value: object, field_name: str) -> RGBColor:
        if (
            not isinstance(value, list)
            or len(value) != 3
            or any(
                type(channel) is not int or not 0 <= channel <= 255 for channel in value
            )
        ):
            raise ColorFilamentError(
                f"{field_name} must be three integers between 0 and 255"
            )
        return cls(value[0], value[1], value[2])

    @classmethod
    def from_hex(cls, value: str, field_name: str = "hex color") -> RGBColor:
        payload = hex_payload(value, field_name, allow_hash=True, allow_alpha=True)
        return cls(
            int(payload[0:2], 16),
            int(payload[2:4], 16),
            int(payload[4:6], 16),
        )

    def as_tuple(self) -> tuple[int, int, int]:
        return (self.red, self.green, self.blue)

    def to_hex(self) -> str:
        return f"{self.red:02X}{self.green:02X}{self.blue:02X}"


@dataclass(frozen=True)
class LabColor:
    """CIELAB color using sRGB's D65 reference white."""

    lightness: float
    a: float
    b: float


@dataclass(frozen=True)
class DetectedColor:
    """A detected image color and its opaque pixel count."""

    rgb: RGBColor
    hex: str
    pixel_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.rgb, RGBColor):
            raise ColorFilamentError("detected color rgb must be an RGBColor object")
        hex_value = hex_payload(self.hex, "detected color hex")
        if hex_value != self.rgb.to_hex():
            raise ColorFilamentError("detected color hex must match rgb")
        if type(self.pixel_count) is not int or self.pixel_count < 0:
            raise ColorFilamentError(
                "detected color pixel_count must be a non-negative integer"
            )
        object.__setattr__(self, "hex", hex_value)


@dataclass(frozen=True)
class DetectedColors:
    """Detected image colors plus the source image dimensions."""

    colors: list[DetectedColor]
    width: int
    height: int


@dataclass(frozen=True)
class Filament:
    """A validated filament-library entry."""

    name: str
    hex: str
    rgb: RGBColor

    def __post_init__(self) -> None:
        name = _validate_filament_name(self.name)
        hex_value = hex_payload(self.hex, "filament hex")
        if not isinstance(self.rgb, RGBColor):
            raise ColorFilamentError("filament rgb must be an RGBColor object")
        if hex_value != self.rgb.to_hex():
            raise ColorFilamentError("filament hex must match rgb")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "hex", hex_value)


@dataclass(frozen=True)
class FilamentCoverage:
    """How many image pixels selected a filament as nearest."""

    filament_name: str
    pixel_count: int


@dataclass(frozen=True)
class FilamentSelection:
    """Selected filaments and the complete nearest-filament ranking."""

    selected: list[Filament]
    ranked: list[FilamentCoverage]


@dataclass(frozen=True)
class FilamentMatch:
    """A detected image color mapped to its nearest filament."""

    filament: Filament
    image_color: DetectedColor
    distance: float


SAMPLE_FILAMENTS_PATH = Path(__file__).parent / "assets" / "filaments.json"
DEFAULT_ALPHA_THRESHOLD = 128
DEFAULT_COLOR_DISTANCE = 30
RESERVED_FILAMENT_NAMES = frozenset({"base", "keyhole_outline"})


def _validate_alpha_threshold(value: object) -> int:
    if type(value) is not int or not 0 <= value <= 255:
        raise ColorFilamentError("alpha_threshold must be an integer between 0 and 255")
    return value


def _validate_color_distance(value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ColorFilamentError("color_distance must be non-negative and finite")
    return float(value)


def _validate_max_count(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise ColorFilamentError("max_filaments must be a positive integer")
    return value


def hex_payload(
    value: object,
    field_name: str,
    *,
    allow_hash: bool = False,
    allow_alpha: bool = False,
) -> str:
    """Return the normalized six-digit RGB payload from a hex color string."""
    if not isinstance(value, str):
        raise ColorFilamentError(f"{field_name} must be a string")
    payload = value[1:] if allow_hash and value.startswith("#") else value
    valid_lengths = (6, 8) if allow_alpha else (6,)
    if len(payload) not in valid_lengths or any(
        digit not in "0123456789abcdefABCDEF" for digit in payload
    ):
        expected = "#RRGGBB or RRGGBB" if allow_hash else "RRGGBB"
        if allow_alpha:
            expected += " with optional AA alpha"
        raise ColorFilamentError(f"{field_name} must be {expected} hexadecimal")
    return payload[:6].upper()


def rgb_to_lab(rgb: RGBColor) -> LabColor:
    """Convert sRGB to CIELAB using the D65 reference white."""
    channels = []
    for channel in rgb.as_tuple():
        normalized = channel / 255.0
        linear = (
            ((normalized + 0.055) / 1.055) ** 2.4
            if normalized > 0.04045
            else normalized / 12.92
        )
        channels.append(linear)

    linear_red, linear_green, linear_blue = channels
    # sRGB to XYZ, normalized by the D65 reference white (Xn=0.95047, Yn=1, Zn=1.08883).
    x = (
        linear_red * 0.4124564 + linear_green * 0.3575761 + linear_blue * 0.1804375
    ) / 0.95047
    y = linear_red * 0.2126729 + linear_green * 0.7151522 + linear_blue * 0.0721750
    z = (
        linear_red * 0.0193339 + linear_green * 0.1191920 + linear_blue * 0.9503041
    ) / 1.08883

    def pivot(value: float) -> float:
        return value ** (1 / 3) if value > 0.008856 else 7.787 * value + 16 / 116

    fx, fy, fz = pivot(x), pivot(y), pivot(z)
    return LabColor(116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def ciede2000(lab1: LabColor, lab2: LabColor) -> float:
    """Return the perceptual CIEDE2000 color difference between two Lab colors."""
    l1, a1, b1 = lab1.lightness, lab1.a, lab1.b
    l2, a2, b2 = lab2.lightness, lab2.a, lab2.b

    avg_l = (l1 + l2) / 2
    c1 = math.hypot(a1, b1)
    c2 = math.hypot(a2, b2)
    avg_c = (c1 + c2) / 2

    g = 0.5 * (1 - math.sqrt(avg_c**7 / (avg_c**7 + 25**7)))
    a1_prime = (1 + g) * a1
    a2_prime = (1 + g) * a2
    c1_prime = math.hypot(a1_prime, b1)
    c2_prime = math.hypot(a2_prime, b2)
    avg_c_prime = (c1_prime + c2_prime) / 2

    def hue_angle(a: float, b: float) -> float:
        if a == 0 and b == 0:
            return 0
        angle = math.degrees(math.atan2(b, a))
        return angle + 360 if angle < 0 else angle

    h1_prime = hue_angle(a1_prime, b1)
    h2_prime = hue_angle(a2_prime, b2)

    delta_l_prime = l2 - l1
    delta_c_prime = c2_prime - c1_prime

    if c1_prime * c2_prime == 0:
        delta_h_prime = 0.0
    else:
        delta_h_prime = h2_prime - h1_prime
        if delta_h_prime > 180:
            delta_h_prime -= 360
        elif delta_h_prime < -180:
            delta_h_prime += 360

    delta_h_cap_prime = (
        2 * math.sqrt(c1_prime * c2_prime) * math.sin(math.radians(delta_h_prime / 2))
    )

    if c1_prime * c2_prime == 0:
        avg_h_prime = h1_prime + h2_prime
    elif abs(h1_prime - h2_prime) <= 180:
        avg_h_prime = (h1_prime + h2_prime) / 2
    elif h1_prime + h2_prime < 360:
        avg_h_prime = (h1_prime + h2_prime + 360) / 2
    else:
        avg_h_prime = (h1_prime + h2_prime - 360) / 2

    t = (
        1
        - 0.17 * math.cos(math.radians(avg_h_prime - 30))
        + 0.24 * math.cos(math.radians(2 * avg_h_prime))
        + 0.32 * math.cos(math.radians(3 * avg_h_prime + 6))
        - 0.20 * math.cos(math.radians(4 * avg_h_prime - 63))
    )
    delta_theta = 30 * math.exp(-(((avg_h_prime - 275) / 25) ** 2))
    r_c = 2 * math.sqrt(avg_c_prime**7 / (avg_c_prime**7 + 25**7))
    s_l = 1 + (0.015 * ((avg_l - 50) ** 2)) / math.sqrt(20 + ((avg_l - 50) ** 2))
    s_c = 1 + 0.045 * avg_c_prime
    s_h = 1 + 0.015 * avg_c_prime * t
    r_t = -math.sin(math.radians(2 * delta_theta)) * r_c

    return math.sqrt(
        (delta_l_prime / s_l) ** 2
        + (delta_c_prime / s_c) ** 2
        + (delta_h_cap_prime / s_h) ** 2
        + r_t * (delta_c_prime / s_c) * (delta_h_cap_prime / s_h)
    )


def _open_rgba_image(image_path: str | os.PathLike[str], role: str) -> Image.Image:
    try:
        with Image.open(image_path) as image:
            image.load()
            if image.width <= 0 or image.height <= 0:
                raise ColorFilamentError(
                    f"{role} must have positive dimensions; got {image.width}x{image.height}"
                )
            return image.convert("RGBA")
    except FileNotFoundError:
        raise ColorFilamentError(
            f"cannot read {role}: file not found: {image_path}"
        ) from None
    except PermissionError:
        raise ColorFilamentError(
            f"cannot read {role}: permission denied: {image_path}"
        ) from None
    except (UnidentifiedImageError, OSError) as exc:
        raise ColorFilamentError(
            f"cannot read {role} {str(image_path)!r}: {exc}"
        ) from None


def _require_filaments(filaments: Iterable[Filament]) -> list[Filament]:
    normalized = list(filaments)
    if not normalized:
        raise ColorFilamentError("filament library must contain at least one filament")
    seen_names: dict[str, int] = {}
    for index, filament in enumerate(normalized, start=1):
        if not isinstance(filament, Filament):
            raise ColorFilamentError(
                f"filament #{index} must be a Filament object; use load_filaments()"
            )
        normalized_name = filament.name.casefold()
        previous_index = seen_names.get(normalized_name)
        if previous_index is not None:
            raise ColorFilamentError(
                f"filament #{index} name {filament.name!r} duplicates filament #{previous_index}"
            )
        seen_names[normalized_name] = index
    return normalized


def _nearest_filament(
    rgb: RGBColor, filament_labs: list[tuple[Filament, LabColor]]
) -> tuple[Filament, float]:
    pixel_lab = rgb_to_lab(rgb)
    best_filament: Filament | None = None
    best_distance = float("inf")
    for filament, filament_lab in filament_labs:
        distance = ciede2000(pixel_lab, filament_lab)
        if distance < best_distance:
            best_distance = distance
            best_filament = filament
    if best_filament is None:
        raise ColorFilamentError("filament library must contain at least one filament")
    return best_filament, best_distance


def _cached_nearest_filament(
    filaments: list[Filament],
) -> Callable[[int, int, int], Filament]:
    """Return a nearest-filament lookup that computes each RGB value only once."""
    filament_labs = [(filament, rgb_to_lab(filament.rgb)) for filament in filaments]
    cache: dict[tuple[int, int, int], Filament] = {}

    def nearest(red: int, green: int, blue: int) -> Filament:
        key = (red, green, blue)
        filament = cache.get(key)
        if filament is None:
            filament, _ = _nearest_filament(RGBColor(red, green, blue), filament_labs)
            cache[key] = filament
        return filament

    return nearest


def select_filaments(
    image_path: str | os.PathLike[str],
    filaments: Iterable[Filament],
    max_count: int,
    alpha_threshold: int = DEFAULT_ALPHA_THRESHOLD,
) -> FilamentSelection:
    """Pick the best ``max_count`` filaments for an image by perceptual distance."""
    max_count = _validate_max_count(max_count)
    alpha_threshold = _validate_alpha_threshold(alpha_threshold)
    normalized_filaments = _require_filaments(filaments)

    image = _open_rgba_image(image_path, "input image")
    pixels = cast(Any, image.load())
    width, height = image.size
    nearest = _cached_nearest_filament(normalized_filaments)
    counts: dict[str, int] = defaultdict(int)

    for y in range(height):
        for x in range(width):
            red, green, blue, alpha = pixels[x, y]
            if alpha < alpha_threshold:
                continue
            counts[nearest(red, green, blue).name] += 1

    if not counts:
        raise ColorFilamentError(
            "input image contains no pixels at or above "
            f"--alpha-threshold ({alpha_threshold})"
        )

    ranked = [
        FilamentCoverage(filament_name=name, pixel_count=count)
        for name, count in sorted(counts.items(), key=lambda item: -item[1])
    ]
    filaments_by_name = {filament.name: filament for filament in normalized_filaments}
    selected = [
        filaments_by_name[coverage.filament_name] for coverage in ranked[:max_count]
    ]
    return FilamentSelection(selected=selected, ranked=ranked)


def posterize_image(
    image_path: str | os.PathLike[str],
    filaments: Iterable[Filament],
    output_path: str | os.PathLike[str],
    alpha_threshold: int = DEFAULT_ALPHA_THRESHOLD,
) -> str | os.PathLike[str]:
    """Remap every opaque-enough pixel to the nearest filament color."""
    alpha_threshold = _validate_alpha_threshold(alpha_threshold)
    normalized_filaments = _require_filaments(filaments)
    image = _open_rgba_image(image_path, "input image")
    pixels = cast(Any, image.load())
    width, height = image.size
    nearest = _cached_nearest_filament(normalized_filaments)

    for y in range(height):
        for x in range(width):
            red, green, blue, alpha = pixels[x, y]
            if alpha < alpha_threshold:
                continue
            filament = nearest(red, green, blue)
            pixels[x, y] = (*filament.rgb.as_tuple(), alpha)

    try:
        image.save(output_path)
    except OSError as exc:
        raise ColorFilamentError(
            f"cannot write posterized image {str(output_path)!r}: {exc}"
        ) from None
    return output_path


def detect_colors(
    image_path: str | os.PathLike[str],
    alpha_threshold: int = DEFAULT_ALPHA_THRESHOLD,
    color_distance: float = DEFAULT_COLOR_DISTANCE,
) -> DetectedColors:
    """Find distinct opaque colors in an image using greedy RGB clustering."""
    alpha_threshold = _validate_alpha_threshold(alpha_threshold)
    color_distance = _validate_color_distance(color_distance)
    image = _open_rgba_image(image_path, "input image")
    pixels = cast(Any, image.load())
    width, height = image.size

    counts: dict[tuple[int, int, int], int] = defaultdict(int)
    for y in range(height):
        for x in range(width):
            red, green, blue, alpha = pixels[x, y]
            if alpha >= alpha_threshold:
                counts[(red, green, blue)] += 1

    clusters: list[tuple[RGBColor, int]] = []
    for rgb_tuple, count in sorted(counts.items(), key=lambda item: -item[1]):
        rgb = RGBColor(*rgb_tuple)
        for cluster_index, (center, cluster_count) in enumerate(clusters):
            distance = math.sqrt(
                sum(
                    (left - right) ** 2
                    for left, right in zip(
                        rgb.as_tuple(), center.as_tuple(), strict=True
                    )
                )
            )
            if distance <= color_distance:
                clusters[cluster_index] = (center, cluster_count + count)
                break
        else:
            clusters.append((rgb, count))

    colors = [
        DetectedColor(rgb=rgb, hex=rgb.to_hex(), pixel_count=count)
        for rgb, count in sorted(clusters, key=lambda item: -item[1])
    ]
    return DetectedColors(colors=colors, width=width, height=height)


def _filament_label(index: int | None) -> str:
    return f"filament #{index}" if index is not None else "filament"


def _validate_filament_name(value: object, index: int | None = None) -> str:
    label = _filament_label(index)
    if not isinstance(value, str) or not value.strip():
        raise ColorFilamentError(f"{label} name must be a non-empty string")
    separators = {os.sep, "/", "\\"}
    if os.altsep:
        separators.add(os.altsep)
    if any(separator in value for separator in separators):
        raise ColorFilamentError(f"{label} name must not contain path separators")
    if any(
        ord(character) < 32 or 0x7F <= ord(character) <= 0x9F for character in value
    ):
        raise ColorFilamentError(f"{label} name must not contain control characters")
    if value.casefold() in RESERVED_FILAMENT_NAMES:
        raise ColorFilamentError(
            f"{label} name {value!r} is reserved for generated model parts"
        )
    return value


def _filament_from_json_object(value: object, index: int) -> Filament:
    if not isinstance(value, dict):
        raise ColorFilamentError(f"filament #{index} must be an object")
    missing = {"name", "hex", "rgb"} - set(value)
    if missing:
        fields = ", ".join(sorted(missing))
        raise ColorFilamentError(
            f"filament #{index} missing required field(s): {fields}"
        )

    name = _validate_filament_name(value["name"], index)
    hex_value = hex_payload(value["hex"], f"filament #{index} hex")
    rgb = RGBColor.from_sequence(value["rgb"], f"filament #{index} rgb")
    return Filament(name=name, hex=hex_value, rgb=rgb)


def load_filaments(path: str | os.PathLike[str]) -> list[Filament]:
    """Load and validate a filament library JSON file."""
    try:
        with Path(path).open(encoding="utf-8") as file:
            payload = json.load(file)
    except FileNotFoundError:
        raise ColorFilamentError(
            f"cannot read filament library: file not found: {path}"
        ) from None
    except PermissionError:
        raise ColorFilamentError(
            f"cannot read filament library: permission denied: {path}"
        ) from None
    except json.JSONDecodeError as exc:
        raise ColorFilamentError(
            f"cannot read filament library {str(path)!r}: malformed JSON at "
            f"line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from None
    except UnicodeDecodeError as exc:
        raise ColorFilamentError(
            f"cannot read filament library {str(path)!r}: invalid UTF-8 at byte "
            f"{exc.start}: {exc.reason}"
        ) from None
    except OSError as exc:
        raise ColorFilamentError(
            f"cannot read filament library {str(path)!r}: {exc}"
        ) from None

    if not isinstance(payload, list) or not payload:
        raise ColorFilamentError("filament library must be a non-empty JSON array")

    filaments = [
        _filament_from_json_object(filament, index)
        for index, filament in enumerate(payload, start=1)
    ]
    check_unique_filament_names(filaments)
    return filaments


def check_unique_filament_names(filaments: Iterable[Filament]) -> None:
    seen_names: set[str] = set()
    for index, filament in enumerate(filaments, start=1):
        normalized_name = filament.name.casefold()
        if normalized_name in seen_names:
            raise ColorFilamentError(
                f"filament #{index} name {filament.name!r} duplicates an earlier filament"
            )
        seen_names.add(normalized_name)


def save_filaments(path: str | os.PathLike[str], filaments: list[Filament]) -> None:
    """Write a filament library in the format that ``load_filaments`` reads."""
    if not filaments:
        raise ColorFilamentError("filament library must contain at least one filament")
    check_unique_filament_names(filaments)
    payload = [
        {
            "name": filament.name,
            "hex": filament.hex,
            "rgb": list(filament.rgb.as_tuple()),
        }
        for filament in filaments
    ]
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise ColorFilamentError(
            f"cannot write filament library {str(target)!r}: {exc}"
        ) from None


def match_filaments(
    colors: Iterable[DetectedColor],
    filaments: Iterable[Filament],
    *,
    allow_duplicate_assignments: bool = False,
) -> list[FilamentMatch]:
    """Map each detected color to the nearest filament by CIEDE2000 distance."""
    normalized_filaments = _require_filaments(filaments)
    detected_colors = list(colors)
    for index, color in enumerate(detected_colors, start=1):
        if not isinstance(color, DetectedColor):
            raise ColorFilamentError(
                f"detected color #{index} must be a DetectedColor object"
            )

    filament_labs = [
        (filament, rgb_to_lab(filament.rgb)) for filament in normalized_filaments
    ]
    matches: list[FilamentMatch] = []
    assigned_by_filament: dict[str, DetectedColor] = {}
    for color in detected_colors:
        filament, distance = _nearest_filament(color.rgb, filament_labs)
        previous_color = assigned_by_filament.get(filament.name)
        if previous_color is not None and not allow_duplicate_assignments:
            raise DuplicateFilamentAssignmentError(
                f"image color #{color.hex} maps to filament {filament.name!r}, "
                f"which is already assigned to image color #{previous_color.hex}"
            )
        assigned_by_filament[filament.name] = color
        matches.append(
            FilamentMatch(filament=filament, image_color=color, distance=distance)
        )
    return matches
