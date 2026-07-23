"""Tests for the Command Palette's in-place-filtered Service:/Skill:
entries (issue #3 follow-up, second iteration) - NO popup windows:
selecting a hit runs the action immediately and writes
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


# --- ServiceCommandProvider: in-place filtering, no popup, state-aware ---

@pytest.mark.asyncio
async def test_running_service_offers_stop_and_restart_but_not_start(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        provider = ServiceCommandProvider(app.screen)
        with patch("ovos_tui_client.app.discover_services_with_state", return_value=[("ovos-core.service", True)]):
            hits = await _collect_hits(provider, "ovos-core")

        texts = [str(h.match_display) for h in hits]
        assert any("Restart" in t and "ovos-core.service" in t for t in texts)
        assert any("Stop" in t and "ovos-core.service" in t for t in texts)
        assert not any("Start" in t and "ovos-core.service" in t for t in texts)


@pytest.mark.asyncio
async def test_stopped_service_offers_only_start(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        provider = ServiceCommandProvider(app.screen)
        with patch("ovos_tui_client.app.discover_services_with_state", return_value=[("ovos-core.service", False)]):
            hits = await _collect_hits(provider, "ovos-core")

        texts = [str(h.match_display) for h in hits]
        assert any("Start" in t and "ovos-core.service" in t for t in texts)
        assert not any("Stop" in t and "ovos-core.service" in t for t in texts)
        assert not any("Restart" in t and "ovos-core.service" in t for t in texts)


@pytest.mark.asyncio
async def test_service_hits_share_the_service_prefix_for_grouping(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        provider = ServiceCommandProvider(app.screen)
        with patch("ovos_tui_client.app.discover_services_with_state", return_value=[("ovos-core.service", True)]):
            hits = await _collect_hits(provider, "service")

        assert all(str(h.match_display).startswith("Service: ") for h in hits)


@pytest.mark.asyncio
async def test_service_search_narrows_by_query(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        provider = ServiceCommandProvider(app.screen)
        with patch("ovos_tui_client.app.discover_services_with_state",
                   return_value=[("ovos-core.service", True), ("ovos-audio.service", True)]):
            hits = await _collect_hits(provider, "restart audio")

        texts = [str(h.match_display) for h in hits]
        assert any("ovos-audio.service" in t for t in texts)
        assert not any("ovos-core.service" in t and "Restart" in t for t in texts)


@pytest.mark.asyncio
async def test_selecting_a_service_hit_runs_the_action_no_popup(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        provider = ServiceCommandProvider(app.screen)
        with patch("ovos_tui_client.app.discover_services_with_state", return_value=[("ovos-core.service", True)]), \
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
        with patch("ovos_tui_client.app.discover_services_with_state", return_value=[("ovos-core.service", True)]), \
             patch("ovos_tui_client.app.stop_service", return_value=(False, "ovos-core.service: permission denied")):
            hits = await _collect_hits(provider, "Stop ovos-core")
            hit = next(h for h in hits if "Stop" in str(h.match_display))
            hit.command()
            await pilot.pause()

            assert "permission denied" in _conversation_text(app)


# --- SkillCommandProvider: in-place filtering over installed_skills cache ---

@pytest.mark.asyncio
async def test_inactive_skill_offers_only_activate(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    app.installed_skills = {"ovos-skill-grimm-tales.andlo": False}
    async with app.run_test() as pilot:
        provider = SkillCommandProvider(app.screen)
        hits = await _collect_hits(provider, "grimm")

        texts = [str(h.match_display) for h in hits]
        assert any("Activate" in t for t in texts)
        assert not any("Deactivate" in t for t in texts)


@pytest.mark.asyncio
async def test_active_skill_offers_only_deactivate(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    app.installed_skills = {"ovos-skill-grimm-tales.andlo": True}
    async with app.run_test() as pilot:
        provider = SkillCommandProvider(app.screen)
        hits = await _collect_hits(provider, "grimm")

        texts = [str(h.match_display) for h in hits]
        assert any("Deactivate" in t for t in texts)
        assert not any("Activate" in t for t in texts)


@pytest.mark.asyncio
async def test_unknown_state_skill_offers_both(tmp_path):
    """active: None (a real, confirmed OVOS response value - unknown/
    unset state) shows both actions, since we genuinely don't know
    which applies."""
    app = _app_with_fake_bus(tmp_path)
    app.installed_skills = {"ovos-skill-pyradios.openvoiceos": None}
    async with app.run_test() as pilot:
        provider = SkillCommandProvider(app.screen)
        hits = await _collect_hits(provider, "pyradios")

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
    app.installed_skills = {"ovos-skill-grimm-tales.andlo": False}
    async with app.run_test() as pilot:
        provider = SkillCommandProvider(app.screen)
        hits = await _collect_hits(provider, "Activate grimm")
        hit = next(h for h in hits if "Activate" in str(h.match_display))
        hit.command()
        await pilot.pause()

        app.bus.activate_skill.assert_called_once_with("ovos-skill-grimm-tales.andlo")
        assert "ovos-skill-grimm-tales.andlo" in _conversation_text(app)


@pytest.mark.asyncio
async def test_selecting_activate_optimistically_updates_the_local_cache(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    app.installed_skills = {"ovos-skill-grimm-tales.andlo": False}
    async with app.run_test() as pilot:
        provider = SkillCommandProvider(app.screen)
        hits = await _collect_hits(provider, "Activate grimm")
        hits[0].command()
        await pilot.pause()

        assert app.installed_skills["ovos-skill-grimm-tales.andlo"] is True


@pytest.mark.asyncio
async def test_selecting_deactivate_calls_bus_deactivate_skill_no_popup(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    app.installed_skills = {"ovos-skill-grimm-tales.andlo": True}
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
            side_effect=lambda cb: cb({"ovos-skill-grimm-tales.andlo": False, "ovos-skill-andersen-tales.andlo": True})
        )
        app._refresh_installed_skills()
        await pilot.pause()

        assert app.installed_skills == {"ovos-skill-grimm-tales.andlo": False, "ovos-skill-andersen-tales.andlo": True}
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

        assert app.installed_skills == {}
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
            side_effect=lambda cb: cb({"ovos-skill-grimm-tales.andlo": False, "ovos-skill-andersen-tales.andlo": True})
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


# --- SkillFilterCommandProvider: log-display skill filter, in the palette ---

@pytest.mark.asyncio
async def test_skill_filter_search_yields_a_hit_per_discovered_skill(tmp_path):
    from ovos_tui_client.app import SkillFilterCommandProvider
    app = _app_with_fake_bus(tmp_path)
    app.skill_enabled = {"ovos-skill-grimm-tales.andlo": False}
    async with app.run_test() as pilot:
        provider = SkillFilterCommandProvider(app.screen)
        hits = await _collect_hits(provider, "grimm")
        assert len(hits) == 1
        assert "Log: Toggle skill: ovos-skill-grimm-tales.andlo" in str(hits[0].match_display)


@pytest.mark.asyncio
async def test_skill_filter_hits_share_the_log_prefix_for_grouping(tmp_path):
    from ovos_tui_client.app import SkillFilterCommandProvider
    app = _app_with_fake_bus(tmp_path)
    app.skill_enabled = {"ovos-skill-grimm-tales.andlo": False}
    async with app.run_test() as pilot:
        provider = SkillFilterCommandProvider(app.screen)
        hits = await _collect_hits(provider, "log")
        assert all(str(h.match_display).startswith("Log: ") for h in hits)


@pytest.mark.asyncio
async def test_selecting_a_skill_filter_hit_toggles_it_and_rerenders(tmp_path):
    from ovos_tui_client.app import SkillFilterCommandProvider
    app = _app_with_fake_bus(tmp_path)
    app.skill_enabled = {"ovos-skill-grimm-tales.andlo": False}
    async with app.run_test() as pilot:
        app.log_buffer.append(("skills", "handling for skill_id=ovos-skill-grimm-tales.andlo now"))
        provider = SkillFilterCommandProvider(app.screen)
        hits = await _collect_hits(provider, "grimm")
        hits[0].command()
        await pilot.pause()

        assert app.skill_enabled["ovos-skill-grimm-tales.andlo"] is True
        assert "grimm-tales" in "\n".join(str(line) for line in app.query_one("#logs-view", RichLog).lines)


@pytest.mark.asyncio
async def test_skill_filter_search_has_no_hits_when_none_discovered_yet(tmp_path):
    from ovos_tui_client.app import SkillFilterCommandProvider
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        provider = SkillFilterCommandProvider(app.screen)
        hits = await _collect_hits(provider, "anything")
        assert hits == []


# --- Textual defaults filtered: Screenshot AND Keys ---

@pytest.mark.asyncio
async def test_keys_command_is_filtered_out(tmp_path):
    """Per feedback: having both Textual's own 'Keys' list and this
    project's own, richer F1/HelpScreen was two different places
    saying similar things - HelpScreen stays as the canonical source."""
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        titles = [cmd.title for cmd in app.get_system_commands(app.screen)]
        assert "Keys" not in titles
        assert "Screenshot" not in titles


# --- retro boot-sequence narration + version number ---

@pytest.mark.asyncio
async def test_startup_writes_version_number(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        text = _conversation_text(app)
        assert "ovos-tui-client v" in text
        assert "starting" in text.lower()


@pytest.mark.asyncio
async def test_startup_narrates_reading_logs(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        text = _conversation_text(app)
        assert "reading logs" in text.lower()


@pytest.mark.asyncio
async def test_startup_narrates_service_states(tmp_path):
    """discover_services_with_state() runs on a background worker
    thread (see on_mount's docstring on why) - checking conversation
    text immediately after run_test() yields is a genuine race, since
    the worker hasn't necessarily finished a real subprocess call yet.
    app.workers.wait_for_complete() actually waits for it, rather than
    guessing at timing with pilot.pause()."""
    app = _app_with_fake_bus(tmp_path)
    with patch("ovos_tui_client.app.discover_services_with_state", return_value=[("ovos-core.service", True)]):
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            text = _conversation_text(app)
            assert "service state" in text.lower()


@pytest.mark.asyncio
async def test_startup_narrates_finding_skills_count_only_not_full_list(tmp_path):
    """Startup should be terse (count only) - the full one-skill-per-
    line listing is reserved for the explicit 'Skill: List installed'
    palette command, not startup noise."""
    app = _app_with_fake_bus(tmp_path)
    # on_mount's skill-list callback fires synchronously here (the
    # fake bus.list_skills calls back immediately), so both mocks must
    # be in place BEFORE run_test() starts mounting - by the time the
    # `async with` block below yields control, on_mount has already
    # run to completion.
    app.bus.list_skills = MagicMock(
        side_effect=lambda cb: cb({"ovos-skill-grimm-tales.andlo": False, "ovos-skill-andersen-tales.andlo": True})
    )
    app.call_from_thread = MagicMock(side_effect=lambda fn, *a, **kw: fn(*a, **kw))
    async with app.run_test() as pilot:
        text = _conversation_text(app)

    assert "finding skills" in text.lower()
    assert "2 found" in text
    assert "ovos-skill-grimm-tales.andlo" not in text


@pytest.mark.asyncio
async def test_startup_ends_with_ok_ready(tmp_path):
    """'OK ready.' is only written once BOTH async startup steps
    (service-state worker, skill lookup) report completion - see
    on_mount()'s docstring for the real ordering bug this fixes.
    Mocking both to resolve immediately + waiting for the worker
    avoids the same timing race as test_startup_narrates_service_states
    above."""
    app = _app_with_fake_bus(tmp_path)
    app.bus.list_skills = MagicMock(side_effect=lambda cb: cb({"ovos-skill-grimm-tales.andlo": True}))
    app.call_from_thread = MagicMock(side_effect=lambda fn, *a, **kw: fn(*a, **kw))
    with patch("ovos_tui_client.app.discover_services_with_state", return_value=[]):
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            view = app.query_one("#conversation", RichLog)
            last_line = str(view.lines[-1])
            assert "ok ready" in last_line.lower()


# --- Log: Select all / Deselect all skills ---

@pytest.mark.asyncio
async def test_select_all_deselect_all_not_offered_when_no_skills_seen_yet(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        titles = [cmd.title for cmd in app.get_system_commands(app.screen)]
        assert "Log: Select all skills" not in titles
        assert "Log: Deselect all skills" not in titles


@pytest.mark.asyncio
async def test_select_all_deselect_all_offered_once_skills_are_known(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    app.skill_enabled = {"ovos-skill-grimm-tales.andlo": False}
    async with app.run_test() as pilot:
        titles = [cmd.title for cmd in app.get_system_commands(app.screen)]
        assert "Log: Select all skills" in titles
        assert "Log: Deselect all skills" in titles


@pytest.mark.asyncio
async def test_select_all_skills_checks_every_known_skill(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    app.skill_enabled = {"ovos-skill-grimm-tales.andlo": False, "ovos-skill-andersen-tales.andlo": False}
    async with app.run_test() as pilot:
        app._select_all_skills()
        await pilot.pause()
        assert all(app.skill_enabled.values())


@pytest.mark.asyncio
async def test_deselect_all_skills_unchecks_every_known_skill(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    app.skill_enabled = {"ovos-skill-grimm-tales.andlo": True, "ovos-skill-andersen-tales.andlo": True}
    async with app.run_test() as pilot:
        app._deselect_all_skills()
        await pilot.pause()
        assert not any(app.skill_enabled.values())


@pytest.mark.asyncio
async def test_select_all_skills_narrows_the_log_view_to_that_set(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    app.skill_enabled = {"ovos-skill-grimm-tales.andlo": False}
    async with app.run_test() as pilot:
        app.log_buffer.append(("skills", "handling for skill_id=ovos-skill-grimm-tales.andlo now"))
        app.log_buffer.append(("skills", "an unrelated line with no skill_id"))
        app._select_all_skills()
        await pilot.pause()

        rendered = "\n".join(str(line) for line in app.query_one("#logs-view", RichLog).lines)
        assert "grimm-tales" in rendered
