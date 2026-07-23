"""The Textual App: a 4-pane layout for testing OVOS without a
mic/speaker, plus three modal screens (services, installed skills,
and one combined filter panel).

    ┌──────────────────────────────────────────┐
    │ Logs (top) - compact filter status line,  │
    │ colored per source, ERROR lines bold      │
    ├───────────────────────────┬───────────────┤
    │ Conversation (2/3 width)  │ Activity (1/3) │
    │ (auto-scrolls to bottom)  │                │
    ├───────────────────────────┴───────────────┤
    │ Input (bottom) - Up/Down browses history   │
    └──────────────────────────────────────────┘

Logs and activity are populated by polling logs.py/bus.py on a timer -
Textual widgets aren't thread-safe to update directly from the
bus-client's background thread, so incoming lines are queued and
drained on the UI's own event loop instead.

Keybindings: F2 services, F3 installed skills, F4 filter (sources +
levels + skills, one combined scrollable list), Escape closes any
open modal.

Filtering UI has gone through two designs based on real user feedback:
1. Three permanently-visible rows of checkboxes directly under the log
   pane - dropped because the rows ate a lot of screen space once
   Checkbox's real 3-row-tall rendering was correctly accounted for.
2. Two separate F4/F5 modals, each with checkboxes packed into a
   horizontal row - dropped in favor of the current design, a single
   F4 modal with everything as a plain vertical list (one checkbox per
   line, grouped under section labels) - simpler to scan, and avoids
   the whole horizontal-packing/width-allocation question entirely.
A compact one-line status summary stays visible in the main view,
showing enabled/total counts per category.
"""
import argparse
from collections import deque

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Header, Footer, Input, RichLog, Checkbox, Label, ListView, ListItem

from ovos_tui_client.bus import OVOSBusConnection
from ovos_tui_client.logs import (
    find_log_dir, discover_log_sources, line_matches_filter, strip_log_prefix,
    extract_log_level, extract_skill_id, KNOWN_LOG_NAMES, KNOWN_LOG_LEVELS,
)
from ovos_tui_client.services import discover_services, restart_service

LOG_POLL_INTERVAL = 0.5  # seconds
LOG_BUFFER_SIZE = 5000  # lines kept in memory for re-filtering; oldest dropped past this

SOURCE_TAG_WIDTH = max(len(name) for name in KNOWN_LOG_NAMES)

LOG_SOURCE_COLORS = {
    "bus": "magenta", "skills": "green", "audio": "yellow", "media": "yellow",
    "voice": "cyan", "gui": "blue", "enclosure": "white", "phal": "red",
}
DEFAULT_LOG_COLOR = "white"


def format_log_line(source_name: str, line: str) -> str:
    """Colors a log line by its source, bolding it if it contains
    'ERROR'. Strips OVOS's own 'TIMESTAMP - COMPONENT - ' prefix first
    (both redundant here), pads the source tag to a fixed width so
    message text starts at the same column regardless of source name
    length."""
    clean_line = strip_log_prefix(line)
    color = LOG_SOURCE_COLORS.get(source_name, DEFAULT_LOG_COLOR)
    padded_name = source_name.ljust(SOURCE_TAG_WIDTH)
    text = f"[{color}]\\[{padded_name}][/{color}] {clean_line}"
    if "ERROR" in clean_line:
        text = f"[bold]{text}[/bold]"
    return text


class ServicesScreen(ModalScreen):
    """Lists discovered ovos-*.service systemd --user units; selecting
    one restarts it. Discovery/restart both go through services.py,
    which never raises - failures are shown as a result line instead
    of crashing the screen."""

    CSS = """
    ServicesScreen { align: center middle; }
    #services-dialog {
        width: 60; height: auto; max-height: 20;
        border: solid $accent; background: $panel; padding: 1 2;
    }
    #services-result { height: auto; color: $text-muted; }
    """

    def __init__(self):
        super().__init__()
        self.services = discover_services()

    def compose(self) -> ComposeResult:
        with Vertical(id="services-dialog"):
            yield Label("OVOS Services - Enter to restart, Esc to close")
            if not self.services:
                yield Label("No ovos-*.service units found via systemctl --user.")
            else:
                yield ListView(*[ListItem(Label(s)) for s in self.services], id="services-list")
            yield Label("", id="services-result")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index
        if index is None or index >= len(self.services):
            return
        unit_name = self.services[index]
        ok, msg = restart_service(unit_name)
        prefix = "OK: " if ok else "FAILED: "
        self.query_one("#services-result", Label).update(prefix + msg)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss()


