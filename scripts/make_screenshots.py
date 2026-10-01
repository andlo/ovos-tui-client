#!/usr/bin/env python3
"""Regenerates the manual's screenshots (docs/images/*.svg).

Every screenshot is a *scene*: the real app, run headless (Textual's own
test harness), with a fake message bus and fixed fixture data instead of a
live OVOS - so the images are reproducible, contain no personal data, and
can be regenerated in one command whenever the UI changes:

    python scripts/make_screenshots.py              # all scenes
    python scripts/make_screenshots.py about-skill  # just one (or several)
    python scripts/make_screenshots.py --list       # scene names

Writes SVG (Textual's own screenshot format): crisp at any zoom, small,
and rendered directly by GitHub and by the MkDocs site.

Adding a scene: write an `async def scene_<name>(app, pilot)` below that
drives the app into the state you want to show, and register it in
SCENES. See docs/maintaining.md.
"""
import argparse
import asyncio
import json
import sys
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from textual.widgets import Input  # noqa: E402

import ovos_tui_client.state as state_module  # noqa: E402
from ovos_tui_client.app import OVOSTUIApp  # noqa: E402
from ovos_tui_client.bus import TUI_CONTEXT_KEY  # noqa: E402
from ovos_tui_client.scripts import GoldenResult, RunSummary, ScriptStep  # noqa: E402

OUT_DIR = REPO / "docs" / "images"
SIZE = (120, 34)
TITLE = "ovos-tui-client"
DISPLAY_LOG_DIR = "~/.local/state/mycroft"
VERSION = "0.2.0"

# ---------------------------------------------------------------------------
# Fixture data - realistic, fixed, nothing personal
# ---------------------------------------------------------------------------

WEATHER = "ovos-skill-weather.openvoiceos"
DATETIME = "ovos-skill-date-time.openvoiceos"
WIKI = "ovos-skill-wikipedia.openvoiceos"

SKILLS = {
    "ovos-skill-alerts.openvoiceos": True,
    DATETIME: True,
    "ovos-skill-hello-world.openvoiceos": True,
    "ovos-skill-naptime.openvoiceos": False,
    "ovos-skill-news.openvoiceos": True,
    "ovos-skill-parrot.openvoiceos": True,
    "ovos-skill-personal.openvoiceos": True,
    "ovos-skill-somafm.openvoiceos": False,
    "ovos-skill-volume.openvoiceos": True,
    WEATHER: True,
    WIKI: True,
    "ovos-skill-wordnet.openvoiceos": True,
}

VERSIONS = {WEATHER: "1.0.6", DATETIME: "1.1.2", WIKI: "0.9.3"}

WEATHER_JSON = {
    "name": "Weather",
    "description": "Get weather conditions, forecasts, expected precipitation and more! "
                   "You can also ask for other cities around the world.",
    "examples": ["what's the weather like", "is it going to rain tomorrow",
                 "what is the temperature in London", "when is the sunset today",
                 "is it windy right now"],
    "tags": ["weather", "forecast", "rain", "temperature"],
    "source": "https://github.com/OpenVoiceOS/ovos-skill-weather",
}

GOLDEN = [ScriptStep(u, "en-US", WEATHER, i) for u, i in [
    ("what's the weather like", "weather.intent"),
    ("what's the weather in Paris", "weather.intent"),
    ("can you tell me the weather", "weather.intent"),
    ("how hot is it", "temperature.intent"),
    ("what is the temperature outside", "temperature.intent"),
    ("is it going to rain tomorrow", "next_rain.intent"),
    ("when will it rain next", "next_rain.intent"),
    ("is it windy right now", "is_wind.intent"),
    ("when is the sunset today", "sunset.intent"),
    ("what time is sunrise", "sunrise.intent"),
]]
GOLDEN_SOURCE = ("https://raw.githubusercontent.com/OpenVoiceOS/ovos-skill-weather/"
                 "HEAD/test/end2end/golden_utterances_en-US.jsonl")

