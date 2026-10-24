"""Terminal editor for the posterize config file (``posterize config``)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.validation import Integer, Number, Validator
from textual.widget import Widget
from textual.widgets import Button, Footer, Input, OptionList, Static, Switch
from textual.widgets.option_list import Option

from posterize.cli_impl import effective_defaults
from posterize.color_filament import ColorFilamentError
from posterize.config import (
    SETTINGS,
    ConfigValue,
    ConfigValues,
    Setting,
    default_filaments_path,
    format_config,
    load_config,
    save_config,
    setting_problem,
)
from posterize.errors import UserFacingError
from posterize.filament_manager import FilamentManager
from posterize.tui_style import (
    AMBER,
    CORAL,
    FORM_CSS,
    MUTED,
    TEXT,
    THEME,
    form_field,
    form_toggle,
)
from posterize.wizard_command import number_text

SECTIONS = tuple(dict.fromkeys(setting.section for setting in SETTINGS))


def run_config_editor(path: Path) -> None:
    try:
        config = load_config(path)
    except UserFacingError as exc:
        sys.exit(f"error: {exc}\nFix or delete the file, then run posterize config.")
    saved = ConfigEditor(path, config).run()
    if saved is None:
        print("No changes saved.")
    for saved_path in saved or []:
        print(f"Saved {saved_path}")


def _section_id(section: str) -> str:
    return "section-" + re.sub(r"[^a-z]+", "-", section.lower()).strip("-")


def _parse(setting: Setting, text: str) -> ConfigValue:
    """Convert input text to the setting's type. Raise ValueError if it fails."""

    if setting.kind is int:
        return int(text)
    if setting.kind is float:
        return float(text)
    return text


def _validators(setting: Setting) -> list[Validator]:
    if setting.kind is int:
        return [Integer(minimum=setting.minimum, maximum=setting.maximum)]
    if setting.kind is float:
        return [Number(minimum=setting.minimum, maximum=setting.maximum)]
    return []


