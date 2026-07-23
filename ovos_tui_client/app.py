"""The Textual App: a 4-pane layout for testing OVOS without a
mic/speaker, plus four modal screens (help, installed skills, skill
filter, and the service-action picker opened from the Command
Palette).

    ┌──────────────────────────────────────────┐
    │ Sources/Levels checkboxes (compact, one   │
    │ line each) - directly visible, no modal   │
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

KEYBINDINGS - F1 help, F3 installed skills, F4 filter by skill, F5-F8
jump focus to Logs/Conversation/Activity/Input, Ctrl+P opens Textual's
built-in command palette (all the F-key actions are
also available there, fuzzy-searchable), Escape closes any open modal.
Tab/Shift+Tab cycle focus across everything focusable (checkboxes,
panes, input) - this is a Textual built-in, not custom code here.
Typing a printable character while focus is on Logs/Conversation/
Activity (none of which are normally typable) redirects that keypress
to the utterance input instead of doing nothing - see on_key - since
that's almost always what was actually meant.

FILTER SEMANTICS - unchecked-by-default, checking narrows (per
category, independently): an UNCHECKED box does not mean "hidden" - it
means "not specifically filtered to". With nothing checked in a
category, nothing in that category is restricted and everything shows.
Checking one or more boxes restricts that category to only the checked
ones. Sources and Log Levels are CHECKED by default (short, fixed
lists - "everything on, uncheck what you don't want" reads naturally);
Skills default UNCHECKED (an open-ended, growing list - "check the few
you care about" reads naturally instead). The underlying filter rule
is identical either way, only the starting values differ.

SCROLL BEHAVIOR: all three RichLog panes (logs/conversation/activity)
only auto-scroll to the newest line if the user is already at (or very
near) the bottom when a new line arrives - scrolling back to read or
copy something is never yanked back down by incoming content. See
_write_to_log().
"""
import argparse
from collections import deque
from functools import partial

from textual.app import App, ComposeResult, SystemCommand
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen, Screen
from textual.widgets import Header, Footer, Input, RichLog, Checkbox, Label, ListView, ListItem

from ovos_tui_client.bus import OVOSBusConnection
from ovos_tui_client.logs import (
    find_log_dir, discover_log_sources, line_matches_filter, strip_log_prefix,
    extract_log_level, extract_skill_id, KNOWN_LOG_NAMES, KNOWN_LOG_LEVELS,
)
from ovos_tui_client.services import discover_services, restart_service, stop_service, start_service
from ovos_tui_client.state import load_filter_state, save_filter_state

LOG_POLL_INTERVAL = 0.5  # seconds
LOG_BUFFER_SIZE = 5000  # lines kept in memory for re-filtering; oldest dropped past this

SOURCE_TAG_WIDTH = max(len(name) for name in KNOWN_LOG_NAMES)

LOG_SOURCE_COLORS = {
    "bus": "magenta", "skills": "green", "audio": "yellow", "media": "yellow",
    "voice": "cyan", "gui": "blue", "enclosure": "white", "phal": "red",
}
DEFAULT_LOG_COLOR = "white"

# Widgets where typing a plain character should redirect focus to the
# utterance input rather than doing nothing - see on_key().
REDIRECT_TO_INPUT_IDS = {"logs-view", "conversation", "activity"}


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


