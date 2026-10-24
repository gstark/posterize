"""Image loading, validation, and crop helpers."""

from __future__ import annotations

from typing import TypeAlias

from PIL import Image, UnidentifiedImageError

from posterize.errors import InputValidationError
from posterize.paths import Pathish

ImageSize: TypeAlias = tuple[int, int]
CropBox: TypeAlias = tuple[int, int, int, int] | tuple[float, float, float, float]


def _open_image(image_path: Pathish, role: str) -> Image.Image:
    try:
        image = Image.open(image_path)
        image.load()
    except FileNotFoundError:
        raise InputValidationError(
            f"cannot read {role}: file not found: {image_path}"
        ) from None
    except PermissionError:
        raise InputValidationError(
            f"cannot read {role}: permission denied: {image_path}"
        ) from None
    except (UnidentifiedImageError, OSError) as exc:
        raise InputValidationError(
            f"cannot read {role} {image_path!r}: {exc}"
        ) from None
    if image.width <= 0 or image.height <= 0:
        raise InputValidationError(
            f"{role} must have positive dimensions; got {image.width}x{image.height}"
        )
    return image


def read_image_size(image_path: Pathish, role: str = "input image") -> ImageSize:
    """Return image size with user-facing errors for malformed image files."""
    with _open_image(image_path, role) as image:
        return image.size


def _open_rgba_image(image_path: Pathish, role: str) -> Image.Image:
    """Load an image as RGBA with user-facing errors for expected read failures."""
    with _open_image(image_path, role) as image:
        return image.convert("RGBA")


def _crop_image_checked(
    image_path: Pathish, crop_box: CropBox, output_path: Pathish, role: str
) -> Pathish:
    """Crop an image while keeping read/write failures user-facing."""
    with _open_image(image_path, role) as image:
        cropped = image.crop(crop_box)
    try:
        cropped.save(output_path)
    except OSError as exc:
        raise InputValidationError(
            f"cannot write cropped {role} {output_path!r}: {exc}"
        ) from None
    return output_path


def pad_image_checked(
    image_path: Pathish,
    pad_x_px: int,
    pad_y_px: int,
    canvas_size: ImageSize,
    output_path: Pathish,
    role: str,
) -> Pathish:
    """Write a copy of an image placed on a larger transparent canvas."""
    if pad_x_px < 0 or pad_y_px < 0:
        raise InputValidationError(
            f"padding must be non-negative; got {pad_x_px}x{pad_y_px}"
        )
    with _open_image(image_path, role) as image:
        source = image.convert("RGBA")
    canvas_w, canvas_h = canvas_size
    if canvas_w < source.width + pad_x_px or canvas_h < source.height + pad_y_px:
        raise InputValidationError(
            f"canvas {canvas_w}x{canvas_h} is too small for {role} "
            f"{source.width}x{source.height} at offset {pad_x_px},{pad_y_px}"
        )
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    canvas.paste(source, (pad_x_px, pad_y_px))
    try:
        canvas.save(output_path)
    except OSError as exc:
        raise InputValidationError(
            f"cannot write padded {role} {output_path!r}: {exc}"
        ) from None
    return output_path


def validate_base_mask_size(
    image_path: Pathish, base_mask_path: Pathish
) -> tuple[ImageSize, ImageSize]:
    img_w, img_h = read_image_size(image_path, "input image")
    mask_w, mask_h = read_image_size(base_mask_path, "base mask")
    if (mask_w, mask_h) != (img_w, img_h):
        raise InputValidationError(
            f"--base-mask image size {mask_w}x{mask_h} "
            f"does not match input image size {img_w}x{img_h}"
        )
    return (img_w, img_h), (mask_w, mask_h)
