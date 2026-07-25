"""Tests for the Textual App using Textual's Pilot testing framework -
simulates keypresses/input against a real (but headless) running app,
with a fake bus connection so no real messagebus is needed."""
from unittest.mock import MagicMock, patch

import pytest
from textual.widgets import RichLog, Input, Label, Checkbox

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
async def test_source_and_level_checkboxes_are_inline_and_checked_by_default(tmp_path):
    """Sources and Log Levels are compact inline checkboxes directly in
    the main view (not a modal) - checked by default (unlike Skills,
    which defaults unchecked): both are short, fixed-length lists
    where 'everything on, uncheck what you don't want' reads
    naturally. See app.py's module docstring for the full rationale."""
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        skills_cb = app.query_one("#toggle-source-skills", Checkbox)
        assert skills_cb.value is True
        debug_cb = app.query_one("#toggle-level-DEBUG", Checkbox)
        assert debug_cb.value is True
        label = app.query_one("#skills-status", Label)
        assert "Skills" in str(label.content)


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
async def test_no_log_sources_shows_a_helpful_message(tmp_path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    app = OVOSTUIApp(log_dir_override=str(empty_dir))
    app.bus = MagicMock()
    with patch("ovos_tui_client.app.detect_container_runtime", return_value=[]):
        async with app.run_test() as pilot:
            # should not crash, and the logs view should have SOME
            # content (the "no logs found" notice) rather than being
            # silently empty
            assert app.log_sources == []
            text = "\n".join(str(line) for line in app.query_one("#logs-view", RichLog).lines)
            assert "no known log files found" in text.lower()
            assert "docker" not in text.lower()


@pytest.mark.asyncio
async def test_no_log_sources_on_a_docker_install_explains_stdout_logging(tmp_path):
    """Confirmed via ovos-docker's own documentation (not just
    inferred): the official sample mycroft.conf sets "logs": {"path":
    "stdout"} - meaning on an install that follows that guide as
    written, there are no log files on the host at all, ever. The
    message here needs to actually say that, not a generic "no logs
    found" that reads like something's broken rather than "this is a
    different kind of install".

    This is the fallback message for when the log BRIDGE itself
    doesn't work out (no usable docker/podman binary despite
    containers being listed - mocked here via
    start_container_log_bridges returning [], deterministically
    forcing that path rather than depending on whatever this sandbox's
    own real docker/podman happens to do with a nonexistent
    container). is_stdout_only_logging() is also explicitly mocked to
    False so the test exercises the tier-2 wording specifically."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    app = OVOSTUIApp(log_dir_override=str(empty_dir))
    app.bus = MagicMock()
    with patch("ovos_tui_client.app.detect_container_runtime", return_value=["ovos-core", "ovos-audio"]), \
         patch("ovos_tui_client.app.start_container_log_bridges", return_value=[]), \
         patch("ovos_tui_client.app.is_stdout_only_logging", return_value=False):
        async with app.run_test() as pilot:
            text = "\n".join(str(line) for line in app.query_one("#logs-view", RichLog).lines)
            assert "docker" in text.lower() or "podman" in text.lower()
            assert "docker logs" in text.lower() or "docker compose logs" in text.lower()


@pytest.mark.asyncio
async def test_no_log_sources_bridges_docker_containers_when_possible(tmp_path):
    """The success path: start_container_log_bridges() actually
    returns process handles (mocked here - real subprocess behavior is
    covered separately in test_services.py) - the app should pick up
    the bridged sources and NOT show the "no logs found" fallback
    message at all."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    app = OVOSTUIApp(log_dir_override=str(empty_dir))
    app.bus = MagicMock()
    fake_proc = MagicMock()

    def fake_bridge(container_names, target_dir):
        # simulates what the real function would have done: create
        # the log file(s) in whatever temp dir on_mount() actually
        # created (a real tempfile.mkdtemp() call, not something this
        # test can predict ahead of time - so writing it here, once
        # on_mount() passes in the real path, is the reliable way to
        # populate it before the (unmocked) discover_log_sources() call
        # right after this one runs)
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "ovos_core.log").write_text("")
        return [fake_proc]

    with patch("ovos_tui_client.app.detect_container_runtime", return_value=["ovos_core"]), \
         patch("ovos_tui_client.app.start_container_log_bridges", side_effect=fake_bridge):
        async with app.run_test() as pilot:
            assert len(app.log_sources) == 1
            assert app.log_sources[0].name == "ovos_core"
            assert app.log_bridge_handles == [fake_proc]
            conv = app.query_one("#conversation", RichLog)
            conv_text = "\n".join(str(line) for line in conv.lines)
            assert "bridged" in conv_text.lower()


