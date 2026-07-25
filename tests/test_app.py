"""Tests for the Textual App using Textual's Pilot testing framework -
simulates keypresses/input against a real (but headless) running app,
with a fake bus connection so no real messagebus is needed."""
import sys
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
    False so the test exercises the tier-2 wording specifically.

    detect_container_runtime()/start_container_log_bridges() are now
    called from __init__(), not on_mount() (see __init__'s own
    docstring note on why - compose() runs before on_mount() and
    needs self.log_sources to already be correct) - so the patches
    must wrap the OVOSTUIApp(...) construction itself, not just
    run_test()."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    with patch("ovos_tui_client.app.detect_container_runtime", return_value=["ovos-core", "ovos-audio"]), \
         patch("ovos_tui_client.app.start_container_log_bridges", return_value=[]), \
         patch("ovos_tui_client.app.is_stdout_only_logging", return_value=False):
        app = OVOSTUIApp(log_dir_override=str(empty_dir))
        app.bus = MagicMock()
        async with app.run_test() as pilot:
            text = "\n".join(str(line) for line in app.query_one("#logs-view", RichLog).lines)
            assert "docker" in text.lower() or "podman" in text.lower()
            assert "docker logs" in text.lower() or "docker compose logs" in text.lower()


@pytest.mark.asyncio
async def test_no_log_sources_bridges_docker_containers_when_possible(tmp_path):
    """The success path: start_container_log_bridges() actually
    returns process handles (mocked here - real subprocess behavior,
    including the container-name -> category grouping, is covered
    separately in test_services.py) - the app should pick up the
    bridged sources and NOT show the "no logs found" fallback message
    at all.

    Bridging now happens in __init__() (see that docstring note), so
    the OVOSTUIApp(...) construction itself must be inside the patch
    block, not just run_test()."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    fake_proc = MagicMock()

    def fake_bridge(container_names, target_dir):
        # simulates what the real function would have done: create
        # the log file(s) in whatever temp dir __init__() actually
        # created (a real tempfile.mkdtemp() call, not something this
        # test can predict ahead of time - so writing it here, once
        # __init__() passes in the real path, is the reliable way to
        # populate it before the (unmocked) discover_log_sources() call
        # right after this one runs). "ovos_core" categorizes to
        # "skills" (see categorize_container_name()), so the file is
        # named skills.log, not ovos_core.log - matching how the real
        # bridge now groups containers into the same small set of
        # filenames a normal install already uses.
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "skills.log").write_text("")
        return [fake_proc]

    with patch("ovos_tui_client.app.detect_container_runtime", return_value=["ovos_core"]), \
         patch("ovos_tui_client.app.start_container_log_bridges", side_effect=fake_bridge):
        app = OVOSTUIApp(log_dir_override=str(empty_dir))
        app.bus = MagicMock()
        async with app.run_test() as pilot:
            assert len(app.log_sources) == 1
            assert app.log_sources[0].name == "skills"
            assert app.log_bridge_handles == [fake_proc]
            conv = app.query_one("#conversation", RichLog)
            conv_text = "\n".join(str(line) for line in conv.lines)
            assert "containers in use" in conv_text.lower()


@pytest.mark.asyncio
async def test_bridged_sources_get_real_sources_checkboxes(tmp_path):
    """Real bug found via live testing on an actual ovos-docker
    install: bridging used to happen in on_mount(), but compose()
    (which builds the Sources: checkboxes FROM self.log_sources at
    that point in time) runs BEFORE on_mount() - so self.log_sources
    ended up correctly populated, but zero checkboxes ever existed for
    it; the Sources: row was simply empty despite log tailing actually
    working. Moving the bridge attempt into __init__() (before
    compose() ever runs) fixes this - this test is the direct
    regression check for it."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    fake_proc = MagicMock()

    def fake_bridge(container_names, target_dir):
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "skills.log").write_text("")
        return [fake_proc]

    with patch("ovos_tui_client.app.detect_container_runtime", return_value=["ovos_core"]), \
         patch("ovos_tui_client.app.start_container_log_bridges", side_effect=fake_bridge):
        app = OVOSTUIApp(log_dir_override=str(empty_dir))
        app.bus = MagicMock()
        async with app.run_test() as pilot:
            checkbox = app.query_one("#toggle-source-skills", Checkbox)
            assert checkbox is not None
            assert checkbox.value is True  # Sources start checked, per existing filter semantics


@pytest.mark.asyncio
async def test_services_boot_line_on_containers_does_not_enumerate_every_one(tmp_path):
    """The Services: boot line used to list every single detected
    container name, one per line - unwieldy on a real ovos-docker
    install (confirmed: 26 containers = 26 lines just for this one
    status message). Now states the count and the one relevant fact
    (start/stop not supported here yet) without the enumeration -
    `docker ps`/`podman ps` is the right place to see the full list
    if actually needed."""
    app = _app_with_fake_bus(tmp_path)
    container_names = [f"ovos_skill_{i}" for i in range(26)]
    with patch("ovos_tui_client.app.discover_services_with_state", return_value=[]), \
         patch("ovos_tui_client.app.detect_container_runtime", return_value=container_names):
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            conv = app.query_one("#conversation", RichLog)
            text = "\n".join(str(line) for line in conv.lines)
            assert "26 container" in text.lower()
            assert "start/stop" in text.lower()
            for name in container_names:
                assert name not in text


