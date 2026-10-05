"""#49: comparing two test results step by step, and what each step records
for it (stage, slots, padatious' score)."""
import json

from ovos_tui_client import compare as cmp
from ovos_tui_client.scripts import StepObservation, clean_slots

DT = "ovos-skill-date-time.openvoiceos"


# --- what a step records ---------------------------------------------------------

def test_ovos_core_3_matched_message_gives_stage_and_slots():
    obs = StepObservation(session_id="s1")
    obs.observe({"type": "ovos.intent.matched", "context": {"session": {"session_id": "s1"}},
                 "data": {"skill_id": DT, "intent_name": f"{DT}:what_time_is_it",
                          "slots": {"location": "paris", f"{DT}:location": "paris"},
                          "pipeline_id": "ovos-padatious-pipeline-plugin"}}, {DT})
    assert obs.stage == "ovos-padatious-pipeline-plugin" and obs.slots == {"location": "paris"}


def test_ovos_core_2_slots_come_from_the_dispatched_intent():
    obs = StepObservation(session_id="s1")
    obs.observe({"type": f"{DT}:what.time.is.it.intent", "context": {"session": {"session_id": "s1"}},
                 "data": {"utterances": ["what time is it in paris"], "lang": "en-US",
                          "location": "paris", "utterance": "what time is it in paris"}}, {DT})
    assert obs.stage is None and obs.slots == {"location": "paris"}


def test_other_sessions_dont_count():
    obs = StepObservation(session_id="s1")
    obs.observe({"type": "ovos.intent.matched", "context": {"session": {"session_id": "other"}},
                 "data": {"slots": {"x": "1"}, "pipeline_id": "p"}}, set())
    assert obs.stage is None and obs.slots == {}


def test_clean_slots_drops_skill_prefixed_and_structured_values():
    assert clean_slots({"b": 2, "a": "x", "s:a": "x", "typed": {"k": 1}, "n": None}) == {"a": "x", "b": "2"}


def test_padatious_conf_only_for_padatious_and_the_same_intent():
    from ovos_tui_client.diagnose import padatious_conf
    from ovos_tui_client.scripts import ScriptStep
    step = ScriptStep(utterance="what time is it", skill_id=DT, intent_label="what_time_is_it", lang="en-US")
    obs = StepObservation()
    obs.add_intent(f"{DT}:what_time_is_it")
    obs.stage = "ovos-padatious-pipeline-plugin"
    req = lambda *a: {"intent": {"name": f"{DT}:what_time_is_it", "conf": 0.97}}
    assert padatious_conf(step, obs, req) == 0.97
    obs.stage = "ovos-m2v-pipeline"
    assert padatious_conf(step, obs, req) is None
    obs.stage = None   # ovos-core 2.x: tried, kept only for the same intent
    assert padatious_conf(step, obs, lambda *a: {"intent": {"name": "x:other", "conf": 1.0}}) is None


def test_report_rows_carry_the_match():
    from ovos_tui_client.report import build_report
    from ovos_tui_client.scripts import PASS, RunSummary, ScriptStep, StepResult
    s = RunSummary(title="t", total=1)
    s.results = [(1, ScriptStep(utterance="u", skill_id=DT, lang="en-US"), StepResult(PASS, "ok"))]
    s.matches = {1: {"stage": "ovos-padatious-pipeline-plugin", "slots": {"location": "paris"}, "conf": None}}
    row = build_report(s, {})["steps"][0]
    assert row["match"] == {"stage": "ovos-padatious-pipeline-plugin", "slots": {"location": "paris"}}


# --- comparing ---------------------------------------------------------------------

def report(steps, channel="testing", core="2.1.1", **manifest):
    m = {"channel": channel, "stack": {"ovos-core": core}, "created_at": "2026-10-05T19:00:00Z",
         "config": {"pipeline": ["ovos-padatious-pipeline-plugin-high"]}}
    m.update(manifest)
    return {"schema": "ovos-test-report/1", "title": "Test: date-time", "manifest": m, "steps": steps}


