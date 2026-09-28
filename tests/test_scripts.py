"""Tests for scripts.py (#30) - parsing, golden-utterance lookup,
judging a step from bus traffic, and the ScriptRunner's step loop.
No real bus or network: fetches and bus messages are injected."""
import json
import threading

from ovos_tui_client.scripts import (
    FAIL, PASS, SENT, TIMEOUT, GoldenInclude, ScriptRunner, ScriptStep, StepObservation,
    evaluate, expand_includes, golden_filenames, github_raw_url, lang_matches,
    list_user_scripts, load_golden, observe, parse_github_repo, parse_script,
)

WEATHER = "ovos-skill-weather.openvoiceos"


def _row(**kw):
    base = {"skill_id": WEATHER, "utterance": "what's the weather", "lang": "en-US",
            "intent_label": "weather.intent", "needs_manual": False}
    base.update(kw)
    return json.dumps(base)


# --- parsing ---

def test_parse_golden_rows_keeps_expectations():
    steps = parse_script(_row(), lang="en-us")
    assert steps == [ScriptStep("what's the weather", "en-US", WEATHER, "weather.intent")]
    assert steps[0].expected_intent == f"{WEATHER}:weather.intent"


def test_parse_skips_needs_manual_other_langs_comments_and_bad_json():
    text = "\n".join([
        "# comment", "",
        _row(needs_manual=True),
        _row(lang="da-DK", utterance="hvad er vejret"),
        "{not json",
        _row(utterance="is it raining"),
    ])
    steps = parse_script(text, lang="en-us")
    assert [s.utterance for s in steps] == ["is it raining"]


def test_parse_plain_text_lines_have_no_expectation():
    steps = parse_script("hello there\nwhat time is it\n", lang="da-dk")
    assert [s.utterance for s in steps] == ["hello there", "what time is it"]
    assert not steps[0].has_expectation


def test_parse_default_skill_id_fills_missing_skill():
    steps = parse_script(json.dumps({"utterance": "hi", "intent_label": "hello.intent"}),
                         default_skill_id="skill-x.me")
    assert steps[0].expected_intent == "skill-x.me:hello.intent"


def test_golden_include_rows_expand_via_loader():
    items = parse_script('{"golden": "a.b"}\nplain line')
    assert items[0] == GoldenInclude("a.b")
    steps = expand_includes(items, lambda sid: [ScriptStep(f"from {sid}")])
    assert [s.utterance for s in steps] == ["from a.b", "plain line"]


def test_lang_matching():
    assert lang_matches("da-DK", "da-dk")
    assert lang_matches("da", "da-dk")
    assert not lang_matches("da-DK", "en-us")
    assert lang_matches(None, "en-us")


# --- finding golden files ---

def test_golden_filenames_upper_region_then_bare():
    assert golden_filenames("da-dk") == ["golden_utterances_da-DK.jsonl", "golden_utterances_da.jsonl"]
    assert golden_filenames("kab") == ["golden_utterances_kab.jsonl"]


def test_parse_github_repo_variants():
    assert parse_github_repo("https://github.com/OpenVoiceOS/ovos-skill-weather") == ("OpenVoiceOS", "ovos-skill-weather")
    assert parse_github_repo("git@github.com:andlo/x.git") == ("andlo", "x")
    assert parse_github_repo("https://pypi.org/x") is None
    assert github_raw_url("https://github.com/o/r", "test/f") == "https://raw.githubusercontent.com/o/r/HEAD/test/f"


def test_load_golden_prefers_local_checkout(tmp_path):
    d = tmp_path / "src" / "ovos-skill-weather" / "test" / "end2end"
    d.mkdir(parents=True)
    (d / "golden_utterances_en-US.jsonl").write_text(_row() + "\n")
    fetched = []
    result = load_golden(WEATHER, "en-us", golden_dirs=[tmp_path / "src"],
                         fetch=lambda url: fetched.append(url), repo_url_finder=lambda s: "https://github.com/o/r",
                         cache_dir=tmp_path / "cache")
    assert len(result.steps) == 1 and "ovos-skill-weather" in result.source
    assert fetched == []


def test_load_golden_fetches_from_github_and_caches(tmp_path):
    urls = []

    def fetch(url):
        urls.append(url)
        return _row(lang="da-DK", utterance="hvad er vejret") if url.endswith("da-DK.jsonl") else None

    result = load_golden(WEATHER, "da-dk", fetch=fetch,
                         repo_url_finder=lambda s: "https://github.com/OpenVoiceOS/ovos-skill-weather",
                         cache_dir=tmp_path)
    assert urls[0] == ("https://raw.githubusercontent.com/OpenVoiceOS/ovos-skill-weather/HEAD/"
                       "test/end2end/golden_utterances_da-DK.jsonl")
    assert [s.utterance for s in result.steps] == ["hvad er vejret"]
    assert (tmp_path / WEATHER / "golden_utterances_da-DK.jsonl").is_file()

    # offline afterwards: served from cache
    offline = load_golden(WEATHER, "da-dk", fetch=lambda url: None,
                          repo_url_finder=lambda s: "https://github.com/o/r", cache_dir=tmp_path)
    assert len(offline.steps) == 1 and "cached" in offline.source


def test_load_golden_nothing_found(tmp_path):
    result = load_golden(WEATHER, "en-us", fetch=lambda u: None, repo_url_finder=lambda s: None, cache_dir=tmp_path)
    assert result.steps == [] and result.source is None