class SkillsScreen(ModalScreen):
    """Shows the list of currently loaded skills, requested from OVOS
    via bus.list_skills() (see bus.py's docstring on that method for
    the honesty note about its response format being unverified
    against a live modern instance)."""

    CSS = """
    SkillsScreen { align: center middle; }
    #skills-dialog {
        width: 70; height: auto; max-height: 25;
        border: solid $accent; background: $panel; padding: 1 2;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="skills-dialog"):
            yield Label("Installed skills (Esc to close)", id="skills-title")
            yield RichLog(id="skills-list-view", wrap=False, markup=False)

    def on_mount(self) -> None:
        self.query_one("#skills-list-view", RichLog).write("Requesting skill list...")

    def show_skills(self, skills) -> None:
        """Called from the main app once bus.list_skills()'s callback
        fires - may arrive from the bus-client's background thread, so
        callers must marshal onto the app thread first (see
        OVOSTUIApp.action_show_skills)."""
        view = self.query_one("#skills-list-view", RichLog)
        view.clear()
        if skills is None:
            view.write("No response received (timed out) - see bus.py's")
            view.write("list_skills() docstring: the response event name")
            view.write("may differ on your OVOS version.")
        elif not skills:
            view.write("(no skills reported)")
        else:
            for skill_id in sorted(skills):
                view.write(skill_id)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss()


class FilterScreen(ModalScreen):
    """F4 - one combined panel for all three filter dimensions (log
    sources, log levels, discovered skills), each a plain single-line
    checkbox in a scrollable vertical list - not squeezed into
    side-by-side boxes (the previous design, replaced after feedback
    that separate F4/F5 screens with cramped rows were harder to scan
    than one straightforward list). Mutates the App's own
    log_sources/level_enabled/skill_enabled directly (passed by
    reference, no separate sync-back step) and re-renders the log view
    live via self.app._rerender_logs() on every toggle.

    Known, accepted limitation: skill_ids discovered while this modal
    is already open won't appear until it's closed and reopened, since
    compose() only runs once at mount."""

    CSS = """
    FilterScreen { align: center middle; }
    #filter-dialog {
        width: 60; height: 80%; max-height: 40;
        border: solid $accent; background: $panel; padding: 1 2;
    }
    #filter-scroll { height: 1fr; }
    .section-label { margin-top: 1; text-style: bold; }
    """

    def __init__(self, log_sources: list, level_enabled: dict, skill_enabled: dict):
        super().__init__()
        self.log_sources = log_sources
        self.level_enabled = level_enabled
        self.skill_enabled = skill_enabled

    def compose(self) -> ComposeResult:
        with Vertical(id="filter-dialog"):
            yield Label("Filter (Esc to close)")
            with VerticalScroll(id="filter-scroll"):
                yield Label("Sources", classes="section-label")
                for src in self.log_sources:
                    yield Checkbox(src.name, value=src.enabled, id=f"modal-source-{src.name}")
                yield Label("Log Levels", classes="section-label")
                for level in KNOWN_LOG_LEVELS:
                    yield Checkbox(level, value=self.level_enabled.get(level, True), id=f"modal-level-{level}")
                if self.skill_enabled:
                    yield Label("Skills seen so far", classes="section-label")
                    for skill_id, enabled in self.skill_enabled.items():
                        widget_id = "modal-skill-" + skill_id.replace(".", "-").replace("_", "-")
                        yield Checkbox(skill_id, value=enabled, id=widget_id)

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        checkbox_id = event.checkbox.id or ""
        if checkbox_id.startswith("modal-source-"):
            name = checkbox_id.removeprefix("modal-source-")
            for src in self.log_sources:
                if src.name == name:
                    src.enabled = event.value
        elif checkbox_id.startswith("modal-level-"):
            level = checkbox_id.removeprefix("modal-level-")
            self.level_enabled[level] = event.value
        elif checkbox_id.startswith("modal-skill-"):
            skill_id = str(event.checkbox.label)
            self.skill_enabled[skill_id] = event.value
        else:
            return
        self.app._rerender_logs()

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss()


class OVOSTUIApp(App):
    CSS = """
    #logs-container {
        height: 45%;
        border: solid $accent;
    }
    #filter-status {
        height: 1;
        color: $text-muted;
    }
    #log-filter {
        height: 1;
        border: none;
    }
    #middle-row {
        height: 1fr;
    }
    #conversation {
        width: 2fr;
        border: solid $accent;
    }
    #activity {
        width: 1fr;
        border: solid $accent;
    }
    #utterance-input {
        dock: bottom;
    }
    """

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("f2", "show_services", "Services"),
        ("f3", "show_skills", "Skills"),
        ("f4", "show_filter", "Filter"),
    ]

    def __init__(self, host="127.0.0.1", port=8181, lang="en-us", log_dir_override=None):
        super().__init__()
        self.bus = OVOSBusConnection(host=host, port=port, lang=lang)
        self.log_dir = find_log_dir(override=log_dir_override)
        self.log_sources = discover_log_sources(self.log_dir)
        self.utterance_history = []
        self.history_index = None
        self.log_buffer = deque(maxlen=LOG_BUFFER_SIZE)
        self.log_filter_text = ""
        self.level_enabled = {level: True for level in KNOWN_LOG_LEVELS}
        self.skill_enabled = {}  # skill_id -> bool, populated dynamically as seen

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="logs-container"):
            yield Label("", id="filter-status")
            yield Input(placeholder="Filter logs (free text)...", id="log-filter")
            yield RichLog(id="logs-view", wrap=False, markup=True, auto_scroll=True)
        with Horizontal(id="middle-row"):
            yield RichLog(id="conversation", wrap=True, markup=True, auto_scroll=True)
            yield RichLog(id="activity", wrap=True, markup=True, auto_scroll=True)
        yield Input(placeholder="Type what you'd say to OVOS...", id="utterance-input")
        yield Footer()

    def on_mount(self) -> None:
        if not self.log_sources:
            self.query_one("#logs-view", RichLog).write(
                f"[yellow]No known log files found"
                + (f" in {self.log_dir}" if self.log_dir else " in any candidate directory")
                + ". Pass --log-dir to point at the right one.[/yellow]"
            )
        self._update_filter_status()
        self.bus.on_speak(self._handle_speak)
        self.bus.on_activity(self._handle_activity)
        self.bus.connect()
        self.set_interval(LOG_POLL_INTERVAL, self._poll_logs)
        self.query_one("#utterance-input", Input).focus()

    def _handle_speak(self, utterance: str) -> None:
        self.call_from_thread(self._write_conversation, f"[blue]OVOS: {utterance}[/blue]")

    def _handle_activity(self, line: str) -> None:
        self.call_from_thread(self._write_activity, line)

    def _write_conversation(self, line: str) -> None:
        self.query_one("#conversation", RichLog).write(line)

    def _write_activity(self, line: str) -> None:
        self.query_one("#activity", RichLog).write(line)

    def _update_filter_status(self) -> None:
        """The compact one-line summary that replaced the always-
        visible checkbox rows - shows counts plus the single function
        key that opens the full filter list for all three categories."""
        try:
            label = self.query_one("#filter-status", Label)
        except NoMatches:
            return
        n_src = len(self.log_sources)
        n_src_on = sum(1 for s in self.log_sources if s.enabled)
        n_lvl = len(self.level_enabled)
        n_lvl_on = sum(1 for v in self.level_enabled.values() if v)
        parts = [f"Sources: {n_src_on}/{n_src}", f"Levels: {n_lvl_on}/{n_lvl}"]
        if self.skill_enabled:
            n_sk = len(self.skill_enabled)
            n_sk_on = sum(1 for v in self.skill_enabled.values() if v)
            parts.append(f"Skills: {n_sk_on}/{n_sk}")
        label.update("   ".join(parts) + "   (F4 to filter)")

    def _line_passes_all_filters(self, source_name: str, line: str) -> bool:
        """The full filter chain a line must pass to be shown: its
        source must be enabled, its log level (if any) must be
        enabled, its skill_id (if the best-effort extractor found one)
        must be enabled, and it must match the free-text filter."""
        src_enabled = any(s.name == source_name and s.enabled for s in self.log_sources)
        if not src_enabled:
            return False
        level = extract_log_level(line)
        if level is not None and not self.level_enabled.get(level, True):
            return False
        skill_id = extract_skill_id(line)
        if skill_id is not None and not self.skill_enabled.get(skill_id, True):
            return False
        return line_matches_filter(line, self.log_filter_text)

    def _maybe_register_skill(self, line: str) -> None:
        """Best-effort: tracks a new skill_id the first time it's seen
        in log text, so it shows up next time the F5 source-filter
        modal is opened. See logs.extract_skill_id's docstring for why
        this only catches some lines, not all skill-related ones."""
        skill_id = extract_skill_id(line)
        if skill_id is None or skill_id in self.skill_enabled:
            return
        self.skill_enabled[skill_id] = True

    def _poll_logs(self) -> None:
        """Runs on a recurring timer (set_interval) - can fire during
        app teardown (e.g. right as a test's `async with app.run_test()`
        block exits, before the timer itself has been cancelled),
        which is a real, if intermittent, source of test flakiness:
        the '#logs-view' widget can already be gone by the time this
        callback runs. NoMatches here just means 'nothing to update
        anymore', not a bug - skip this tick rather than crash."""
        try:
            view = self.query_one("#logs-view", RichLog)
        except NoMatches:
            return
        new_skill_seen = False
        for src in self.log_sources:
            new_lines = src.read_new_lines()
            for line in new_lines:
                self.log_buffer.append((src.name, line))
                before = len(self.skill_enabled)
                self._maybe_register_skill(line)
                if len(self.skill_enabled) != before:
                    new_skill_seen = True
                if self._line_passes_all_filters(src.name, line):
                    view.write(format_log_line(src.name, line))
        if new_skill_seen:
            self._update_filter_status()

    def _rerender_logs(self) -> None:
        """Re-draws the whole logs pane from self.log_buffer against
        the current filters - needed because RichLog is append-only,
        so a filter/toggle change has to replay history rather than
        just affecting future lines. Also refreshes the compact status
        line, since this is called from both filter modals on every
        toggle."""
        view = self.query_one("#logs-view", RichLog)
        view.clear()
        for source_name, line in self.log_buffer:
            if self._line_passes_all_filters(source_name, line):
                view.write(format_log_line(source_name, line))
        self._update_filter_status()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "log-filter":
            return
        self.log_filter_text = event.value
        self._rerender_logs()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "utterance-input":
            return
        text = event.value.strip()
        if not text:
            return
        self.query_one("#conversation", RichLog).write(f"[green]You: {text}[/green]")
        self.bus.send_utterance(text)
        self.utterance_history.append(text)
        self.history_index = None
        event.input.value = ""

    def action_show_services(self) -> None:
        self.push_screen(ServicesScreen())

    def action_show_skills(self) -> None:
        screen = SkillsScreen()
        self.push_screen(screen)
        self.bus.list_skills(lambda skills: self.call_from_thread(screen.show_skills, skills))

    def action_show_filter(self) -> None:
        self.push_screen(FilterScreen(self.log_sources, self.level_enabled, self.skill_enabled))

    def on_key(self, event) -> None:
        input_widget = self.query_one("#utterance-input", Input)
        if self.focused is not input_widget:
            return
        if event.key == "up":
            self._navigate_history(-1)
            event.prevent_default()
            event.stop()
        elif event.key == "down":
            self._navigate_history(1)
            event.prevent_default()
            event.stop()

    def _navigate_history(self, direction: int) -> None:
        """Up/down arrow browsing of previously submitted utterances,
        shell-style: past the most recent entry, the input clears back
        to blank rather than stopping at the last item."""
        if not self.utterance_history:
            return
        input_widget = self.query_one("#utterance-input", Input)
        if self.history_index is None:
            self.history_index = len(self.utterance_history)
        new_index = self.history_index + direction
        if new_index < 0:
            new_index = 0
        elif new_index >= len(self.utterance_history):
            self.history_index = None
            input_widget.value = ""
            input_widget.cursor_position = 0
            return
        self.history_index = new_index
        input_widget.value = self.utterance_history[new_index]
        input_widget.cursor_position = len(input_widget.value)


def build_arg_parser():
    parser = argparse.ArgumentParser(description="A split-pane terminal UI for testing OVOS without a mic/speaker")
    parser.add_argument("--host", default="127.0.0.1", help="messagebus host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8181, help="messagebus port (default: 8181)")
    parser.add_argument("--lang", default="en-us", help="BCP-47 language code for typed utterances (default: en-us)")
    parser.add_argument("--log-dir", default=None, help="override log directory auto-detection")
    return parser


def run():
    args = build_arg_parser().parse_args()
    app = OVOSTUIApp(host=args.host, port=args.port, lang=args.lang, log_dir_override=args.log_dir)
    app.run()


if __name__ == "__main__":
    run()
