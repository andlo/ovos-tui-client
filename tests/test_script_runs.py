"""Pilot tests for scripted test runs in the App (#30): palette entries,
the visible 'script running' state, per-step lines in the conversation
pane and the closing summary. Bus is faked; bus traffic for each step
is fed straight into the running ScriptRunner."""
import json
from unittest.mock import MagicMock, patch

import pytest
from textual.widgets import Input, RichLog

from ovos_tui_client.app import OVOSTUIApp, ScriptCommandProvider, SkillTestCommandProvider
from ovos_tui_client.scripts import GoldenResult, ScriptStep

WEATHER = "ovos-skill-weather.openvoiceos"


def _app(tmp_path, scripts_dir=None):
    (tmp_path / "skills.log").write_text("")
    app = OVOSTUIApp(log_dir_override=str(tmp_path), scripts_dir=scripts_dir or tmp_path / "scripts")
    app.bus = MagicMock()
    app.bus.lang = "en-us"
    return app


def _conversation(app) -> str:
    """Joined WITHOUT separators - RichLog wraps long lines at the pane
    width, which would otherwise split the phrases asserted on."""
    return "".join(str(line.text).rstrip() + " " for line in app.query_one("#conversation", RichLog).lines).replace("  ", " ")


async def _hits(provider, query):
    return [hit async for hit in provider.search(query)]


def _fake_ovos(app, matches):
    """send_utterance side effect: pretend OVOS routed `text` to matches[text]."""
    def send(text, lang=None, session_id=None, script=None):
        runner = app.script_runner
        if matches.get(text):
            runner.feed(matches[text])
        runner.feed("ovos.utterance.handled")
    app.bus.send_utterance.side_effect = send


@pytest.mark.asyncio
async def test_user_script_runs_shows_steps_results_and_summary(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "smoke.jsonl").write_text("\n".join([
        json.dumps({"utterance": "what's the weather", "skill_id": WEATHER, "intent_label": "weather.intent"}),
        json.dumps({"utterance": "who is lincoln", "skill_id": WEATHER, "intent_label": "weather.intent"}),
        "hello there",
    ]))
    app = _app(tmp_path, scripts)
    app.installed_skills = {WEATHER: True, "ovos-skill-wikipedia.openvoiceos": True}
    _fake_ovos(app, {
        "what's the weather": f"{WEATHER}:weather.intent",
        "who is lincoln": "ovos-skill-wikipedia.openvoiceos:wiki",
    })
    with patch("ovos_tui_client.scripts.SETTLE", 0):
        async with app.run_test() as pilot:
            hits = await _hits(ScriptCommandProvider(app.screen), "smoke")
            assert [str(h.text) for h in hits] == ["Script: smoke"]
            hits[0].command()
            await app.workers.wait_for_complete()
            await pilot.pause()

            text = _conversation(app)
            assert "▶ Script: smoke - 3 utterance(s)" in text
            assert "[1/3] You: what's the weather" in text
            assert "✓ ovos-skill-weather.openvoiceos:weather.intent" in text
            assert "✗ expected ovos-skill-weather.openvoiceos:weather.intent, got ovos-skill-wikipedia" in text
            assert "■ Script: smoke finished: 1/2 passed · 1 failed · 1 sent without a check" in text
            assert '[2] "who is lincoln"' in text
            # running state cleared again
            assert app.script_runner is None
            assert not app.query_one("#utterance-input", Input).disabled
            assert not app.query_one("#conversation", RichLog).has_class("script-running")
            assert app.sub_title.split("   ")[0].startswith("OVOS")  # only the channel is left