def row(utt, status="pass", handled=f"{DT}:what_time_is_it", expected=f"{DT}:what_time_is_it", **kw):
    r = {"utterance": utt, "lang": "en-US", "expected": expected, "status": status, "handled_by": handled}
    r.update(kw)
    return r


def test_identical_steps_are_counted_not_listed_and_2x_names_dont_differ():
    a = report([row("what time is it", handled=f"{DT}:what.time.is.it.intent")])
    b = report([row("what time is it")], channel="alpha", core="3.7.2a2")
    c = cmp.compare(a, b)
    assert c["summary"]["identical"] == 1 and c["differences"] == []


def test_fix_regression_unclear():
    a = report([row("kitchen sink", "fail", "ovos-skill-wolfie.openvoiceos",
                    diagnosis={"category": "below_threshold", "lines": ["scores it 0.92"]}),
                row("tell me a joke", "pass", "ovos-skill-icanhazdadjokes.openvoiceos:joke",
                    expected="ovos-skill-icanhazdadjokes.openvoiceos:joke"),
                row("paris", match={"stage": "ovos-padatious-pipeline-plugin", "slots": {"location": "paris"}})])
    b = report([row("kitchen sink", match={"stage": "ovos-m2v-pipeline"}),
                row("tell me a joke", "fail", "ovos-skill-wiki.openvoiceos",
                    expected="ovos-skill-icanhazdadjokes.openvoiceos:joke"),
                row("paris", match={"stage": "ovos-m2v-pipeline", "slots": {}})], channel="alpha")
    c = cmp.compare(a, b)
    by = {d["utterance"]: d for d in c["differences"]}
    assert by["kitchen sink"]["class"] == cmp.FIX
    assert by["tell me a joke"]["class"] == cmp.REGRESSION
    assert by["paris"]["class"] == cmp.UNCLEAR
    assert {ch["what"] for ch in by["paris"]["changes"]} == {"stage", "slots"}
    assert c["summary"] == {"compared": 3, "identical": 0, "changed": 3, "fix": 1, "regression": 1,
                            "unclear": 1, "only_a": 0, "only_b": 0}
    md = cmp.comparison_markdown(c)
    assert md.index("## Regressions") < md.index("## Fixes") < md.index("## Unclear")
    assert "↳ A: scores it 0.92" in md


def test_padatious_scores_differ_only_beyond_the_step():
    a = report([row("u", match={"conf": 0.97})])
    assert cmp.compare(a, report([row("u", match={"conf": 0.99})]))["differences"] == []
    d = cmp.compare(a, report([row("u", match={"conf": 0.90})]))["differences"][0]
    assert d["changes"] == [{"what": "padatious score", "a": 0.97, "b": 0.9}]


def test_stage_is_only_compared_when_both_sides_name_it():
    a = report([row("u")])   # ovos-core 2.x: no stage
    b = report([row("u", match={"stage": "ovos-padatious-pipeline-plugin"})])
    assert cmp.compare(a, b)["differences"] == []


def test_pairing_by_occurrence_and_only_one_side():
    a = report([row("u"), row("u", "fail"), row("only a")])
    b = report([row("u"), row("u"), row("only b")])
    c = cmp.compare(a, b)
    assert [d["class"] for d in c["differences"]] == [cmp.FIX]
    assert c["summary"]["only_a"] == 1 and c["summary"]["only_b"] == 1


def test_installs_say_what_was_compared():
    a = report([], pipeline_plugins={"ovos-m2v-pipeline": "0.29.5a1"}, channel_health={"conflicts": ["x"]})
    b = report([], channel="alpha", core="3.7.2a2", pipeline_plugins={"ovos-m2v-pipeline": "0.30.0a1"})
    whats = {d["what"]: (d["a"], d["b"]) for d in cmp.compare(a, b)["installs"]}
    assert whats["channel"] == ("testing", "alpha")
    assert whats["ovos-core"] == ("2.1.1", "3.7.2a2")
    assert whats["ovos-m2v-pipeline"] == ("0.29.5a1", "0.30.0a1")
    assert whats["install health"] == ("1 conflicts", "not recorded")


