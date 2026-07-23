"""Tests for SkillFilterScreen (F4) - the one remaining modal screen
besides HelpScreen. ServicesScreen/ServicePickerScreen and SkillsScreen
are gone entirely: service management and installed-skill listing/
activation now live in the Command Palette with results written to
the conversation pane instead of a popup - see test_command_palette.py
for that."""
from unittest.mock import MagicMock

import pytest

from ovos_tui_client.app import OVOSTUIApp, SkillFilterScreen


def _app_with_fake_bus(tmp_path):
    (tmp_path / "skills.log").write_text("")
    app = OVOSTUIApp(log_dir_override=str(tmp_path))
    app.bus = MagicMock()
    return app


@pytest.mark.asyncio
async def test_pressing_f4_opens_the_skill_filter_screen(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("f4")
        await pilot.pause()
        assert isinstance(app.screen, SkillFilterScreen)


@pytest.mark.asyncio
async def test_skill_filter_screen_lists_discovered_skills_unchecked_by_default(tmp_path):
    from textual.widgets import Checkbox
    app = _app_with_fake_bus(tmp_path)
    app.skill_enabled = {"ovos-skill-grimm-tales.andlo": False}
    async with app.run_test() as pilot:
        await pilot.press("f4")
        await pilot.pause()
        checkbox = app.screen.query_one("#modal-skill-ovos-skill-grimm-tales-andlo", Checkbox)
        assert checkbox.value is False


@pytest.mark.asyncio
async def test_checking_a_skill_in_the_filter_screen_narrows_to_that_skill(tmp_path):
    from textual.widgets import Checkbox, RichLog
    app = _app_with_fake_bus(tmp_path)
    app.skill_enabled = {"ovos-skill-grimm-tales.andlo": False, "ovos-skill-andersen-tales.andlo": False}
    async with app.run_test() as pilot:
        app.log_buffer.append(("skills", "handling for skill_id=ovos-skill-grimm-tales.andlo now"))
        app.log_buffer.append(("skills", "handling for skill_id=ovos-skill-andersen-tales.andlo now"))
        await pilot.press("f4")
        await pilot.pause()

        checkbox = app.screen.query_one("#modal-skill-ovos-skill-grimm-tales-andlo", Checkbox)
        checkbox.value = True
        await pilot.pause()

        assert app.skill_enabled["ovos-skill-grimm-tales.andlo"] is True
        rendered = "\n".join(str(line) for line in app.query_one("#logs-view", RichLog).lines)
        assert "grimm-tales" in rendered
        assert "andersen-tales" not in rendered


@pytest.mark.asyncio
async def test_skill_filter_screen_shows_placeholder_when_no_skills_seen_yet(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("f4")
        await pilot.pause()
        assert app.skill_enabled == {}
        # doesn't crash with an empty skill_enabled dict - see compose()'s
        # "(no skills seen in the log stream yet)" placeholder Label


@pytest.mark.asyncio
async def test_escape_closes_the_skill_filter_screen(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("f4")
        await pilot.pause()
        assert isinstance(app.screen, SkillFilterScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, SkillFilterScreen)
