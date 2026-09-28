"""Tests for the golden-utterance picker (#34): group toggling,
filtering, run/cancel, and the palette flow around it."""
from unittest.mock import MagicMock, patch

import pytest
from textual.app import App
from textual.widgets import Input, SelectionList

from ovos_tui_client.scripts import GoldenResult, ScriptStep
from ovos_tui_client.test_picker import TestPickerScreen

W = "ovos-skill-weather.openvoiceos"
STEPS = [
    ScriptStep("hvad er vejret", "da-DK", W, "weather.intent"),
    ScriptStep("er det godt udenfor", "da-DK", W, "weather.intent"),
    ScriptStep("bliver det koldt i morgen", "da-DK", W, "forecast.intent"),
]


class _Host(App):
    def __init__(self, preselected=None):
        super().__init__()
        self.result = "unset"
        self.preselected = preselected

    def on_mount(self):
        self.push_screen(TestPickerScreen("pick", STEPS, self.preselected),
                         lambda r: setattr(self, "result", r))


def _values(app):
    return list(app.screen.query_one("#picker-list", SelectionList).selected)


@pytest.mark.asyncio
async def test_group_header_ticks_the_whole_intent_group_and_run_returns_indices():
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("space")          # first option = '▸ weather.intent (2)'
        await pilot.pause()
        assert set(_values(app)) == {"g:weather.intent", "s:0", "s:1"}
        await pilot.press("ctrl+r")
        await pilot.pause()
    assert app.result == [0, 1]


@pytest.mark.asyncio
async def test_single_row_toggle_and_header_follows():
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down", "space")  # 'hvad er vejret'
        await pilot.pause()
        assert set(_values(app)) == {"s:0"}
        await pilot.press("down", "space")  # 'er det godt udenfor' -> whole group now on
        await pilot.pause()
        assert set(_values(app)) == {"g:weather.intent", "s:0", "s:1"}
        await pilot.press("ctrl+r")
        await pilot.pause()
    assert app.result == [0, 1]


@pytest.mark.asyncio
async def test_filter_narrows_and_all_ticks_only_visible_rows():
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.query_one("#picker-filter", Input).value = "koldt"
        await pilot.pause()
        await pilot.press("ctrl+a")
        await pilot.pause()
        await pilot.press("ctrl+r")
        await pilot.pause()
    assert app.result == [2]


@pytest.mark.asyncio
async def test_cancel_and_empty_run():
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+r")          # nothing ticked -> stays open
        await pilot.pause()
        assert isinstance(app.screen, TestPickerScreen)
        await pilot.press("escape")
        await pilot.pause()
    assert app.result is None


@pytest.mark.asyncio
async def test_preselected_rows_are_ticked():
    app = _Host(preselected=[2])
    async with app.run_test() as pilot:
        await pilot.pause()
        assert set(_values(app)) == {"g:forecast.intent", "s:2"}


# --- palette flow in the real app ---

from ovos_tui_client.app import OVOSTUIApp, SkillTestCommandProvider


@pytest.mark.asyncio
async def test_choose_then_last_selection_replays_the_same_subset(tmp_path):
    (tmp_path / "skills.log").write_text("")
    app = OVOSTUIApp(log_dir_override=str(tmp_path), scripts_dir=tmp_path / "scripts")
    app.bus = MagicMock()
    app.bus.lang = "da-dk"
    app.installed_skills = {W: True}
    sent = []

    def send(text, lang=None, session_id=None, script=None):
        sent.append(text)
        app.script_runner.feed(f"{W}:weather.intent")
        app.script_runner.feed("ovos.utterance.handled")
    app.bus.send_utterance.side_effect = send

    with patch("ovos_tui_client.app.load_golden", return_value=GoldenResult(list(STEPS), "test")), \
         patch("ovos_tui_client.scripts.SETTLE", 0):
        async with app.run_test() as pilot:
            hits = [h async for h in SkillTestCommandProvider(app.screen).search("weather choose")]
            assert [str(h.text) for h in hits][0] == "Test: weather — choose…"
            hits[0].command()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, TestPickerScreen)
            await pilot.press("down", "space", "ctrl+r")   # just 'hvad er vejret'
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert sent == ["hvad er vejret"]

            hits = [h async for h in SkillTestCommandProvider(app.screen).search("weather last")]
            assert [str(h.text) for h in hits] == ["Test: weather — last selection (1)"]
            hits[0].command()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert sent == ["hvad er vejret", "hvad er vejret"]

            # reopening the picker pre-ticks what was chosen last time
            app.choose_skill_tests(W)
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert set(app.screen.query_one("#picker-list", SelectionList).selected) == {"s:0"}