@pytest.mark.asyncio
async def test_running_state_is_visible_while_a_step_is_in_flight(tmp_path):
    app = _app(tmp_path)
    app.installed_skills = {WEATHER: True}
    seen = {}

    def send(text, lang=None, session_id=None, script=None):
        conv = app.query_one("#conversation", RichLog)
        seen["class"] = conv.has_class("script-running")
        seen["title"] = str(conv.border_title)
        seen["disabled"] = app.query_one("#utterance-input", Input).disabled
        seen["sub_title"] = app.sub_title
        seen["own_session"] = bool(session_id and session_id == app.script_runner.session_id)
        app.script_runner.feed(f"{WEATHER}:weather.intent")
        app.script_runner.feed("ovos.utterance.handled")
    app.bus.send_utterance.side_effect = send

    steps = [ScriptStep("what's the weather", "en-us", WEATHER, "weather.intent")]
    with patch("ovos_tui_client.app.load_golden", return_value=GoldenResult(steps, "test")), \
         patch("ovos_tui_client.scripts.SETTLE", 0):
        async with app.run_test() as pilot:
            hits = await _hits(SkillTestCommandProvider(app.screen), "weather")
            assert [str(h.text) for h in hits] == ["Test: Weather - All", "Test: Weather - Choose"]
            hits[0].command()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert seen == {"class": True, "title": "▶ Test: Weather - All  1/1", "disabled": True,
                            "sub_title": "▶ Test: Weather - All  1/1", "own_session": True}
            assert "1/1 passed" in _conversation(app)
            # count now known -> shown in the palette entry
            hits = await _hits(SkillTestCommandProvider(app.screen), "weather")
            assert [str(h.text) for h in hits] == ["Test: Weather - All (1)", "Test: Weather - Choose"]


@pytest.mark.asyncio
async def test_skill_without_golden_utterances_reports_it(tmp_path):
    app = _app(tmp_path)
    app.installed_skills = {WEATHER: True}
    with patch("ovos_tui_client.app.load_golden", return_value=GoldenResult([], None)):
        async with app.run_test() as pilot:
            app.start_skill_tests([WEATHER], "Test: weather")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert "nothing to test for en-us" in _conversation(app)
            app.bus.send_utterance.assert_not_called()
            # hidden from the palette afterwards
            assert await _hits(SkillTestCommandProvider(app.screen), "weather") == []