@pytest.mark.asyncio
async def test_no_log_sources_with_confirmed_stdout_logging_is_definitive(tmp_path):
    """Tier 1 - is_stdout_only_logging() confirmed True (read directly
    from mycroft.conf via ovos_utils.log, not inferred from container
    detection) - the message should be worded as a fact, not a guess."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    # detect_container_runtime() is mocked here too (returning []) only
    # to keep the unrelated Services: boot line deterministic in this
    # test - it's a separate code path (_check_services_worker) that
    # calls the same function for a different purpose, not something
    # this test's own logs-message logic depends on. Still needs to
    # wrap construction since __init__() also calls it now (to decide
    # whether to attempt bridging).
    with patch("ovos_tui_client.app.detect_container_runtime", return_value=[]), \
         patch("ovos_tui_client.app.is_stdout_only_logging", return_value=True):
        app = OVOSTUIApp(log_dir_override=str(empty_dir))
        app.bus = MagicMock()
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


# --- --web / --web-host / --web-port / _detect_outbound_ip() / run() ---

def test_build_arg_parser_web_flags_default_off():
    from ovos_tui_client.app import build_arg_parser
    args = build_arg_parser().parse_args([])
    assert args.web is False
    assert args.web_host is None
    assert args.web_port == 8000


def test_build_arg_parser_web_flags_parsed():
    from ovos_tui_client.app import build_arg_parser
    args = build_arg_parser().parse_args(["--web", "--web-host", "10.0.0.5", "--web-port", "9000"])
    assert args.web is True
    assert args.web_host == "10.0.0.5"
    assert args.web_port == 9000


def test_detect_outbound_ip_returns_a_string():
    """Not asserting a specific IP (genuinely environment-dependent) -
    just that it returns something usable and never raises, including
    in a sandboxed/restricted-network environment where the real
    detection might fail and fall back to 127.0.0.1."""
    from ovos_tui_client.app import _detect_outbound_ip
    result = _detect_outbound_ip()
    assert isinstance(result, str)
    assert len(result) > 0


def test_detect_outbound_ip_falls_back_on_socket_error():
    from ovos_tui_client.app import _detect_outbound_ip
    with patch("socket.socket") as mock_socket_cls:
        mock_socket_cls.return_value.connect.side_effect = OSError("network unreachable")
        result = _detect_outbound_ip()
    assert result == "127.0.0.1"


def test_run_web_exits_cleanly_with_a_clear_message_if_textual_serve_missing(monkeypatch, capsys):
    """textual-serve is an optional extra (pip install
    ovos-tui-client[web]) - --web without it installed must fail with
    a clear, actionable message, not an ugly ImportError traceback."""
    from ovos_tui_client import app as app_module
    monkeypatch.setattr(sys, "argv", ["ovos-tui", "--web"])
    with patch.dict(sys.modules, {"textual_serve.server": None}):
        with pytest.raises(SystemExit) as exc_info:
            app_module.run()
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "textual-serve" in captured.err
    assert "pip install" in captured.err


def test_run_web_builds_command_and_starts_server(monkeypatch):
    """Confirms the underlying ovos-tui command line is correctly
    reassembled (host/port/lang always included, optional flags only
    when actually given) and handed to textual_serve.server.Server,
    rather than testing the real Server (which would actually try to
    bind a port and launch a subprocess)."""
    from ovos_tui_client import app as app_module
    monkeypatch.setattr(sys, "argv", [
        "ovos-tui", "--web", "--web-host", "10.0.0.5", "--web-port", "9000",
        "--host", "192.168.1.1", "--port", "8181", "--lang", "da-dk",
        "--mycroft-conf", "/tmp/my conf.json",
    ])
    fake_server_cls = MagicMock()
    fake_module = MagicMock()
    fake_module.Server = fake_server_cls
    with patch.dict(sys.modules, {"textual_serve.server": fake_module}):
        app_module.run()

    fake_server_cls.assert_called_once()
    _, kwargs = fake_server_cls.call_args
    assert kwargs["host"] == "10.0.0.5"
    assert kwargs["port"] == 9000
    assert "--host 192.168.1.1" in kwargs["command"]
    assert "--port 8181" in kwargs["command"]
    assert "--lang da-dk" in kwargs["command"]
    assert "'/tmp/my conf.json'" in kwargs["command"]  # shlex-quoted, path has a space
    fake_server_cls.return_value.serve.assert_called_once()


def test_run_web_auto_detects_host_when_not_given(monkeypatch):
    from ovos_tui_client import app as app_module
    monkeypatch.setattr(sys, "argv", ["ovos-tui", "--web"])
    fake_server_cls = MagicMock()
    fake_module = MagicMock()
    fake_module.Server = fake_server_cls
    with patch.dict(sys.modules, {"textual_serve.server": fake_module}):
        with patch("ovos_tui_client.app._detect_outbound_ip", return_value="203.0.113.5"):
            app_module.run()

    _, kwargs = fake_server_cls.call_args
    assert kwargs["host"] == "203.0.113.5"
