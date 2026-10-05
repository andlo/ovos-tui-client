"""'Test: Compare results' (#49): pick two saved results, see how they
differ step by step, set each difference's class, save the comparison.

Two small windows, like the test picker (#34) and for the same reason:
the palette picks one thing, this needs a choice from a list and a class
per difference.
"""
import json
from pathlib import Path
from typing import Dict, List, Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Markdown, OptionList
from textual.widgets.option_list import Option

from ovos_tui_client import compare as cmp

CLASS_STYLE = {cmp.FIX: "green", cmp.REGRESSION: "red", cmp.UNCLEAR: "yellow"}

_CSS = """
#cmp-box { width: 92%; height: 88%; border: heavy $accent; background: $surface; padding: 0 1; }
#cmp-title { text-style: bold; margin-bottom: 1; }
#cmp-list { height: 1fr; }
#cmp-detail-scroll { height: 1fr; border-top: solid $accent; }
#cmp-buttons { height: auto; margin-top: 1; }
#cmp-buttons Button { margin-right: 1; }
"""


def saved_results(directory) -> List[Path]:
    """The saved .report.json files, newest first."""
    try:
        return sorted(Path(directory).expanduser().glob("*.report.json"), reverse=True)
    except OSError:
        return []


def result_row(path: Path) -> str:
    """'2026-10-05 19:48 · Script: cmp-test · testing · ovos-core 2.1.1 · 4/8 passed'."""
    try:
        r = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return f"{path.name} (can't be read)"
    m, s = r.get("manifest") or {}, r.get("summary") or {}
    when = str(m.get("created_at") or "")[:16].replace("T", " ")
    core = (m.get("stack") or {}).get("ovos-core")
    parts = [when, r.get("title") or path.stem, m.get("channel") or "channel unknown",
             f"ovos-core {core}" if core else "",
             f"{s.get('passed', '?')}/{s.get('checked', s.get('steps', '?'))} passed"]
    return " · ".join(p for p in parts if p)


class ResultPickerScreen(ModalScreen):
    """Pick one saved result. Dismisses with its path, or None."""
    BINDINGS = [Binding("escape", "cancel", "Cancel")]
    DEFAULT_CSS = "ResultPickerScreen { align: center middle; }" + _CSS

    def __init__(self, title: str, files: List[Path], directory: Path):
        super().__init__()
        self._title = title
        self._files = list(files)
        self._directory = directory

    def compose(self) -> ComposeResult:
        with Vertical(id="cmp-box"):
            yield Label(self._title, id="cmp-title")
            yield OptionList(*[Option(result_row(p), id=str(i)) for i, p in enumerate(self._files)],
                             id="cmp-list")
            yield Label(f"Saved results in {self._directory}. A result from another machine: copy its "
                        ".report.json there first.")
            with Horizontal(id="cmp-buttons"):
                yield Button("Cancel (Esc)", id="cmp-cancel")

    def on_mount(self) -> None:
        self.query_one("#cmp-list", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self._files[int(event.option.id)])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


def summary_markdown(c: Dict) -> str:
    s = c["summary"]
    lines = [f"**A:** {c['a']['label']} ({c['a'].get('created_at') or '?'})  ",
             f"**B:** {c['b']['label']} ({c['b'].get('created_at') or '?'})  ",
             f"**{s['compared']} steps:** {s['identical']} identical · {s['changed']} changed "
             f"(fix {s['fix']}, regression {s['regression']}, unclear {s['unclear']})"
             + (f" · only on A {s['only_a']}" if s["only_a"] else "")
             + (f" · only on B {s['only_b']}" if s["only_b"] else "")]
    if c.get("installs"):
        lines += ["", "| | A | B |", "|---|---|---|"]
        lines += [f"| {d['what']} | {cmp._fmt(d['a'])} | {cmp._fmt(d['b'])} |" for d in c["installs"][:12]]
        if len(c["installs"]) > 12:
            lines += [f"| … {len(c['installs']) - 12} more in the saved comparison | | |"]
    return "\n".join(lines)


