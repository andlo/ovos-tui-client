"""The Textual App: a 4-pane layout for testing OVOS without a
mic/speaker.

    ┌──────────────────────────────────────────┐
    │ Logs (top, full width) - toggleable       │
    ├───────────────────────────┬───────────────┤
    │ Conversation (2/3 width)  │ Activity (1/3) │
    ├───────────────────────────┴───────────────┤
    │ Input (bottom, full width)                 │
    └──────────────────────────────────────────┘

Logs and activity are populated by polling logs.py/bus.py on a timer -
Textual widgets aren't thread-safe to update directly from the
bus-client's background thread, so incoming lines are queued and
drained on the UI's own event loop via a timer instead.
"""
import argparse
import queue

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, Input, RichLog, Checkbox

from ovos_tui_client.bus import OVOSBusConnection
from ovos_tui_client.logs import find_log_dir, discover_log_sources

LOG_POLL_INTERVAL = 0.5  # seconds


class OVOSTUIApp(App):
    CSS = """
    #logs-container {
        height: 30%;
        border: solid $accent;
    }
    #log-toggles {
        height: 1;
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

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="logs-container"):
            with Horizontal(id="log-toggles"):
                for src in self.log_sources:
                    yield Checkbox(src.name, value=True, id=f"toggle-{src.name}")
            yield RichLog(id="logs-view", wrap=False, markup=True)
        with Horizontal(id="middle-row"):
            yield RichLog(id="conversation", wrap=True, markup=True)
            yield RichLog(id="activity", wrap=True, markup=True)
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
        self.call_from_thread(self._write_conversation, f"[cyan]OVOS:[/cyan] {utterance}")

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
            if not src.enabled or not new_lines:
                continue
            for line in new_lines:
                view.write(f"[dim]\\[{src.name}][/dim] {line}")

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        name = event.checkbox.id.removeprefix("toggle-")
        for src in self.log_sources:
            if src.name == name:
                src.enabled = event.value

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        self.query_one("#conversation", RichLog).write(f"[green]You:[/green] {text}")
        self.bus.send_utterance(text)
        event.input.value = ""


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
