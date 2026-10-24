"""Authoritative plate-grid geometry for posterized models."""

from __future__ import annotations

import bisect
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from PIL import Image


DEFAULT_WIDTH_MM = 500


def resolve_target_width(
    img_w: int,
    img_h: int,
    width: float | None = None,
    height: float | None = None,
    default_width: float = DEFAULT_WIDTH_MM,
) -> float:
    """Return the target model width implied by a width or height option."""
    if img_w <= 0:
        raise ValueError("image width must be positive")
    if img_h <= 0:
        raise ValueError("image height must be positive")
    if width is not None and height is not None:
        raise ValueError("width and height are mutually exclusive")
    if height is not None:
        return height * img_w / img_h
    return default_width if width is None else width


@dataclass(frozen=True)
class PixelBounds:
    """Half-open pixel-space bounds for one plate cell."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def integer_box(self) -> tuple[int, int, int, int]:
        return (round(self.x0), round(self.y0), round(self.x1), round(self.y1))


@dataclass(frozen=True)
class ScadPlate:
    """Model-space placement and size for an OpenSCAD tile."""

    x: float
    y: float
    width: float
    height: float
    row: int
    col: int
    label: str

    def as_build_tuple(self) -> tuple[float, float, float, float, int, int]:
        return (self.x, self.y, self.width, self.height, self.row, self.col)


@dataclass(frozen=True)
class PlateGrid:
    """Shared plate layout and coordinate transforms.

    Rows and columns are zero-based in source-image order: row 0 is the top
    image row, col 0 is the left image column. Pixel bounds are half-open and
    always cover the full source image without overlap.
    """

    image_width_px: int
    image_height_px: int
    target_width_mm: float
    plate_size_mm: float
    rows: int
    cols: int
    model_width_mm: float
    model_height_mm: float
    mm_per_px: float
    x_bounds_px: tuple[float, ...]
    y_bounds_px: tuple[float, ...]

    def __post_init__(self) -> None:
        if self.image_width_px <= 0 or self.image_height_px <= 0:
            raise ValueError("image dimensions must be positive")
        if not math.isfinite(self.target_width_mm) or self.target_width_mm <= 0:
            raise ValueError("target_width_mm must be positive and finite")
        if not math.isfinite(self.plate_size_mm) or self.plate_size_mm <= 0:
            raise ValueError("plate_size_mm must be positive and finite")
        if self.rows <= 0 or self.cols <= 0:
            raise ValueError("rows and cols must be positive")
        if self.rows > self.image_height_px or self.cols > self.image_width_px:
            raise ValueError("rows and cols must not exceed image dimensions")
        if (
            not math.isfinite(self.model_width_mm)
            or not math.isfinite(self.model_height_mm)
            or self.model_width_mm <= 0
            or self.model_height_mm <= 0
        ):
            raise ValueError("model dimensions must be positive and finite")
        if not math.isfinite(self.mm_per_px) or self.mm_per_px <= 0:
            raise ValueError("mm_per_px must be positive and finite")
        self._validate_model_scale()
        self._validate_bounds(
            "x_bounds_px", self.x_bounds_px, self.cols, self.image_width_px
        )
        self._validate_bounds(
            "y_bounds_px", self.y_bounds_px, self.rows, self.image_height_px
        )
        self._validate_plate_cell_sizes()

    def _validate_model_scale(self) -> None:
        expected_width_mm = self.image_width_px * self.mm_per_px
        expected_height_mm = self.image_height_px * self.mm_per_px
        if not math.isclose(
            self.model_width_mm, expected_width_mm, rel_tol=1e-9, abs_tol=1e-9
        ):
            raise ValueError("model_width_mm must equal image_width_px * mm_per_px")
        if not math.isclose(
            self.model_height_mm, expected_height_mm, rel_tol=1e-9, abs_tol=1e-9
        ):
            raise ValueError("model_height_mm must equal image_height_px * mm_per_px")
        if not math.isclose(
            self.target_width_mm, self.model_width_mm, rel_tol=1e-9, abs_tol=1e-9
        ):
            raise ValueError("target_width_mm must equal model_width_mm")

    @staticmethod
    def _is_integer_pixel_bound(bound: float) -> bool:
        return math.isfinite(bound) and math.isclose(
            bound, round(bound), rel_tol=0.0, abs_tol=1e-9
        )

    @staticmethod
    def _validate_bounds(
        name: str, bounds: tuple[float, ...], cell_count: int, expected_end: int
    ) -> None:
        if len(bounds) != cell_count + 1:
            raise ValueError(f"{name} must contain {cell_count + 1} entries")
        if any(not math.isfinite(bound) for bound in bounds):
            raise ValueError(f"{name} must contain finite values")
        if any(not PlateGrid._is_integer_pixel_bound(bound) for bound in bounds):
            raise ValueError(f"{name} must contain integer pixel boundaries")
        if not math.isclose(
            bounds[0], 0.0, rel_tol=0.0, abs_tol=1e-9
        ) or not math.isclose(
            bounds[-1], float(expected_end), rel_tol=0.0, abs_tol=1e-9
        ):
            raise ValueError(f"{name} must start at 0.0 and end at {expected_end}.0")
        if any(right <= left for left, right in zip(bounds, bounds[1:])):
            raise ValueError(f"{name} must be strictly increasing")

    @staticmethod
    def _max_bound_span(bounds: tuple[float, ...]) -> float:
        return max(right - left for left, right in zip(bounds, bounds[1:]))

    def _validate_plate_cell_sizes(self) -> None:
        max_column_width_mm = self._max_bound_span(self.x_bounds_px) * self.mm_per_px
        max_row_height_mm = self._max_bound_span(self.y_bounds_px) * self.mm_per_px
        if max(max_column_width_mm, max_row_height_mm) > self.plate_size_mm + 1e-9:
            raise ValueError("plate cells must not exceed plate_size_mm")

    @classmethod
    def from_dimensions(
        cls,
        image_width_px: int,
        image_height_px: int,
        target_width_mm: float,
        plate_size_mm: float,
    ) -> Self:
        if image_width_px <= 0 or image_height_px <= 0:
            raise ValueError("image dimensions must be positive")
        if not math.isfinite(target_width_mm) or target_width_mm <= 0:
            raise ValueError("target_width_mm must be positive and finite")
        if not math.isfinite(plate_size_mm) or plate_size_mm <= 0:
            raise ValueError("plate_size_mm must be positive and finite")

        mm_per_px = target_width_mm / image_width_px
        cols = cls._cell_count_for_plate_size(image_width_px, mm_per_px, plate_size_mm)
        rows = cls._cell_count_for_plate_size(image_height_px, mm_per_px, plate_size_mm)

        return cls.from_grid_dimensions(
            image_width_px=image_width_px,
            image_height_px=image_height_px,
            target_width_mm=target_width_mm,
            rows=rows,
            cols=cols,
            plate_size_mm=plate_size_mm,
        )

    @classmethod
    def from_grid_dimensions(
        cls,
        image_width_px: int,
        image_height_px: int,
        target_width_mm: float,
        rows: int,
        cols: int,
        plate_size_mm: float | None = None,
    ) -> Self:
        if image_width_px <= 0 or image_height_px <= 0:
            raise ValueError("image dimensions must be positive")
        if not math.isfinite(target_width_mm) or target_width_mm <= 0:
            raise ValueError("target_width_mm must be positive and finite")
        if rows <= 0 or cols <= 0:
            raise ValueError("rows and cols must be positive")
        if rows > image_height_px or cols > image_width_px:
            raise ValueError("rows and cols must not exceed image dimensions")
        if plate_size_mm is not None and (
            not math.isfinite(plate_size_mm) or plate_size_mm <= 0
        ):
            raise ValueError("plate_size_mm must be positive and finite")

        mm_per_px = target_width_mm / image_width_px
        model_width_mm = float(target_width_mm)
        model_height_mm = image_height_px * mm_per_px
        x_bounds_px = cls._pixel_bounds(image_width_px, cols)
        y_bounds_px = cls._pixel_bounds(image_height_px, rows)
        max_column_width_mm = cls._max_bound_span(x_bounds_px) * mm_per_px
        max_row_height_mm = cls._max_bound_span(y_bounds_px) * mm_per_px
        effective_plate_size = (
            plate_size_mm
            if plate_size_mm is not None
            else max(max_column_width_mm, max_row_height_mm)
        )
        return cls(
            image_width_px=image_width_px,
            image_height_px=image_height_px,
            target_width_mm=float(target_width_mm),
            plate_size_mm=float(effective_plate_size),
            rows=rows,
            cols=cols,
            model_width_mm=model_width_mm,
            model_height_mm=model_height_mm,
            mm_per_px=mm_per_px,
            x_bounds_px=x_bounds_px,
            y_bounds_px=y_bounds_px,
        )

    @staticmethod
    def _cell_count_for_plate_size(
        length_px: int, mm_per_px: float, plate_size_mm: float
    ) -> int:
        max_pixels_per_cell = math.floor(plate_size_mm / mm_per_px + 1e-9)
        if max_pixels_per_cell < 1:
            raise ValueError("plate_size_mm is too small for image resolution")
        return math.ceil(length_px / max_pixels_per_cell)

    @staticmethod
    def _pixel_bounds(length_px: int, count: int) -> tuple[float, ...]:
        base_step = length_px // count
        remainder = length_px % count
        bounds = [0.0]
        position = 0
        for index in range(count):
            position += base_step + (1 if index >= count - remainder else 0)
            bounds.append(float(position))
        return tuple(bounds)

    @staticmethod
    def label_for(row: int, col: int) -> str:
        return f"r{row + 1}_c{col + 1}"

    def label(self, row: int, col: int) -> str:
        self._validate_cell(row, col)
        return self.label_for(row, col)

    def pixel_bounds(self, row: int, col: int) -> PixelBounds:
        self._validate_cell(row, col)
        return PixelBounds(
            self.x_bounds_px[col],
            self.y_bounds_px[row],
            self.x_bounds_px[col + 1],
            self.y_bounds_px[row + 1],
        )

    def crop_box(self, row: int, col: int) -> tuple[int, int, int, int]:
        return self.pixel_bounds(row, col).integer_box()

    def crop_image(
        self,
        image_path: str | os.PathLike[str],
        row: int,
        col: int,
        output_dir: str | os.PathLike[str],
    ) -> str:
        img = Image.open(image_path)
        if img.size != (self.image_width_px, self.image_height_px):
            raise ValueError(
                f"image size {img.size[0]}x{img.size[1]} does not match "
                f"PlateGrid size {self.image_width_px}x{self.image_height_px}"
            )

        cropped = img.crop(self.crop_box(row, col))
        out_path = Path(output_dir) / f"plate_{self.label(row, col)}.png"
        cropped.save(out_path)
        return os.fspath(out_path)

    def scad_plate(self, row: int, col: int) -> ScadPlate:
        bounds = self.pixel_bounds(row, col)
        x = self.pixel_x_to_model_mm(bounds.x0)
        top_y = self.pixel_y_to_model_mm(bounds.y0)
        bottom_y = self.pixel_y_to_model_mm(bounds.y1)
        return ScadPlate(
            x=x,
            y=bottom_y,
            width=bounds.width * self.mm_per_px,
            height=top_y - bottom_y,
            row=row,
            col=col,
            label=self.label(row, col),
        )

    def scad_plates(self) -> list[ScadPlate]:
        return [
            self.scad_plate(row, col)
            for row in range(self.rows)
            for col in range(self.cols)
        ]

    def pixel_x_to_model_mm(self, x_px: float) -> float:
        return x_px * self.mm_per_px - self.model_width_mm / 2

    @staticmethod
    def index_for_bounds(
        bounds: tuple[float, ...], coordinate: float, cell_count: int
    ) -> int:
        """Return the half-open cell index containing a coordinate."""
        index = bisect.bisect_right(bounds, coordinate) - 1
        return max(0, min(index, cell_count - 1))

    def column_for_model_x(self, x_mm: float) -> int:
        """Return the half-open column containing a model-space x coordinate."""
        x_px = (x_mm + self.model_width_mm / 2) / self.mm_per_px
        return self.index_for_bounds(self.x_bounds_px, x_px, self.cols)

    def pixel_y_to_model_mm(self, y_px: float) -> float:
        return self.model_height_mm / 2 - y_px * self.mm_per_px

    def plate_center_mm(self, row: int, col: int) -> tuple[float, float]:
        bounds = self.pixel_bounds(row, col)
        center_x_px = (bounds.x0 + bounds.x1) / 2
        center_y_px = (bounds.y0 + bounds.y1) / 2
        return (
            self.pixel_x_to_model_mm(center_x_px),
            self.pixel_y_to_model_mm(center_y_px),
        )

    def column_widths_mm(self) -> list[float]:
        return [
            (self.x_bounds_px[col + 1] - self.x_bounds_px[col]) * self.mm_per_px
            for col in range(self.cols)
        ]

    def row_heights_mm(self) -> list[float]:
        return [
            (self.y_bounds_px[row + 1] - self.y_bounds_px[row]) * self.mm_per_px
            for row in range(self.rows)
        ]

    def row_bounds(self) -> list[tuple[float, float]]:
        return [
            (self.y_bounds_px[row], self.y_bounds_px[row + 1])
            for row in range(self.rows)
        ]

    def _validate_cell(self, row: int, col: int) -> None:
        if not 0 <= row < self.rows:
            raise IndexError(f"row {row} out of range for {self.rows} rows")
        if not 0 <= col < self.cols:
            raise IndexError(f"col {col} out of range for {self.cols} cols")
