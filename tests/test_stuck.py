"""#74: a skill that keeps talking after stop is asked again (its session and
everywhere); one that still talks ends the run, since what comes after it
can't be trusted (seen live: a count_to_n going on for core's whole 300 s
handler timeout, then the next one)."""
import threading

from ovos_tui_client.results import report_markdown, summary_parts
from ovos_tui_client.report import build_report
from ovos_tui_client.scripts import PASS, ScriptRunner, ScriptStep

FAST = dict(step_timeout=2, speech_timeout=0.2, stop_wait=0.3, settle=0, quiet_after_match=0,
            provider_wait=0, busy_wait=0)
STEPS = [ScriptStep(utterance="count to 500", skill_id="ovos-skill-count.x"),
         ScriptStep(utterance="what time is it", skill_id="ovos-skill-time.x")]


def _runner(stops_on, stuck_calls):
    """stops_on: None (never), 'session' or 'all' - which stop silences it."""
    runner = None
    state = {"quiet": False}

    def send(i, n, step):
        sid = {"session": {"session_id": runner.session_id}}
        if i == 1:
            state["quiet"] = False
            runner.feed("speak", {"utterance": "one", "meta": {"skill": "ovos-skill-count.x"}}, sid)
            runner.feed("ovos-skill-count.x:count_to_n", {}, sid)
            runner.feed("mycroft.audio.speech.start", {}, {})
        else:
            runner.feed(f"{step.skill_id}:it", {}, sid)
        runner.feed("ovos.utterance.handled", {}, sid)

    def heard(which):
        if stops_on == which or state["quiet"]:
            state["quiet"] = True
            runner.feed("mycroft.audio.speech.stop", {}, {})
        else:   # the next number starts right after the stop - unless it went quiet since
            threading.Timer(0.05, lambda: state["quiet"] or runner.feed(
                "mycroft.audio.speech.start", {}, {})).start()

    runner = ScriptRunner(STEPS, "t", send=send, stop_session=lambda s: heard("session"),
                          stop_all=lambda: heard("all"),
                          on_stuck=lambda i, n, s, skill: stuck_calls.append((i, skill)), **FAST)
    return runner


def test_a_skill_that_never_stops_ends_the_run():
    calls = []
    summary = _runner(None, calls).run()
    assert summary.halted_by == "ovos-skill-count.x" and summary.cancelled
    assert len(summary.results) == 1                         # the step it happened in is kept
    (_, _, first), = summary.results
    assert first.notes == ["did not stop: ovos-skill-count.x was still speaking 0.3 s after stop"]
    assert calls == [(1, "ovos-skill-count.x")]
    assert "stopped after 1/2: ovos-skill-count.x kept talking after stop" in summary_parts(summary)
    assert build_report(summary, {})["summary"]["halted_by"] == "ovos-skill-count.x"
    assert "## Warnings" in report_markdown(summary, {})


def test_a_skill_that_only_stops_for_a_stop_to_everything_doesnt_end_the_run():
    calls = []
    summary = _runner("all", calls).run()
    assert summary.halted_by is None and len(summary.results) == 2
    (_, _, first), (_, _, second) = summary.results
    assert first.notes and first.notes[0].endswith("a stop for everything ended it")
    assert second.notes == [] and second.status == PASS     # it went quiet before step 2
    assert calls == [(1, "ovos-skill-count.x")]


def test_a_skill_that_stops_gets_no_note():
    calls = []
    summary = _runner("session", calls).run()
    assert summary.halted_by is None and all(not r.notes for _, _, r in summary.results) and calls == []
    assert "## Warnings" not in report_markdown(summary, {})
