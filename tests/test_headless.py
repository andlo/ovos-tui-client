"""Headless test runs (#51): `ovos-tui --run ...`. The bus is faked: it
answers the skill list and 'routes' each utterance by feeding the
running ScriptRunner what real OVOS would emit."""
import json
from argparse import Namespace
from io import StringIO
from unittest.mock import patch

import pytest

from ovos_tui_client import headless, manifest as manifest_mod, report as report_mod
from ovos_tui_client.headless import EXIT_CANNOT_RUN, EXIT_FAILED, EXIT_OK, run_headless
from ovos_tui_client.scripts import GoldenResult, ScriptStep

WEATHER = "ovos-skill-weather.openvoiceos"
WIKI = "ovos-skill-wikipedia.openvoiceos"


class FakeBus:
    """OVOSBusConnection's interface, with OVOS behaviour scripted."""

    def __init__(self, host="127.0.0.1", port=8181, lang="en-us", skills=None, routes=None,
                 replies=None, answer_skill_list=True):
        self.host, self.port, self.lang = host, port, lang
        self.skills = {WEATHER: True, WIKI: True} if skills is None else skills
        self.routes = routes or {}
        self.replies = replies or {}
        self.answer_skill_list = answer_skill_list
        self.handlers = []
        self.sent = []
        self.stopped = []

    def connect(self):
        pass

    def list_skills(self, callback, timeout=5, timer_factory=None):
        callback(dict(self.skills) if self.answer_skill_list else None)

    def on_message(self, handler):
        self.handlers.append(handler)

    def stop_session(self, session_id):
        self.stopped.append(session_id)

    def send_utterance(self, text, lang=None, session_id=None, script=None):
        self.sent.append((text, session_id))
        ctx = {"session": {"session_id": session_id}}
        for handler in self.handlers:
            if self.routes.get(text):
                handler(self.routes[text], {}, ctx)
            for reply in self.replies.get(text, []):
                handler("speak", {"utterance": reply}, ctx)
            handler("ovos.utterance.handled", {}, ctx)


def _args(**kw):
    base = dict(run="all", host="127.0.0.1", port=8181, lang="en-us", golden_dir=[], output=None,
                report=None, report_replies=False, channel=None, notes=None, submit_url=None,
                mycroft_conf=None)
    base.update(kw)
    return Namespace(**base)


def _golden(steps_by_skill):
    def loader(skill_id, lang, golden_dirs=()):
        steps = steps_by_skill.get(skill_id, [])
        return GoldenResult(steps, "test" if steps else None)
    return loader


STEPS = {
    WEATHER: [ScriptStep(utterance="what's the weather", skill_id=WEATHER, intent_label="weather.intent", lang="en-us")],
    WIKI: [ScriptStep(utterance="who is lincoln", skill_id=WIKI, intent_label="wiki.intent", lang="en-us")],
}


@pytest.fixture(autouse=True)
def _fast(monkeypatch, tmp_path):
    for name in ("SETTLE", "LATE_HANDLED_WAIT", "QUIET_AFTER_MATCH", "STOP_WAIT", "PROVIDER_WAIT"):
        monkeypatch.setattr(f"ovos_tui_client.scripts.{name}", 0)
    monkeypatch.setattr("ovos_tui_client.scripts.STEP_TIMEOUT", 1)
    monkeypatch.setattr(headless, "load_golden", _golden(STEPS))
    monkeypatch.setattr(headless, "SKILL_LIST_RETRY_DELAYS", (0, 0))
    monkeypatch.setattr(manifest_mod, "INSTALLER_STATE_FILE", tmp_path / "no-installer.json")


def _run(bus, tmp_path, **kw):
    out, err = StringIO(), StringIO()
    kw.setdefault("output", str(tmp_path / "results"))
    code = run_headless(_args(**kw), bus_factory=lambda **_: bus, out=out, err=err, config={},
                        tool_version="9.9.9")
    return code, out.getvalue(), err.getvalue()


def test_all_pass_exit_0_and_files_written(tmp_path):
    bus = FakeBus(routes={"what's the weather": f"{WEATHER}:weather.intent",
                          "who is lincoln": f"{WIKI}:wiki.intent"})
    code, out, _ = _run(bus, tmp_path)
    assert code == EXIT_OK
    assert "✓ \"what's the weather\"" in out and "2/2 passed" in out
    files = sorted(p.name for p in (tmp_path / "results").iterdir())
    assert any(f.endswith(".md") for f in files)
    assert any(f.endswith(".jsonl") for f in files)
    assert any(f.endswith(".manifest.json") for f in files)
    # each step ran in its own session, and that session was stopped
    assert len({sid for _, sid in bus.sent}) == 2
    assert set(bus.stopped) >= {sid for _, sid in bus.sent}


def test_a_misrouted_step_exits_1(tmp_path):
    bus = FakeBus(routes={"what's the weather": f"{WIKI}:wiki.intent",
                          "who is lincoln": f"{WIKI}:wiki.intent"})
    code, out, _ = _run(bus, tmp_path)
    assert code == EXIT_FAILED
    assert "✗" in out


def test_no_skill_list_exits_2(tmp_path):
    code, out, _ = _run(FakeBus(answer_skill_list=False), tmp_path)
    assert code == EXIT_CANNOT_RUN
    assert "never answered the skill list" in out


def test_unknown_target_exits_2(tmp_path):
    code, out, _ = _run(FakeBus(), tmp_path, run="ovos-skill-nope.someone")
    assert code == EXIT_CANNOT_RUN
    assert "not an installed skill" in out


