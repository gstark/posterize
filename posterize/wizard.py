"""Interactive terminal wizard that builds a posterize command line."""

from __future__ import annotations

import re
import shlex
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal, cast, get_args

from PIL import Image
from rich.style import Style
from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.geometry import Size
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.suggester import SuggestFromList
from textual.validation import Integer, Number
from textual.widget import Widget
from textual.widgets import (
    Button,
    ContentSwitcher,
    Footer,
    Input,
    OptionList,
    RadioButton,
    RadioSet,
    Static,
    Switch,
)
from textual.widgets.option_list import Option

from posterize.cli_impl import build_parser, check_arguments, effective_defaults
from posterize.config import ConfigValues
from posterize.color_filament import (
    ColorFilamentError,
    detect_colors,
    load_filaments,
    posterize_image,
    select_filaments,
)
from posterize.errors import UserFacingError
from posterize.plate_grid import PlateGrid, resolve_target_width
from posterize.tui_style import (
    AMBER,
    CORAL,
    FORM_CSS,
    MUTED,
    SAGE,
    SPRUCE,
    TEXT,
    THEME,
    form_field,
    form_toggle,
)
from posterize.wizard_command import (
    Mount,
    OffsetMode,
    WizardChoices,
    format_command,
    number_text,
)

PREVIEW_MAX_PX = 320
PLATE_PRESETS = {"180": "180 mm (A1 mini)", "256": "256 mm (A1, P1, X1)"}
PLATE_PRESETS["320"] = "320 mm (H2D)"
SKIPPED_DIRS = {".venv", ".git", "build", "output", "node_modules"}

Step = Literal["image", "size", "plates", "colors", "base", "mount", "files", "review"]
STEP_TITLES: dict[Step, str] = {
    "image": "Image",
    "size": "Size",
    "plates": "Plates",
    "colors": "Colors",
    "base": "Base",
    "mount": "Wall mount",
    "files": "Files and tools",
    "review": "Review",
}
COLOR_MODE_STEPS: tuple[Step, ...] = ("image", "colors", "review")
WizardResult = tuple[Literal["run", "print"], list[str]]


def run_wizard(config: ConfigValues) -> list[str] | None:
    """Open the wizard. Return arguments to run, or None when nothing runs."""

    result = PosterizeWizard(config).run()
    if result is None:
        return None
    action, argv = result
    command = format_command(argv)
    if action == "print":
        print(command)
        return None
    print(f"$ {command}\n")
    return argv


@dataclass(frozen=True)
class PreviewData:
    """A small copy of the image, recolored with the filaments it would use."""

    image: Image.Image | None = None
    width_px: int = 0
    height_px: int = 0
    swatches: list[tuple[str, str, float]] = field(default_factory=list)
    message: str = ""


def _build_preview(choices: WizardChoices) -> PreviewData:
    path = Path(choices.image.strip()).expanduser()
    if not choices.image.strip():
        return PreviewData()
    if not path.is_file():
        return PreviewData(message=f"No file at {path}.")
    try:
        with Image.open(path) as opened:
            source = opened.convert("RGBA")
    except OSError as exc:
        return PreviewData(message=f"Cannot read the image: {exc}")

    thumb = source.copy()
    thumb.thumbnail((PREVIEW_MAX_PX, PREVIEW_MAX_PX), Image.Resampling.NEAREST)
    alpha = _int_or(choices.alpha_threshold, 128)
    with tempfile.TemporaryDirectory() as tmp:
        thumb_path = Path(tmp) / "thumb.png"
        thumb.save(thumb_path)
        try:
            if choices.mode == "colors":
                swatches = _detected_swatches(thumb_path, choices, alpha)
                return PreviewData(thumb, source.width, source.height, swatches)
            library = load_filaments(
                choices.filaments.strip() or str(choices.defaults["filaments"])
            )
            selection = select_filaments(
                thumb_path, library, _int_or(choices.max_filaments, 4), alpha
            )
            mapped_path = Path(tmp) / "mapped.png"
            posterize_image(thumb_path, selection.selected, mapped_path, alpha)
            with Image.open(mapped_path) as mapped_file:
                mapped = mapped_file.convert("RGBA")
        except (ColorFilamentError, UserFacingError, OSError) as exc:
            return PreviewData(
                thumb, source.width, source.height, message=f"Colors: {exc}"
            )

    selected = {filament.name for filament in selection.selected}
    total = sum(item.pixel_count for item in selection.ranked) or 1
    by_name = {filament.name: filament for filament in selection.selected}
    swatches = [
        (
            by_name[item.filament_name].rgb.to_hex(),
            item.filament_name,
            item.pixel_count / total,
        )
        for item in selection.ranked
        if item.filament_name in selected
    ]
    return PreviewData(mapped, source.width, source.height, swatches)


