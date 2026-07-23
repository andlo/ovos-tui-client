"""Tests for the newest round of features: F1 help screen, F5-F8 pane
focus shortcuts, the clickable 'Skills:' status label, redirecting
stray typing in non-input panes to the utterance input, and
scroll-position-aware writing (never yanking a scrolled-up user back
to the bottom)."""
from unittest.mock import MagicMock

import pytest
from textual.widgets import RichLog, Input, Checkbox

from ovos_tui_client.app import OVOSTUIApp, HelpScreen


def _app_with_fake_bus(tmp_path):
    (tmp_path / "skills.log").write_text("")
    (tmp_path / "bus.log").write_text("")
    app = OVOSTUIApp(log_dir_override=str(tmp_path))
    app.bus = MagicMock()
    return app


# --- F1 help screen ---

@pytest.mark.asyncio
async def test_pressing_f1_opens_the_help_screen(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("f1")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)


@pytest.mark.asyncio
async def test_escape_closes_the_help_screen(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("f1")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, HelpScreen)


# --- F5-F8 pane focus shortcuts ---

@pytest.mark.asyncio
async def test_f5_focuses_logs_pane(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("f5")
        await pilot.pause()
        assert app.focused is app.query_one("#logs-view", RichLog)


@pytest.mark.asyncio
async def test_f6_focuses_conversation_pane(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("f6")
        await pilot.pause()
        assert app.focused is app.query_one("#conversation", RichLog)


@pytest.mark.asyncio
async def test_f7_focuses_activity_pane(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("f7")
        await pilot.pause()
        assert app.focused is app.query_one("#activity", RichLog)


@pytest.mark.asyncio
async def test_f8_focuses_utterance_input(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("f5")  # move away first
        await pilot.pause()
        await pilot.press("f8")
        await pilot.pause()
        assert app.focused is app.query_one("#utterance-input", Input)


# --- clickable "Skills:" status label ---

@pytest.mark.asyncio
async def test_clicking_skills_label_opens_skill_filter_screen(tmp_path):
    from ovos_tui_client.app import SkillFilterScreen
    app = _app_with_fake_bus(tmp_path)
    # default 80-col test size crowds the skills-status label off the
    # visible region once several source/level checkboxes are packed
    # into the same row - use a wider size, matching real terminals
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.click("#skills-status")
        await pilot.pause()
        assert isinstance(app.screen, SkillFilterScreen)


@pytest.mark.asyncio
async def test_enter_on_focused_skills_label_opens_skill_filter_screen(tmp_path):
    """Keyboard-only equivalent of clicking - for users without a
    mouse in their terminal."""
    from ovos_tui_client.app import SkillFilterScreen
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        app.query_one("#skills-status").focus()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, SkillFilterScreen)


# --- typing in a non-input pane redirects to the utterance input ---

@pytest.mark.asyncio
async def test_typing_while_logs_focused_redirects_to_utterance_input(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("f5")  # focus logs-view
        await pilot.pause()
        await pilot.press("h", "i")
        await pilot.pause()

        input_widget = app.query_one("#utterance-input", Input)
        assert app.focused is input_widget
        assert input_widget.value == "hi"


@pytest.mark.asyncio
async def test_typing_while_activity_focused_redirects_to_utterance_input(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("f7")  # focus activity
        await pilot.pause()
        await pilot.press("s", "t", "o", "p")
        await pilot.pause()

        input_widget = app.query_one("#utterance-input", Input)
        assert app.focused is input_widget
        assert input_widget.value == "stop"


@pytest.mark.asyncio
async def test_typing_on_a_checkbox_does_not_redirect(tmp_path):
    """The redirect must NOT hijack Space-to-toggle on a focused
    checkbox - only Logs/Conversation/Activity panes redirect."""
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        checkbox = app.query_one("#toggle-source-skills", Checkbox)
        checkbox.focus()
        before = checkbox.value
        await pilot.press("space")
        await pilot.pause()

        assert checkbox.value is not before  # toggled normally
        input_widget = app.query_one("#utterance-input", Input)
        assert app.focused is not input_widget  # NOT redirected
        assert input_widget.value == ""


# --- scroll-aware writing: never yank a scrolled-up user back down ---

@pytest.mark.asyncio
async def test_write_to_log_disables_auto_scroll_when_user_has_scrolled_up(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        view = app.query_one("#logs-view", RichLog)
        for i in range(50):
            view.write(f"line {i}")
        await pilot.pause()
        view.scroll_home(animate=False)  # scroll to the very top
        await pilot.pause()
        assert view.is_vertical_scroll_end is False

        app._write_to_log(view, "a brand new line")
        await pilot.pause()

        assert view.is_vertical_scroll_end is False  # did NOT jump to bottom


@pytest.mark.asyncio
async def test_write_to_log_keeps_auto_scrolling_when_already_at_bottom(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        view = app.query_one("#logs-view", RichLog)
        for i in range(50):
            app._write_to_log(view, f"line {i}")
        await pilot.pause()
        assert view.is_vertical_scroll_end is True

        app._write_to_log(view, "a brand new line")
        await pilot.pause()

        assert view.is_vertical_scroll_end is True  # stayed at the bottom


# --- command palette: same actions as F1-F8, fuzzy-searchable ---

@pytest.mark.asyncio
async def test_get_system_commands_includes_our_actions(tmp_path):
    """Service Restart/Stop/Start are NOT static entries here - they're
    dynamic hits from ServiceCommandProvider (see test_command_palette.py),
    since they need per-service names filled in at search time."""
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        titles = [cmd.title for cmd in app.get_system_commands(app.screen)]
        assert any("Help" in t for t in titles)
        assert any("Skill: List installed" in t for t in titles)
        assert any("filter" in t.lower() for t in titles)
        assert any("Focus: Logs" in t for t in titles)
        assert any("Focus: Conversation" in t for t in titles)
        assert any("Focus: Activity" in t for t in titles)
        assert any("Focus: Utterance input" in t for t in titles)


@pytest.mark.asyncio
async def test_get_system_commands_still_includes_textuals_own_defaults(tmp_path):
    """Confirms we're extending, not replacing, the base command set
    (e.g. Textual's own 'Quit the application' / theme-switcher
    commands should still be present)."""
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        titles = [cmd.title for cmd in app.get_system_commands(app.screen)]
        assert len(titles) > 8  # our 8 + at least one Textual default


# --- Ctrl+Q quits, Ctrl+C no longer does (issue #1) ---

@pytest.mark.asyncio
async def test_ctrl_q_is_bound_to_quit(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        keys = [b[0] for b in app.BINDINGS]
        assert "ctrl+q" in keys
        assert "ctrl+c" not in keys


# --- Command Palette: filter toggle commands (issue #3) ---

@pytest.mark.asyncio
async def test_get_system_commands_includes_source_and_level_toggles(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        titles = [cmd.title for cmd in app.get_system_commands(app.screen)]
        assert "Log: Toggle source: skills" in titles
        assert "Log: Toggle source: bus" in titles
        assert "Log: Toggle level: ERROR" in titles


@pytest.mark.asyncio
async def test_toggle_source_command_flips_state_and_syncs_checkbox(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        before = next(s.enabled for s in app.log_sources if s.name == "skills")

        app._toggle_source("skills")
        await pilot.pause()

        after = next(s.enabled for s in app.log_sources if s.name == "skills")
        assert after is not before
        checkbox = app.query_one("#toggle-source-skills", Checkbox)
        assert checkbox.value == after


@pytest.mark.asyncio
async def test_toggle_level_command_flips_state_and_syncs_checkbox(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        before = app.level_enabled["ERROR"]

        app._toggle_level("ERROR")
        await pilot.pause()

        assert app.level_enabled["ERROR"] is not before
        checkbox = app.query_one("#toggle-level-ERROR", Checkbox)
        assert checkbox.value == app.level_enabled["ERROR"]


@pytest.mark.asyncio
async def test_toggle_source_via_command_rerenders_the_log_view(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        app.log_buffer.append(("skills", "a skills line"))
        app._toggle_source("skills")  # skills was checked by default -> now unchecked
        await pilot.pause()

        rendered = "\n".join(str(line) for line in app.query_one("#logs-view", RichLog).lines)
        assert "a skills line" not in rendered
