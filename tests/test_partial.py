"""A run is saved as it runs, and a cut-short run can be taken up again."""
from types import SimpleNamespace

from ovos_tui_client import partial
from ovos_tui_client.scripts import FAIL, PASS, RunSummary, ScriptRunner, ScriptStep, StepResult

STEPS = [ScriptStep(utterance=f"say {n}", skill_id="ovos-skill-a.x", intent_label="a", lang="en-US")
         for n in range(1, 6)]


def _summary(done: int) -> RunSummary:
    s = RunSummary(title="t", total=len(STEPS), started_at=1000.0, duration=12.0)
    for i in range(1, done + 1):
        s.results.append((i, STEPS[i - 1], StepResult(PASS if i % 2 else FAIL, "" if i % 2 else "expected a",
                                                      notes=["n"] if i == 2 else [])))
        s.handled_by[i] = "ovos-skill-a.x:a"
        s.matches[i] = {"stage": "ovos-padatious-pipeline-plugin"}
    return s


def test_fingerprint_is_the_steps():
    fp = partial.fingerprint("t", STEPS)
    assert fp == partial.fingerprint("t", list(STEPS)) and len(fp) == 12
    assert fp != partial.fingerprint("t", STEPS[:4])
    assert fp != partial.fingerprint("other title", STEPS)


def test_save_find_load_round_trip(tmp_path):
    fp = partial.fingerprint("t", STEPS)
    assert partial.save(_summary(3), {"channel": "alpha"}, tmp_path, fp)
    found = partial.find(tmp_path, fp)
    assert (found["done"], found["total"]) == (3, 5)
    assert (tmp_path / f"partial_{fp}.md").read_text().startswith("> **Partial run:** 3/5 steps")
    s = partial.load(found["path"], "t", STEPS)
    assert [i for i, _, _ in s.results] == [1, 2, 3]
    assert s.results[1][2].status == FAIL and s.results[1][2].notes == ["n"]
    assert s.matches[1] == {"stage": "ovos-padatious-pipeline-plugin"} and s.duration == 12.0
    assert not list(tmp_path.glob("*.tmp"))          # written whole, then moved in place


def test_other_steps_are_never_resumed(tmp_path):
    fp = partial.fingerprint("t", STEPS)
    partial.save(_summary(3), {}, tmp_path, fp)
    changed = STEPS[:2] + [ScriptStep(utterance="something else", skill_id="x", lang="en-US")] + STEPS[3:]
    assert partial.find(tmp_path, partial.fingerprint("t", changed)) is None
    assert partial.load(partial.find(tmp_path, fp)["path"], "t", changed) is None


def test_remove(tmp_path):
    fp = partial.fingerprint("t", STEPS)
    partial.save(_summary(2), {}, tmp_path, fp)
    partial.remove(tmp_path, fp)
    assert partial.find(tmp_path, fp) is None and not list(tmp_path.iterdir())


def test_the_runner_goes_on_from_the_next_step():
    sent = []
    runner = None

    def send(i, n, step):
        sent.append(i)
        runner.feed("ovos-skill-a.x:a", {}, {"session": {"session_id": runner.session_id}})
        runner.feed("ovos.utterance.handled", {}, {"session": {"session_id": runner.session_id}})

    runner = ScriptRunner(STEPS, "t", send=send, step_timeout=1, settle=0, quiet_after_match=0,
                          provider_wait=0, busy_wait=0)
    summary = runner.run(resume=_summary(3))
    assert sent == [4, 5]
    assert [i for i, _, _ in summary.results] == [1, 2, 3, 4, 5]
    assert summary.resumed_at and summary.duration >= 12.0 and not summary.cancelled


def test_headless_hints_without_resume_and_resumes_with_it(tmp_path):
    from ovos_tui_client.headless import PartialRun
    fp = partial.fingerprint("t", STEPS)
    partial.save(_summary(3), {}, tmp_path, fp)
    logged = []
    args = SimpleNamespace(output=str(tmp_path), host="127.0.0.1", lang="en-US", channel="alpha", resume=False)
    saver = PartialRun(args, "t", STEPS, {}, logged.append)
    assert saver.resume is None and "add --resume to go on from step 4" in logged[-1]
    args.resume = True
    saver = PartialRun(args, "t", STEPS, {}, logged.append)
    assert len(saver.resume.results) == 3 and "Resuming: 3/5" in logged[-1]
    saver.finish(_summary(5))                          # done: the partial files go
    assert partial.find(tmp_path, fp) is None


import pytest  # noqa: E402
from unittest.mock import MagicMock, patch  # noqa: E402


@pytest.mark.asyncio
async def test_the_tui_asks_and_resumes(tmp_path):
    from ovos_tui_client.app import OVOSTUIApp
    (tmp_path / "skills.log").write_text("")
    app = OVOSTUIApp(log_dir_override=str(tmp_path))
    app.bus = MagicMock()
    app.bus.lang = "en-US"
    app.results_dir = tmp_path / "results"
    partial.save(_summary(3), {}, app.results_dir, partial.fingerprint("t", STEPS))
    got = {}

    class FakeRunner:
        def __init__(self, steps, title, **kw):
            self.summary = None

        def run(self, resume=None):
            got["resume"] = resume
            return resume or _summary(5)

    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        with patch("ovos_tui_client.app.ScriptRunner", FakeRunner), \
                patch.object(app, "_partial_manifest", return_value={}):
            app._run_steps_worker("t", STEPS)
            await pilot.pause()
            assert type(app.screen).__name__ == "ChoiceAboutScreen"
            await pilot.click("#choice-resume")
            await app.workers.wait_for_complete()
            await pilot.pause()
    assert [i for i, _, _ in got["resume"].results] == [1, 2, 3]
    # still not finished (3/5): the partial stays, to resume again
    assert partial.find(app.results_dir, partial.fingerprint("t", STEPS))["done"] == 3
