"""'Test: Create shareable report' (#51, interactive side): the few things
a report needs from a person, asked in one window after a test run.

Everything else in the report is filled in by itself (manifest.py): the
installed versions, the machine type, language, STT/TTS names and each
step. This window only asks what the tool can't know or shouldn't decide
alone: the release channel (pre-set from channel.py's check), a note on
the setup, and whether OVOS's replies go in (off: they can hold personal
data). Dismisses with {"channel", "notes", "replies", "action"} or None;
action is "save" (save and copy to the clipboard) or "show" (save and show
it in ReportViewScreen, for terminals that don't pass the copy on).
"""
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select, Static, TextArea

from ovos_tui_client.channel import CHANNELS

UNKNOWN = "unknown"

NOTES_PLACEHOLDER = "e.g. API key set · Mark II · Raspberry Pi 5 with ReSpeaker · wake word 'hey mycroft'"


def channel_hint(result) -> str:
    """One line on how the pre-set channel was found, for the window."""
    if result is None:
        return "OVOS is on another machine, so the channel can't be checked from here. Pick it if you know it."
    ch = result.get("channel")
    if ch:
        how = {"ovos-installer": "from the OVOS installer's state file",
               "raspOVOS": "from raspOVOS's /opt/ovos/tag",
               "installed versions": "worked out from the installed versions"}.get(result.get("source"), "")
        return f"Found: {ch} ({how}, checked against today's constraints)."
    if result.get("declared"):
        return (f"The install says {result['declared']}, but its versions don't match that channel today. "
                "Leave it 'unknown' unless you're sure.")
    if len(result.get("matches") or []) > 1:
        return f"The versions fit {' and '.join(result['matches'])}. Pick the one you installed."
    if result.get("checked"):
        return "The versions match no channel as it is today. Leave it 'unknown'."
    return "Could not check (no network?). Pick it if you know it."


class ReportScreen(ModalScreen):
    __test__ = False

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save report"),
        Binding("ctrl+o", "show", "Save and show"),
    ]

    DEFAULT_CSS = """
    ReportScreen { align: center middle; }
    #report-box {
        width: 90%; max-width: 110; height: auto; max-height: 90%;
        border: heavy $accent; background: $surface; padding: 0 1;
    }
    #report-title { text-style: bold; margin-bottom: 1; }
    .report-help { color: $text-muted; margin-bottom: 1; }
    .report-label { text-style: bold; }
    #report-buttons { height: auto; margin-top: 1; }
    #report-buttons Button { margin-right: 2; }
    """

    def __init__(self, title: str, channel_result, n_steps: int):
        super().__init__()
        self._title = title
        self._result = channel_result
        self._n = n_steps

    def compose(self) -> ComposeResult:
        found = (self._result or {}).get("channel")
        options = [(c, c) for c in CHANNELS] + [("unknown", UNKNOWN)]
        with Vertical(id="report-box"):
            steps = "1 step" if self._n == 1 else f"{self._n} steps"
            yield Label(f"Create a shareable report: {self._title} ({steps})", id="report-title")
            with VerticalScroll():
                yield Static(
                    "A report is one JSON file you can give a skill's maintainer, put in an issue, or "
                    "paste into a skill store. It holds the installed OVOS versions, the release channel, "
                    "machine type, language, the STT and TTS plugin names, and each sentence with what "
                    "handled it. It never holds your hostname, IP address, user name or any settings.",
                    classes="report-help")
                yield Label("Release channel", classes="report-label")
                yield Static(channel_hint(self._result) +
                             "  (Ctrl+P → 'OVOS: Release channel' explains the channels and how to change.)",
                             classes="report-help")
                yield Select(options, value=found or UNKNOWN, allow_blank=False, id="report-channel")
                yield Label("Notes on this setup (optional)", classes="report-label")
                yield Static("What the skill needed, or what is special here. Don't write names, "
                             "addresses or keys: the report is meant to be shared.", classes="report-help")
                yield Input(placeholder=NOTES_PLACEHOLDER, id="report-notes", max_length=500)
                yield Checkbox("Include OVOS's replies", value=False, id="report-replies")
                yield Static("Only for a report you keep yourself: replies can hold personal data "
                             "(\"14 degrees in <your town>\"), and a skill store will refuse them.",
                             classes="report-help")
            with Horizontal(id="report-buttons"):
                yield Button("Save and copy (Ctrl+S)", id="report-save", variant="primary")
                yield Button("Save and show (Ctrl+O)", id="report-show")
                yield Button("Cancel (Esc)", id="report-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "report-save":
            self.action_save()
        elif event.button.id == "report-show":
            self.action_show()
        else:
            self.action_cancel()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_save()

    def action_save(self, action: str = "save") -> None:
        channel = self.query_one("#report-channel", Select).value
        self.dismiss({
            "action": action,
            "channel": None if channel in (UNKNOWN, Select.BLANK) else channel,
            "notes": self.query_one("#report-notes", Input).value.strip() or None,
            "replies": self.query_one("#report-replies", Checkbox).value,
        })

    def action_show(self) -> None:
        self.action_save("show")

    def action_cancel(self) -> None:
        self.dismiss(None)


class ReportViewScreen(ModalScreen):
    """A saved report as text, to read before sharing it and to copy by hand
    when the terminal doesn't pass Copy on to the clipboard. Also opened by
    'Test: Show last report'."""
    __test__ = False

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("ctrl+c", "copy", "Copy"),
    ]

    DEFAULT_CSS = """
    ReportViewScreen { align: center middle; }
    #report-view-box {
        width: 95%; height: 90%;
        border: heavy $accent; background: $surface; padding: 0 1;
    }
    #report-view-title { text-style: bold; }
    .report-help { color: $text-muted; }
    #report-view-text { height: 1fr; }
    #report-view-buttons { height: auto; }
    #report-view-buttons Button { margin-right: 2; }
    """

    def __init__(self, title: str, text: str, path=None):
        super().__init__()
        self._title = title
        self._text = text
        self._path = path

    def compose(self) -> ComposeResult:
        with Vertical(id="report-view-box"):
            yield Label(f"Report: {self._title}", id="report-view-title")
            where = f"Saved as {self._path}. " if self._path else ""
            yield Static(
                f"{where}Copy puts it on the clipboard when the terminal allows it. If it doesn't: select "
                "the text with the mouse while holding Shift (Option on macOS), or attach the file.",
                classes="report-help")
            yield TextArea(self._text, read_only=True, id="report-view-text")
            with Horizontal(id="report-view-buttons"):
                yield Button("Copy (Ctrl+C)", id="report-view-copy", variant="primary")
                yield Button("Close (Esc)", id="report-view-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "report-view-copy":
            self.action_copy()
        else:
            self.action_close()

    def action_copy(self) -> None:
        try:
            self.app.copy_to_clipboard(self._text)
            self.notify("Copied, if your terminal allows it. Otherwise: Shift + mouse to select.")
        except Exception:  # noqa: BLE001
            self.notify("This terminal can't copy. Shift + mouse selects the text.", severity="warning")

    def action_close(self) -> None:
        self.dismiss(None)
