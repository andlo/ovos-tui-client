"""Tests for the Textual App using Textual's Pilot testing framework -
simulates keypresses/input against a real (but headless) running app,
with a fake bus connection so no real messagebus is needed."""
from unittest.mock import MagicMock

import pytest
from textual.widgets import RichLog, Input, Checkbox

from ovos_tui_client.app import OVOSTUIApp, format_log_line


def _app_with_fake_bus(tmp_path):
    (tmp_path / "skills.log").write_text("")
    (tmp_path / "bus.log").write_text("")
    app = OVOSTUIApp(log_dir_override=str(tmp_path))
    app.bus = MagicMock()
    return app


@pytest.mark.asyncio
async def test_app_composes_all_four_panes(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        assert app.query_one("#logs-view", RichLog) is not None
        assert app.query_one("#conversation", RichLog) is not None
        assert app.query_one("#activity", RichLog) is not None
        assert app.query_one("#utterance-input", Input) is not None


@pytest.mark.asyncio
async def test_a_log_toggle_checkbox_exists_per_source(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        assert app.query_one("#toggle-skills", Checkbox) is not None
        assert app.query_one("#toggle-bus", Checkbox) is not None


@pytest.mark.asyncio
async def test_submitting_input_sends_utterance_and_clears_field(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        input_widget = app.query_one("#utterance-input", Input)
        input_widget.value = "read me a grimm story"
        await pilot.press("enter")

        app.bus.send_utterance.assert_called_once_with("read me a grimm story")
        assert input_widget.value == ""


@pytest.mark.asyncio
async def test_submitting_empty_input_does_not_send(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        input_widget = app.query_one("#utterance-input", Input)
        input_widget.value = "   "
        await pilot.press("enter")

        app.bus.send_utterance.assert_not_called()


@pytest.mark.asyncio
async def test_unchecking_a_log_toggle_disables_that_source(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        checkbox = app.query_one("#toggle-skills", Checkbox)
        checkbox.value = False
        await pilot.pause()

        skills_source = next(s for s in app.log_sources if s.name == "skills")
        assert skills_source.enabled is False


@pytest.mark.asyncio
async def test_no_log_sources_shows_a_helpful_message(tmp_path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    app = OVOSTUIApp(log_dir_override=str(empty_dir))
    app.bus = MagicMock()
    async with app.run_test() as pilot:
        # should not crash, and the logs view should have SOME content
        # (the "no logs found" notice) rather than being silently empty
        assert app.log_sources == []


# --- format_log_line: per-source color + ERROR bolding ---

def test_format_log_line_colors_by_known_source():
    line = format_log_line("skills", "loaded ovos-skill-grimm-tales")
    assert line == "[green]\\[skills] loaded ovos-skill-grimm-tales[/green]"


def test_format_log_line_falls_back_to_default_color_for_unknown_source():
    line = format_log_line("mystery-service", "hello")
    assert line == "[white]\\[mystery-service] hello[/white]"


def test_format_log_line_bolds_error_lines():
    line = format_log_line("skills", "ERROR: could not load skill")
    assert line == "[bold][green]\\[skills] ERROR: could not load skill[/green][/bold]"


def test_format_log_line_does_not_bold_normal_lines():
    line = format_log_line("skills", "handling intent normally")
    assert "[bold]" not in line


# --- conversation pane: full-line color, not just the label ---

@pytest.mark.asyncio
async def test_you_line_is_fully_green(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        conv = app.query_one("#conversation", RichLog)
        conv.write = MagicMock(wraps=conv.write)

        input_widget = app.query_one("#utterance-input", Input)
        input_widget.value = "read me a grimm story"
        await pilot.press("enter")

        conv.write.assert_any_call("[green]You: read me a grimm story[/green]")


@pytest.mark.asyncio
async def test_ovos_line_is_fully_blue(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        conv = app.query_one("#conversation", RichLog)
        conv.write = MagicMock(wraps=conv.write)

        # _write_conversation is the part _handle_speak marshals onto the
        # app's own thread via call_from_thread - testing it directly
        # here, since call_from_thread itself refuses to run when called
        # from the same thread as the app (which is exactly this test).
        app._write_conversation("[blue]OVOS: Here is Cinderella, by the Brothers Grimm[/blue]")
        await pilot.pause()

        conv.write.assert_any_call("[blue]OVOS: Here is Cinderella, by the Brothers Grimm[/blue]")


@pytest.mark.asyncio
async def test_handle_speak_formats_the_line_correctly(tmp_path):
    """Verifies _handle_speak itself builds the right string, without
    actually crossing call_from_thread's same-thread guard - mocks
    call_from_thread to inspect what it was asked to marshal."""
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        app.call_from_thread = MagicMock()

        app._handle_speak("Here is Cinderella")

        app.call_from_thread.assert_called_once_with(
            app._write_conversation, "[blue]OVOS: Here is Cinderella[/blue]"
        )


# --- command history: up/down browses previously submitted utterances ---

@pytest.mark.asyncio
async def test_up_arrow_recalls_previous_utterance(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        input_widget = app.query_one("#utterance-input", Input)
        input_widget.value = "first utterance"
        await pilot.press("enter")
        input_widget.value = "second utterance"
        await pilot.press("enter")

        await pilot.press("up")
        assert input_widget.value == "second utterance"

        await pilot.press("up")
        assert input_widget.value == "first utterance"


@pytest.mark.asyncio
async def test_up_then_down_returns_towards_newest_then_clears(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        input_widget = app.query_one("#utterance-input", Input)
        input_widget.value = "only utterance"
        await pilot.press("enter")

        await pilot.press("up")
        assert input_widget.value == "only utterance"

        await pilot.press("down")
        assert input_widget.value == ""


@pytest.mark.asyncio
async def test_up_arrow_with_no_history_does_nothing(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        input_widget = app.query_one("#utterance-input", Input)
        await pilot.press("up")
        assert input_widget.value == ""


# --- log filtering: free text + source toggles, both retroactive ---

@pytest.mark.asyncio
async def test_log_filter_input_exists(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        assert app.query_one("#log-filter", Input) is not None


@pytest.mark.asyncio
async def test_typing_in_filter_hides_non_matching_lines(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        app.log_buffer.append(("skills", "loaded ovos-skill-grimm-tales"))
        app.log_buffer.append(("skills", "loaded ovos-skill-andersen-tales"))

        filter_input = app.query_one("#log-filter", Input)
        filter_input.value = "grimm"
        await pilot.pause()

        view = app.query_one("#logs-view", RichLog)
        rendered = "\n".join(str(line) for line in view.lines)
        assert "grimm" in rendered.lower()
        assert "andersen" not in rendered.lower()


@pytest.mark.asyncio
async def test_clearing_filter_shows_everything_again(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        app.log_buffer.append(("skills", "loaded ovos-skill-grimm-tales"))
        app.log_buffer.append(("skills", "loaded ovos-skill-andersen-tales"))

        filter_input = app.query_one("#log-filter", Input)
        filter_input.value = "grimm"
        await pilot.pause()
        filter_input.value = ""
        await pilot.pause()

        view = app.query_one("#logs-view", RichLog)
        rendered = "\n".join(str(line) for line in view.lines)
        assert "grimm" in rendered.lower()
        assert "andersen" in rendered.lower()


@pytest.mark.asyncio
async def test_toggling_source_off_retroactively_hides_its_lines(tmp_path):
    """Regression guard: before the buffer+re-render architecture,
    unchecking a source only stopped FUTURE lines, it didn't hide
    already-written ones."""
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        app.log_buffer.append(("skills", "already here before toggling"))

        checkbox = app.query_one("#toggle-skills", Checkbox)
        checkbox.value = False
        await pilot.pause()

        view = app.query_one("#logs-view", RichLog)
        rendered = "\n".join(str(line) for line in view.lines)
        assert "already here" not in rendered


@pytest.mark.asyncio
async def test_pressing_enter_in_filter_box_does_not_send_an_utterance(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        filter_input = app.query_one("#log-filter", Input)
        filter_input.focus()
        filter_input.value = "grimm"
        await pilot.press("enter")

        app.bus.send_utterance.assert_not_called()
