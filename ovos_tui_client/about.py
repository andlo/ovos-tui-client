"""About windows (#29, #15): read-only information, opened from the
Command Palette like the test picker (#34).

- About: <Skill>          - what a skill is and what it can do: name,
  description, examples and tags from its own skill.json, the installed
  package/version and repo, and its golden test coverage per intent
  (looked up in the background). Buttons jump straight into testing it.
- About: Installed skills - every installed skill, one per line; the
  checkbox is its active state (Space toggles), Enter opens its About.
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
from textual.widgets import Button, Input, Label, Markdown, SelectionList
from textual.widgets.selection_list import Selection

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
    """Dismisses with "test_all", "choose" or None. Activate/deactivate
    happens in place (the window stays open and re-renders the state)."""
    __test__ = False

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("t", "test_all", "Test: All"),
        Binding("c", "choose", "Test: Choose"),
        Binding("a", "toggle_active", "Activate/Deactivate"),
    ]

    def __init__(self, render_markdown, show_test_buttons: bool = True,
                 get_active=lambda: None, on_toggle_active=None):
        super().__init__()
        self._make_markdown = render_markdown   # callable(golden, golden_source) -> str (not "_render": that name is Widget internals)
        self._show_test_buttons = show_test_buttons
        self._get_active = get_active            # current state, read fresh each render
        self._on_toggle_active = on_toggle_active
        # golden results can arrive before compose() has run (a warm cache
        # or a fast network answers within the same tick) - kept here and
        # used by compose() instead of being lost
        self._golden = (None, None)

    def _toggle_label(self) -> str:
        return "Deactivate (a)" if self._get_active() else "Activate (a)"

    def compose(self) -> ComposeResult:
        with Vertical(classes="about-box"):
            with VerticalScroll(classes="about-scroll"):
                yield Markdown(self._make_markdown(*self._golden), id="about-md")
            with Horizontal(classes="about-buttons"):
                if self._show_test_buttons:
                    yield Button("Test: All (t)", id="about-test-all", variant="primary")
                    yield Button("Test: Choose (c)", id="about-choose")
                if self._on_toggle_active is not None:
                    yield Button(self._toggle_label(), id="about-toggle")
                yield Button("Close (Esc)", id="about-close")

    def on_mount(self) -> None:
        # covers results that arrived between compose() and mount
        if self._golden != (None, None):
            self.query_one("#about-md", Markdown).update(self._make_markdown(*self._golden))

    def show_golden(self, golden, source) -> None:
        self._golden = (golden, source)
        if self.is_mounted:
            self.query_one("#about-md", Markdown).update(self._make_markdown(golden, source))

    def refresh_state(self) -> None:
        """Re-render after the skill's active state changed."""
        if not self.is_mounted:
            return
        self.query_one("#about-md", Markdown).update(self._make_markdown(*self._golden))
        for button in self.query("#about-toggle"):
            button.label = self._toggle_label()

    def action_test_all(self) -> None:
        if self._show_test_buttons:
            self.dismiss("test_all")

    def action_choose(self) -> None:
        if self._show_test_buttons:
            self.dismiss("choose")

    def action_toggle_active(self) -> None:
        if self._on_toggle_active is not None:
            self._on_toggle_active(not bool(self._get_active()))
            self.refresh_state()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "about-toggle":
            self.action_toggle_active()
            return
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


