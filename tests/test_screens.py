"""Tests for the two modal screens: ServicesScreen (restart) and
SkillsScreen (list installed skills)."""
from unittest.mock import MagicMock, patch

import pytest
from textual.widgets import Label, ListView

from ovos_tui_client.app import OVOSTUIApp, ServicesScreen, SkillsScreen


def _app_with_fake_bus(tmp_path):
    (tmp_path / "skills.log").write_text("")
    app = OVOSTUIApp(log_dir_override=str(tmp_path))
    app.bus = MagicMock()
    return app


@pytest.mark.asyncio
async def test_pressing_r_opens_the_services_screen(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    with patch("ovos_tui_client.app.discover_services", return_value=["ovos-core.service"]):
        async with app.run_test() as pilot:
            await pilot.press("f2")
            await pilot.pause()
            assert isinstance(app.screen, ServicesScreen)


@pytest.mark.asyncio
async def test_services_screen_lists_discovered_services(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    with patch("ovos_tui_client.app.discover_services",
               return_value=["ovos-core.service", "ovos-audio.service"]):
        async with app.run_test() as pilot:
            await pilot.press("f2")
            await pilot.pause()
            list_view = app.screen.query_one("#services-list", ListView)
            assert len(list_view.children) == 2


@pytest.mark.asyncio
async def test_services_screen_shows_no_units_message_when_empty(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    with patch("ovos_tui_client.app.discover_services", return_value=[]):
        async with app.run_test() as pilot:
            await pilot.press("f2")
            await pilot.pause()
            with pytest.raises(Exception):
                app.screen.query_one("#services-list", ListView)


@pytest.mark.asyncio
async def test_escape_closes_the_services_screen(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    with patch("ovos_tui_client.app.discover_services", return_value=["ovos-core.service"]):
        async with app.run_test() as pilot:
            await pilot.press("f2")
            await pilot.pause()
            assert isinstance(app.screen, ServicesScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, ServicesScreen)


@pytest.mark.asyncio
async def test_selecting_a_service_calls_restart(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    with patch("ovos_tui_client.app.discover_services", return_value=["ovos-core.service"]), \
         patch("ovos_tui_client.app.restart_service", return_value=(True, "ovos-core.service: restarted")) as mock_restart:
        async with app.run_test() as pilot:
            await pilot.press("f2")
            await pilot.pause()
            list_view = app.screen.query_one("#services-list", ListView)
            list_view.focus()
            await pilot.press("enter")
            await pilot.pause()

            mock_restart.assert_called_once_with("ovos-core.service")
            result_label = app.screen.query_one("#services-result", Label)
            assert "restarted" in str(result_label.content)


@pytest.mark.asyncio
async def test_pressing_s_opens_the_skills_screen_and_requests_list(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("f3")
        await pilot.pause()

        assert isinstance(app.screen, SkillsScreen)
        app.bus.list_skills.assert_called_once()


@pytest.mark.asyncio
async def test_skills_screen_shows_skills_once_callback_fires(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("f3")
        await pilot.pause()

        callback = app.bus.list_skills.call_args[0][0]
        # bypass call_from_thread (would raise on the same thread, as
        # established earlier) by calling the screen's method directly -
        # this test is about show_skills() rendering, not the thread
        # marshalling call_from_thread already has its own test for
        screen = app.screen
        screen.show_skills(["ovos-skill-grimm-tales.andlo", "ovos-skill-andersen-tales.andlo"])
        await pilot.pause()

        from textual.widgets import RichLog
        view = app.screen.query_one("#skills-list-view", RichLog)
        rendered = "\n".join(str(line) for line in view.lines)
        assert "ovos-skill-grimm-tales.andlo" in rendered
        assert "ovos-skill-andersen-tales.andlo" in rendered


@pytest.mark.asyncio
async def test_skills_screen_shows_timeout_message_on_none(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("f3")
        await pilot.pause()

        screen = app.screen
        screen.show_skills(None)
        await pilot.pause()

        from textual.widgets import RichLog
        view = app.screen.query_one("#skills-list-view", RichLog)
        rendered = "\n".join(str(line) for line in view.lines)
        assert "timed out" in rendered.lower() or "no response" in rendered.lower()


@pytest.mark.asyncio
async def test_escape_closes_the_skills_screen(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("f3")
        await pilot.pause()
        assert isinstance(app.screen, SkillsScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, SkillsScreen)


# --- F4: skill filter modal (Sources/Levels are inline in the main
# view now, not a modal - see test_app.py) ---

@pytest.mark.asyncio
async def test_pressing_f4_opens_the_skill_filter_screen(tmp_path):
    from ovos_tui_client.app import SkillFilterScreen
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
    from ovos_tui_client.app import SkillFilterScreen
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("f4")
        await pilot.pause()
        assert isinstance(app.screen, SkillFilterScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, SkillFilterScreen)
