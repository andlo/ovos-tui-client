"""#48: why a test step failed. The probe answers below are the real reply
shapes from ovos-core 3.7.2a2 (alpha) and 2.1.1 (testing), October 2026."""
import time
from pathlib import Path

from ovos_tui_client import diagnose as dg
from ovos_tui_client.scripts import FAIL, PASS, TIMEOUT, ScriptStep, StepObservation, StepResult

ALPHA_PIPELINE = ["ovos-stop-pipeline-plugin-high", "ovos-converse-pipeline-plugin",
                  "ovos-padatious-pipeline-plugin-high", "ovos-m2v-pipeline-high",
                  "ovos-fallback-pipeline-plugin-high", "ovos-common-query-pipeline-plugin",
                  "ovos-fallback-pipeline-plugin-medium", "ovos-fallback-pipeline-plugin-low"]
DT = "ovos-skill-date-time.openvoiceos"


def step(utt="what time is it", skill=DT, intent="what_time_is_it", lang="en-US"):
    return ScriptStep(utterance=utt, skill_id=skill, intent_label=intent, lang=lang)


def ctx(**kw):
    base = dict(installed={DT: True, "ovos-skill-ddg.openvoiceos": True}, local=True,
                pipeline=ALPHA_PIPELINE)
    base.update(kw)
    return dg.Context(**base)


def answers(would=None, pad=None, registered=None, manifest=None):
    """A fake bus: request(type, data, reply, timeout) -> reply data."""
    def request(msg_type, data, reply, timeout):
        if msg_type == "intent.service.intent.get":
            return {"intent": would, "utterance": data["utterance"]}
        if msg_type == "intent.service.padatious.get":
            return {"intent": pad}
        if msg_type == "ovos.intent.list":
            return None if registered is None else {"ok": True, "intents": registered}
        if msg_type == "intent.service.padatious.manifest.get":
            return None if manifest is None else {"intents": manifest}
        return None
    return request


FALLBACK = {"skill_id": "ovos-skill-ddg.openvoiceos", "intent_name":
            "ovos.skills.fallback.ovos-skill-ddg.openvoiceos.request",
            "intent_service": "ovos-fallback-pipeline-plugin-medium"}
REG_EN = [{"skill_id": DT, "intent_name": "what_time_is_it", "lang": "en-US"},
          {"skill_id": DT, "intent_name": "what_day_is_it", "lang": "en-US"}]


def run(st, request, c, obs=None, status=FAIL):
    return dg.diagnose(st, StepResult(status, "x"), obs or StepObservation(), request, c, time.time())


def test_below_threshold_names_the_pipelines_padatious_stages():
    """'what time is it in the kitchen sink please': padatious scores the
    right intent 0.92, but the pipeline only has padatious-high."""
    d = run(step("what time is it in the kitchen sink please"),
            answers(would=FALLBACK, pad={"name": f"{DT}:what_time_is_it", "conf": 0.918},
                    registered=REG_EN), ctx())
    assert d.category == dg.BELOW_THRESHOLD
    assert "scores it 0.92" in d.lines[0] and "ovos-padatious-pipeline-plugin-high (≥0.95)" in d.lines[0]


def test_other_intent_won_says_who_and_padatious_best_guess():
    d = run(step("blorp"), answers(
        would=FALLBACK, pad={"name": "ovos-skill-alerts.openvoiceos:timer_status", "conf": 0.94},
        registered=REG_EN), ctx())
    assert d.category == dg.OTHER_WON
    assert d.lines[0].startswith("ovos-skill-ddg.openvoiceos wins it")
    assert "padatious' best guess: ovos-skill-alerts.openvoiceos:timer_status (0.94)" in d.lines[1]


def test_not_registered_for_this_language_points_to_secondary_langs():
    d = run(step("hvad er klokken", lang="da-DK"),
            answers(would=None, registered=REG_EN), ctx())
    assert d.category == dg.NOT_REGISTERED
    assert "no intents for da-DK" in d.lines[0] and "secondary_langs" in d.lines[0]


def test_intent_not_registered_lists_what_the_skill_has():
    d = run(step(intent="what_year_is_it"), answers(would=FALLBACK, registered=REG_EN), ctx())
    assert d.category == dg.NOT_REGISTERED
    assert "what_year_is_it is not registered for en-US" in d.lines[0]
    assert "what_day_is_it" in d.lines[0]


def test_ovos_core_2_names_compare_with_the_steps_labels():
    """2.x names intents 'what.time.is.it.intent'; the step says what_time_is_it."""
    d = run(step("what time is it in the kitchen sink please"),
            answers(would={"skill_id": None, "intent_name": "ovos.skills.fallback.x.request",
                           "intent_service": "ovos-fallback-pipeline-plugin-low"},
                    pad={"name": f"{DT}:what.time.is.it.intent", "conf": 0.9182},
                    manifest=[f"{DT}:what.time.is.it.intent"]), ctx())
    assert d.category == dg.BELOW_THRESHOLD


def test_skill_not_loaded_or_deactivated():
    assert run(step(skill="ovos-skill-nope.x"), answers(), ctx()).category == dg.NOT_LOADED
    d = run(step(), answers(), ctx(installed={DT: False}))
    assert d.category == dg.NOT_LOADED and "deactivated" in d.lines[0]