# what the fake OVOS "does" with each utterance: (dispatched intent, reply)
ROUTES = {
    "what's the weather like": (f"{WEATHER}:weather.intent", "It's 14 degrees and partly cloudy."),
    "what's the weather in Paris": (f"{WEATHER}:weather.intent", "In Paris it's 17 degrees and sunny."),
    "can you tell me the weather": (f"{WIKI}:wiki", "Weather is the state of the atmosphere..."),
    "how hot is it": (f"{WEATHER}:temperature.intent", "It's 14 degrees right now."),
}

LOG_LINES = {
    "skills": [
        "2026-09-28 10:02:14.101 - skills - ovos_core.intent_services.service:handle_utterance:450 - INFO - Parsing utterance: ['what time is it']",
        "2026-09-28 10:02:14.388 - skills - ovos_core.intent_services.service:handle_utterance:471 - INFO - ovos-padatious-pipeline-plugin-high match (en-US): ovos-skill-date-time.openvoiceos:what.time.is.it.intent",
        "2026-09-28 10:02:14.402 - ovos-skill-date-time.openvoiceos - INFO - handling what time is it",
        "2026-09-28 10:02:31.720 - skills - ovos_core.intent_services.service:handle_utterance:450 - INFO - Parsing utterance: ['tell me about the eiffel tower']",
        "2026-09-28 10:02:32.015 - skills - ovos_core.intent_services.common_query_service:handle_question:210 - INFO - Searching for common query answers",
        "2026-09-28 10:02:33.410 - ovos-skill-wikipedia.openvoiceos - INFO - Found answer for 'eiffel tower'",
    ],
    "audio": [
        "2026-09-28 10:02:14.690 - audio - ovos_audio.service:handle_speak:318 - INFO - Speak: It's two minutes past ten",
        "2026-09-28 10:02:33.902 - audio - ovos_audio.service:handle_speak:318 - INFO - Speak: The Eiffel Tower is a wrought-iron lattice tower in Paris.",
    ],
    "bus": [
        "2026-09-28 10:02:14.100 - bus - ovos_messagebus.event_handler:on_message:74 - DEBUG - recognizer_loop:utterance",
    ],
}

# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _fake_bus(app):
    bus = MagicMock()
    bus.lang = "en-us"
    bus.instance_id = "screens"
    bus.host = "workstation"

    def list_skills(callback, **kw):
        threading.Thread(target=callback, args=(dict(SKILLS),), daemon=True).start()
    bus.list_skills.side_effect = list_skills
    return bus


def _reply_later(app, text):
    """Pretend to be OVOS for a scripted step: speak, then dispatch + end."""
    intent, reply = ROUTES.get(text, (None, None))
    runner = app.script_runner

    def run():
        time.sleep(0.05)
        if reply:
            app._handle_speak(reply)
        if runner is not None:
            if intent:
                runner.feed(intent)
            runner.feed("ovos.utterance.handled")
    threading.Thread(target=run, daemon=True).start()


async def _settle(pilot, seconds=0.3):
    await pilot.pause()
    await asyncio.sleep(seconds)
    await pilot.pause()


async def _conversation(app, pilot):
    """A normal exchange, the way it looks after typing a few things."""
    for you, ovos in [("what time is it", "It's two minutes past ten."),
                      ("tell me about the eiffel tower",
                       "The Eiffel Tower is a wrought-iron lattice tower in Paris.")]:
        app._write_conversation(f"[green]You: {you}[/green]")
        app._write_conversation(f"[blue]OVOS: {ovos}[/blue]")
    for line in ['→ heard: "what time is it"',
                 "▶ ovos-skill-date-time.openvoiceos is handling this",
                 "🔊 speaking...", "🔇 done speaking",
                 '→ heard: "tell me about the eiffel tower"',
                 '🔍 asking all skills: "tell me about the eiffel tower"',
                 '📥 ovos-skill-wikipedia.openvoiceos: "The Eiffel Tower is..."',
                 "✗ ovos-skill-wordnet.openvoiceos: no answer",
                 "🏆 ovos-skill-wikipedia.openvoiceos selected to answer"]:
        app._write_activity(line)


# ---------------------------------------------------------------------------
# Scenes
# ---------------------------------------------------------------------------

async def scene_overview(app, pilot):
    await _conversation(app, pilot)
    await _settle(pilot, 0.8)


