"""About windows (#29, #15): read-only information, opened from the
Command Palette like the test picker (#34).

- About: <Skill>          - what a skill is and what it can do: name,
  description, examples and tags from its own skill.json, the installed
  package/version and repo, and its golden test coverage per intent
  (looked up in the background). Buttons jump straight into testing it.
- About: Installed skills - every installed skill, one per line, grouped
  by state; picking one opens its About.
- About: ovos-tui-client  - version, repo, connection and where this tool
  keeps its logs, scripts and caches.

The Markdown for each is built by plain functions below (no UI), so the
content is unit-testable without running an App.
"""
from collections import Counter

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Markdown, OptionList
from textual.widgets.option_list import Option

TUI_REPO_URL = "https://github.com/andlo/ovos-tui-client"


def _md_escape(text) -> str:
    return str(text).replace("|", "\\|")


def skill_about_markdown(skill_id: str, display_name: str, skill_json: dict, active,
                         distribution=None, repo_url=None, golden=None, golden_source=None,
                         lang: str = "en-us") -> str:
    """golden: None = still looking up; [] = none found; else ScriptStep list."""
    name = skill_json.get("name") or display_name
    state = {True: "Active", False: "Inactive"}.get(active, "State unknown")
    lines = [f"# {_md_escape(name)}", "", f"`{skill_id}` · **{state}**", ""]
    if distribution:
        lines.append(f"- **Package:** `{distribution[0]}` {distribution[1]}")
    source = repo_url or skill_json.get("source")
    if source:
        lines.append(f"- **Source:** {source}")
    if distribution or source:
        lines.append("")

    description = skill_json.get("description") or skill_json.get("short_description")
    lines += ["## Description", "", _md_escape(description) if description
              else "_No description in this skill's skill.json (or the skill isn't installed on this machine)._", ""]

    examples = [e for e in (skill_json.get("examples") or []) if isinstance(e, str)]
    if examples:
        lines += ["## Examples", ""] + [f"- {_md_escape(e)}" for e in examples] + [""]

    tags = [t for t in (skill_json.get("tags") or []) if isinstance(t, str)]
    if tags:
        lines += [f"**Tags:** {', '.join(_md_escape(t) for t in tags)}", ""]

    lines += [f"## Golden tests ({lang})", ""]
    if golden is None:
        lines.append("_Looking up golden utterances…_")
    elif not golden:
        lines.append("_None found - no golden utterances for this language and no skill.json examples._")
    else:
        counts = Counter(step.intent_label or "(no intent label - skill-level check)" for step in golden)
        lines += [f"{len(golden)} utterance(s)" + (f", from {golden_source}" if golden_source else "") + ":", "",
                  "| Intent | Utterances |", "|---|---|"]
        lines += [f"| {_md_escape(label)} | {n} |" for label, n in counts.items()]
    return "\n".join(lines) + "\n"


def tui_about_markdown(version: str, host: str, port: int, lang: str, log_dir, log_sources,
                       scripts_dir, golden_cache_dir, golden_dirs, n_skills: int) -> str:
    sources = ", ".join(log_sources) if log_sources else "none found"
    lines = [
        "# ovos-tui-client", "",
        f"**Version {version}** · {TUI_REPO_URL}", "",
        "A split-pane terminal UI for testing OVOS without a mic/speaker.", "",
        "## Connection", "",
        f"- **Messagebus:** {host}:{port}",
        f"- **Language:** {lang}",
        f"- **Installed skills seen:** {n_skills}", "",
        "## Where things are", "",
        f"- **Logs:** {log_dir or 'not found'} ({sources})",
        f"- **Your test scripts:** {scripts_dir}",
        f"- **Golden utterance cache:** {golden_cache_dir}",
    ]
    if golden_dirs:
        lines.append(f"- **--golden-dir:** {', '.join(str(d) for d in golden_dirs)}")
    lines += ["", "## Getting around", "",
              "- **Ctrl+P** - Command Palette: `Test:`, `Script:`, `About:`, `Service:`, `Skill:`, `Log:`, `Clear:`",
              "- **F1** help · **F5-F8** focus Logs / Conversation / Activity / Input · **Ctrl+Q** quit"]
    return "\n".join(lines) + "\n"


