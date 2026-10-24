"""Theme, shared CSS, and form helpers for the posterize terminal apps."""

from __future__ import annotations

from textual.containers import Horizontal, Vertical
from textual.theme import Theme
from textual.widget import Widget
from textual.widgets import Label, Static, Switch

SPRUCE = "#16282a"
SURFACE = "#1e3436"
PANEL = "#264143"
TEXT = "#e6ede8"
MUTED = "#8fa8a3"
AMBER = "#f2b33d"
CORAL = "#ff7a6b"
SAGE = "#8fd694"

THEME = Theme(
    name="posterize",
    primary=AMBER,
    secondary=MUTED,
    accent=AMBER,
    foreground=TEXT,
    background=SPRUCE,
    surface=SURFACE,
    panel=PANEL,
    error=CORAL,
    success=SAGE,
    warning=AMBER,
    dark=True,
)

FORM_CSS = """
Screen { background: $background; }
#body { height: 1fr; }
.side-list {
    width: 24; height: 100%; padding: 1 0;
    border: none; background: $surface;
}
.side-list > .option-list--option { padding: 0 2; }
.side-list > .option-list--option-highlighted {
    color: $background; background: $accent; text-style: bold;
}
#form { width: 1fr; min-width: 44; padding: 1 3; }
.section-title { text-style: bold; color: $foreground; }
.intro { color: $secondary; margin-bottom: 1; }
.field { height: auto; margin-bottom: 1; }
.field-label { color: $foreground; }
.help { color: $secondary; }
.toggle-row { height: auto; }
.toggle-row Label { padding: 0 1; }
Switch { border: none; height: 1; padding: 0; width: auto; }
Switch:focus { border: none; background: $panel; }
Input {
    border: none; height: 1; padding: 0 1; margin: 0;
    border-left: wide $panel; background: $panel;
    width: 100%; max-width: 72;
}
Input:focus { border: none; border-left: wide $accent; background: $panel; }
Input.-invalid { border: none; border-left: wide $error; }
Input.-invalid:focus { border: none; border-left: wide $error; }
.note { color: $warning; margin-bottom: 1; }
.row { height: auto; }
.row Input { width: 1fr; max-width: 66; }
RadioSet { width: 100%; border: none; background: transparent; padding: 0; }
RadioSet:focus { border: none; }
RadioSet:blur > RadioButton.-selected > .toggle--label { background: transparent; }
.bottom-bar { height: auto; padding: 1 3; background: $panel; }
.bottom-bar Button { margin-left: 2; min-width: 10; }
"""


def form_field(
    label: str, widget: Widget, help_text: str = "", id: str | None = None
) -> Vertical:
    children: list[Widget] = [Label(label, classes="field-label"), widget]
    if help_text:
        children.append(Static(help_text, classes="help"))
    return Vertical(*children, classes="field", id=id)


def form_toggle(
    label: str, switch_id: str, value: bool, help_text: str = "", id: str | None = None
) -> Vertical:
    row = Horizontal(
        Switch(value=value, id=switch_id), Label(label), classes="toggle-row"
    )
    children: list[Widget] = [row]
    if help_text:
        children.append(Static(help_text, classes="help"))
    return Vertical(*children, classes="field", id=id)