def detail_markdown(d: Dict) -> str:
    note = "" if d["class"] == d["suggested"] else f" (suggested: {d['suggested']})"
    lines = [f"**\"{d['utterance']}\"** ({d.get('lang') or '?'}), expected {d.get('expected') or '-'}",
             "", f"Class: **{d['class']}**{note}. Change it with **f** fix, **r** regression, **u** unclear.", ""]
    lines += [f"- {ch['what']}: {cmp._fmt(ch['a'])} → {cmp._fmt(ch['b'])}" for ch in d["changes"]]
    for label, sd in (("A", d["a"]), ("B", d["b"])):
        for why in (sd.get("diagnosis") or {}).get("lines") or []:
            lines.append(f"- ↳ {label}: {why}")
    return "\n".join(lines)


class ComparisonScreen(ModalScreen):
    """The comparison. Dismisses with 'save' (the comparison as it is now,
    with the tester's classes) or None."""
    BINDINGS = [
        Binding("escape", "cancel", "Close"),
        Binding("f", "set('fix')", "Fix"),
        Binding("r", "set('regression')", "Regression"),
        Binding("u", "set('unclear')", "Unclear"),
        Binding("ctrl+s", "save", "Save"),
    ]
    DEFAULT_CSS = "ComparisonScreen { align: center middle; }" + _CSS + """
    #cmp-summary-scroll { height: auto; max-height: 45%; }
    """

    def __init__(self, comparison: Dict):
        super().__init__()
        self.comparison = comparison

    def compose(self) -> ComposeResult:
        with Vertical(id="cmp-box"):
            yield Label(f"Comparison: {self.comparison['a'].get('title') or '?'}", id="cmp-title")
            with VerticalScroll(id="cmp-summary-scroll"):
                yield Markdown(summary_markdown(self.comparison), id="cmp-summary")
            yield OptionList(*self._options(), id="cmp-list")
            with VerticalScroll(id="cmp-detail-scroll"):
                yield Markdown("", id="cmp-detail")
            with Horizontal(id="cmp-buttons"):
                yield Button("Save (Ctrl+S)", id="cmp-save", variant="primary")
                yield Button("Close (Esc)", id="cmp-cancel")

    def _options(self) -> List[Option]:
        diffs = self.comparison.get("differences") or []
        if not diffs:
            return [Option("No differences: every step behaves the same on both installs.", disabled=True)]
        out = []
        for i, d in enumerate(diffs):
            row = Text()
            row.append(f"{d['class']:<10} ", style=CLASS_STYLE.get(d["class"], ""))
            row.append(f"\"{d['utterance']}\"  ")
            row.append(", ".join(ch["what"] for ch in d["changes"]), style="dim")
            out.append(Option(row, id=str(i)))
        return out

    def on_mount(self) -> None:
        ol = self.query_one("#cmp-list", OptionList)
        ol.focus()
        if self.comparison.get("differences"):
            ol.highlighted = 0
            self._show(0)

    def _show(self, index: Optional[int]) -> None:
        diffs = self.comparison.get("differences") or []
        text = detail_markdown(diffs[index]) if index is not None and index < len(diffs) else ""
        self.query_one("#cmp-detail", Markdown).update(text)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option and event.option.id is not None:
            self._show(int(event.option.id))

    def action_set(self, cls: str) -> None:
        ol = self.query_one("#cmp-list", OptionList)
        index = ol.highlighted
        if index is None or not self.comparison.get("differences"):
            return
        cmp.set_class(self.comparison, index, cls)
        ol.clear_options()
        ol.add_options(self._options())
        ol.highlighted = index
        self.query_one("#cmp-summary", Markdown).update(summary_markdown(self.comparison))
        self._show(index)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cmp-save":
            self.action_save()
        else:
            self.action_cancel()

    def action_save(self) -> None:
        self.dismiss("save")

    def action_cancel(self) -> None:
        self.dismiss(None)