def test_single_skill_and_script_targets(tmp_path):
    bus = FakeBus(routes={"what's the weather": f"{WEATHER}:weather.intent"})
    code, out, _ = _run(bus, tmp_path, run=WEATHER)
    assert code == EXIT_OK and [t for t, _ in bus.sent] == ["what's the weather"]

    script = tmp_path / "smoke.jsonl"
    script.write_text(json.dumps({"golden": WIKI}) + "\nhello there\n")
    bus = FakeBus(routes={"who is lincoln": f"{WIKI}:wiki.intent"})
    code, out, _ = _run(bus, tmp_path, run=str(script))
    assert [t for t, _ in bus.sent] == ["who is lincoln", "hello there"]
    assert code == EXIT_OK  # "hello there" has no expectation: sent, not checked


def test_report_to_stdout_keeps_progress_on_stderr(tmp_path):
    bus = FakeBus(routes={"what's the weather": f"{WEATHER}:weather.intent",
                          "who is lincoln": f"{WIKI}:wiki.intent"},
                  replies={"what's the weather": ["It's 14 degrees in Kvistgård"]})
    code, out, err = _run(bus, tmp_path, report="-", notes="API key set")
    report = json.loads(out)  # stdout is ONLY the report
    assert report["schema"] == "ovos-test-report/1"
    assert report["summary"]["passed"] == 2 and report["summary"]["answered"] == 1
    assert report["notes"] == "API key set"
    assert "2/2 passed" in err
    step = report["steps"][0]
    assert step["expected"] == f"{WEATHER}:weather.intent" and step["answered"] is True
    # replies are private by default
    assert "Kvistgård" not in out and "replies" not in step


def test_report_replies_only_on_request(tmp_path):
    bus = FakeBus(routes={"what's the weather": f"{WEATHER}:weather.intent"},
                  replies={"what's the weather": ["Sunny"]})
    report_file = tmp_path / "r.json"
    _run(bus, tmp_path, run=WEATHER, report=str(report_file), report_replies=True)
    assert json.loads(report_file.read_text())["steps"][0]["replies"] == ["Sunny"]


def test_manifest_has_no_private_data(tmp_path, monkeypatch):
    monkeypatch.setattr(manifest_mod, "installer_channel", lambda *a, **k: "alpha")
    bus = FakeBus(routes={"what's the weather": f"{WEATHER}:weather.intent"})
    _, out, _ = _run(bus, tmp_path, run=WEATHER, report="-")
    m = json.loads(out)["manifest"]
    assert m["channel"] == "alpha" and m["channel_source"] == "ovos-installer"
    assert m["bus"] == "local"
    import socket
    text = json.dumps(m)
    assert socket.gethostname() not in text
    assert str(tmp_path.home()) not in text


def test_remote_bus_reports_versions_unavailable(tmp_path):
    bus = FakeBus(routes={"what's the weather": f"{WEATHER}:weather.intent"})
    _, out, err = _run(bus, tmp_path, run=WEATHER, report="-", host="192.0.2.10")
    m = json.loads(out)["manifest"]
    assert m["bus"] == "remote" and m["versions_from"] == "unavailable"
    assert m["stack"] == {} and m["channel"] is None
    assert "192.0.2.10" not in out
    assert "could not be read" in err


def test_channel_argument_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(manifest_mod, "installer_channel", lambda *a, **k: "alpha")
    bus = FakeBus(routes={"what's the weather": f"{WEATHER}:weather.intent"})
    _, out, _ = _run(bus, tmp_path, run=WEATHER, report="-", channel="testing")
    m = json.loads(out)["manifest"]
    assert m["channel"] == "testing" and m["channel_source"] == "argument"


def test_submit_url_is_filled_and_store_agnostic(tmp_path):
    bus = FakeBus(routes={"what's the weather": f"{WEATHER}:weather.intent"})
    _, _, err = _run(bus, tmp_path, run=WEATHER, report="-", channel="testing",
                     submit_url="https://example.org/submit?skill={skill_id}&ch={channel}&r={report}")
    line = [l for l in err.splitlines() if l.startswith("Submit it here: ")][0]
    assert f"skill={WEATHER}" in line and "ch=testing" in line and "r=%7B" in line


def test_submit_url_too_long_says_paste(tmp_path):
    report = {"schema": "ovos-test-report/1", "manifest": {}, "steps": [{"u": "x" * 9000}]}
    assert report_mod.submit_url("https://example.org/?r={report}", report) is None


def test_installer_channel_reads_the_state_file(tmp_path):
    state = tmp_path / "installer.json"
    state.write_text(json.dumps({"channel": "testing", "profile": "ovos"}))
    assert manifest_mod.installer_channel(state) == "testing"
    state.write_text("not json")
    assert manifest_mod.installer_channel(state) is None
    assert manifest_mod.installer_channel(tmp_path / "missing.json") is None


def test_cli_dispatches_to_headless():
    from ovos_tui_client.app import build_arg_parser
    args = build_arg_parser().parse_args(["--run", "all", "--report", "-", "--channel", "alpha"])
    assert args.run == "all" and args.report == "-" and args.channel == "alpha"
    with patch("ovos_tui_client.headless.run_headless", return_value=0) as rh, \
            patch("sys.argv", ["ovos-tui", "--run", "all"]):
        from ovos_tui_client.app import run
        with pytest.raises(SystemExit) as exc:
            run()
    assert exc.value.code == 0 and rh.called
