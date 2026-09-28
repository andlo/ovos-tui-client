"""Tests for #29 (About windows), #15 (installed skills one per line),
#13 (Clear panes) and saving a picked selection as a script."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from textual.widgets import Input, Markdown, RichLog, SelectionList

from ovos_tui_client.about import (
    SkillAboutScreen, SkillsScreen, TextAboutScreen,
    skill_about_markdown, skill_rows, tui_about_markdown,
)
from ovos_tui_client.app import AboutCommandProvider, OVOSTUIApp, SkillTestCommandProvider
from ovos_tui_client.scripts import GoldenResult, ScriptStep, parse_script
from ovos_tui_client.skill_examples import find_skill_json

W = "ovos-skill-weather.openvoiceos"
SKILL_JSON = {"name": "Weather", "description": "Get weather conditions | forecasts.",
              "examples": ["is it hot", "what's the forecast"], "tags": ["weather", "rain"],
              "source": "https://github.com/OpenVoiceOS/ovos-skill-weather"}
STEPS = [ScriptStep("is it hot", "en-US", W, "is_hot_or_cold.intent"),
         ScriptStep("is it cold", "en-US", W, "is_hot_or_cold.intent"),
         ScriptStep("what's the weather", "en-US", W, "weather.intent")]


# --- markdown builders ---

def test_skill_about_markdown_has_description_examples_tags_and_golden_table():
    md = skill_about_markdown(W, "Weather", SKILL_JSON, True, ("ovos-skill-weather", "1.2.3"),
                              None, STEPS, "https://raw.example/x.jsonl", "en-us")
    assert "# Weather" in md and "**Active**" in md
    assert "`ovos-skill-weather` 1.2.3" in md
    assert "https://github.com/OpenVoiceOS/ovos-skill-weather" in md
    assert "Get weather conditions \\| forecasts." in md       # pipe escaped for the table-safe markdown
    assert "- is it hot" in md and "**Tags:** weather, rain" in md
    assert "| is_hot_or_cold.intent | 2 |" in md and "| weather.intent | 1 |" in md
    assert "3 utterance(s), from https://raw.example/x.jsonl" in md


def test_skill_about_markdown_loading_and_none_states():
    assert "Looking up golden utterances" in skill_about_markdown(W, "Weather", {}, None, golden=None)
    md = skill_about_markdown(W, "Weather", {}, False, golden=[])
    assert "**Inactive**" in md and "None found" in md and "No description" in md


def test_tui_about_markdown():
    md = tui_about_markdown("0.2.0a2", "127.0.0.1", 8181, "da-dk", "/logs", ["skills", "audio"],
                            "/scripts", "/cache", [], 55)
    assert "Version 0.2.0a2" in md and "127.0.0.1:8181" in md and "da-dk" in md
    assert "/logs (skills, audio)" in md and "/scripts" in md and "55" in md


def test_skill_rows_one_per_line_sorted_filtered_with_state():
    installed = {"ovos-skill-weather.openvoiceos": True, "ovos-skill-alerts.openvoiceos": False,
                 "skill-y.me": None}
    name = lambda sid: sid.split(".")[0].replace("ovos-skill-", "").capitalize()
    rows = skill_rows(installed, name, lambda sid: "1.0" if "weather" in sid else None)
    assert [sid for sid, _, _ in rows] == ["ovos-skill-alerts.openvoiceos", "skill-y.me", "ovos-skill-weather.openvoiceos"]
    assert rows[2][1] == "Weather  ·  ovos-skill-weather.openvoiceos  ·  1.0" and rows[2][2] is True
    assert rows[1][1].endswith("(state unknown)")
    assert rows[0][1].endswith("  ·  inactive")
    assert [sid for sid, _, _ in skill_rows(installed, name, needle="alert")] == ["ovos-skill-alerts.openvoiceos"]


# --- skill.json lookup: locale folder case ---

def test_find_skill_json_matches_locale_folder_case_insensitively(tmp_path):
    pkg = tmp_path / "ovos_skill_weather"
    (pkg / "locale" / "en-US").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "locale" / "en-US" / "skill.json").write_text(json.dumps(SKILL_JSON))
    spec = MagicMock()
    spec.origin = str(pkg / "__init__.py")
    with patch("importlib.util.find_spec", return_value=spec):
        assert find_skill_json(W, "en-us")["name"] == "Weather"


# --- app: palette, windows, clear, save ---

def _app(tmp_path):
    (tmp_path / "skills.log").write_text("")
    app = OVOSTUIApp(log_dir_override=str(tmp_path), scripts_dir=tmp_path / "scripts")
    app.bus = MagicMock()
    app.bus.lang = "en-us"
    app.installed_skills = {W: True, "skill-x.me": False}
    return app


async def _hits(provider, query):
    return [str(h.text) async for h in _raw(provider, query)]


async def _raw(provider, query):
    async for h in provider.search(query):
        yield h


@pytest.mark.asyncio
async def test_about_palette_entries(tmp_path):
    app = _app(tmp_path)
    async with app.run_test():
        texts = await _hits(AboutCommandProvider(app.screen), "about")
        assert "About: ovos-tui-client" in texts
        assert "About: Installed skills (2)" in texts
        assert "About: Weather" in texts


@pytest.mark.asyncio
async def test_skill_about_window_fills_in_golden_and_test_all_starts_tests(tmp_path):
    app = _app(tmp_path)
    with patch("ovos_tui_client.app.find_skill_json", return_value=SKILL_JSON), \
         patch("ovos_tui_client.app.find_repo_url", return_value="https://github.com/o/r"), \
         patch("ovos_tui_client.app.load_golden", return_value=GoldenResult(list(STEPS), "src")):
        async with app.run_test() as pilot:
            app.start_skill_tests = MagicMock()
            app.show_skill_about(W)
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, SkillAboutScreen)
            md = app.screen.query_one("#about-md", Markdown)
            assert "| weather.intent | 1 |" in md.source and "https://github.com/o/r" in md.source
            await pilot.click("#about-test-all")
            await pilot.pause()
            app.start_skill_tests.assert_called_once_with([W], "Test: Weather - All")


@pytest.mark.asyncio
async def test_skills_window_space_toggles_active_and_enter_opens_about(tmp_path):
    app = _app(tmp_path)
    with patch("ovos_tui_client.app.load_golden", return_value=GoldenResult([], None)), \
         patch("ovos_tui_client.app.find_repo_url", return_value=None), \
         patch.object(OVOSTUIApp, "SKILL_STATE_CONFIRM_DELAY", 3600):
        async with app.run_test() as pilot:
            app.show_installed_skills()
            await pilot.pause()
            assert isinstance(app.screen, SkillsScreen)
            sl = app.screen.query_one("#skills-list", SelectionList)
            assert set(sl.selected) == {W}                  # Weather active, skill-x inactive
            await pilot.press("space")                      # first row (skill-x) -> activate
            await pilot.pause()
            app.bus.activate_skill.assert_called_once_with("skill-x.me")
            assert app.installed_skills["skill-x.me"] is True
            assert set(sl.selected) == {W, "skill-x.me"}
            await pilot.press("down", "enter")              # Weather -> About
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, SkillAboutScreen)
            await pilot.press("escape")                     # back to the Skills window
            await pilot.pause()
            assert isinstance(app.screen, SkillsScreen)


@pytest.mark.asyncio
async def test_about_window_shortcuts_toggle_active_and_start_tests(tmp_path):
    app = _app(tmp_path)
    with patch("ovos_tui_client.app.load_golden", return_value=GoldenResult([], None)), \
         patch("ovos_tui_client.app.find_repo_url", return_value=None), \
         patch.object(OVOSTUIApp, "SKILL_STATE_CONFIRM_DELAY", 3600):
        async with app.run_test() as pilot:
            app.start_skill_tests = MagicMock()
            app.show_skill_about(W)
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert str(app.screen.query_one("#about-toggle").label) == "Deactivate (a)"
            await pilot.press("a")
            await pilot.pause()
            app.bus.deactivate_skill.assert_called_once_with(W)
            assert str(app.screen.query_one("#about-toggle").label) == "Activate (a)"
            assert "**Inactive**" in app.screen.query_one("#about-md", Markdown).source
            await pilot.press("t")
            await pilot.pause()
            app.start_skill_tests.assert_called_once_with([W], "Test: Weather - All")


@pytest.mark.asyncio
async def test_state_change_is_confirmed_against_ovos(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        app.bus.list_skills.side_effect = lambda cb, **kw: cb({W: True, "skill-x.me": False})
        app.set_skill_active(W, False)              # OVOS will say: still active
        app._confirm_skill_state(W, False)
        await pilot.pause()
        text = " ".join(str(l.text) for l in app.query_one("#conversation", RichLog).lines)
        assert "Weather: deactivate requested" in text
        assert "OVOS reports it is still active" in text
        assert app.installed_skills[W] is True      # corrected from OVOS's own list

        app.bus.list_skills.side_effect = lambda cb, **kw: cb({W: False, "skill-x.me": False})
        app._confirm_skill_state(W, False)
        await pilot.pause()
        text = " ".join(str(l.text) for l in app.query_one("#conversation", RichLog).lines)
        assert "Weather: now inactive (confirmed by OVOS)" in text


@pytest.mark.asyncio
async def test_tui_about_window(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        app.show_tui_about()
        await pilot.pause()
        assert isinstance(app.screen, TextAboutScreen)
        assert "ovos-tui-client" in app.screen.query_one("#about-md", Markdown).source
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, TextAboutScreen)


@pytest.mark.asyncio
async def test_clear_panes_keeps_input_history(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        app.utterance_history = ["hello"]
        app.log_buffer.append(("skills", "a line"))
        app._write_conversation("You: hi")
        app._write_activity("→ heard")
        await pilot.pause()
        app.clear_panes("logs", "conversation", "activity")
        await pilot.pause()
        assert len(app.log_buffer) == 0
        assert len(app.query_one("#conversation", RichLog).lines) == 0
        assert len(app.query_one("#activity", RichLog).lines) == 0
        assert app.utterance_history == ["hello"]


@pytest.mark.asyncio
async def test_save_last_selection_writes_a_runnable_script(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        app.last_selection[W] = STEPS[:2]
        texts = await _hits(SkillTestCommandProvider(app.screen), "weather save")
        assert texts == ["Test: Weather - Save last selection as script"]
        app.save_last_selection(W)
        app.save_last_selection(W)      # second save doesn't overwrite the first
        files = sorted(p.name for p in (tmp_path / "scripts").iterdir())
        assert files == ["weather-selection-2.jsonl", "weather-selection.jsonl"]
        steps = parse_script((tmp_path / "scripts" / "weather-selection.jsonl").read_text(), "en-us")
        assert [(s.utterance, s.intent_label) for s in steps] == [
            ("is it hot", "is_hot_or_cold.intent"), ("is it cold", "is_hot_or_cold.intent")]