def test_set_class_updates_the_summary():
    c = cmp.compare(report([row("u", "fail")]), report([row("u")]))
    cmp.set_class(c, 0, cmp.UNCLEAR)
    assert c["differences"][0]["class"] == cmp.UNCLEAR and c["summary"]["fix"] == 0
    assert "(suggested: fix)" in cmp.comparison_markdown(c)


def test_cli_saves_and_exits_1_on_regressions(tmp_path, capsys):
    pa, pb = tmp_path / "a.report.json", tmp_path / "b.report.json"
    pa.write_text(json.dumps(report([row("u")])))
    pb.write_text(json.dumps(report([row("u", "fail", "x")], channel="alpha")))
    assert cmp.cli(str(pa), str(pb), output=str(tmp_path / "out")) == 1
    assert "## Regressions (1)" in capsys.readouterr().out
    saved = list((tmp_path / "out").glob("*.comparison.json"))
    assert json.loads(saved[0].read_text())["schema"] == "ovos-test-comparison/1"
    (tmp_path / "bad.json").write_text("{}")
    assert cmp.cli(str(pa), str(tmp_path / "bad.json"), output=str(tmp_path)) == 2


# --- the TUI: 'Test: Compare results' ---------------------------------------------

import pytest  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402


def _app(tmp_path):
    from ovos_tui_client.app import OVOSTUIApp
    (tmp_path / "skills.log").write_text("")
    app = OVOSTUIApp(log_dir_override=str(tmp_path))
    app.bus = MagicMock()
    app.results_dir = tmp_path / "results"
    app.results_dir.mkdir()
    return app


def test_result_row_reads_a_saved_report(tmp_path):
    from ovos_tui_client.compare_screen import result_row
    p = tmp_path / "x.report.json"
    r = report([row("u")])
    r["summary"] = {"passed": 4, "checked": 8}
    p.write_text(json.dumps(r))
    assert result_row(p) == "2026-10-05 19:00 · Test: date-time · testing · ovos-core 2.1.1 · 4/8 passed"


@pytest.mark.asyncio
async def test_compare_needs_two_saved_results(tmp_path):
    from textual.widgets import RichLog
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        app.compare_results()
        await pilot.pause()
        text = "\n".join(str(l) for l in app.query_one("#conversation", RichLog).lines)
        assert "Comparing needs two saved results" in text


@pytest.mark.asyncio
async def test_pick_compare_reclassify_and_save(tmp_path):
    app = _app(tmp_path)
    (app.results_dir / "2026-10-05_1900_a.report.json").write_text(json.dumps(report([row("u", "fail")])))
    (app.results_dir / "2026-10-05_1901_b.report.json").write_text(
        json.dumps(report([row("u")], channel="alpha", core="3.7.2a2")))
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        app.compare_results()
        await pilot.pause()
        assert type(app.screen).__name__ == "ResultPickerScreen"
        await pilot.press("down", "enter")       # A: the older one (testing)
        await pilot.pause()
        await pilot.press("enter")               # B: the one left
        await pilot.pause()
        screen = app.screen
        assert type(screen).__name__ == "ComparisonScreen"
        c = screen.comparison
        assert c["a"]["channel"] == "testing" and c["differences"][0]["class"] == cmp.FIX
        await pilot.press("u")
        await pilot.pause()
        assert c["differences"][0]["class"] == cmp.UNCLEAR
        await pilot.press("ctrl+s")
        await pilot.pause()
    saved = list(app.results_dir.glob("*.comparison.json"))
    assert len(saved) == 1
    data = json.loads(saved[0].read_text())
    assert data["differences"][0]["class"] == "unclear" and data["differences"][0]["suggested"] == "fix"
