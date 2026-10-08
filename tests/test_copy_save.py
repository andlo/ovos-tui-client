"""Copy / save a pane: the TUI takes the mouse, so the terminal can't
select its text, and what scrolled past is out of reach."""
import re
from unittest.mock import MagicMock

import pytest
from textual.widgets import RichLog

from ovos_tui_client.app import OVOSTUIApp


def _app(tmp_path):
    (tmp_path / "skills.log").write_text("")
    app = OVOSTUIApp(log_dir_override=str(tmp_path))
    app.bus = MagicMock()
    app.bus.lang = "en-US"
    app.results_dir = tmp_path / "results"
    return app


@pytest.mark.asyncio
async def test_copy_a_pane_to_the_clipboard(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        act = app.query_one("#activity", RichLog)
        act.clear()
        act.write("→ heard: what time is it")
        act.write("▶ ovos-skill-date-time.openvoiceos:what_time_is_it")
        await pilot.pause()
        app.copy_to_clipboard = MagicMock()
        app.copy_pane("activity")
        copied = app.copy_to_clipboard.call_args[0][0].splitlines()
    # each entry with the time it was written, as a column
    assert len(copied) == 2
    assert re.fullmatch(r"\d\d:\d\d:\d\d\.\d{3} → heard: what time is it", copied[0])
    assert re.fullmatch(r"\d\d:\d\d:\d\d\.\d{3} ▶ ovos-skill-date-time.openvoiceos:what_time_is_it", copied[1])


@pytest.mark.asyncio
async def test_a_long_entry_is_one_line_and_later_lines_stay_under_the_text(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(60, 30)) as pilot:
        await app.workers.wait_for_complete()
        conv = app.query_one("#conversation", RichLog)
        conv.clear()
        conv.write("[purple]OVOS:[/purple] " + "word " * 40)   # wraps in a 60-wide pane
        conv.write("Summary\n  3 passed")
        await pilot.pause()
        text = app.pane_text("conversation").splitlines()
    assert len(text) == 3
    assert text[0].endswith("OVOS: " + ("word " * 40).strip())
    assert re.fullmatch(r"\d\d:\d\d:\d\d\.\d{3} Summary", text[1])
    assert text[2] == " " * 13 + "  3 passed"


@pytest.mark.asyncio
async def test_save_a_pane_to_a_file(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        from ovos_tui_client.app import format_log_line
        logs = app.query_one("#logs-view", RichLog)
        logs.clear()
        logs.write(format_log_line("skills", "2026-10-08 14:31:05.401 - skills - "
                                             "ovos_core.intent_services.service:handle:12 - INFO - hello"))
        await pilot.pause()
        app.save_pane("logs")
        await pilot.pause()
    saved = list((tmp_path / "results").glob("*_logs.txt"))
    assert len(saved) == 1
    assert saved[0].read_text() == ("[skills   ] 14:31:05.401 ovos_core.intent_services.service:handle:12 "
                                    "- INFO - hello\n")


@pytest.mark.asyncio
async def test_an_empty_pane_says_so(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        app.query_one("#activity", RichLog).clear()
        app.copy_to_clipboard = MagicMock()
        said = []
        app._write_status = lambda text, ok=True: said.append(text)
        app.copy_pane("activity")
        await pilot.pause()
    app.copy_to_clipboard.assert_not_called()
    assert said == ["The Activity pane is empty: nothing to copy."]


@pytest.mark.asyncio
async def test_the_palette_offers_copy_and_save_for_each_pane(tmp_path):
    app = _app(tmp_path)
    async with app.run_test():
        await app.workers.wait_for_complete()
        titles = [c.title for c in app.get_system_commands(app.screen)]
    for name in ("Logs", "Conversation", "Activity"):
        assert f"Copy: {name}" in titles and f"Save: {name} to file" in titles