class ConfigEditor(App[list[Path]]):
    TITLE = "posterize config"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("ctrl+s", "save", "Save"),
        Binding("ctrl+q", "quit", "Quit without saving"),
    ]
    CSS = (
        FORM_CSS
        + """
    #form > Vertical { height: auto; margin-bottom: 1; }
    .section-title { margin-bottom: 1; }
    #base_color-row Input { max-width: 66; }
    #base_color-swatch { width: 4; height: 1; margin-left: 1; }
    #file-pane { width: 40%; min-width: 34; padding: 1 2; background: $surface; }
    #file-pane.hidden { display: none; }
    #file-path { color: $secondary; margin-bottom: 1; }
    #status { width: 1fr; }
    #library-note { margin-bottom: 1; }
    #use-sample { margin-bottom: 1; margin-left: 0; }
    #filament-table {
        height: auto; max-height: 18; margin-bottom: 1; background: $surface;
    }
    #filament-edit { height: auto; }
    #filament-name { width: 1fr; max-width: 56; }
    #filament-hex { width: 12; margin-left: 1; }
    #filament-swatch { width: 4; height: 1; margin-left: 1; }
    #filament-buttons { height: auto; margin-top: 1; }
    #filament-buttons Button { margin-right: 2; margin-left: 0; }
    #filament-status { height: auto; margin-top: 1; }
    #actions { width: auto; height: auto; }
    """
    )

    def __init__(self, path: Path, config: ConfigValues) -> None:
        super().__init__()
        self.path = path
        self.config = config
        self.builtin = effective_defaults({})
        self.saved_values = {
            key: value for key, value in config.items() if value != self.builtin[key]
        }

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            yield OptionList(
                *(Option(section, id=_section_id(section)) for section in SECTIONS),
                id="sections",
                classes="side-list",
            )
            with VerticalScroll(id="form"):
                yield Static(
                    "Set the defaults that every posterize run starts from. "
                    "Flags on the command line still win. An empty field uses "
                    "the built-in value shown in grey.",
                    classes="intro",
                )
                for section in SECTIONS:
                    children: list[Widget] = [
                        Static(section, classes="section-title"),
                        *(
                            self._setting_field(setting)
                            for setting in SETTINGS
                            if setting.section == section
                        ),
                    ]
                    if section == "Filaments":
                        children.append(
                            FilamentManager(
                                self._library_path(
                                    str(self.config.get("filaments", ""))
                                )
                            )
                        )
                    yield Vertical(*children, id=_section_id(section))
            with Vertical(id="file-pane"):
                yield Static(str(self.path), id="file-path")
                yield Static(id="file-text")
        with Horizontal(classes="bottom-bar"):
            yield Static(id="status")
            with Horizontal(id="actions"):
                yield Button("Cancel", id="cancel")
                yield Button("Save", id="save", variant="primary")
        yield Footer()

    def _setting_field(self, setting: Setting) -> Widget:
        builtin = self.builtin[setting.key]
        if setting.kind is bool:
            value = self.config.get(setting.key, builtin)
            return form_toggle(setting.label, setting.key, bool(value), setting.help)

        current = self.config.get(setting.key)
        text = "" if current is None else number_text(current) or str(current)
        placeholder = number_text(builtin) or str(builtin or "Found automatically")
        widget: Widget = Input(
            text,
            placeholder=placeholder,
            id=setting.key,
            validators=_validators(setting),
        )
        if setting.key == "base_color":
            widget = Horizontal(
                widget,
                Static(id="base_color-swatch"),
                id="base_color-row",
                classes="row",
            )
        return form_field(setting.label, widget, setting.help)

    @staticmethod
    def _library_path(text: str) -> Path:
        """Return the library file that the Filaments section edits."""

        text = text.strip()
        return Path(text).expanduser() if text else default_filaments_path()

    def on_mount(self) -> None:
        self.register_theme(THEME)
        self.theme = THEME.name
        self.query_one("#sections", OptionList).highlighted = 0
        self.watch(self.query_one("#form"), "scroll_y", self._track_section)
        self._refresh()

    def _track_section(self, scroll_y: float) -> None:
        """Highlight the section at the top of the form."""

        form = self.query_one("#form", VerticalScroll)
        current = 0
        for index, section in enumerate(SECTIONS):
            widget = self.query_one(f"#{_section_id(section)}")
            if widget.virtual_region.y <= scroll_y + 1:
                current = index
        if form.scroll_y >= form.max_scroll_y:
            current = len(SECTIONS) - 1
        self.query_one("#sections", OptionList).highlighted = current

    def on_resize(self) -> None:
        self.query_one("#file-pane").set_class(self.size.width < 110, "hidden")

    @on(OptionList.OptionSelected, "#sections")
    def _section_picked(self, event: OptionList.OptionSelected) -> None:
        self.query_one(f"#{event.option_id}").scroll_visible(top=True, animate=False)

    @on(Input.Changed)
    @on(Switch.Changed)
    @on(FilamentManager.Changed)
    def _changed(self) -> None:
        self._refresh()

    @on(Input.Changed, "#filaments")
    def _library_path_changed(self, event: Input.Changed) -> None:
        self.query_one(FilamentManager).set_path(self._library_path(event.value))

    @on(Button.Pressed, "#save")
    def _save_pressed(self) -> None:
        self.action_save()

    @on(Button.Pressed, "#cancel")
    def _cancel_pressed(self) -> None:
        self.exit()

    def action_save(self) -> None:
        values, problems = self._collect()
        if problems:
            key, message = problems[0]
            self._show_status(message, CORAL)
            self.query_one(f"#{key}").focus()
            return
        saved: list[Path] = []
        manager = self.query_one(FilamentManager)
        if manager.dirty:
            try:
                manager.save()
            except ColorFilamentError as exc:
                self._show_status(f"Filaments: {exc}.", CORAL)
                return
            saved.append(manager.path)
        saved.append(save_config(values, self.path))
        self.exit(saved)

    def _collect(self) -> tuple[ConfigValues, list[tuple[str, str]]]:
        """Return the values that differ from the built-in defaults, and problems."""

        values: ConfigValues = {}
        problems: list[tuple[str, str]] = []
        for setting in SETTINGS:
            builtin = self.builtin[setting.key]
            if setting.kind is bool:
                value: ConfigValue = self.query_one(f"#{setting.key}", Switch).value
            else:
                text = self.query_one(f"#{setting.key}", Input).value.strip()
                if not text:
                    continue
                try:
                    value = _parse(setting, text)
                except ValueError:
                    kind = "whole number" if setting.kind is int else "number"
                    problems.append((setting.key, f"{setting.label}: enter a {kind}."))
                    continue
                problem = setting_problem(setting, value)
                if problem:
                    problems.append((setting.key, f"{setting.label} {problem}."))
                    continue
            if value != builtin:
                values[setting.key] = value
        return values, problems

    def _refresh(self) -> None:
        values, problems = self._collect()
        manager = self.query_one(FilamentManager)
        file_text = self._file_text(values)
        file_text.append(f"\n{manager.path}\n", style=MUTED)
        count = len(manager.filaments)
        file_text.append(
            f"{count} filament{'' if count == 1 else 's'}"
            + (", unsaved changes" if manager.dirty else ""),
            style=AMBER if manager.dirty else TEXT,
        )
        self.query_one("#file-text", Static).update(file_text)

        hex_text = self.query_one("#base_color", Input).value.strip().lstrip("#")
        if not hex_text:
            hex_text = str(self.builtin["base_color"]).lstrip("#")
        if re.fullmatch(r"[0-9a-fA-F]{6}", hex_text):
            self.query_one("#base_color-swatch").styles.background = f"#{hex_text}"

        if problems:
            self._show_status(problems[0][1], CORAL)
        elif values == self.saved_values and not manager.dirty:
            self._show_status("No changes.", MUTED)
        else:
            self._show_status("Unsaved changes.", AMBER)

    def _show_status(self, message: str, color: str) -> None:
        self.query_one("#status", Static).update(Text(message, style=color))

    @staticmethod
    def _file_text(values: ConfigValues) -> Text:
        if not values:
            return Text("No settings changed. Built-in defaults apply.\n", style=MUTED)
        text = Text()
        for line in format_config(values).splitlines():
            if line.startswith("#"):
                text.append(line, style=MUTED)
            elif " = " in line:
                key, value = line.split(" = ", 1)
                text.append(key, style=AMBER)
                text.append(" ")
                text.append("=", style=MUTED)
                text.append(" ")
                text.append(value, style=TEXT)
            text.append("\n")
        return text
