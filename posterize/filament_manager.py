"""Widget that lists, adds, edits, and removes filament library entries."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, DataTable, Input, Static

from posterize.color_filament import (
    SAMPLE_FILAMENTS_PATH,
    ColorFilamentError,
    Filament,
    RGBColor,
    hex_payload,
    load_filaments,
    save_filaments,
)
from posterize.tui_style import CORAL, MUTED, SAGE, TEXT


class FilamentManager(Vertical):
    """Edit the filament library in memory; ``save`` writes it to disk."""

    DEFAULT_CSS = """
    FilamentManager { height: auto; }
    """

    class Changed(Message):
        """The in-memory library changed."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path
        self.filaments: list[Filament] = []
        self.saved: list[Filament] = []
        self.selected: int | None = None

    @property
    def dirty(self) -> bool:
        return self.filaments != self.saved

    def compose(self) -> ComposeResult:
        yield Static(id="library-note")
        yield Button("Start from the sample list", id="use-sample")
        yield DataTable(id="filament-table", cursor_type="row", zebra_stripes=False)
        with Horizontal(id="filament-edit"):
            yield Input(
                placeholder="Name, such as PLA Matte Ivory White", id="filament-name"
            )
            yield Input(placeholder="#RRGGBB", id="filament-hex")
            yield Static(id="filament-swatch")
        with Horizontal(id="filament-buttons"):
            yield Button("Add", id="filament-add")
            yield Button("Update", id="filament-update")
            yield Button("Remove", id="filament-remove")
        yield Static(
            "Pick a row to edit it. Add puts a new filament at the end.",
            classes="help",
        )
        yield Static(id="filament-status")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_column("", key="swatch", width=2)
        table.add_column("Name", key="name")
        table.add_column("Hex", key="hex")
        self.load(self.path)

    def load(self, path: Path) -> None:
        """Read the library at ``path``. A missing file starts an empty list."""

        self.path = path
        problem = ""
        try:
            self.filaments = load_filaments(path) if path.exists() else []
        except ColorFilamentError as exc:
            self.filaments = []
            problem = f"{exc}. Saving replaces the file."
        self.saved = list(self.filaments)
        self.selected = None
        self._rebuild()
        self._say(problem, CORAL)

    def set_path(self, path: Path) -> None:
        """Follow a new library path. Unsaved edits stay and save to the new path."""

        if path == self.path:
            return
        if self.dirty:
            self.path = path
            self._update_note()
        else:
            self.load(path)

    def save(self) -> None:
        save_filaments(self.path, self.filaments)
        self.saved = list(self.filaments)
        self._update_note()

    def _rebuild(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        for filament in self.filaments:
            table.add_row(
                Text("██", style=f"#{filament.hex}"),
                filament.name,
                Text(f"#{filament.hex}", style=MUTED),
            )
        table.display = bool(self.filaments)
        self.query_one("#use-sample").display = not self.filaments
        if self.selected is not None and self.filaments:
            self.selected = min(self.selected, len(self.filaments) - 1)
            table.move_cursor(row=self.selected)
        else:
            self.selected = None
        self._update_note()
        self._update_buttons()
        self.post_message(self.Changed())

    def _update_note(self) -> None:
        count = len(self.filaments)
        if count:
            noun = "filament" if count == 1 else "filaments"
            note = Text(f"{count} {noun} in {self.path}", style=TEXT)
        else:
            note = Text(
                f"No filaments yet. They will be saved to {self.path}.", style=MUTED
            )
        self.query_one("#library-note", Static).update(note)

    def _update_buttons(self) -> None:
        self.query_one("#filament-update").disabled = self.selected is None
        self.query_one("#filament-remove").disabled = self.selected is None

    def _say(self, message: str, color: str = MUTED) -> None:
        self.query_one("#filament-status", Static).update(Text(message, style=color))

    @on(DataTable.RowHighlighted)
    def _row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if not self.filaments:
            return
        self.selected = event.cursor_row
        filament = self.filaments[self.selected]
        self.query_one("#filament-name", Input).value = filament.name
        self.query_one("#filament-hex", Input).value = f"#{filament.hex}"
        self._update_buttons()

    @on(Input.Changed, "#filament-hex")
    def _hex_changed(self, event: Input.Changed) -> None:
        swatch = self.query_one("#filament-swatch")
        try:
            swatch.styles.background = (
                f"#{hex_payload(event.value.strip(), 'hex', allow_hash=True)}"
            )
        except ColorFilamentError:
            swatch.styles.background = None

    def _entry(self, replacing: int | None) -> Filament | None:
        """Build a filament from the inputs, or report why it is not valid."""

        name = self.query_one("#filament-name", Input).value.strip()
        hex_text = self.query_one("#filament-hex", Input).value.strip()
        if not name:
            self._say("Enter a name.", CORAL)
            return None
        try:
            payload = hex_payload(hex_text, "Hex", allow_hash=True)
            filament = Filament(name=name, hex=payload, rgb=RGBColor.from_hex(payload))
        except ColorFilamentError as exc:
            message = str(exc)
            self._say(f"{message[:1].upper()}{message[1:]}.", CORAL)
            return None
        for index, other in enumerate(self.filaments):
            if index != replacing and other.name.casefold() == name.casefold():
                self._say(f"A filament named {other.name!r} already exists.", CORAL)
                return None
        return filament

    @on(Button.Pressed, "#filament-add")
    def _add(self) -> None:
        filament = self._entry(replacing=None)
        if filament is None:
            return
        self.filaments.append(filament)
        self.selected = len(self.filaments) - 1
        self._rebuild()
        self._say(f"Added {filament.name}.", SAGE)

    @on(Button.Pressed, "#filament-update")
    def _update(self) -> None:
        if self.selected is None:
            return
        filament = self._entry(replacing=self.selected)
        if filament is None:
            return
        self.filaments[self.selected] = filament
        self._rebuild()
        self._say(f"Updated {filament.name}.", SAGE)

    @on(Button.Pressed, "#filament-remove")
    def _remove(self) -> None:
        if self.selected is None:
            return
        removed = self.filaments.pop(self.selected)
        self._rebuild()
        self._say(f"Removed {removed.name}.", SAGE)

    @on(Button.Pressed, "#use-sample")
    def _use_sample(self) -> None:
        self.filaments = load_filaments(SAMPLE_FILAMENTS_PATH)
        self.selected = 0
        self._rebuild()
        self._say("Loaded the sample list. Remove the filaments you do not own.", SAGE)
