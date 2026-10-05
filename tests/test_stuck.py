"""#74: a skill that keeps talking after stop is noticed, and the steps run
while it talks are flagged."""
import threading

from ovos_tui_client.results import report_markdown, summary_parts
from ovos_tui_client.report import build_report
from ovos_tui_client.scripts import PASS, ScriptRunner, ScriptStep

FAST = dict(step_timeout=2, speech_timeout=0.2, stop_wait=0.3, settle=0, quiet_after_match=0,
            provider_wait=0, busy_wait=0)


def _runner(steps, ignores_stop: bool, stuck_calls: list):
    runner = None

    def send(i, n, step):
        sid = {"session": {"session_id": runner.session_id}}
        if i == 1:
            runner.feed("speak", {"utterance": "Once upon a time", "meta": {"skill": "ovos-skill-tales.x"}}, sid)
            runner.feed("ovos-skill-tales.x:story", {}, sid)
            runner.feed("mycroft.audio.speech.start", {}, {})
        else:
            runner.feed(f"{step.skill_id}:it", {}, sid)
        runner.feed("ovos.utterance.handled", {}, sid)

    def stop_session(session):
        if ignores_stop:   # the next sentence starts right after the stop
            threading.Timer(0.05, lambda: runner.feed("mycroft.audio.speech.start", {}, {})).start()
        else:
            runner.feed("mycroft.audio.speech.stop", {}, {})

    runner = ScriptRunner(steps, "t", send=send, stop_session=stop_session,
                          on_stuck=lambda i, n, s, skill: stuck_calls.append((i, skill)), **FAST)
    return runner


STEPS = [ScriptStep(utterance="read me a story", skill_id="ovos-skill-tales.x"),
         ScriptStep(utterance="what time is it", skill_id="ovos-skill-time.x")]


def test_a_skill_that_ignores_stop_is_named_and_later_steps_are_flagged():
    calls = []
    summary = _runner(STEPS, ignores_stop=True, stuck_calls=calls).run()
    (_, _, first), (_, _, second) = summary.results
    assert first.notes == ["did not stop: ovos-skill-tales.x was still speaking 0.3 s after stop"]
    assert second.notes == ["possibly affected: ovos-skill-tales.x was still speaking when this step started"]
    assert second.status == PASS            # the verdict stands; the note says why to doubt it
    assert calls == [(1, "ovos-skill-tales.x")]   # said once, not per step
    assert "1 possibly affected by a skill that didn't stop" in summary_parts(summary)
    md = report_markdown(summary, {})
    assert "## Warnings" in md and "⚠ [2] \"what time is it\": possibly affected" in md
    assert build_report(summary, {})["steps"][1]["notes"] == second.notes


def test_a_skill_that_stops_gets_no_note():
    calls = []
    summary = _runner(STEPS, ignores_stop=False, stuck_calls=calls).run()
    assert all(not r.notes for _, _, r in summary.results) and calls == []
    assert "## Warnings" not in report_markdown(summary, {})