async def scene_palette(app, pilot):
    await _conversation(app, pilot)
    app.action_command_palette()
    await _settle(pilot)
    await pilot.press(*"test weather")
    await _settle(pilot, 0.5)


async def scene_help(app, pilot):
    await _conversation(app, pilot)
    app.action_toggle_help_panel()
    await _settle(pilot)


async def _start_weather_run(app, pilot, steps):
    app.bus.send_utterance.side_effect = lambda text, **kw: _reply_later(app, text)
    with patch("ovos_tui_client.app.load_golden", return_value=GoldenResult(steps, GOLDEN_SOURCE)):
        app.start_skill_tests([WEATHER], "Test: Weather - All")
        await _settle(pilot, 0.2)


async def scene_test_running(app, pilot):
    steps = GOLDEN[:4]

    def send(text, **kw):
        if text == steps[2].utterance:      # hold step 3 open for the picture
            return
        _reply_later(app, text)
    with patch("ovos_tui_client.scripts.QUIET_AFTER_MATCH", 0.3), \
         patch("ovos_tui_client.scripts.SETTLE", 0.05):
        app.bus.send_utterance.side_effect = send
        with patch("ovos_tui_client.app.load_golden", return_value=GoldenResult(steps, GOLDEN_SOURCE)):
            app.start_skill_tests([WEATHER], "Test: Weather - All")
            for _ in range(60):
                await asyncio.sleep(0.1)
                if app.script_runner and app.script_runner.current == 3:
                    break
            await _settle(pilot, 0.3)


async def scene_test_finished(app, pilot):
    # a real wall-clock duration would differ from run to run
    fixed = property(lambda self: 23.0, lambda self, value: None)
    with patch.object(RunSummary, "duration", fixed), patch("ovos_tui_client.scripts.QUIET_AFTER_MATCH", 0.3), \
         patch("ovos_tui_client.scripts.SETTLE", 0.05):
        await _start_weather_run(app, pilot, GOLDEN[:4])
        await app.workers.wait_for_complete()
    await _settle(pilot, 0.3)


async def scene_picker(app, pilot):
    with patch("ovos_tui_client.app.load_golden", return_value=GoldenResult(GOLDEN, GOLDEN_SOURCE)):
        app.choose_skill_tests(WEATHER)
        await app.workers.wait_for_complete()
        await _settle(pilot)
        await pilot.press("space", "down", "down", "down", "down", "space")
        await _settle(pilot)


async def scene_about_skill(app, pilot):
    with patch("ovos_tui_client.app.find_skill_json", return_value=WEATHER_JSON), \
         patch("ovos_tui_client.app.find_repo_url", return_value=WEATHER_JSON["source"]), \
         patch("ovos_tui_client.app.load_golden", return_value=GoldenResult(GOLDEN, GOLDEN_SOURCE)):
        app.show_skill_about(WEATHER)
        await app.workers.wait_for_complete()
        await _settle(pilot, 0.5)


async def scene_skills(app, pilot):
    app.show_installed_skills()
    await _settle(pilot)
    await pilot.press("down", "down", "down")
    await _settle(pilot)


async def scene_about_tui(app, pilot):
    # show the usual paths, not this machine's temp folder or $HOME: the
    # window wraps its text by length, so a longer path elsewhere (a CI
    # runner) would change the picture even after replacing the text
    real = app.log_dir, app.scripts_dir
    app.log_dir, app.scripts_dir = DISPLAY_LOG_DIR, "~/.config/ovos-tui-client/scripts"
    app.show_tui_about()
    app.log_dir, app.scripts_dir = real
    await _settle(pilot, 0.5)