@pytest.mark.asyncio
async def test_stop_entry_only_while_running(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        hits = await _hits(ScriptCommandProvider(app.screen), "script")
        assert "Script: Stop running script" not in [str(h.text) for h in hits]
        app.script_runner = MagicMock()
        hits = await _hits(ScriptCommandProvider(app.screen), "script")
        assert [str(h.text) for h in hits] == ["Script: Stop running script"]
        assert await _hits(SkillTestCommandProvider(app.screen), "test") == []
        hits[0].command()
        app.script_runner.stop.assert_called_once()
        app.script_runner = None


# --- #32: others' utterances and other TUIs' script runs ---

from ovos_tui_client.bus import TUI_CONTEXT_KEY


@pytest.mark.asyncio
async def test_utterance_from_the_mic_shows_in_the_conversation(tmp_path):
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        app._write_heard("what time is it", {"client_name": "ovos_dinkum_listener"})
        app._write_heard("hej", {TUI_CONTEXT_KEY: {"instance": "x", "host": "laptop", "script": {"title": "T", "i": 2, "n": 5}}})
        await pilot.pause()
        text = _conversation(app)
        assert "🎤 Mic said: what time is it" in text
        assert "💻 laptop [2/5] said: hej" in text


@pytest.mark.asyncio
async def test_another_tuis_script_run_is_visible_in_conversation_and_header(tmp_path):
    app = _app(tmp_path)
    ctx = {TUI_CONTEXT_KEY: {"instance": "x", "host": "laptop"}}
    async with app.run_test() as pilot:
        app._show_remote_script_event("ovos.tui.script.started", {"title": "Test: weather", "n": 3, "lang": "da-dk"}, ctx)
        app._show_remote_script_event("ovos.tui.script.step", {"title": "Test: weather", "i": 1, "n": 3,
                                                              "status": "pass", "detail": "weather.intent"}, ctx)
        await pilot.pause()
        assert app.sub_title.startswith("⚠ laptop: Test: weather 1/3")
        app._show_remote_script_event("ovos.tui.script.finished", {
            "title": "Test: weather", "state": "finished", "summary": "2/3 passed · 1 failed · 9s", "colour": "red",
            "failures": [[3, "er det godt udenfor", "fail", "expected x, got y"]]}, ctx)
        await pilot.pause()
        text = _conversation(app)
        assert "💻 laptop ▶ started Test: weather - 3 utterance(s), lang da-dk" in text
        assert "💻 laptop [1/3] ✓ weather.intent" in text
        assert "💻 laptop ■ Test: weather finished: 2/3 passed · 1 failed · 9s" in text
        assert '[3] "er det godt udenfor" → expected x, got y' in text
        assert "⚠" not in app.sub_title


@pytest.mark.asyncio
async def test_own_script_run_is_announced_for_other_tuis(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "one.txt").write_text("hello there\n")
    app = _app(tmp_path, scripts)
    _fake_ovos(app, {"hello there": None})
    with patch("ovos_tui_client.scripts.SETTLE", 0), patch("ovos_tui_client.scripts.STEP_TIMEOUT", 0.3):
        async with app.run_test() as pilot:
            app.start_user_script(scripts / "one.txt")
            await app.workers.wait_for_complete()
            await pilot.pause()
            names = [c.args[0] for c in app.bus.emit_tui_event.call_args_list]
            assert names == ["script.started", "script.step", "script.finished"]
            assert app.bus.send_utterance.call_args.kwargs["script"] == {"title": "Script: one", "i": 1, "n": 1}
            sent_session = app.bus.send_utterance.call_args.kwargs["session_id"]
            # stopped after the step, and again when the run ends
            assert {c.args for c in app.bus.stop_session.call_args_list} == {(sent_session,)}
            assert "Sent stop to the 1 test session(s)" in _conversation(app)


@pytest.mark.asyncio
async def test_result_is_saved_as_md_and_report(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "smoke.jsonl").write_text("\n".join([
        json.dumps({"utterance": "what's the weather", "skill_id": WEATHER, "intent_label": "weather.intent"}),
        json.dumps({"utterance": "who is lincoln", "skill_id": WEATHER, "intent_label": "weather.intent"}),
    ]))
    app = _app(tmp_path, scripts)
    app.results_dir = tmp_path / "results"
    app.installed_skills = {WEATHER: True}
    _fake_ovos(app, {
        "what's the weather": f"{WEATHER}:weather.intent",
        "who is lincoln": "ovos-skill-wikipedia.openvoiceos:wiki",
    })
    with patch("ovos_tui_client.scripts.SETTLE", 0):
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            titles = [c.title for c in app.get_system_commands(app.screen)]
            assert not any(t.startswith("Test: Save result") for t in titles)
            app.start_user_script(scripts / "smoke.jsonl")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert "Test: Save result" in _conversation(app)
            cmd = [c for c in app.get_system_commands(app.screen)
                   if c.title == "Test: Save result… (Script: smoke)"]
            assert cmd
            cmd[0].callback()
            await pilot.pause()
            await pilot.press("ctrl+s")
            await pilot.pause()
            md = list((tmp_path / "results").glob("*_script-smoke.md"))
            rep = list((tmp_path / "results").glob("*_script-smoke.report.json"))
            assert len(md) == 1 and len(rep) == 1
            assert not list((tmp_path / "results").glob("*.jsonl"))
            text = md[0].read_text()
            assert "# Script: smoke" in text
            assert "1/2 passed · 1 failed" in text
            assert "## Failures" in text and '"who is lincoln"' in text
            assert "| 1 | ✓ | what's the weather | ovos-skill-weather.openvoiceos:weather.intent |" in text
            assert "- **Language:** en-us" in text
            report = json.loads(rep[0].read_text())
            assert [st["status"] for st in report["steps"]] == ["pass", "fail"]
            assert report["steps"][1]["handled_by"] == "ovos-skill-wikipedia.openvoiceos:wiki"
            assert "(the report to share)" in _conversation(app)


@pytest.mark.asyncio
async def test_shareable_report_asks_channel_notes_and_replies(tmp_path, monkeypatch):
    from textual.widgets import Checkbox, Select
    from ovos_tui_client.report_screen import ReportScreen
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "smoke.jsonl").write_text(json.dumps(
        {"utterance": "what's the weather", "skill_id": WEATHER, "intent_label": "weather.intent"}))
    app = _app(tmp_path, scripts)
    app.results_dir = tmp_path / "results"
    app.installed_skills = {WEATHER: True}
    # the channel check (network) is replaced by a settled result
    monkeypatch.setattr("ovos_tui_client.app.channel_check", lambda stack: {
        "channel": "testing", "source": "installed versions", "declared": None, "declared_source": None,
        "declared_note": None, "checked": ["stable", "testing", "alpha"], "unreachable": [],
        "problems": {}, "matches": ["testing"]})
    _fake_ovos(app, {"what's the weather": f"{WEATHER}:weather.intent"})
    with patch("ovos_tui_client.scripts.SETTLE", 0):
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            titles = [c.title for c in app.get_system_commands(app.screen)]
            assert "OVOS: Release channel (testing)" in titles
            app.start_user_script(scripts / "smoke.jsonl")
            await app.workers.wait_for_complete()
            await pilot.pause()
            cmd = [c for c in app.get_system_commands(app.screen)
                   if c.title == "Test: Save result… (Script: smoke)"]
            assert cmd
            cmd[0].callback()
            await pilot.pause()
            assert isinstance(app.screen, ReportScreen)
            assert app.screen.query_one("#report-channel", Select).value == "testing"
            assert app.screen.query_one("#report-replies", Checkbox).value is False
            app.screen.query_one("#report-notes", Input).value = "Mark II"
            await pilot.press("ctrl+s")
            await pilot.pause()
            files = list((tmp_path / "results").glob("*.report.json"))
            assert len(files) == 1
            report = json.loads(files[0].read_text())
            assert report["schema"] == "ovos-test-report/1" and report["notes"] == "Mark II"
            assert report["manifest"]["channel"] == "testing"
            assert report["manifest"]["channel_source"] == "installed versions"
            assert "replies" not in report["steps"][0]
            assert "(the report to share)" in _conversation(app)