def _detected_swatches(
    thumb_path: Path, choices: WizardChoices, alpha: int
) -> list[tuple[str, str, float]]:
    detected = detect_colors(
        thumb_path,
        alpha_threshold=alpha,
        color_distance=_float_or(choices.color_distance, 30),
    )
    total = sum(color.pixel_count for color in detected.colors) or 1
    top = detected.colors[: _int_or(choices.max_filaments, 4)]
    return [(color.hex, f"#{color.hex}", color.pixel_count / total) for color in top]


def _int_or(text: str, fallback: int) -> int:
    try:
        return max(int(text), 0) or fallback
    except ValueError:
        return fallback


def _float_or(text: str, fallback: float) -> float:
    try:
        return float(text)
    except ValueError:
        return fallback


def _positive_float(text: str) -> float | None:
    try:
        value = float(text)
    except ValueError:
        return None
    return value if value > 0 else None


class PlatePreview(Widget):
    """Half-block render of the preview image with plate seams drawn over it."""

    DEFAULT_CSS = "PlatePreview { height: auto; max-height: 70%; }"
    EMPTY = "Your image shows here, in your filament colors, cut into plates."

    def __init__(self, id: str | None = None) -> None:
        super().__init__(id=id)
        self.data = PreviewData()
        self.grid: PlateGrid | None = None

    def show(self, data: PreviewData, grid: PlateGrid | None) -> None:
        self.data = data
        self.grid = grid
        self.refresh(layout=True)

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        image = self.data.image
        if image is None:
            return 2
        return max(1, round(image.height * width / image.width / 2))

    def render(self) -> Text:
        image = self.data.image
        cols, rows = self.size.width, self.size.height
        if image is None or cols < 4 or rows < 2:
            return Text(self.data.message or self.EMPTY, style=MUTED)
        scale = min(cols / image.width, rows * 2 / image.height)
        width = max(1, round(image.width * scale))
        height = max(2, round(image.height * scale))
        height += height % 2
        pixels = image.resize((width, height), Image.Resampling.NEAREST).load()
        assert pixels is not None

        seam_cols: set[int] = set()
        seam_rows: set[int] = set()
        if self.grid is not None:
            seam_cols = {
                round(bound / self.grid.image_width_px * width)
                for bound in self.grid.x_bounds_px[1:-1]
            }
            seam_rows = {
                round(bound / self.grid.image_height_px * height) // 2
                for bound in self.grid.y_bounds_px[1:-1]
            }

        def color(x: int, y: int) -> str:
            red, green, blue, alpha = pixels[x, y]  # type: ignore[misc]
            return SPRUCE if alpha < 128 else f"#{red:02x}{green:02x}{blue:02x}"

        text = Text(no_wrap=True, overflow="crop")
        for row in range(height // 2):
            for col in range(width):
                top, bottom = color(col, row * 2), color(col, row * 2 + 1)
                on_col, on_row = col in seam_cols, row in seam_rows
                if on_col or on_row:
                    glyph = "┼" if on_col and on_row else ("│" if on_col else "─")
                    text.append(glyph, Style(color=AMBER, bgcolor=top, bold=True))
                else:
                    text.append("▀", Style(color=top, bgcolor=bottom))
            text.append("\n")
        return text


def _radios(attr: str, options: dict[str, str], current: str) -> RadioSet:
    return RadioSet(
        *(
            RadioButton(label, value=key == current, id=f"{attr}--{key}")
            for key, label in options.items()
        ),
        id=attr,
    )


def _png_suggestions() -> list[str]:
    cwd = Path.cwd()
    found = [*cwd.glob("*.png"), *cwd.glob("*/*.png"), *cwd.glob("*/*/*.png")]
    return sorted(
        str(path.relative_to(cwd))
        for path in found
        if not SKIPPED_DIRS.intersection(path.relative_to(cwd).parts)
    )


def _flag_descriptions() -> dict[str, str]:
    descriptions: dict[str, str] = {}
    for action in build_parser()._actions:
        for option in action.option_strings:
            help_text = re.sub(r"\s*\((default|or set)\b[^)]*\)", "", action.help or "")
            descriptions[option] = help_text.replace("%%", "%")
    return descriptions


class PosterizeWizard(App[WizardResult]):
    TITLE = "posterize"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("ctrl+n", "next_step", "Next step"),
        Binding("ctrl+b", "previous_step", "Back"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    CSS = (
        FORM_CSS
        + """
    #steps { width: 24; }
    #form ContentSwitcher { height: auto; }
    .step { height: auto; }
    #base_color_swatch { width: 4; height: 1; margin-left: 1; }
    #preview-pane {
        width: 46%; min-width: 34; padding: 1 2;
        background: $surface;
    }
    #preview-pane.hidden { display: none; }
    #facts { height: auto; margin-top: 1; }
    #command { width: 1fr; }
    #nav { width: auto; height: auto; }
    #review-explain { height: auto; margin-bottom: 1; }
    #review-status { margin-bottom: 1; }
    #review-actions { height: auto; }
    #review-actions Button { margin-right: 2; margin-left: 0; }
    """
    )

    def __init__(self, config: ConfigValues) -> None:
        super().__init__()
        self.config = config
        self.choices = WizardChoices.from_defaults(effective_defaults(config))
        self.flag_help = _flag_descriptions()
        self.preview_key: tuple[str, ...] = ()
        self.preview_data = PreviewData()
        self.step: Step = "image"
        self.argv_valid = False

    # Layout -------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            yield OptionList(
                *(
                    Option(f"{index}  {title}", id=step)
                    for index, (step, title) in enumerate(STEP_TITLES.items(), start=1)
                ),
                id="steps",
                classes="side-list",
            )
            with VerticalScroll(id="form"):
                with ContentSwitcher(initial="step-image"):
                    yield from self._compose_steps()
            with Vertical(id="preview-pane"):
                yield PlatePreview(id="preview")
                yield Static(id="facts")
        with Horizontal(id="command-bar", classes="bottom-bar"):
            yield Static(id="command")
            with Horizontal(id="nav"):
                yield Button("Back", id="back")
                yield Button("Next", id="next", variant="primary")
        yield Footer()

    def _compose_steps(self) -> ComposeResult:
        c = self.choices
        default_width = number_text(c.defaults["default_width"])
        positive = [Number(minimum=0.001)]
        non_negative = [Number(minimum=0)]

        yield self._step(
            "image",
            "Pick the flat-color PNG to turn into a print.",
            form_field(
                "PNG file",
                Input(
                    c.image,
                    placeholder="art/poster.png",
                    id="image",
                    suggester=SuggestFromList(_png_suggestions(), case_sensitive=False),
                ),
                f"Relative to {Path.cwd()}. Transparent pixels are not printed.",
            ),
            form_field(
                "What do you want to do?",
                _radios(
                    "mode",
                    {
                        "model": "Build a printable 3MF model",
                        "colors": "List the image colors to buy as filament",
                    },
                    c.mode,
                ),
            ),
        )
        yield self._step(
            "size",
            "Set the size of the finished print. The other side follows the image proportions.",
            form_field(
                "Measure along",
                _radios("size_by", {"width": "Width", "height": "Height"}, c.size_by),
            ),
            form_field(
                "Size in mm",
                Input(
                    c.size_mm,
                    placeholder=default_width,
                    id="size_mm",
                    validators=positive,
                ),
                f"Leave empty for a print {default_width} mm wide.",
            ),
        )
        yield self._step(
            "plates",
            "Cut a large print into tiles that fit your printer.",
            form_toggle(
                "Split into plates",
                "split",
                c.split,
                "Off prints the whole image as one piece.",
            ),
            Vertical(
                form_field(
                    "Plate size",
                    _radios(
                        "plate_preset",
                        {**PLATE_PRESETS, "other": "Other size"},
                        c.plate_size if c.plate_size in PLATE_PRESETS else "other",
                    ),
                ),
                form_field(
                    "Plate size in mm",
                    Input(c.plate_size, id="plate_size", validators=positive),
                    id="g-plate-other",
                ),
                form_field(
                    "Seam placement",
                    _radios(
                        "offset_mode",
                        {
                            "none": "Keep the image where it is",
                            "optimize": "Search for a shift that needs fewer parts",
                            "manual": "Shift by a set number of pixels",
                        },
                        c.offset_mode,
                    ),
                    "A shift adds transparent padding, so the seams move but the art keeps its size.",
                ),
                form_field(
                    "Shift in pixels, as X,Y",
                    Input(c.plate_offset, placeholder="0,0", id="plate_offset"),
                    id="g-offset-manual",
                ),
                form_toggle(
                    "Pick filaments for each plate",
                    "per_plate_palette",
                    c.per_plate_palette,
                    "Each plate can use its own colors, up to the filament limit.",
                ),
                Vertical(
                    form_toggle(
                        "Dowel alignment holes",
                        "dowel_holes",
                        c.dowel_holes,
                        "6 mm holes on shared edges, plus a separate dowel file.",
                    ),
                    form_toggle(
                        "Keep holes where the base is thin",
                        "force_dowel_holes",
                        c.force_dowel_holes,
                        "Each kept hole prints a warning.",
                        id="g-dowel-force",
                    ),
                    id="g-dowels",
                    classes="row",
                ),
                id="g-plates",
                classes="row",
            ),
        )
        yield self._step(
            "colors",
            "Choose how many filaments to use and how the image colors are read.",
            form_field(
                "Most filaments to use",
                Input(
                    c.max_filaments, id="max_filaments", validators=[Integer(minimum=1)]
                ),
                "The preview shows the filaments that cover the most of the image.",
            ),
            form_field(
                "Filament library",
                Input(c.filaments, id="filaments"),
                "A JSON list of the filaments you own.",
                id="g-library",
            ),
            form_field(
                "Color merge distance",
                Input(c.color_distance, id="color_distance", validators=non_negative),
                "Colors closer than this RGB distance count as one color.",
            ),
            form_field(
                "Opacity cut-off",
                Input(
                    c.alpha_threshold,
                    id="alpha_threshold",
                    validators=[Integer(minimum=0, maximum=255)],
                ),
                "Pixels with alpha below this value are empty. Use 0 to 255.",
            ),
        )
        yield self._step(
            "base",
            "The base is the solid backing under the color layers.",
            form_toggle(
                "Print a base",
                "base",
                c.base,
                "Off prints only the color layers.",
            ),
            Vertical(
                form_field(
                    "Base thickness in mm",
                    Input(c.base_height, id="base_height", validators=positive),
                ),
                form_field(
                    "Base color",
                    Horizontal(
                        Input(c.base_color, placeholder="#000000", id="base_color"),
                        Static(id="base_color_swatch"),
                        classes="row",
                    ),
                    "A hex color, such as #1A1A1A.",
                ),
                form_field(
                    "Base shape mask",
                    Input(c.base_mask, placeholder="Optional", id="base_mask"),
                    "A PNG the same size as the image. Opaque pixels get a base.",
                ),
                id="g-base",
                classes="row",
            ),
            form_field(
                "Color layer height in mm",
                Input(c.raised_height, id="raised_height", validators=positive),
                "How far the colors stand above the surface below them.",
            ),
        )
        yield self._step(
            "mount",
            "Add keyholes on the back of the top plates to hang the print on a wall.",
            Static(
                "Wall mounts need a base. Turn on the base to use them.",
                id="mount-note",
                classes="note",
            ),
            form_field(
                "Mounting",
                _radios(
                    "mount",
                    {
                        "none": "No wall mount",
                        "edge": "Keyholes inset from the plate edges",
                        "stud": "Keyholes at 16-inch stud spacing",
                    },
                    c.mount,
                ),
            ),
            Vertical(
                form_field(
                    "Inset from the edge, in percent of plate width",
                    Input(c.keyhole_inset, id="keyhole_inset", validators=non_negative),
                    id="g-inset",
                ),
                form_field(
                    "Sideways shift in mm",
                    Input(c.keyhole_offset, id="keyhole_offset", validators=[Number()]),
                    "Positive moves the keyholes right, for example off a seam.",
                ),
                form_field(
                    "Edge clearance in mm",
                    Input(
                        c.keyhole_edge_clearance,
                        id="keyhole_edge_clearance",
                        validators=non_negative,
                    ),
                    "Extra base material to keep around each keyhole.",
                ),
                id="g-keyhole",
                classes="row",
            ),
        )
        yield self._step(
            "files",
            "Choose where files go and which tools to use. The defaults suit most runs.",
            form_field(
                "Output folder",
                Input(c.output_dir, id="output_dir"),
                "The 3MF files go here.",
            ),
            form_field(
                "Build folder",
                Input(c.build_dir, id="build_dir"),
                "Intermediate files go here.",
            ),
            form_field(
                "OpenSCAD program",
                Input(c.openscad, placeholder="Found automatically", id="openscad"),
            ),
            form_field(
                "potrace program",
                Input(c.potrace, placeholder="Found automatically", id="potrace"),
            ),
            form_toggle(
                "Save the command in the 3MF",
                "command_metadata",
                c.command_metadata,
                "Off by default, because arguments can contain secrets.",
            ),
        )
        yield self._step(
            "review",
            "Check the command. Run it now, or print it to use later.",
            Static(id="review-explain"),
            Static(id="review-status"),
            Horizontal(
                Button("Run posterize", id="run", variant="primary"),
                Button("Print command", id="print"),
                id="review-actions",
            ),
        )

    @staticmethod
    def _step(step: Step, intro: str, *children: Widget) -> Vertical:
        return Vertical(
            Static(STEP_TITLES[step], classes="section-title"),
            Static(intro, classes="intro"),
            *children,
            id=f"step-{step}",
            classes="step",
        )

    def on_mount(self) -> None:
        self.register_theme(THEME)
        self.theme = THEME.name
        self._sync()
        self.query_one("#image", Input).focus()

    def on_resize(self) -> None:
        self.query_one("#preview-pane").set_class(self.size.width < 100, "hidden")

    # Navigation ---------------------------------------------------------

    @property
    def steps(self) -> tuple[Step, ...]:
        if self.choices.mode == "colors":
            return COLOR_MODE_STEPS
        return get_args(Step)

    def _go(self, step: Step) -> None:
        self.step = step
        self.query_one(ContentSwitcher).current = f"step-{step}"
        self.query_one("#form", VerticalScroll).scroll_home(animate=False)
        self._sync()
        if step == "review":
            self._check()

    def action_next_step(self) -> None:
        index = self.steps.index(self.step)
        if index + 1 < len(self.steps):
            self._go(self.steps[index + 1])

    def action_previous_step(self) -> None:
        index = self.steps.index(self.step)
        if index > 0:
            self._go(self.steps[index - 1])

    @on(OptionList.OptionSelected, "#steps")
    def _step_picked(self, event: OptionList.OptionSelected) -> None:
        if event.option_id in self.steps:
            self._go(event.option_id)  # type: ignore[arg-type]

    @on(Button.Pressed, "#next")
    def _next_pressed(self) -> None:
        self.action_next_step()

    @on(Button.Pressed, "#back")
    def _back_pressed(self) -> None:
        self.action_previous_step()

    @on(Button.Pressed, "#run")
    def _run_pressed(self) -> None:
        if self.argv_valid:
            self.exit(("run", self.choices.to_argv()))

    @on(Button.Pressed, "#print")
    def _print_pressed(self) -> None:
        self.exit(("print", self.choices.to_argv()))

    # Field changes ------------------------------------------------------

    @on(Input.Changed)
    def _input_changed(self, event: Input.Changed) -> None:
        attr = event.input.id or ""
        if hasattr(self.choices, attr):
            setattr(self.choices, attr, event.value)
            self._sync()

    @on(Switch.Changed)
    def _switch_changed(self, event: Switch.Changed) -> None:
        setattr(self.choices, event.switch.id or "", event.value)
        self._sync()

    @on(RadioSet.Changed)
    def _radio_changed(self, event: RadioSet.Changed) -> None:
        attr, _, value = (event.pressed.id or "").partition("--")
        if attr == "plate_preset":
            if value != "other":
                self.query_one("#plate_size", Input).value = value
        elif attr == "mode":
            self.choices.mode = "colors" if value == "colors" else "model"
        elif attr == "size_by":
            self.choices.size_by = "height" if value == "height" else "width"
        elif attr == "offset_mode":
            self.choices.offset_mode = _offset_mode(value)
        elif attr == "mount":
            self.choices.mount = _mount(value)
        self._sync()

    # Derived display ----------------------------------------------------

    def _sync(self) -> None:
        c = self.choices
        show = {
            "#g-plates": c.split,
            "#g-plate-other": c.plate_size not in PLATE_PRESETS
            or self.query_one("#plate_preset--other", RadioButton).value,
            "#g-offset-manual": c.offset_mode == "manual",
            "#g-dowels": c.base,
            "#g-dowel-force": c.dowel_holes,
            "#g-base": c.base,
            "#g-keyhole": c.base and c.mount != "none",
            "#g-inset": c.mount == "edge",
            "#mount-note": not c.base,
            "#g-library": c.mode == "model",
        }
        for selector, visible in show.items():
            self.query_one(selector).display = visible
        self.query_one("#mount", RadioSet).disabled = not c.base

        options = self.query_one("#steps", OptionList)
        for step in get_args(Step):
            if step not in self.steps:
                options.disable_option(step)
            else:
                options.enable_option(step)
        options.highlighted = options.get_option_index(self.step)

        last = self.step == "review"
        self.query_one("#next", Button).display = not last
        self.query_one("#back", Button).disabled = self.step == self.steps[0]
        swatch = self.query_one("#base_color_swatch")
        hex_text = c.base_color.strip().lstrip("#")
        if re.fullmatch(r"[0-9a-fA-F]{6}", hex_text):
            swatch.styles.background = f"#{hex_text}"
        self.query_one("#command", Static).update(self._command_text())
        self._refresh_preview()

    def _command_text(self) -> Text:
        text = Text("$ ", style=MUTED)
        text.append("posterize", style=f"bold {TEXT}")
        for arg in self.choices.to_argv():
            text.append(" ")
            if arg.startswith("--"):
                text.append(arg, style=AMBER)
            else:
                text.append(shlex.quote(arg) if arg else "<image>", style=TEXT)
        return text

    def _refresh_preview(self) -> None:
        c = self.choices
        key = (
            c.image,
            c.mode,
            c.filaments,
            c.max_filaments,
            c.alpha_threshold,
            c.color_distance,
        )
        if key != self.preview_key:
            self.preview_key = key
            self._load_preview(replace(c))
        self._show_preview()

    @work(thread=True, exclusive=True, group="preview")
    def _load_preview(self, choices: WizardChoices) -> None:
        data = _build_preview(choices)
        self.call_from_thread(self._preview_loaded, data)

    def _preview_loaded(self, data: PreviewData) -> None:
        self.preview_data = data
        self._show_preview()

    def _plate_grid(self) -> tuple[float | None, PlateGrid | None, str]:
        c, data = self.choices, self.preview_data
        if not data.width_px:
            return None, None, ""
        size = _positive_float(c.size_mm)
        try:
            width = resolve_target_width(
                data.width_px,
                data.height_px,
                width=size if c.size_by == "width" else None,
                height=size if c.size_by == "height" else None,
                default_width=float(cast(float, c.defaults["default_width"])),
            )
        except ValueError, UserFacingError:
            return None, None, ""
        plate = _positive_float(c.plate_size)
        if not c.uses_plates or plate is None:
            return width, None, ""
        try:
            grid = PlateGrid.from_dimensions(
                data.width_px, data.height_px, width, plate
            )
        except ValueError as exc:
            return width, None, f"Plates: {exc}"
        return width, grid, ""

    def _show_preview(self) -> None:
        data = self.preview_data
        width, grid, grid_error = self._plate_grid()
        self.query_one("#preview", PlatePreview).show(data, grid)

        facts = Text()
        if data.width_px:
            facts.append(f"{data.width_px} × {data.height_px} px", style=MUTED)
        if width is not None and self.choices.mode == "model":
            height = width * data.height_px / data.width_px
            facts.append(f"\n{width:.0f} × {height:.0f} mm print", style=f"bold {TEXT}")
            if grid is not None:
                count = grid.rows * grid.cols
                facts.append(
                    f"\n{grid.cols} × {grid.rows} = {count} plates of {grid.plate_size_mm:g} mm",
                    style=AMBER,
                )
            elif self.choices.mode == "model" and not self.choices.uses_plates:
                facts.append("\nOne piece", style=MUTED)
        for message in (grid_error, data.message if data.image is not None else ""):
            if message:
                facts.append(f"\n{message}", style=CORAL)
        if data.swatches:
            facts.append("\n")
            for hex_code, name, share in data.swatches:
                facts.append("\n██ ", style=f"#{hex_code.lstrip('#')}")
                facts.append(f"{name}  ", style=TEXT)
                facts.append(f"{share:.0%}", style=MUTED)
        self.query_one("#facts", Static).update(facts)

    # Review -------------------------------------------------------------

    def _check(self) -> None:
        argv = self.choices.to_argv()
        table = Table.grid(padding=(0, 2))
        table.add_column(no_wrap=True, style=AMBER)
        table.add_column(no_wrap=True, style=TEXT)
        table.add_column(style=MUTED)
        image = shlex.quote(argv[0]) if argv[0] else "<no image>"
        table.add_row(Text("posterize", style=f"bold {TEXT}"), image)
        for flag, value in _flag_pairs(argv[1:]):
            shown = shlex.quote(value) if value is not None else ""
            table.add_row(flag, shown, self.flag_help.get(flag, ""))
        if len(argv) == 1:
            table.add_row("", "", "All other options keep their defaults.")
        self.query_one("#review-explain", Static).update(table)

        self.argv_valid = False
        self.query_one("#run", Button).disabled = True
        self.query_one("#review-status", Static).update(
            Text("Checking the options…", style=MUTED)
        )
        self._validate(argv)

    @work(thread=True, exclusive=True, group="check")
    def _validate(self, argv: list[str]) -> None:
        error = (
            check_arguments(argv, self.config)
            if argv[0]
            else "Pick a PNG file on the Image step."
        )
        self.call_from_thread(self._validated, argv, error)

    def _validated(self, argv: list[str], error: str | None) -> None:
        if argv != self.choices.to_argv():
            return
        status = self.query_one("#review-status", Static)
        self.argv_valid = error is None
        self.query_one("#run", Button).disabled = not self.argv_valid
        if error is None:
            status.update(Text("Ready to run.", style=f"bold {SAGE}"))
        else:
            status.update(Text(f"Cannot run yet: {error}", style=CORAL))


def _flag_pairs(args: list[str]) -> list[tuple[str, str | None]]:
    """Pair each flag with the value after it, if it takes one."""

    pairs: list[tuple[str, str | None]] = []
    for arg in args:
        if arg.startswith("--"):
            pairs.append((arg, None))
        else:
            pairs[-1] = (pairs[-1][0], arg)
    return pairs


def _offset_mode(value: str) -> OffsetMode:
    return (
        "optimize" if value == "optimize" else "manual" if value == "manual" else "none"
    )


def _mount(value: str) -> Mount:
    return "edge" if value == "edge" else "stud" if value == "stud" else "none"