async def scene_others(app, pilot):
    app._write_conversation("[green]You: what time is it[/green]")
    app._write_conversation("[blue]OVOS: It's two minutes past ten.[/blue]")
    app._write_heard("turn up the volume", {"client_name": "ovos_dinkum_listener", "source": "audio"})
    app._write_conversation("[blue]OVOS: Volume set to 70 percent.[/blue]")
    app._write_heard("what's on the news", {"source": "kitchen-satellite",
                                             "session": {"session_id": "kitchen"}})
    laptop = {TUI_CONTEXT_KEY: {"instance": "a1b2", "host": "laptop"}}
    app._show_remote_script_event("ovos.tui.script.started",
                                  {"title": "Test: Weather - All", "n": 10, "lang": "en-us"}, laptop)
    app._write_heard("what's the weather like", {TUI_CONTEXT_KEY: {
        "instance": "a1b2", "host": "laptop", "script": {"title": "Test: Weather - All", "i": 1, "n": 10}}})
    app._show_remote_script_event("ovos.tui.script.step", {"title": "Test: Weather - All", "i": 1, "n": 10,
                                                           "status": "pass", "detail": "weather.intent"}, laptop)
    await _settle(pilot)


async def scene_report_save(app, pilot):
    """'Test: Save result…' after a run, with the channel already found."""
    fixed = property(lambda self: 23.0, lambda self, value: None)
    with patch.object(RunSummary, "duration", fixed), patch("ovos_tui_client.scripts.QUIET_AFTER_MATCH", 0.3), \
         patch("ovos_tui_client.scripts.SETTLE", 0.05):
        await _start_weather_run(app, pilot, GOLDEN[:4])
        await app.workers.wait_for_complete()
    app.channel_result = {"channel": "testing", "source": "ovos-installer", "checked": True}
    app._channel_checked = True
    app.create_report()
    await _settle(pilot, 0.3)
    app.screen.query_one("#report-notes", Input).value = "Raspberry Pi 5 with ReSpeaker"
    await _settle(pilot)


async def scene_store_link(app, pilot):
    """Asked before the first share while no store link is set."""
    from ovos_tui_client.report_screen import SubmitUrlScreen
    app.push_screen(SubmitUrlScreen())
    await _settle(pilot, 0.3)
    app.screen.query_one("#submit-url", Input).value = (
        "https://andlo.github.io/ovos-klondike-mercantile/detail.html?skill={skill_id}#report={report_fragment}")
    await _settle(pilot)


async def scene_report_view(app, pilot):
    """The report window: Copy, Share, Close."""
    from ovos_tui_client.report_screen import ReportViewScreen
    text = json.dumps(DEMO_REPORT, indent=2) + "\n"
    app.push_screen(ReportViewScreen("Test: Weather - All", text,
                                     "~/.local/share/ovos-tui-client/results/2026-10-01_101500_test-weather-all.report.json"))
    await _settle(pilot, 0.3)


async def scene_share(app, pilot):
    """'Save and share': the report on a short link, in a window."""
    import ovos_tui_client.headless as headless_mod
    import ovos_tui_client.share as share_mod

    class DemoShare:
        def __init__(self, *a, **k):
            pass

        def start(self):
            return "http://192.168.1.50:41733/q3v9XcA2Lk0e/"

        def stop(self):
            pass

    path = "~/.local/share/ovos-tui-client/results/2026-10-01_101500_test-weather-all.report.json"
    app.last_report = {"title": "Test: Weather - All", "text": json.dumps(DEMO_REPORT), "path": None}
    with patch.object(share_mod, "ReportShare", DemoShare), \
         patch.object(share_mod, "scp_hint", lambda p, address=None: f"scp ovos@192.168.1.50:{path} ."), \
         patch.object(headless_mod, "load_config", lambda *a, **k: {"submit_url": "https://store.example/r#report={report_fragment}"}):
        app.last_report["path"] = path
        app.share_last_report()
        await _settle(pilot, 0.3)


SCENES = {
    "overview": scene_overview,
    "palette": scene_palette,
    "help": scene_help,
    "test-running": scene_test_running,
    "test-finished": scene_test_finished,
    "picker": scene_picker,
    "about-skill": scene_about_skill,
    "skills": scene_skills,
    "about-tui": scene_about_tui,
    "others": scene_others,
    "report-save": scene_report_save,
    "store-link": scene_store_link,
    "report-view": scene_report_view,
    "share": scene_share,
}