def test_handler_failed_and_silent():
    obs = StepObservation()
    obs.add_intent(f"{DT}:what_time_is_it")
    obs.observe({"type": "mycroft.skill.handler.error",
                 "data": {"name": "handle_query_time", "exception": "KeyError('tz')"}, "context": {}})
    d = run(step(), answers(), ctx(), obs=obs)
    assert d.category == dg.HANDLER_FAILED and "KeyError('tz')" in d.lines[0]
    quiet = StepObservation()
    quiet.add_intent(f"{DT}:what_time_is_it")
    d = run(step(), answers(), ctx(), obs=quiet, status=TIMEOUT)
    assert d.category == dg.SILENT and "said nothing before the time limit" in d.lines[0]


def test_matches_now():
    d = run(step(), answers(would={"skill_id": DT, "intent_name": f"{DT}:what_time_is_it",
                                   "intent_service": "ovos-padatious-pipeline-plugin-high"},
                            registered=REG_EN), ctx())
    assert d.category == dg.MATCHES_NOW


def test_remote_says_cause_unknown_and_keeps_stage_one():
    d = run(step(), answers(would=FALLBACK, registered=REG_EN), ctx(local=False, pipeline=[]))
    assert d.category == dg.OTHER_WON
    assert d.lines[-1].startswith("cause unknown: OVOS is on another machine")


def test_no_replies_at_all_is_unknown():
    d = run(step(), lambda *a: None, ctx())
    assert d.category == dg.UNKNOWN


# --- stage 2 ---------------------------------------------------------------------

def _log(tmp_path: Path, *lines):
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    (tmp_path / "skills.log").write_text("".join(f"{stamp}.123 - skills - {l}\n" for l in lines))
    return tmp_path


def test_rule_translate_servers_down(tmp_path):
    c = ctx(log_dir=_log(tmp_path, "ERROR - All OVOS Translate servers are down!"))
    d = dg.diagnose(step(), StepResult(FAIL, "x"), StepObservation(),
                    answers(would=FALLBACK, registered=REG_EN), c, time.time() - 5)
    assert d.cause and "translate servers are down" in d.cause
    assert d.lines[-1].startswith("Likely cause:")


def test_rule_padatious_trailing_slot_bug():
    lines = ["convert {quantity}", "how many {unit} in {quantity}", "what is {x} in {y}", "help me"]
    c = ctx(versions={"ovos-padatious": "1.4.3"}, intent_lines=lambda s, i, l: lines)
    d = run(step("convert 10 cm to inches", skill=DT, intent="what_time_is_it"),
            answers(would=FALLBACK, pad={"name": f"{DT}:what_time_is_it", "conf": 0.25},
                    registered=REG_EN), c)
    assert "3/4 lines of what_time_is_it.intent end in a slot" in d.cause
    assert "ovos-padatious-pipeline-plugin#175" in d.cause


def test_rule_adjacent_slots_on_workshop_9():
    c = ctx(versions={"ovos-workshop": "9.8.14a1", "ovos-padatious": "2.2.7a1"},
            intent_lines=lambda s, i, l: ["set {hours} {minutes}", "timer for {x}"])
    d = run(step(), answers(would=FALLBACK, registered=REG_EN), c)
    assert "1/2 lines" in d.cause and "side by side" in d.cause


def test_rule_m2v_common_query_never_spoken():
    m2v = {"skill_id": "ovos-skill-wikipedia.openvoiceos", "intent_name": "common_query.question",
           "intent_service": "ovos-m2v-pipeline-high"}
    d = run(step("who is einstein", skill="ovos-skill-wikipedia.openvoiceos", intent=""),
            answers(would=m2v), ctx(installed={"ovos-skill-wikipedia.openvoiceos": True}))
    assert "ovos-m2v-pipeline#68" in d.cause


def test_log_lines_since_skips_older(tmp_path):
    old = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 3600))
    new = time.strftime("%Y-%m-%d %H:%M:%S")
    (tmp_path / "skills.log").write_text(f"{old}.1 - old line\n{new}.1 - new line\n  continued\n")
    got = dg.log_lines_since(tmp_path, time.time() - 60)
    assert got == [f"{new}.1 - new line", "  continued"]


# --- the runner, the report ------------------------------------------------------

def test_runner_diagnoses_failures_only_before_the_next_step():
    from ovos_tui_client.scripts import ScriptRunner
    steps = [step("a"), step("b")]
    seen = []

    def send(i, n, s):
        runner.feed("ovos.utterance.handled", {}, {"session": {"session_id": runner.session_id}})

    def judge_all_fail(s, result, obs, since):
        seen.append((s.utterance, result.status, since > 0))
        return {"category": "unknown", "lines": ["because"]}
    runner = ScriptRunner(steps, "t", send=send, diagnose=judge_all_fail, step_timeout=1,
                          settle=0, quiet_after_match=0, provider_wait=0, busy_wait=0)
    summary = runner.run()
    assert [s[0] for s in seen] == ["a", "b"] and all(s[2] for s in seen)
    assert all(r.diagnosis == {"category": "unknown", "lines": ["because"]} for _, _, r in summary.results)


def test_report_row_and_markdown_carry_the_diagnosis():
    from ovos_tui_client.report import build_report
    from ovos_tui_client.results import report_markdown
    from ovos_tui_client.scripts import RunSummary
    s = RunSummary(title="t", total=2)
    diag = {"category": "below_threshold", "lines": ["padatious scores it 0.92"], "evidence": {}}
    s.results = [(1, step(), StepResult(FAIL, "expected x", diagnosis=diag)),
                 (2, step(), StepResult(PASS, "ok"))]
    rep = build_report(s, {})
    assert rep["steps"][0]["diagnosis"] == diag and "diagnosis" not in rep["steps"][1]
    assert "  - ↳ padatious scores it 0.92" in report_markdown(s, {})