class ChoiceAboutScreen(AboutBase):
    """Markdown plus a row of buttons; dismisses with the chosen button's
    id (None on Esc). Used where the reader decides what happens next,
    e.g. --set-channel's dry run -> apply it (#65)."""
    __test__ = False

    def __init__(self, markdown: str, choices):
        super().__init__()
        self._markdown = markdown
        self._choices = list(choices)  # [(id, label)], the first is the default action

    def compose(self) -> ComposeResult:
        with Vertical(classes="about-box"):
            with VerticalScroll(classes="about-scroll"):
                yield Markdown(self._markdown, id="about-md")
            with Horizontal(classes="about-buttons"):
                for i, (cid, label) in enumerate(self._choices):
                    yield Button(label, id=f"choice-{cid}", variant="primary" if i == 0 else "default")
                yield Button("Close (Esc)", id="about-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        self.dismiss(bid[len("choice-"):] if bid.startswith("choice-") else None)


def skill_rows(installed: dict, display_name, version_of=lambda skill_id: None, needle: str = ""):
    """[(skill_id, label, active)] sorted by display name, filtered by
    needle (matches name or skill_id). #15: one skill per line."""
    needle = (needle or "").lower()
    rows = []
    for sid in sorted(installed, key=lambda sid: display_name(sid).lower()):
        name = display_name(sid)
        if needle and needle not in name.lower() and needle not in sid.lower():
            continue
        version = version_of(sid)
        label = f"{name}  ·  {sid}" + (f"  ·  {version}" if version else "")
        # spelled out, not left to the checkbox colour alone - the
        # ticked/unticked contrast is subtle in some terminal themes
        if installed[sid] is None:
            label += "  ·  (state unknown)"
        elif installed[sid] is False:
            label += "  ·  inactive"
        rows.append((sid, label, installed[sid]))
    return rows


class SkillsScreen(AboutBase):
    """One window for all installed skills (#15 + activate/deactivate):
    the checkbox IS the skill's active state - Space toggles it (applied
    right away, then confirmed against OVOS by the app), Enter opens the
    highlighted skill's About. Dismisses with the skill_id to open, or None."""
    __test__ = False

    BINDINGS = [
        Binding("escape", "close", "Close"),
        # priority: SelectionList would otherwise treat Enter as "toggle"
        Binding("enter", "about", "About", priority=True),
    ]

    def __init__(self, installed: dict, display_name, version_of, on_toggle):
        super().__init__()
        self._installed = installed          # the app's dict - read fresh on every rebuild
        self._display_name = display_name
        self._version_of = version_of
        self._on_toggle = on_toggle          # callable(skill_id, active: bool)
        self._filter = ""
        self._versions = {}

    def _version(self, sid):
        if sid not in self._versions:
            self._versions[sid] = self._version_of(sid)
        return self._versions[sid]

    def compose(self) -> ComposeResult:
        with Vertical(classes="about-box"):
            yield Label("", id="skills-title")
            yield Input(placeholder="Filter skills…", id="skills-filter")
            yield SelectionList(id="skills-list", classes="about-scroll")
            yield Label("Space: activate / deactivate   ·   Enter: About   ·   Esc: close", id="skills-help")
            with Horizontal(classes="about-buttons"):
                yield Button("About (Enter)", id="skills-about")
                yield Button("Close (Esc)", id="about-close")

    def on_mount(self) -> None:
        # is_mounted is still False while on_mount runs - build directly
        self._rebuild()
        self.query_one("#skills-list", SelectionList).focus()

    def refresh_states(self) -> None:
        """Called by the app after a state change / confirmation."""
        if self.is_mounted:
            self._rebuild()

    def _rebuild(self) -> None:
        sl = self.query_one("#skills-list", SelectionList)
        highlighted = sl.highlighted
        rows = skill_rows(self._installed, self._display_name, self._version, self._filter)
        sl.clear_options()
        sl.add_options([Selection(label, sid, bool(active)) for sid, label, active in rows])
        if rows:
            sl.highlighted = min(highlighted or 0, len(rows) - 1)
        n_active = sum(1 for v in self._installed.values() if v)
        self.query_one("#skills-title", Label).update(
            f"Installed skills: {len(self._installed)} - {n_active} active")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "skills-filter":
            self._filter = event.value.strip()
            self._rebuild()

    def on_selection_list_selection_toggled(self, event: SelectionList.SelectionToggled) -> None:
        sid = event.selection.value
        self._on_toggle(sid, sid in event.selection_list.selected)
        self._rebuild()

    def _highlighted_skill(self):
        sl = self.query_one("#skills-list", SelectionList)
        if sl.highlighted is None:
            return None
        return sl.get_option_at_index(sl.highlighted).value

    def action_about(self) -> None:
        if isinstance(self.focused, Input):
            self.query_one("#skills-list", SelectionList).focus()
            return
        sid = self._highlighted_skill()
        if sid:
            self.dismiss(sid)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "skills-about":
            self.action_about()
        else:
            self.dismiss(None)