@pytest.mark.asyncio
async def test_report_can_be_shown_and_shown_again_from_the_palette(tmp_path, monkeypatch):
    from textual.widgets import TextArea
    from ovos_tui_client.report_screen import ReportScreen, ReportViewScreen
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "smoke.jsonl").write_text(json.dumps(
        {"utterance": "what's the weather", "skill_id": WEATHER, "intent_label": "weather.intent"}))
    app = _app(tmp_path, scripts)
    app.results_dir = tmp_path / "results"
    app.installed_skills = {WEATHER: True}
    monkeypatch.setattr("ovos_tui_client.app.channel_check", lambda stack: {
        "channel": None, "source": None, "declared": None, "declared_source": None, "declared_note": None,
        "checked": [], "unreachable": ["stable", "testing", "alpha"], "problems": {}, "matches": []})
    _fake_ovos(app, {"what's the weather": f"{WEATHER}:weather.intent"})
    with patch("ovos_tui_client.scripts.SETTLE", 0):
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            titles = [c.title for c in app.get_system_commands(app.screen)]
            assert not any(t.startswith("Test: Show last result") for t in titles)
            app.start_user_script(scripts / "smoke.jsonl")
            await app.workers.wait_for_complete()
            await pilot.pause()
            app.create_report()
            await pilot.pause()
            assert isinstance(app.screen, ReportScreen)
            await pilot.press("ctrl+o")
            await pilot.pause()
            assert isinstance(app.screen, ReportViewScreen)
            text = app.screen.query_one(TextArea).text
            assert json.loads(text)["schema"] == "ovos-test-report/1"
            assert json.loads(text)["manifest"]["channel"] is None
            await pilot.press("escape")
            await pilot.pause()
            assert len(list((tmp_path / "results").glob("*.report.json"))) == 1
            assert len(list((tmp_path / "results").glob("*.md"))) == 1
            cmd = [c for c in app.get_system_commands(app.screen)
                   if c.title == "Test: Show last result (Script: smoke)"]
            assert cmd
            cmd[0].callback()
            await pilot.pause()
            assert isinstance(app.screen, ReportViewScreen)
            assert app.screen.query_one(TextArea).text == text
