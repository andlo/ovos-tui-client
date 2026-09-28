"""#41: the skill list follows skills installed / loaded / removed while
the TUI runs - automatically from bus messages, and on demand from the
palette ('Refresh: Skills and services')."""
from unittest.mock import MagicMock, patch

import pytest
from textual.widgets import RichLog

from ovos_tui_client.app import OVOSTUIApp

W = "ovos-skill-weather.openvoiceos"
C = "ovos-skill-convert.andlo"


def _app(tmp_path):
    (tmp_path / "skills.log").write_text("")
    app = OVOSTUIApp(log_dir_override=str(tmp_path), scripts_dir=tmp_path / "scripts")
    app.bus = MagicMock()
    app.bus.lang = "en-us"
    app.installed_skills = {W: True}
    return app


def _text(app):
    return " ".join(str(line.text) for line in app.query_one("#conversation", RichLog).lines)


# --- which bus messages count --------------------------------------------

@pytest.mark.parametrize("msg_type,data,context,expected", [
    ("detach_skill", {"skill_id": W}, {}, True),                     # removed / deactivated
    ("ovos.skill.loaded", {"skill_id": C}, {}, True),                # ovos-core 3.x
    ("mycroft.skills.loaded", {}, {}, True),
    ("homescreen.register.examples", {"skill_id": C}, {}, True),     # 2.1.x: a new skill registering
    ("homescreen.register.examples", {"skill_id": W}, {}, False),    # known skill - every startup
    ("padatious:register_intent", {"name": f"{C}:convert.intent"}, {"skill_id": C}, True),
    ("padatious:register_intent", {"name": f"{W}:weather.intent"}, {"skill_id": W}, False),
    ("register_intent", {"name": f"{C}:ConvertIntent"}, {}, True),   # skill_id from the intent name
    ("recognizer_loop:utterance", {"utterances": ["hi"]}, {}, False),
    ("speak", {"utterance": "hi"}, {"skill_id": C}, False),
])
def test_which_messages_mean_the_skills_changed(tmp_path, msg_type, data, context, expected):
    app = _app(tmp_path)
    assert app._is_skill_change(msg_type, data, context) is expected


# --- automatic refresh -----------------------------------------------------

@pytest.mark.asyncio
async def test_a_newly_loaded_skill_is_picked_up_and_announced(tmp_path):
    app = _app(tmp_path)
    with patch.object(OVOSTUIApp, "SKILL_REFRESH_DEBOUNCE", 0.05):
        async with app.run_test() as pilot:
            app.golden_counts = {W: 0, C: 0}
            app.bus.list_skills.reset_mock()
            app.bus.list_skills.side_effect = lambda cb, **kw: cb({W: True, C: True})
            app._handle_bus_message("ovos.skill.loaded", {"skill_id": C}, {})
            await pilot.pause(0.3)
            assert app.installed_skills == {W: True, C: True}
            assert "Skills changed: added Convert" in _text(app)
            # a new skill gets its golden tests looked up again; others keep theirs
            assert C not in app.golden_counts and app.golden_counts[W] == 0


@pytest.mark.asyncio
async def test_a_burst_of_messages_leads_to_one_refresh(tmp_path):
    app = _app(tmp_path)
    with patch.object(OVOSTUIApp, "SKILL_REFRESH_DEBOUNCE", 0.1):
        async with app.run_test() as pilot:
            app.bus.list_skills.reset_mock()
            app.bus.list_skills.side_effect = lambda cb, **kw: cb({W: True, C: True})
            for _ in range(20):
                app._handle_bus_message("padatious:register_intent", {"name": f"{C}:x.intent"}, {"skill_id": C})
            await pilot.pause(0.4)
            assert app.bus.list_skills.call_count == 1


@pytest.mark.asyncio
async def test_removed_skill_is_announced_and_nothing_said_when_unchanged(tmp_path):
    app = _app(tmp_path)
    app.installed_skills = {W: True, C: True}
    with patch.object(OVOSTUIApp, "SKILL_REFRESH_DEBOUNCE", 0.05):
        async with app.run_test() as pilot:
            app.bus.list_skills.side_effect = lambda cb, **kw: cb({W: True})
            app._handle_bus_message("detach_skill", {"skill_id": C}, {})
            await pilot.pause(0.3)
            assert "Skills changed: removed Convert" in _text(app)
            before = _text(app)
            app._handle_bus_message("detach_skill", {"skill_id": W}, {})   # e.g. a deactivate
            await pilot.pause(0.3)
            assert _text(app) == before                                       # quiet: same skills


@pytest.mark.asyncio
async def test_the_skills_window_dict_is_updated_in_place(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        held = app.installed_skills
        app._apply_skill_refresh({W: False, C: True}, dict(held), announce=False)
        await pilot.pause()
        assert held is app.installed_skills and held == {W: False, C: True}


# --- palette: Refresh: Skills and services ---------------------------------

@pytest.mark.asyncio
async def test_palette_refresh_rereads_everything_and_reports(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        titles = [c.title for c in app.get_system_commands(app.screen)]
        assert "Refresh: Skills and services" in titles
        app.golden_counts = {W: 0}
        app.bus.list_skills.side_effect = lambda cb, **kw: cb({W: True})
        with patch("ovos_tui_client.app.discover_services_with_state",
                   return_value=[("ovos-core.service", True)]):
            app.refresh_all()
            await app.workers.wait_for_complete()
            await pilot.pause()
        text = _text(app)
        assert "Refresh: 1 active 0 inactive skills - no changes" in text
        assert "ovos-core.service Active" in text
        assert app.golden_counts == {}          # skills hidden for "no tests" get another chance
        assert text.count("OK ready.") <= 1     # the services worker must not re-announce startup


@pytest.mark.asyncio
async def test_palette_refresh_reports_a_timeout(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        app.bus.list_skills.side_effect = lambda cb, **kw: cb(None)
        app.refresh_skills(announce=True)
        await pilot.pause()
        assert "Refresh: skill list - no response (timed out)" in _text(app)
        assert app.installed_skills == {W: True}   # kept as it was


@pytest.mark.asyncio
async def test_ok_ready_is_written_once_even_when_the_services_worker_runs_again(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        app._startup_steps_remaining = 2
        app._finish_startup()
        app._finish_startup()          # startup done
        app._finish_startup()          # services worker again, from Refresh
        await pilot.pause()
        assert _text(app).count("OK ready.") == 1
