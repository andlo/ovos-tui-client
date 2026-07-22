"""The Textual App: a 4-pane layout for testing OVOS without a
mic/speaker.

    ┌──────────────────────────────────────────┐
    │ Logs (top, full width) - toggleable,      │
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
"""
import argparse
from collections import deque

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, Input, RichLog, Checkbox

from ovos_tui_client.bus import OVOSBusConnection
from ovos_tui_client.logs import find_log_dir, discover_log_sources, line_matches_filter, strip_log_prefix, KNOWN_LOG_NAMES

LOG_POLL_INTERVAL = 0.5  # seconds
LOG_BUFFER_SIZE = 5000  # lines kept in memory for re-filtering; oldest dropped past this

# All [source] tags are padded to this width so message text lines up
# in the same column regardless of source name length ('bus' vs
# 'enclosure'). Based on the full known list, not just currently active
# sources, so toggling a source on/off doesn't shift alignment around.
SOURCE_TAG_WIDTH = max(len(name) for name in KNOWN_LOG_NAMES)

# One color per known log source (see logs.py's KNOWN_LOG_NAMES) - any
# source not listed here falls back to DEFAULT_LOG_COLOR rather than
# raising, since new log names can appear on newer OVOS versions.
LOG_SOURCE_COLORS = {
    "bus": "magenta",
    "skills": "green",
    "audio": "yellow",
    "media": "yellow",
    "voice": "cyan",
    "gui": "blue",
    "enclosure": "white",
    "phal": "red",
}
DEFAULT_LOG_COLOR = "white"


def format_log_line(source_name: str, line: str) -> str:
    """Colors a log line by its source, bolding it if it contains
    'ERROR' - pulled out as a standalone function so it's testable
    without a running App.

    Strips OVOS's own 'TIMESTAMP - COMPONENT - ' prefix first (see
    logs.strip_log_prefix - both fields are redundant here: the
    timestamp isn't useful in a live-scrolling view, and the component
    name just repeats the [source] tag this function adds), and pads
    the source tag to a fixed width so message text starts at the same
    column regardless of source name length."""
    clean_line = strip_log_prefix(line)
    color = LOG_SOURCE_COLORS.get(source_name, DEFAULT_LOG_COLOR)
    padded_name = source_name.ljust(SOURCE_TAG_WIDTH)
    text = f"[{color}]\\[{padded_name}][/{color}] {clean_line}"
    if "ERROR" in clean_line:
        text = f"[bold]{text}[/bold]"
    return text


class OVOSTUIApp(App):
    CSS = """
    #logs-container {
        height: 45%;
        border: solid $accent;
    }
    #log-toggles {
        height: 1;
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

    BINDINGS = [("ctrl+c", "quit", "Quit")]

    def __init__(self, host="127.0.0.1", port=8181, lang="en-us", log_dir_override=None):
        super().__init__()
        self.bus = OVOSBusConnection(host=host, port=port, lang=lang)
        self.log_dir = find_log_dir(override=log_dir_override)
        self.log_sources = discover_log_sources(self.log_dir)
        self.utterance_history = []
        self.history_index = None
        self.log_buffer = deque(maxlen=LOG_BUFFER_SIZE)  # (source_name, line) pairs, for re-filtering
        self.log_filter_text = ""

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="logs-container"):
            with Horizontal(id="log-toggles"):
                for src in self.log_sources:
                    yield Checkbox(src.name, value=True, id=f"toggle-{src.name}")
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

    def _poll_logs(self) -> None:
        view = self.query_one("#logs-view", RichLog)
        for src in self.log_sources:
            new_lines = src.read_new_lines()
            for line in new_lines:
                self.log_buffer.append((src.name, line))
                if src.enabled and line_matches_filter(line, self.log_filter_text):
                    view.write(format_log_line(src.name, line))

    def _rerender_logs(self) -> None:
        """Re-draws the whole logs pane from self.log_buffer against
        the current filter text and per-source enabled state - needed
        because RichLog is append-only, so a filter/toggle change has
        to replay history rather than just affecting future lines."""
        enabled = {src.name for src in self.log_sources if src.enabled}
        view = self.query_one("#logs-view", RichLog)
        view.clear()
        for source_name, line in self.log_buffer:
            if source_name in enabled and line_matches_filter(line, self.log_filter_text):
                view.write(format_log_line(source_name, line))

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        name = event.checkbox.id.removeprefix("toggle-")
        for src in self.log_sources:
            if src.name == name:
                src.enabled = event.value
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
        self.query_one("#conversation", RichLog).write(f"[green]You: {text}[/green]")
        self.bus.send_utterance(text)
        self.utterance_history.append(text)
        self.history_index = None
        event.input.value = ""

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