@pytest.mark.asyncio
async def test_no_log_sources_with_confirmed_stdout_logging_is_definitive(tmp_path):
    """Tier 1 - is_stdout_only_logging() confirmed True (read directly
    from mycroft.conf via ovos_utils.log, not inferred from container
    detection) - the message should be worded as a fact, not a guess."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    app = OVOSTUIApp(log_dir_override=str(empty_dir))
    app.bus = MagicMock()
    # detect_container_runtime() is mocked here too (returning []) only
    # to keep the unrelated Services: boot line deterministic in this
    # test - it's a separate code path (_check_services_worker) that
    # calls the same function for a different purpose, not something
    # this test's own logs-message logic depends on.
    with patch("ovos_tui_client.app.detect_container_runtime", return_value=[]), \
         patch("ovos_tui_client.app.is_stdout_only_logging", return_value=True):
        async with app.run_test() as pilot:
            text = "\n".join(str(line) for line in app.query_one("#logs-view", RichLog).lines)
            assert "stdout" in text.lower()
            assert "confirmed" in text.lower()
            assert "docker logs" in text.lower() or "docker compose logs" in text.lower()


@pytest.mark.asyncio
async def test_is_local_true_for_localhost_variants():
    for host in ("127.0.0.1", "localhost", "::1"):
        app = OVOSTUIApp(host=host)
        assert app.is_local is True


@pytest.mark.asyncio
async def test_is_local_false_for_a_remote_host():
    app = OVOSTUIApp(host="192.168.1.50")
    assert app.is_local is False


# --- format_log_line: per-source color + padding + ERROR bolding + timestamp/component stripping ---

def test_format_log_line_colors_by_known_source():
    line = format_log_line("skills", "loaded ovos-skill-grimm-tales")
    assert line == "[green]\\[skills   ][/green] loaded ovos-skill-grimm-tales"


def test_format_log_line_falls_back_to_default_color_for_unknown_source():
    line = format_log_line("mystery-service", "hello")
    assert line == "[white]\\[mystery-service][/white] hello"


def test_format_log_line_bolds_error_lines():
    line = format_log_line("skills", "ERROR: could not load skill")
    assert line == "[bold][green]\\[skills   ][/green] ERROR: could not load skill[/bold]"


def test_format_log_line_does_not_bold_normal_lines():
    line = format_log_line("skills", "handling intent normally")
    assert "[bold]" not in line


def test_format_log_line_pads_short_names_to_align_with_longest():
    import re
    bus_line = format_log_line("bus", "short name")
    enclosure_line = format_log_line("enclosure", "long name")
    bus_tag = re.search(r"\\\[(.*?)\]", bus_line).group(1)
    enclosure_tag = re.search(r"\\\[(.*?)\]", enclosure_line).group(1)
    assert len(bus_tag) == len(enclosure_tag)


def test_format_log_line_strips_timestamp_and_component_prefix():
    raw = "2026-07-22 21:13:03.456 - skills - some_module:func:12 - INFO - handling intent"
    line = format_log_line("skills", raw)
    assert "2026-07-22" not in line
    assert line == "[green]\\[skills   ][/green] some_module:func:12 - INFO - handling intent"


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
        app._write_conversation("[blue]OVOS: Here is Cinderella, by the Brothers Grimm[/blue]")
        await pilot.pause()
        conv.write.assert_any_call("[blue]OVOS: Here is Cinderella, by the Brothers Grimm[/blue]")


@pytest.mark.asyncio
async def test_handle_speak_formats_the_line_correctly(tmp_path):
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


# --- log filtering: free text, source/level/skill state (now driven
# via the F4 filter modal - see test_screens.py - or direct state
# mutation here for the underlying filter LOGIC, independent of the
# modal UI) ---

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
async def test_pressing_enter_in_filter_box_does_not_send_an_utterance(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        filter_input = app.query_one("#log-filter", Input)
        filter_input.focus()
        filter_input.value = "grimm"
        await pilot.press("enter")
        app.bus.send_utterance.assert_not_called()


@pytest.mark.asyncio
async def test_nothing_checked_shows_everything_regardless_of_source_or_level(tmp_path):
    """The core new-semantics invariant: with no source/level checked,
    nothing is restricted - the unfiltered, default state."""
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        app.log_buffer.append(("skills", "module:func:1 - INFO - all good"))
        app.log_buffer.append(("bus", "module:func:2 - ERROR - something broke"))
        app._rerender_logs()
        await pilot.pause()
        view = app.query_one("#logs-view", RichLog)
        rendered = "\n".join(str(line) for line in view.lines)
        assert "all good" in rendered
        assert "something broke" in rendered


@pytest.mark.asyncio
async def test_unchecking_one_source_narrows_away_from_it(tmp_path):
    """Regression guard: before the buffer+re-render architecture,
    toggling a source only affected FUTURE lines, not already-written
    ones. All sources are checked by default (see the checked-by-
    default test above), so unchecking 'skills' should hide skills
    lines while a bus line (still checked) remains."""
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        app.log_buffer.append(("skills", "already here before toggling"))
        app.log_buffer.append(("bus", "a bus line"))
        for src in app.log_sources:
            if src.name == "skills":
                src.enabled = False
        app._rerender_logs()
        await pilot.pause()
        view = app.query_one("#logs-view", RichLog)
        rendered = "\n".join(str(line) for line in view.lines)
        assert "already here" not in rendered
        assert "a bus line" in rendered


@pytest.mark.asyncio
async def test_unchecking_error_level_hides_it_but_keeps_info(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        app.log_buffer.append(("skills", "module:func:1 - INFO - all good"))
        app.log_buffer.append(("skills", "module:func:2 - ERROR - something broke"))
        app.level_enabled["ERROR"] = False
        app._rerender_logs()
        await pilot.pause()
        view = app.query_one("#logs-view", RichLog)
        rendered = "\n".join(str(line) for line in view.lines)
        assert "all good" in rendered
        assert "something broke" not in rendered


@pytest.mark.asyncio
async def test_a_new_skill_is_tracked_unchecked_the_first_time_its_id_is_seen(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        src = app.log_sources[0]
        src.read_new_lines = MagicMock(return_value=[
            "IntentHandlerMatch(skill_id='ovos-skill-grimm-tales.andlo')"
        ])
        for s in app.log_sources[1:]:
            s.read_new_lines = MagicMock(return_value=[])

        app._poll_logs()
        await pilot.pause()

        assert app.skill_enabled == {"ovos-skill-grimm-tales.andlo": False}


@pytest.mark.asyncio
async def test_checking_one_skill_narrows_to_only_that_skill(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        app.log_buffer.append(("skills", "handling for skill_id=grimm-tales now"))
        app.log_buffer.append(("skills", "handling for skill_id=andersen-tales now"))
        app.skill_enabled = {"grimm-tales": True, "andersen-tales": False}

        app._rerender_logs()
        await pilot.pause()

        view = app.query_one("#logs-view", RichLog)
        rendered = "\n".join(str(line) for line in view.lines)
        assert "grimm-tales" in rendered
        assert "andersen-tales" not in rendered
