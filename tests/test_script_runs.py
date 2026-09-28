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
    def send(text, lang=None, session_id=None):
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
            assert "✓ weather.intent" in text
            assert "✗ expected ovos-skill-weather.openvoiceos:weather.intent, got ovos-skill-wikipedia" in text
            assert "■ Script: smoke finished: 1/2 passed · 1 failed · 1 sent without a check" in text
            assert '[2] "who is lincoln"' in text
            # running state cleared again
            assert app.script_runner is None
            assert not app.query_one("#utterance-input", Input).disabled
            assert not app.query_one("#conversation", RichLog).has_class("script-running")
            assert app.sub_title == ""


@pytest.mark.asyncio
async def test_running_state_is_visible_while_a_step_is_in_flight(tmp_path):
    app = _app(tmp_path)
    app.installed_skills = {WEATHER: True}
    seen = {}

    def send(text, lang=None, session_id=None):
        conv = app.query_one("#conversation", RichLog)
        seen["class"] = conv.has_class("script-running")
        seen["title"] = str(conv.border_title)
        seen["disabled"] = app.query_one("#utterance-input", Input).disabled
        seen["sub_title"] = app.sub_title
        seen["own_session"] = bool(session_id and session_id.startswith("ovos-tui-test-"))
        app.script_runner.feed(f"{WEATHER}:weather.intent")
        app.script_runner.feed("ovos.utterance.handled")
    app.bus.send_utterance.side_effect = send

    steps = [ScriptStep("what's the weather", "en-us", WEATHER, "weather.intent")]
    with patch("ovos_tui_client.app.load_golden", return_value=GoldenResult(steps, "test")), \
         patch("ovos_tui_client.scripts.SETTLE", 0):
        async with app.run_test() as pilot:
            hits = await _hits(SkillTestCommandProvider(app.screen), "weather")
            assert [str(h.text) for h in hits] == ["Test: weather"]
            hits[0].command()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert seen == {"class": True, "title": "▶ Test: weather  1/1", "disabled": True,
                            "sub_title": "▶ Test: weather  1/1", "own_session": True}
            assert "1/1 passed" in _conversation(app)
            # count now known -> shown in the palette entry
            hits = await _hits(SkillTestCommandProvider(app.screen), "weather")
            assert [str(h.text) for h in hits] == ["Test: weather (1)"]


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