def test_list_user_scripts(tmp_path):
    (tmp_path / "b.txt").write_text("x")
    (tmp_path / "a.jsonl").write_text("x")
    (tmp_path / "notes.md").write_text("x")
    assert [p.name for p in list_user_scripts(tmp_path)] == ["a.jsonl", "b.txt"]
    assert list_user_scripts(tmp_path / "missing") == []


# --- observing and judging ---

def test_observe_dispatched_intent_message_for_known_skill_only():
    obs = StepObservation()
    observe(obs, "recognizer_loop:utterance", {}, {}, [WEATHER])
    observe(obs, f"{WEATHER}:weather.intent", {}, {}, [WEATHER])
    assert obs.intents == [f"{WEATHER}:weather.intent"] and obs.skills == [WEATHER]


def test_observe_intent_matched_with_bare_intent_name():
    obs = StepObservation()
    observe(obs, "ovos.intent.matched", {"intent_name": "weather.intent", "skill_id": WEATHER}, {})
    assert obs.intents == [f"{WEATHER}:weather.intent"]


def test_observe_skill_level_signals():
    obs = StepObservation()
    observe(obs, "mycroft.skill.handler.start", {}, {"skill_id": "a.b"})
    observe(obs, "ovos.skills.fallback.c.d.response", {"result": True}, {})
    observe(obs, "question:action", {"skill_id": "e.f"}, {})
    observe(obs, "complete_intent_failure", {}, {})
    observe(obs, "speak", {"utterance": "hi"}, {})
    assert obs.skills == ["a.b", "c.d", "e.f"] and obs.failed and obs.spoke == ["hi"]


def _step():
    return ScriptStep("what's the weather", "en-us", WEATHER, "weather.intent")


def test_evaluate_pass_on_exact_intent_ignoring_dot_intent_suffix_and_case():
    obs = StepObservation()
    obs.add_intent(f"{WEATHER}:Weather")
    assert evaluate(_step(), obs).status == PASS


def test_evaluate_fail_on_other_skill():
    obs = StepObservation()
    obs.add_intent("ovos-skill-wikipedia.openvoiceos:wiki")
    result = evaluate(_step(), obs)
    assert result.status == FAIL and "ovos-skill-wikipedia" in result.detail


def test_evaluate_fail_on_same_skill_wrong_intent():
    obs = StepObservation()
    obs.add_intent(f"{WEATHER}:temperature.intent")
    result = evaluate(_step(), obs)
    assert result.status == FAIL and "temperature.intent" in result.detail


def test_evaluate_pass_on_skill_level_signal_when_intent_not_reported():
    obs = StepObservation()
    obs.add_skill(WEATHER)
    assert evaluate(_step(), obs).status == PASS


def test_evaluate_timeout_and_sent():
    assert evaluate(_step(), StepObservation(), timed_out=True).status == TIMEOUT
    plain = ScriptStep("hello")
    obs = StepObservation()
    obs.add_intent("x.y:hello")
    result = evaluate(plain, obs)
    assert result.status == SENT and result.detail == "x.y:hello"


# --- runner ---

def _runner(steps, reply, **kw):
    """reply(runner, step) is called from send() to fake OVOS's bus traffic."""
    done = []
    holder = {}

    def send(i, n, step):
        reply(holder["r"], step)

    runner = ScriptRunner(steps, "t", send=send,
                          on_step_done=lambda i, n, s, r, o: done.append((i, r.status)),
                          known_skills=lambda: [WEATHER], settle=0, late_handled_wait=0.01,
                          step_timeout=kw.pop("step_timeout", 2), **kw)
    holder["r"] = runner
    return runner, done


def test_runner_judges_each_step_from_fed_messages():
    def reply(r, step):
        if "weather" in step.utterance:
            r.feed(f"{WEATHER}:weather.intent")
        else:
            r.feed("ovos-skill-wikipedia.openvoiceos:wiki")
        r.feed("ovos.utterance.handled")

    steps = [_step(), ScriptStep("who is lincoln", "en-us", WEATHER, "weather.intent")]
    runner, done = _runner(steps, reply)
    summary = runner.run()
    assert done == [(1, PASS), (2, FAIL)]
    assert summary.count(PASS) == 1 and len(summary.failures) == 1 and not summary.cancelled


def test_runner_accepts_handler_complete_as_end_on_older_core():
    def reply(r, step):
        r.feed("mycroft.skill.handler.start", {}, {"skill_id": WEATHER})
        r.feed("mycroft.skill.handler.complete")

    runner, done = _runner([_step()], reply)
    runner.run()
    assert done == [(1, PASS)]


def test_runner_times_out_when_nothing_happens():
    runner, done = _runner([_step()], lambda r, s: None, step_timeout=0.2)
    runner.run()
    assert done == [(1, TIMEOUT)]


def test_runner_waits_for_speech_to_end():
    def reply(r, step):
        r.feed(f"{WEATHER}:weather.intent")
        r.feed("mycroft.audio.speech.start")
        r.feed("ovos.utterance.handled")
        threading.Timer(0.2, r.feed, args=("mycroft.audio.speech.stop",)).start()

    runner, done = _runner([_step(), _step()], reply)
    summary = runner.run()
    assert done == [(1, PASS), (2, PASS)]
    assert summary.duration >= 0.4


def test_runner_stop_cancels_remaining_steps():
    def reply(r, step):
        r.stop()

    runner, done = _runner([_step(), _step()], reply)
    summary = runner.run()
    assert summary.cancelled and done == []
