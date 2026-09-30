import json

from ovos_tui_client.results import markdown_meta, report_markdown, save_result, summary_parts
from ovos_tui_client.scripts import FAIL, PASS, TIMEOUT, RunSummary, ScriptStep, StepResult

W = "ovos-skill-weather.openvoiceos"


def _summary():
    s = RunSummary(title="Test: Weather - All", total=3, duration=12.4, started_at=1790000000)
    s.results = [
        (1, ScriptStep("what's the weather", "en-us", W, "weather.intent"), StepResult(PASS, f"{W}:weather.intent")),
        (2, ScriptStep("is it | raining", "en-us", W, "weather.intent"), StepResult(FAIL, "expected x, got y")),
        (3, ScriptStep("forecast", "en-us", W, None), StepResult(TIMEOUT, "no response")),
    ]
    s.handled_by = {1: f"{W}:weather.intent", 2: "wiki", 3: "nothing matched"}
    s.replies = {1: ["It's sunny"], 2: [], 3: []}
    return s


def test_summary_parts():
    assert summary_parts(_summary()) == ["1/3 passed", "1 failed", "1 timed out", "12s"]


def test_markdown_report():
    md = report_markdown(_summary(), {"OVOS": "h:8181", "Language": "en-us"}, {W: "1.2.3"})
    assert md.startswith("# Test: Weather - All")
    assert "- **Result:** finished: 1/3 passed · 1 failed · 1 timed out · 12s" in md
    assert "- **OVOS:** h:8181" in md
    assert '- ✗ [2] "is it | raining" → expected x, got y' in md
    assert "| 2 | ✗ | is it \\| raining |" in md  # pipes escaped in the table
    assert "It's sunny" in md
    assert f"- {W} 1.2.3" in md


def test_save_writes_md_and_report_side_by_side(tmp_path):
    md1, r1 = save_result(_summary(), {}, directory=tmp_path, report_text='{"schema": "x"}\n')
    md2, r2 = save_result(_summary(), {}, directory=tmp_path, report_text='{"schema": "x"}\n')
    assert md1 != md2 and md1.exists() and md2.exists() and r1.exists() and r2.exists()
    assert r1.name == md1.name[:-3] + ".report.json"
    assert md1.name.endswith("_test-weather-all.md")
    assert json.loads(r1.read_text()) == {"schema": "x"}
    assert not list(tmp_path.glob("*.jsonl"))
    md3, r3 = save_result(_summary(), {}, directory=tmp_path)
    assert md3.exists() and r3 is None


def test_markdown_meta_from_the_manifest():
    meta, versions = markdown_meta({"bus": "local", "channel": "testing", "channel_source": "installed versions",
                                    "lang": "en-us", "tool": "ovos-tui-client 9",
                                    "skills": {"a.b": {"version": "1.0"}}})
    assert meta["Channel"] == "testing (installed versions)" and meta["OVOS"] == "local"
    assert versions == {"a.b": "1.0"}
    assert markdown_meta({})[0]["Channel"] == "unknown"