# A short, believable report for the report window (not a real run)
DEMO_REPORT = {
    "schema": "ovos-test-report/1",
    "title": "Test: Weather - All",
    "manifest": {
        "channel": "testing", "channel_source": "ovos-installer", "bus": "local", "lang": "en-us",
        "stack": {"ovos-core": "2.1.1", "ovos-workshop": "7.0.6", "ovos-padatious": "1.4.3"},
        "skills": {WEATHER: {"package": "ovos-skill-weather", "version": "1.0.6"}},
        "installed_skills": 60,
        "machine": {"arch": "aarch64", "model": "Raspberry Pi 5 Model B Rev 1.0"},
        "tool": "ovos-tui-client 0.2.0",
    },
    "summary": {"steps": 4, "checked": 4, "passed": 3, "failed": 1, "timed_out": 0, "answered": 4},
    "steps": [
        {"i": 1, "utterance": "what's the weather like", "lang": "en-us",
         "expected": f"{WEATHER}:current_weather.intent", "status": "pass", "answered": True},
    ],
    "notes": "Raspberry Pi 5 with ReSpeaker",
}


async def render(name, scene, out_dir: Path):
    work = Path(tempfile.mkdtemp(prefix="ovos-tui-screens-"))
    logs = work / "logs"
    logs.mkdir()
    for source in LOG_LINES:
        (logs / f"{source}.log").write_text("")
    with patch.object(state_module, "STATE_FILE", work / "state.json"), \
         patch("ovos_tui_client.app._ovos_tui_version", return_value=VERSION), \
         patch("ovos_tui_client.app.discover_services_with_state",
               return_value=[("ovos-messagebus.service", True), ("ovos-core.service", True),
                             ("ovos-audio.service", True), ("ovos-phal.service", True)]), \
         patch("ovos_tui_client.app.detect_container_runtime", return_value=[]), \
         patch("ovos_tui_client.app.find_skill_distribution",
               side_effect=lambda sid: (sid.split(".")[0], VERSIONS.get(sid, "1.0.0"))), \
         patch("ovos_tui_client.app.SCRIPTS_DIR", work / "scripts"), \
         patch("ovos_tui_client.app.GOLDEN_CACHE_DIR", Path("~/.cache/ovos-tui-client/golden")):
        app = OVOSTUIApp(log_dir_override=str(logs), lang="en-us", scripts_dir="~/.config/ovos-tui-client/scripts")
        app.bus = _fake_bus(app)
        async with app.run_test(size=SIZE) as pilot:
            for inp in app.query(Input):              # a blinking cursor makes pictures flaky
                inp.cursor_blink = False
            await _settle(pilot, 0.6)                 # startup lines, skill list
            for source, lines in LOG_LINES.items():   # logs arrive via the normal poller
                with open(logs / f"{source}.log", "a") as f:
                    f.write("\n".join(lines) + "\n")
            await _settle(pilot, 0.7)
            await scene(app, pilot)
            # windows opened by the scene (palette, pickers) have inputs of
            # their own - stop every cursor blinking so it's always drawn
            for screen in app.screen_stack:
                for inp in screen.query(Input):
                    inp.cursor_blink = False
            await _settle(pilot, 0.2)
            svg = app.export_screenshot(title=TITLE)
            # the temp folder name would change on every run
            svg = svg.replace(str(logs), DISPLAY_LOG_DIR)
            svg = svg.replace(str(Path.home()) + "/", "~/")   # same on every machine
            # ...and so would the CSS id Rich derives from the content hash
            svg = re.sub(r"terminal-\d+", f"terminal-{name}", svg)
            (out_dir / f"{name}.svg").write_text(svg)
            if app.script_runner is not None:
                app.script_runner.stop()
    shutil.rmtree(work, ignore_errors=True)
    print(f"  {name}.svg")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("scenes", nargs="*", help="scene names (default: all)")
    parser.add_argument("--out", default=str(OUT_DIR), help="output folder (default: docs/images)")
    parser.add_argument("--list", action="store_true", help="list scene names and exit")
    args = parser.parse_args(argv)
    if args.list:
        print("\n".join(SCENES))
        return 0
    unknown = [s for s in args.scenes if s not in SCENES]
    if unknown:
        parser.error(f"unknown scene(s): {', '.join(unknown)} - see --list")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Writing screenshots to {out_dir}:")
    for name in args.scenes or SCENES:
        asyncio.run(render(name, SCENES[name], out_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
