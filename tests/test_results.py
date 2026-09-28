import json

from ovos_tui_client.results import report_markdown, report_rows, save_result, summary_parts
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


def test_rows_and_save(tmp_path):
    rows = [json.loads(line) for line in report_rows(_summary()).splitlines()]
    assert [r["status"] for r in rows] == ["pass", "fail", "timeout"]
    assert rows[0]["replies"] == ["It's sunny"]
    md1, j1 = save_result(_summary(), {}, directory=tmp_path)
    md2, j2 = save_result(_summary(), {}, directory=tmp_path)
    assert md1 != md2 and md1.exists() and md2.exists() and j1.exists() and j2.exists()
    assert md1.name.endswith("_test-weather-all.md")
