"""Tests for the Command Palette's in-place-filtered Service:/Skill:
entries (issue #3 follow-up, second iteration) - per feedback, NO
popup windows: selecting a hit runs the action immediately and writes
the result to the conversation pane (dim/grey text) via
App._write_status(), instead of opening a screen. services.py's and
bus.py's own functions are mocked throughout; this file tests the
Provider/glue layer, not systemctl or the real bus."""
from unittest.mock import MagicMock, patch

import pytest
from textual.widgets import RichLog

from ovos_tui_client.app import OVOSTUIApp, ServiceCommandProvider, SkillCommandProvider


def _app_with_fake_bus(tmp_path):
    (tmp_path / "skills.log").write_text("")
    app = OVOSTUIApp(log_dir_override=str(tmp_path))
    app.bus = MagicMock()
    return app


async def _collect_hits(provider, query):
    return [hit async for hit in provider.search(query)]


def _conversation_text(app) -> str:
    return "\n".join(str(line) for line in app.query_one("#conversation", RichLog).lines)


# --- ServiceCommandProvider: in-place filtering, no popup ---

@pytest.mark.asyncio
async def test_service_search_yields_a_hit_per_service_per_action(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        provider = ServiceCommandProvider(app.screen)
        with patch("ovos_tui_client.app.discover_services", return_value=["ovos-core.service"]):
            hits = await _collect_hits(provider, "ovos-core")

        texts = [str(h.match_display) for h in hits]
        assert any("Restart" in t and "ovos-core.service" in t for t in texts)
        assert any("Stop" in t and "ovos-core.service" in t for t in texts)
        assert any("Start" in t and "ovos-core.service" in t for t in texts)


@pytest.mark.asyncio
async def test_service_hits_share_the_service_prefix_for_grouping(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        provider = ServiceCommandProvider(app.screen)
        with patch("ovos_tui_client.app.discover_services", return_value=["ovos-core.service"]):
            hits = await _collect_hits(provider, "service")

        assert all(str(h.match_display).startswith("Service: ") for h in hits)


@pytest.mark.asyncio
async def test_service_search_narrows_by_query(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        provider = ServiceCommandProvider(app.screen)
        with patch("ovos_tui_client.app.discover_services",
                   return_value=["ovos-core.service", "ovos-audio.service"]):
            hits = await _collect_hits(provider, "restart audio")

        texts = [str(h.match_display) for h in hits]
        assert any("ovos-audio.service" in t for t in texts)
        assert not any("ovos-core.service" in t and "Restart" in t for t in texts)


@pytest.mark.asyncio
async def test_selecting_a_service_hit_runs_the_action_no_popup(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        provider = ServiceCommandProvider(app.screen)
        with patch("ovos_tui_client.app.discover_services", return_value=["ovos-core.service"]), \
             patch("ovos_tui_client.app.restart_service", return_value=(True, "ovos-core.service: restarted")) as mock_restart:
            hits = await _collect_hits(provider, "Restart ovos-core")
            hit = next(h for h in hits if "Restart" in str(h.match_display))
            hit.command()
            await pilot.pause()

            mock_restart.assert_called_once_with("ovos-core.service")
            assert not isinstance(app.screen, type(None))
            assert app.screen is app.screen  # still the main screen - no modal pushed
            assert "restarted" in _conversation_text(app)


@pytest.mark.asyncio
async def test_failed_service_action_still_writes_to_conversation(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        provider = ServiceCommandProvider(app.screen)
        with patch("ovos_tui_client.app.discover_services", return_value=["ovos-core.service"]), \
             patch("ovos_tui_client.app.stop_service", return_value=(False, "ovos-core.service: permission denied")):
            hits = await _collect_hits(provider, "Stop ovos-core")
            hit = next(h for h in hits if "Stop" in str(h.match_display))
            hit.command()
            await pilot.pause()

            assert "permission denied" in _conversation_text(app)


# --- SkillCommandProvider: in-place filtering over installed_skills cache ---

@pytest.mark.asyncio
async def test_skill_search_yields_activate_and_deactivate_per_cached_skill(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    app.installed_skills = ["ovos-skill-grimm-tales.andlo"]
    async with app.run_test() as pilot:
        provider = SkillCommandProvider(app.screen)
        hits = await _collect_hits(provider, "grimm")

        texts = [str(h.match_display) for h in hits]
        assert any("Activate" in t for t in texts)
        assert any("Deactivate" in t for t in texts)


@pytest.mark.asyncio
async def test_skill_search_has_no_hits_when_cache_is_empty(tmp_path):
    """Not an error - just nothing to offer yet if the skill list
    hasn't been fetched (bus.list_skills) successfully."""
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        provider = SkillCommandProvider(app.screen)
        hits = await _collect_hits(provider, "anything")
        assert hits == []


@pytest.mark.asyncio
async def test_selecting_activate_calls_bus_activate_skill_no_popup(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    app.installed_skills = ["ovos-skill-grimm-tales.andlo"]
    async with app.run_test() as pilot:
        provider = SkillCommandProvider(app.screen)
        hits = await _collect_hits(provider, "Activate grimm")
        hit = next(h for h in hits if "Activate" in str(h.match_display))
        hit.command()
        await pilot.pause()

        app.bus.activate_skill.assert_called_once_with("ovos-skill-grimm-tales.andlo")
        assert "ovos-skill-grimm-tales.andlo" in _conversation_text(app)


@pytest.mark.asyncio
async def test_selecting_deactivate_calls_bus_deactivate_skill_no_popup(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    app.installed_skills = ["ovos-skill-grimm-tales.andlo"]
    async with app.run_test() as pilot:
        provider = SkillCommandProvider(app.screen)
        hits = await _collect_hits(provider, "Deactivate grimm")
        hit = next(h for h in hits if "Deactivate" in str(h.match_display))
        hit.command()
        await pilot.pause()

        app.bus.deactivate_skill.assert_called_once_with("ovos-skill-grimm-tales.andlo")


# --- "Skill: List installed" static command + conversation output ---

@pytest.mark.asyncio
async def test_system_commands_include_skill_list_installed(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        titles = [cmd.title for cmd in app.get_system_commands(app.screen)]
        assert "Skill: List installed" in titles


@pytest.mark.asyncio
async def test_refresh_installed_skills_populates_cache_and_writes_conversation(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        # bus.list_skills()'s callback normally fires from the bus
        # client's own background thread, hence _on_result's use of
        # call_from_thread() - mocked here to just call synchronously,
        # since real thread-marshalling would raise "must run in a
        # different thread" when invoked directly from the test's own
        # (== the app's) thread. Same pattern used elsewhere for
        # testing thread-marshaled callbacks in this codebase.
        app.call_from_thread = MagicMock(side_effect=lambda fn, *a, **kw: fn(*a, **kw))
        app.bus.list_skills = MagicMock(
            side_effect=lambda cb: cb(["ovos-skill-grimm-tales.andlo", "ovos-skill-andersen-tales.andlo"])
        )
        app._refresh_installed_skills()
        await pilot.pause()

        assert app.installed_skills == ["ovos-skill-andersen-tales.andlo", "ovos-skill-grimm-tales.andlo"]
        text = _conversation_text(app)
        assert "grimm-tales" in text
        assert "andersen-tales" in text


@pytest.mark.asyncio
async def test_refresh_installed_skills_handles_timeout_gracefully(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        app.call_from_thread = MagicMock(side_effect=lambda fn, *a, **kw: fn(*a, **kw))
        app.bus.list_skills = MagicMock(side_effect=lambda cb: cb(None))
        app._refresh_installed_skills()
        await pilot.pause()

        assert app.installed_skills == []
        assert "no response" in _conversation_text(app).lower() or "timed out" in _conversation_text(app).lower()


# --- startup status + _write_status formatting ---

@pytest.mark.asyncio
async def test_startup_writes_connection_status_to_conversation(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        text = _conversation_text(app)
        assert "127.0.0.1" in text
        assert "8181" in text


def test_write_status_uses_dim_style_on_success(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    app._write_conversation = MagicMock()
    app._write_status("all good", ok=True)
    app._write_conversation.assert_called_once_with("[dim]all good[/dim]")


def test_write_status_uses_red_style_on_failure(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    app._write_conversation = MagicMock()
    app._write_status("it broke", ok=False)
    app._write_conversation.assert_called_once_with("[red]it broke[/red]")


# --- "Log: " prefix grouping (unchanged from the prior design) ---

@pytest.mark.asyncio
async def test_log_toggle_titles_share_a_common_prefix_for_palette_grouping(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        titles = [cmd.title for cmd in app.get_system_commands(app.screen)]
        log_titles = [t for t in titles if t.startswith("Log: ")]
        assert any("Toggle source" in t for t in log_titles)
        assert any("Toggle level" in t for t in log_titles)


# --- Screenshot command filtered out ---

@pytest.mark.asyncio
async def test_screenshot_command_is_filtered_out(tmp_path):
    """Textual's default 'Screenshot' system command is noise for this
    tool - explicitly removed."""
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        titles = [cmd.title for cmd in app.get_system_commands(app.screen)]
        assert "Screenshot" not in titles


@pytest.mark.asyncio
async def test_other_textual_defaults_are_not_filtered(tmp_path):
    """Confirms the filter is specific to 'Screenshot', not a blanket
    removal of Textual's own defaults (e.g. Quit/Theme should stay)."""
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        titles = [cmd.title for cmd in app.get_system_commands(app.screen)]
        assert "Quit" in titles or any("quit" in t.lower() for t in titles)


# --- "Skill: List installed" - one skill per line ---

@pytest.mark.asyncio
async def test_list_installed_skills_writes_one_per_line(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        app.call_from_thread = MagicMock(side_effect=lambda fn, *a, **kw: fn(*a, **kw))
        app.bus.list_skills = MagicMock(
            side_effect=lambda cb: cb(["ovos-skill-grimm-tales.andlo", "ovos-skill-andersen-tales.andlo"])
        )
        app._refresh_installed_skills()
        await pilot.pause()

        view = app.query_one("#conversation", RichLog)
        lines = [str(line) for line in view.lines]
        assert any("ovos-skill-grimm-tales.andlo" in line and "ovos-skill-andersen-tales.andlo" not in line
                   for line in lines)
        assert any("ovos-skill-andersen-tales.andlo" in line and "ovos-skill-grimm-tales.andlo" not in line
                   for line in lines)


# --- "Pipeline: List" ---

@pytest.mark.asyncio
async def test_system_commands_include_pipeline_list(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        titles = [cmd.title for cmd in app.get_system_commands(app.screen)]
        assert "Pipeline: List" in titles


@pytest.mark.asyncio
async def test_list_pipeline_writes_stages_to_conversation(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        fake_config = MagicMock()
        fake_config.get.return_value = {"pipeline": ["stop_high", "ocp_high", "adapt_high"]}
        with patch("ovos_config.config.Configuration", return_value=fake_config):
            app._list_pipeline()
            await pilot.pause()

        text = _conversation_text(app)
        assert "stop_high" in text
        assert "ocp_high" in text
        assert "adapt_high" in text


@pytest.mark.asyncio
async def test_list_pipeline_handles_missing_config_gracefully(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        fake_config = MagicMock()
        fake_config.get.return_value = {}
        with patch("ovos_config.config.Configuration", return_value=fake_config):
            app._list_pipeline()
            await pilot.pause()

        assert "empty" in _conversation_text(app).lower() or "not set" in _conversation_text(app).lower()


@pytest.mark.asyncio
async def test_list_pipeline_handles_read_errors_gracefully(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        with patch("ovos_config.config.Configuration", side_effect=RuntimeError("boom")):
            app._list_pipeline()  # must not raise
            await pilot.pause()

        assert "could not read" in _conversation_text(app).lower()