class AboutBase(ModalScreen):
    BINDINGS = [Binding("escape", "close", "Close")]
    DEFAULT_CSS = """
    AboutBase {
        align: center middle;
    }
    .about-box {
        width: 90%;
        height: 85%;
        border: heavy $accent;
        background: $surface;
        padding: 0 1;
    }
    .about-scroll {
        height: 1fr;
    }
    .about-buttons {
        height: auto;
        margin-top: 1;
    }
    .about-buttons Button {
        margin-right: 1;
    }
    """

    def action_close(self) -> None:
        self.dismiss(None)


class SkillAboutScreen(AboutBase):
    """Dismisses with "test_all", "choose" or None."""
    __test__ = False

    def __init__(self, render_markdown, show_test_buttons: bool = True):
        super().__init__()
        self._make_markdown = render_markdown   # callable(golden, golden_source) -> str (not "_render": that name is Widget internals)
        self._show_test_buttons = show_test_buttons
        # golden results can arrive before compose() has run (a warm cache
        # or a fast network answers within the same tick) - kept here and
        # used by compose() instead of being lost
        self._golden = (None, None)

    def compose(self) -> ComposeResult:
        with Vertical(classes="about-box"):
            with VerticalScroll(classes="about-scroll"):
                yield Markdown(self._make_markdown(*self._golden), id="about-md")
            with Horizontal(classes="about-buttons"):
                if self._show_test_buttons:
                    yield Button("Test: All", id="about-test-all", variant="primary")
                    yield Button("Test: Choose", id="about-choose")
                yield Button("Close (Esc)", id="about-close")

    def on_mount(self) -> None:
        # covers results that arrived between compose() and mount
        if self._golden != (None, None):
            self.query_one("#about-md", Markdown).update(self._make_markdown(*self._golden))

    def show_golden(self, golden, source) -> None:
        self._golden = (golden, source)
        if self.is_mounted:
            self.query_one("#about-md", Markdown).update(self._make_markdown(golden, source))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss({"about-test-all": "test_all", "about-choose": "choose"}.get(event.button.id))


class TextAboutScreen(AboutBase):
    __test__ = False

    def __init__(self, markdown: str):
        super().__init__()
        self._markdown = markdown

    def compose(self) -> ComposeResult:
        with Vertical(classes="about-box"):
            with VerticalScroll(classes="about-scroll"):
                yield Markdown(self._markdown, id="about-md")
            with Horizontal(classes="about-buttons"):
                yield Button("Close (Esc)", id="about-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)


def installed_skills_rows(installed: dict, display_name, version_of=lambda skill_id: None):
    """[(heading, [(skill_id, line), ...]), ...] - Active, Inactive, State
    unknown; each group sorted by display name. #15: one skill per line."""
    groups = [("Active", True), ("Inactive", False), ("State unknown", None)]
    out = []
    for heading, state in groups:
        ids = sorted((sid for sid, active in installed.items() if active is state),
                     key=lambda sid: display_name(sid).lower())
        if not ids:
            continue
        rows = []
        for sid in ids:
            version = version_of(sid)
            rows.append((sid, f"{display_name(sid)}  ·  {sid}" + (f"  ·  {version}" if version else "")))
        out.append((heading, rows))
    return out


class InstalledSkillsScreen(AboutBase):
    """Dismisses with the picked skill_id (to open its About) or None."""
    __test__ = False

    def __init__(self, title: str, grouped_rows):
        super().__init__()
        self._title = title
        self._grouped = grouped_rows

    def compose(self) -> ComposeResult:
        options = []
        for heading, rows in self._grouped:
            options.append(Option(f"── {heading} ({len(rows)}) ──", disabled=True))
            options += [Option(line, id=skill_id) for skill_id, line in rows]
        with Vertical(classes="about-box"):
            yield Label(self._title, id="installed-title")
            yield OptionList(*options, id="installed-list", classes="about-scroll")
            with Horizontal(classes="about-buttons"):
                yield Button("Close (Esc)", id="about-close")

    def on_mount(self) -> None:
        self.query_one("#installed-list", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id:
            self.dismiss(event.option.id)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)
