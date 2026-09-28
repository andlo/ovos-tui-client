"""Checklist for picking which of a skill's golden utterances to run
(#34) - opened from the Command Palette ("Test: <skill> — choose…").

This is deliberately the ONE window in an otherwise palette-only tool:
choosing *some* of 100+ utterances is multi-select, which the palette
(single-select, one entry = one action) can't express, and listing every
utterance as its own palette entry would drown any search for "test".

Rows are grouped by intent. Toggling a group header ticks/unticks the
whole group; the filter box narrows long lists by utterance text or
intent name without losing what's already ticked. Dismisses with the
sorted indices of the chosen steps, or None if cancelled.
"""
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, SelectionList
from textual.widgets.selection_list import Selection

NO_INTENT = "(no intent label - skill-level check)"


def group_label(step) -> str:
    return step.intent_label or NO_INTENT


class TestPickerScreen(ModalScreen):
    __test__ = False  # not a pytest class, despite the name

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+r", "run", "Run"),
        Binding("ctrl+a", "select_all", "All"),
        Binding("ctrl+n", "select_none", "None"),
    ]

    DEFAULT_CSS = """
    TestPickerScreen {
        align: center middle;
    }
    #picker {
        width: 90%;
        height: 85%;
        border: heavy $accent;
        background: $surface;
        padding: 0 1;
    }
    #picker-title {
        text-style: bold;
        margin-bottom: 1;
    }
    #picker-filter {
        margin-bottom: 1;
    }
    #picker-list {
        height: 1fr;
    }
    #picker-buttons {
        height: auto;
        margin-top: 1;
    }
    #picker-buttons Button {
        margin-right: 1;
    }
    """

    def __init__(self, title: str, steps: list, preselected=None):
        super().__init__()
        self.picker_title = title
        self.steps = list(steps)
        self._selected = set(preselected) if preselected is not None else set()
        self._filter = ""
        self._groups = {}  # label -> [step indices], insertion-ordered
        for i, step in enumerate(self.steps):
            self._groups.setdefault(group_label(step), []).append(i)

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            yield Label(self.picker_title, id="picker-title")
            yield Input(placeholder="Filter by utterance or intent…", id="picker-filter")
            yield SelectionList(id="picker-list")
            with Horizontal(id="picker-buttons"):
                yield Button("All (Ctrl+A)", id="picker-all")
                yield Button("None (Ctrl+N)", id="picker-none")
                yield Button("Run", id="picker-run", variant="primary")
                yield Button("Cancel (Esc)", id="picker-cancel")

    def on_mount(self) -> None:
        self._rebuild()
        self.query_one("#picker-list", SelectionList).focus()

    # -- model ------------------------------------------------------

    def _visible(self, label: str) -> list:
        needle = self._filter.lower()
        rows = self._groups[label]
        if not needle or needle in label.lower():
            return rows
        return [i for i in rows if needle in self.steps[i].utterance.lower()]

    def _rebuild(self) -> None:
        sl = self.query_one("#picker-list", SelectionList)
        highlighted = sl.highlighted
        sl.clear_options()
        options = []
        for label in self._groups:
            rows = self._visible(label)
            if not rows:
                continue
            all_on = all(i in self._selected for i in self._groups[label])
            header = Text(f"▸ {label} ({len(self._groups[label])})", style="bold")
            options.append(Selection(header, f"g:{label}", all_on))
            for i in rows:
                options.append(Selection(f"    {self.steps[i].utterance}", f"s:{i}", i in self._selected))
        sl.add_options(options)
        if options:
            # start on the first row, and keep the cursor where it was
            # when a group toggle or filter change rebuilds the list
            sl.highlighted = min(highlighted or 0, len(options) - 1)
        self._update_run_button()

    def _update_run_button(self) -> None:
        button = self.query_one("#picker-run", Button)
        button.label = f"Run {len(self._selected)} (Ctrl+R)"
        button.disabled = not self._selected

    # -- events -----------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "picker-filter":
            self._filter = event.value.strip()
            self._rebuild()

    def on_selection_list_selection_toggled(self, event: SelectionList.SelectionToggled) -> None:
        value = event.selection.value
        now_on = value in event.selection_list.selected
        if value.startswith("g:"):
            rows = self._groups.get(value[2:], [])
            if now_on:
                self._selected.update(rows)
            else:
                self._selected.difference_update(rows)
            self._rebuild()
        else:
            i = int(value[2:])
            if now_on:
                self._selected.add(i)
            else:
                self._selected.discard(i)
            # keep the group header's tick in sync without a full rebuild
            label = group_label(self.steps[i])
            sl = event.selection_list
            all_on = all(j in self._selected for j in self._groups[label])
            header = f"g:{label}"
            if all_on and header not in sl.selected:
                sl.select(header)
            elif not all_on and header in sl.selected:
                sl.deselect(header)
            self._update_run_button()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {"picker-all": self.action_select_all, "picker-none": self.action_select_none,
                   "picker-run": self.action_run, "picker-cancel": self.action_cancel}
        action = actions.get(event.button.id)
        if action:
            action()

    # -- actions ----------------------------------------------------

    def action_select_all(self) -> None:
        # "All" respects the filter: ticks everything currently visible
        for label in self._groups:
            self._selected.update(self._visible(label))
        self._rebuild()

    def action_select_none(self) -> None:
        self._selected.clear()
        self._rebuild()

    def action_run(self) -> None:
        if self._selected:
            self.dismiss(sorted(self._selected))

    def action_cancel(self) -> None:
        self.dismiss(None)
