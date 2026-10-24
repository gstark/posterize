# Posterize architecture

Posterize converts a flat-color raster image into OpenSCAD geometry and then into Bambu Studio 3MF projects. All code lives under `posterize/`. Run it with the `posterize` console script or `python -m posterize`.

## Package modules

- `posterize.pipeline_runner` owns normal and per-plate pipeline orchestration via `PipelineRunner`, `PipelineConfig`, and `PipelineResult`.
- `posterize.project_builder` owns `build_3mf`, checked Bambu 3MF project creation, plate insertion, STL-group insertion, and saving.
- `posterize.cli_impl` owns command-line parsing and validated `CliRunConfig` construction.
- `posterize.color_filament` owns image color detection, posterization, CIEDE2000 matching, and filament-library validation.
- `posterize.plate_grid` is the authoritative grid and coordinate transform module. Other modules should ask `PlateGrid` for bounds, labels, centers, and SCAD clipping tuples.
- `posterize.plate_offset` searches for transparent padding that moves the plate seams so that fewer plates and fewer connected parts are printed. It only measures layouts; `posterize.cli_impl` applies the padding to the image before the pipeline runs.
- `posterize.keyholes`, `posterize.dowels`, and `posterize.lateral_depth` own wall-mount and inter-plate alignment geometry.
- `posterize.scad` owns OpenSCAD template names, part IDs, unclipped sentinels, and Jinja rendering.
- `posterize.scad_rendering` owns potrace SVG generation, SCAD file generation, STL rendering, and command metadata formatting.
- `posterize.external_tools`, `posterize.image_io`, `posterize.masks`, and `posterize.formatting` own external command execution, user-facing image IO, PBM mask generation, and shared formatting/color conversion respectively.
- `posterize.threemf` owns color slots, filament payloads, metadata, and the local fallback project used when `bambu3mf` is unavailable in tests.

## SCAD and templates

Python renders `posterize/assets/scad/model.scad.j2` plus fragments under `posterize/assets/scad/fragments/`. `StrictUndefined` is enabled so missing template variables fail immediately. Jinja autoescape is disabled because SCAD is code, not HTML.

The generated SCAD imports potrace SVGs, builds base/color modules, and exposes numeric constants for OpenSCAD CLI `-D` overrides. `render_stl()` always passes the selected part, target width, plate clipping rectangle, keyhole positions, and dowel positions explicitly.

## Plate coordinate conventions

Rows and columns are zero-based internally in source-image order: row 0 is the top row, column 0 is the left column. Human/file labels are one-based (`r1_c1`). Pixel bounds are half-open and integer-aligned. Model-space X is centered around 0; model-space Y follows the SCAD/template convention used by `PlateGrid.plate_center_mm()`.

SCAD plate clipping uses `(plate_x, plate_y, plate_w, plate_h, row, col)` tuples from `ScadPlate.as_build_tuple()`. A non-positive width/height is the unclipped sentinel for local per-plate SCAD files.

## Part IDs

Part IDs are shared by Python and SCAD through `posterize.scad` constants:

- `SCAD_BASE_PART = 1`: base layer.
- `SCAD_FIRST_COLOR_PART = 2`: first color layer; following colors increment by slot order.
- `KEYHOLE_OUTLINE_PART = 100`: optional printed keyhole outline.
- `SCAD_ALL_PARTS = 0`: template preview/all-parts mode.

Do not hard-code these IDs outside the boundary modules; import the constants.

## Tool discovery

OpenSCAD and potrace are resolved in this order: explicit CLI/config override, environment variable (`POSTERIZE_OPENSCAD` / `POSTERIZE_POTRACE`), then `PATH`. Empty or invalid overrides fail instead of silently falling back. External commands delete stale expected outputs before running and raise `ExternalToolError` with stdout/stderr on failure.

## Filament schema

Filament libraries are JSON arrays. Each item must contain:

```json
{"name": "Red", "hex": "FF0000", "rgb": [255, 0, 0]}
```

Names must be non-empty, path-safe, unique case-insensitively, and not reserved (`base`, `keyhole_outline`). `hex` and `rgb` must describe the same sRGB color. 3MF assembly uses typed `ColorSlot` objects.

## Testing strategy

Tests exercise behavior through public entrypoints where possible: CLI subprocess help/error paths, pipeline runs against real temporary images, OpenSCAD/potrace failure handling via patched subprocess calls, and geometry helpers with concrete grids/masks. Quality gates are:

```bash
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy posterize posterize_build_backend.py
```