class HelpScreen(ModalScreen):
    """F1 - a quick reference for every keybinding and the filter/
    scroll behaviors that aren't otherwise obvious from the UI alone."""

    CSS = """
    HelpScreen { align: center middle; }
    #help-dialog {
        width: 64; height: auto; max-height: 30;
        border: solid $accent; background: $panel; padding: 1 2;
    }
    #help-dialog Label { height: 1; }
    .help-spacer { height: 1; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="help-dialog"):
            yield Label("Keybindings (Esc to close)")
            yield Label("")
            yield Label("F1   Show this help")
            yield Label("F3   Installed skills (from the bus)")
            yield Label("F4   Filter by skill (click 'Skills:' works too)")
            yield Label("F5   Jump focus to Logs")
            yield Label("F6   Jump focus to Conversation")
            yield Label("F7   Jump focus to Activity")
            yield Label("F8   Jump focus to the utterance input")
            yield Label("Ctrl+P   Command palette (all actions, searchable)")
            yield Label("Ctrl+Q   Quit")
            yield Label("Tab / Shift+Tab   Cycle focus")
            yield Label("Space / Enter   Toggle a focused checkbox")
            yield Label("Up / Down   Browse utterance history (in the input)")
            yield Label("")
            yield Label("Service restart/stop/start lives in the Command")
            yield Label("Palette only (type 'service') - pick an action,")
            yield Label("then pick which ovos-*.service unit.")
            yield Label("")
            yield Label("Typing anywhere in Logs/Conversation/Activity")
            yield Label("switches focus to the utterance input automatically -")
            yield Label("that's almost always what you meant to do.")
            yield Label("")
            yield Label("Filter checkboxes: unchecked = show everything in")
            yield Label("that category. Checking one or more narrows to only")
            yield Label("the checked ones. Sources/Levels start checked;")
            yield Label("Skills start unchecked (it's an open-ended list).")
            yield Label("")
            yield Label("Scrolled-up panes never get yanked back to the")
            yield Label("bottom by new lines - only auto-scrolls while")
            yield Label("already at the bottom.")

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss()


class ServicePickerScreen(ModalScreen):
    """The 'then choose which service' half of the two-step Command
    Palette flow (issue #3 follow-up): typing 'service' in the palette
    groups 'Service: Restart...' / 'Service: Stop...' / 'Service:
    Start...' together (see get_system_commands()); selecting one of
    those opens THIS screen with the matching action already bound,
    listing every discovered ovos-*.service unit to pick from.

    Replaces the old standalone ServicesScreen/F2 binding, which only
    ever did restart and is now redundant - this covers restart, stop,
    and start, all reachable from the palette instead of a dedicated
    keybinding.

    Discovery/action both go through services.py, which never
    raises - failures are shown as a result line instead of crashing
    the screen."""

    CSS = """
    ServicePickerScreen { align: center middle; }
    #service-picker-dialog {
        width: 60; height: auto; max-height: 20;
        border: solid $accent; background: $panel; padding: 1 2;
    }
    #service-picker-result { height: auto; color: $text-muted; }
    """

    def __init__(self, action_label: str, action_fn):
        super().__init__()
        self.action_label = action_label
        self.action_fn = action_fn
        self.services = discover_services()

    def compose(self) -> ComposeResult:
        with Vertical(id="service-picker-dialog"):
            yield Label(f"{self.action_label} which service? (Enter to confirm, Esc to close)")
            if not self.services:
                yield Label("No ovos-*.service units found via systemctl --user.")
            else:
                yield ListView(*[ListItem(Label(s)) for s in self.services], id="service-picker-list")
            yield Label("", id="service-picker-result")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index
        if index is None or index >= len(self.services):
            return
        unit_name = self.services[index]
        ok, msg = self.action_fn(unit_name)
        prefix = "OK: " if ok else "FAILED: "
        self.query_one("#service-picker-result", Label).update(prefix + msg)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss()


class SkillsScreen(ModalScreen):
    """F3 - shows the list of currently loaded skills, requested from
    OVOS via bus.list_skills() (see bus.py's docstring on that method
    for the honesty note about its response format being unverified
    against a live modern instance). Not to be confused with
    SkillFilterScreen (F4) below - this one is a read-only list of
    what's actually installed; that one is a filter over skill_ids
    seen so far in the log stream."""

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


class SkillFilterScreen(ModalScreen):
    """F4 (also reachable by clicking the 'Skills:' status label) -
    filter which discovered skill_ids are shown. Sources and Log
    Levels are compact inline checkboxes directly in the main view
    (see OVOSTUIApp.compose) since both lists are short and
    fixed-length; skills stay in a modal since that list grows
    arbitrarily long as more are discovered from the log stream.

    Same unchecked-narrows-nothing / checked-narrows-to-checked
    semantics as the inline source/level checkboxes (see module
    docstring) - except Skills defaults UNCHECKED, not checked, since
    "check the few you care about" reads naturally for a list that
    keeps growing. Mutates the App's own skill_enabled dict directly
    (passed by reference, no separate sync-back step) and re-renders
    the log view live via self.app._rerender_logs() on every toggle.

    Known, accepted limitation: skill_ids discovered while this modal
    is already open won't appear until it's closed and reopened, since
    compose() only runs once at mount."""

    CSS = """
    SkillFilterScreen { align: center middle; }
    #skill-filter-dialog {
        width: 70; height: auto; max-height: 25;
        border: solid $accent; background: $panel; padding: 1 2;
    }
    Checkbox { height: 1; padding: 0; border: none; }
    """

    def __init__(self, skill_enabled: dict):
        super().__init__()
        self.skill_enabled = skill_enabled

    def compose(self) -> ComposeResult:
        with Vertical(id="skill-filter-dialog"):
            yield Label("Filter by skill (Esc to close) - none checked = show all")
            if not self.skill_enabled:
                yield Label("(no skills seen in the log stream yet)")
            for skill_id, enabled in self.skill_enabled.items():
                widget_id = "modal-skill-" + skill_id.replace(".", "-").replace("_", "-")
                yield Checkbox(skill_id, value=enabled, id=widget_id)

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        skill_id = str(event.checkbox.label)
        self.skill_enabled[skill_id] = event.value
        self.app._rerender_logs()

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss()


class ClickableLabel(Label):
    """A Label that also responds to being clicked, so it can double
    as a lightweight button without separate Button styling - used
    for the 'Skills: N/M' status text, which opens the skill-filter
    screen (same as pressing F4) when clicked. `can_focus = True` so
    it also picks up a visible focus outline and participates in the
    normal Tab cycle, useful for keyboard/no-mouse users - Enter or
    Space on a focused Label doesn't activate it by default in
    Textual, so on_key is added explicitly alongside on_click."""
    can_focus = True

    def on_click(self, event) -> None:
        self.app.action_show_skill_filter()

    def on_key(self, event) -> None:
        if event.key in ("enter", "space"):
            self.app.action_show_skill_filter()
            event.stop()


class OVOSTUIApp(App):
    CSS = """
    #logs-container {
        height: 45%;
        border: solid $accent;
    }
    #filter-row {
        height: 1;
    }
    #filter-row Checkbox {
        height: 1;
        padding: 0;
        border: none;
        margin-right: 2;
    }
    .filter-label {
        margin-right: 1;
        color: $text-muted;
    }
    #skills-status {
        margin-right: 1;
        color: $text-muted;
        text-style: underline;
    }
    #skills-status:focus {
        color: $accent;
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
        ("ctrl+q", "quit", "Quit"),
        ("f1", "show_help", "Help"),
        ("f3", "show_skills", "Skills"),
        ("f4", "show_skill_filter", "Skill Filter"),
        ("f5", "focus_logs", "Logs"),
        ("f6", "focus_conversation", "Conversation"),
        ("f7", "focus_activity", "Activity"),
        ("f8", "focus_input", "Input"),
    ]
    # No F2/Services binding anymore - service management (restart/
    # stop/start) moved entirely into the Command Palette (type
    # "service", see get_system_commands() below), which replaced the
    # old standalone Services modal. Not renumbering F3+ to fill the
    # gap - no benefit to disrupting keys that already work.

    def __init__(self, host="127.0.0.1", port=8181, lang="en-us", log_dir_override=None):
        super().__init__()
        self.bus = OVOSBusConnection(host=host, port=port, lang=lang)
        self.log_dir = find_log_dir(override=log_dir_override)
        self.log_sources = discover_log_sources(self.log_dir)
        self.utterance_history = []
        self.history_index = None
        self.log_buffer = deque(maxlen=LOG_BUFFER_SIZE)
        self.log_filter_text = ""
        # True by default: Sources/Levels are short, fixed-length
        # lists where "everything on, uncheck what you don't want"
        # reads naturally - see module docstring's FILTER SEMANTICS.
        self.level_enabled = {level: True for level in KNOWN_LOG_LEVELS}
        self.skill_enabled = {}  # skill_id -> bool, unchecked by default as discovered

        # Restores filter choices from a previous session (state.py) -
        # only for sources/levels that still exist on THIS run (a
        # saved 'phal: False' is meaningless if phal.log doesn't exist
        # here), applied on top of the defaults above rather than
        # replacing them, so anything not covered by the save just
        # keeps its normal default.
        saved = load_filter_state()
        for src in self.log_sources:
            if src.name in saved["sources"]:
                src.enabled = saved["sources"][src.name]
        for level in self.level_enabled:
            if level in saved["levels"]:
                self.level_enabled[level] = saved["levels"][level]
        self.skill_enabled.update(saved["skills"])

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="logs-container"):
            with Horizontal(id="filter-row"):
                yield Label("Sources:", classes="filter-label")
                for src in self.log_sources:
                    yield Checkbox(src.name, value=src.enabled, id=f"toggle-source-{src.name}")
                yield Label("Log Levels:", classes="filter-label")
                for level in KNOWN_LOG_LEVELS:
                    yield Checkbox(level, value=self.level_enabled.get(level, True), id=f"toggle-level-{level}")
                yield ClickableLabel("", id="skills-status")
            yield Input(placeholder="Filter logs (free text)...", id="log-filter")
            yield RichLog(id="logs-view", wrap=False, markup=True, auto_scroll=True)
        with Horizontal(id="middle-row"):
            yield RichLog(id="conversation", wrap=True, markup=True, auto_scroll=True)
            yield RichLog(id="activity", wrap=True, markup=True, auto_scroll=True)
        yield Input(placeholder="Type what you'd say to OVOS...", id="utterance-input", select_on_focus=False)
        yield Footer()

    def on_mount(self) -> None:
        if not self.log_sources:
            self._write_to_log(
                self.query_one("#logs-view", RichLog),
                f"[yellow]No known log files found"
                + (f" in {self.log_dir}" if self.log_dir else " in any candidate directory")
                + ". Pass --log-dir to point at the right one.[/yellow]"
            )
        self._update_skills_status()
        self.bus.on_speak(self._handle_speak)
        self.bus.on_activity(self._handle_activity)
        self.bus.connect()
        self.set_interval(LOG_POLL_INTERVAL, self._poll_logs)
        self.query_one("#utterance-input", Input).focus()

    def _write_to_log(self, widget: RichLog, content) -> None:
        """The one place every RichLog write goes through, for all
        three panes (logs/conversation/activity). Only keeps
        auto-scrolling if the user is already at (or very near) the
        bottom - scrolling back to read or copy something is never
        yanked back down by new content arriving. is_vertical_scroll_end
        is checked fresh on every write, so it re-engages automatically
        once the user scrolls back to the bottom themselves."""
        widget.auto_scroll = widget.is_vertical_scroll_end
        widget.write(content)

    def _handle_speak(self, utterance: str) -> None:
        self.call_from_thread(self._write_conversation, f"[blue]OVOS: {utterance}[/blue]")

    def _handle_activity(self, line: str) -> None:
        self.call_from_thread(self._write_activity, line)

    def _write_conversation(self, line: str) -> None:
        self._write_to_log(self.query_one("#conversation", RichLog), line)

    def _write_activity(self, line: str) -> None:
        self._write_to_log(self.query_one("#activity", RichLog), line)

    def _update_skills_status(self) -> None:
        """Sources/Levels are now directly visible as checkboxes, so
        they need no separate status text. Skills stays behind an F4
        modal (its list can grow long), so a small trailing count +
        hint is all that's shown for it here. The label itself is
        clickable (ClickableLabel) as an alternative to F4."""
        try:
            label = self.query_one("#skills-status", ClickableLabel)
        except NoMatches:
            return
        if not self.skill_enabled:
            label.update("Skills: none seen yet (F4)")
            return
        n_sk = len(self.skill_enabled)
        n_sk_on = sum(1 for v in self.skill_enabled.values() if v)
        label.update(f"Skills: {n_sk_on}/{n_sk} filtered (click or F4)")

    def _line_passes_all_filters(self, source_name: str, line: str) -> bool:
        """The full filter chain a line must pass to be shown - see
        the module docstring's FILTER SEMANTICS section: an empty
        'checked' set for a category means that category is
        unrestricted (everything passes); a non-empty checked set
        restricts to only the checked ones. Lines where a category
        doesn't apply at all (e.g. no detectable log level or
        skill_id) always pass that category's check regardless of
        what's checked, since the filter has nothing to match against."""
        checked_sources = {s.name for s in self.log_sources if s.enabled}
        if checked_sources and source_name not in checked_sources:
            return False
        checked_levels = {lvl for lvl, on in self.level_enabled.items() if on}
        if checked_levels:
            level = extract_log_level(line)
            if level is not None and level not in checked_levels:
                return False
        checked_skills = {sid for sid, on in self.skill_enabled.items() if on}
        if checked_skills:
            skill_id = extract_skill_id(line)
            if skill_id is not None and skill_id not in checked_skills:
                return False
        return line_matches_filter(line, self.log_filter_text)

    def _maybe_register_skill(self, line: str) -> None:
        """Best-effort: tracks a new skill_id the first time it's seen
        in log text, so it shows up next time the F4 skill-filter
        modal is opened. See logs.extract_skill_id's docstring for why
        this only catches some lines, not all skill-related ones."""
        skill_id = extract_skill_id(line)
        if skill_id is None or skill_id in self.skill_enabled:
            return
        self.skill_enabled[skill_id] = False

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
                    self._write_to_log(view, format_log_line(src.name, line))
        if new_skill_seen:
            self._update_skills_status()

    def _rerender_logs(self) -> None:
        """Re-draws the whole logs pane from self.log_buffer against
        the current filters - needed because RichLog is append-only,
        so a filter/toggle change has to replay history rather than
        just affecting future lines. Also refreshes the skills status
        label, since this is called from the skill-filter modal on
        every toggle. Forces auto-scroll back on for this one
        operation regardless of prior scroll position - a filter
        change is a deliberate action, unlike new background log
        activity, so jumping to the (re-filtered) bottom is expected
        here rather than something to protect the user's place from."""
        view = self.query_one("#logs-view", RichLog)
        view.auto_scroll = True
        view.clear()
        for source_name, line in self.log_buffer:
            if self._line_passes_all_filters(source_name, line):
                view.write(format_log_line(source_name, line))
        self._update_skills_status()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        """Handles the inline Sources/Levels checkboxes that now live
        directly in the main view (compose()). Checkboxes belonging to
        a pushed modal screen (SkillFilterScreen, etc) have their own
        on_checkbox_changed on the screen itself, which Textual calls
        instead of this one - this only sees the main screen's own
        checkboxes."""
        checkbox_id = event.checkbox.id or ""
        if checkbox_id.startswith("toggle-source-"):
            name = checkbox_id.removeprefix("toggle-source-")
            for src in self.log_sources:
                if src.name == name:
                    src.enabled = event.value
        elif checkbox_id.startswith("toggle-level-"):
            level = checkbox_id.removeprefix("toggle-level-")
            self.level_enabled[level] = event.value
        else:
            return
        self._rerender_logs()

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
        self._write_conversation(f"[green]You: {text}[/green]")
        self.bus.send_utterance(text)
        self.utterance_history.append(text)
        self.history_index = None
        event.input.value = ""

    def get_system_commands(self, screen: Screen):
        """Surfaces the same actions available via F1/F3/F4-F8 in
        Textual's built-in command palette (Ctrl+P) too, so they're
        discoverable by fuzzy search as well as by key.

        GROUPING BY PREFIX (issue #3 follow-up): the palette has no
        native submenu/nested-selection support (confirmed - it's a
        flat fuzzy-matched list, same as every other command-palette
        implementation), so "typing a category, then narrowing" is
        achieved by giving related commands a shared literal prefix.
        Typing "log" clusters every Source/Level toggle together
        ("Log: Toggle source: ..." / "Log: Toggle level: ..."); typing
        "service" clusters the three service actions together
        ("Service: Restart..." / "Service: Stop..." / "Service:
        Start..."). The service actions go one step further: since
        picking e.g. "Service: Restart..." still needs a SPECIFIC unit
        chosen afterward (unlike a toggle, which is already atomic),
        selecting one opens ServicePickerScreen with that action
        pre-bound, listing discovered services to choose from - a
        genuine two-step flow, just implemented as 'command opens a
        small follow-up screen' rather than a nested palette, since
        the palette itself doesn't support that.

        This is also why the old standalone Services modal (F2) is
        gone: it's now entirely subsumed by these three palette
        entries + ServicePickerScreen, redundant to keep as a separate
        keybinding."""
        yield from super().get_system_commands(screen)
        yield SystemCommand("Help: show keybindings", "Lists every keybinding (same as F1)", self.action_show_help)
        yield SystemCommand("Skills: show installed", "Lists currently loaded skills (same as F3)", self.action_show_skills)
        yield SystemCommand("Skills: filter logs by skill", "Opens the skill-filter panel (same as F4)", self.action_show_skill_filter)
        yield SystemCommand("Focus: Logs", "Jump focus to the logs pane (same as F5)", self.action_focus_logs)
        yield SystemCommand("Focus: Conversation", "Jump focus to the conversation pane (same as F6)", self.action_focus_conversation)
        yield SystemCommand("Focus: Activity", "Jump focus to the activity pane (same as F7)", self.action_focus_activity)
        yield SystemCommand("Focus: Utterance input", "Jump focus to the input box (same as F8)", self.action_focus_input)
        yield SystemCommand("Service: Restart...", "Choose a service to restart", partial(self._pick_service, "Restart", restart_service))
        yield SystemCommand("Service: Stop...", "Choose a service to stop", partial(self._pick_service, "Stop", stop_service))
        yield SystemCommand("Service: Start...", "Choose a service to start", partial(self._pick_service, "Start", start_service))
        for src in self.log_sources:
            state = "checked" if src.enabled else "unchecked"
            yield SystemCommand(f"Log: Toggle source: {src.name}", f"Currently {state}", partial(self._toggle_source, src.name))
        for level in KNOWN_LOG_LEVELS:
            state = "checked" if self.level_enabled.get(level, True) else "unchecked"
            yield SystemCommand(f"Log: Toggle level: {level}", f"Currently {state}", partial(self._toggle_level, level))

    def _pick_service(self, action_label: str, action_fn) -> None:
        self.push_screen(ServicePickerScreen(action_label, action_fn))

    def _toggle_source(self, name: str) -> None:
        """Flips a source's enabled state and syncs the visible
        checkbox + re-renders - shared by both the palette command
        above and could be reused anywhere else that needs to toggle a
        source programmatically."""
        for src in self.log_sources:
            if src.name == name:
                src.enabled = not src.enabled
        try:
            self.query_one(f"#toggle-source-{name}", Checkbox).value = next(
                s.enabled for s in self.log_sources if s.name == name
            )
        except NoMatches:
            pass
        self._rerender_logs()

    def _toggle_level(self, level: str) -> None:
        self.level_enabled[level] = not self.level_enabled.get(level, True)
        try:
            self.query_one(f"#toggle-level-{level}", Checkbox).value = self.level_enabled[level]
        except NoMatches:
            pass
        self._rerender_logs()

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_show_skills(self) -> None:
        screen = SkillsScreen()
        self.push_screen(screen)
        self.bus.list_skills(lambda skills: self.call_from_thread(screen.show_skills, skills))

    def action_show_skill_filter(self) -> None:
        self.push_screen(SkillFilterScreen(self.skill_enabled))

    def action_focus_logs(self) -> None:
        self.query_one("#logs-view", RichLog).focus()

    def action_focus_conversation(self) -> None:
        self.query_one("#conversation", RichLog).focus()

    def action_focus_activity(self) -> None:
        self.query_one("#activity", RichLog).focus()

    def action_focus_input(self) -> None:
        self.query_one("#utterance-input", Input).focus()

    async def action_quit(self) -> None:
        """Overrides Textual's default just to save filter state first
        (state.py) - so Sources/Levels/Skills choices survive to the
        next session. save_filter_state() never raises, so this can't
        turn a normal quit into a crash."""
        checked_sources = {s.name: s.enabled for s in self.log_sources}
        save_filter_state(checked_sources, dict(self.level_enabled), dict(self.skill_enabled))
        await super().action_quit()

    def on_key(self, event) -> None:
        input_widget = self.query_one("#utterance-input", Input)

        # Typing a plain printable character while focus is on a pane
        # that was never meant to receive text (Logs/Conversation/
        # Activity - none of them are Input widgets) almost certainly
        # means the person meant to talk to OVOS and just hadn't
        # clicked into the input box first. Redirect the keystroke
        # there instead of silently doing nothing. Deliberately scoped
        # to those three ids only - checkboxes' own Space-to-toggle
        # handling must NOT be intercepted here.
        focused_id = self.focused.id if self.focused else None
        if focused_id in REDIRECT_TO_INPUT_IDS and event.character and event.character.isprintable():
            input_widget.focus()
            # insert_text_at_cursor(), not a manual `.value +=` - the
            # latter leaves Input's internal selection/cursor state
            # inconsistent. Real bug found via testing: Input's own
            # _on_focus() has a built-in "select all on focus" default
            # (select_on_focus=True), which fired AFTER this method's
            # own code ran (the Focus message is processed on a later
            # tick, not synchronously within this call) and silently
            # re-selected the just-inserted text - so the VERY NEXT
            # keystroke replaced it instead of appending (standard
            # text-widget behavior: typing over a selection replaces
            # it), and "hi" typed as two redirected keystrokes ended
            # up as just "i". Fixed at the source: the utterance-input
            # is constructed with select_on_focus=False in compose()
            # (makes sense for a chat-style input anyway - you're
            # almost always continuing to type, not overwriting).
            input_widget.insert_text_at_cursor(event.character)
            event.prevent_default()
            event.stop()
            return

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
