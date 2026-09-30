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


# --- found live on ovos-core 2.1.1 (test instance) ---

def test_pipeline_plugin_dispatch_counts_as_a_match_even_if_not_an_installed_skill():
    obs = StepObservation()
    observe(obs, "ovos-common-reading-pipeline-plugin.andlo:read_content", {}, {}, [WEATHER])
    result = evaluate(_step(), obs)
    assert result.status == FAIL and "ovos-common-reading-pipeline-plugin.andlo:read_content" in result.detail


def test_non_intent_colon_messages_are_ignored():
    obs = StepObservation()
    for t in ("recognizer_loop:utterance", "recognizer_loop:audio_output_start", "question:query"):
        observe(obs, t, {}, {}, [WEATHER])
    assert obs.intents == []


def test_converse_get_response_capture_is_reported_clearly():
    dt = "ovos-skill-date-time.openvoiceos"
    obs = StepObservation()
    observe(obs, f"{dt}.converse.get_response", {}, {}, [dt])
    result = evaluate(ScriptStep("what time is it", "en-us", dt, "what_time_is_it"), obs)
    assert result.status == FAIL and "get_response" in result.detail and "waiting for an answer" in result.detail


def test_runner_ends_step_after_quiet_period_when_core_sends_no_end_marker():
    def reply(r, step):
        r.feed(f"{WEATHER}:weather.intent")  # no handler.complete, no utterance.handled

    runner, done = _runner([_step()], reply, step_timeout=5, quiet_after_match=0.2)
    summary = runner.run()
    assert done == [(1, PASS)] and summary.duration < 2


def test_runner_waits_longer_for_tts_after_a_reply():
    def reply(r, step):
        r.feed(f"{WEATHER}:weather.intent")
        r.feed("speak", {"utterance": "sunny"})
        threading.Timer(0.35, r.feed, args=("recognizer_loop:audio_output_start",)).start()
        threading.Timer(0.5, r.feed, args=("recognizer_loop:audio_output_end",)).start()

    runner, done = _runner([_step()], reply, step_timeout=5, quiet_after_match=0.2)
    summary = runner.run()
    # quiet 0.2s would have ended at ~0.2s; waiting for TTS keeps it until audio ends
    assert done == [(1, PASS)] and summary.duration >= 0.5


def test_load_golden_falls_back_to_skill_json_examples(tmp_path):
    result = load_golden("ovos-skill-metronome.andlo", "en-us", fetch=lambda u: None,
                         repo_url_finder=lambda s: "https://github.com/andlo/ovos-skill-metronome",
                         cache_dir=tmp_path, examples_finder=lambda sid, lang: ["start a metronome at 90 bpm"])
    assert [(s.utterance, s.skill_id, s.intent_label) for s in result.steps] == [
        ("start a metronome at 90 bpm", "ovos-skill-metronome.andlo", None)]
    assert "skill.json examples" in result.source


def test_common_reading_fetch_counts_as_the_provider_skill_answering():
    obs = StepObservation()
    observe(obs, "ovos-common-reading-pipeline-plugin.andlo:read_content", {}, {})
    observe(obs, "ovos.common_reading.fetch_content.ovos-skill-andersen-tales.andlo", {}, {})
    observe(obs, "ovos.common_reading.fetch_content.response", {}, {})
    step = ScriptStep("read me the little mermaid", "en-us", "ovos-skill-andersen-tales.andlo", None)
    assert evaluate(step, obs).status == PASS


def test_converse_capture_by_a_pipeline_plugin_is_detected_too():
    obs = StepObservation()
    observe(obs, "ovos-common-reading-pipeline-plugin.andlo.converse.get_response", {}, {}, [WEATHER])
    result = evaluate(_step(), obs)
    assert result.status == FAIL and "ovos-common-reading-pipeline-plugin.andlo" in result.detail
    assert "get_response" in result.detail


def test_padatious_dotted_name_matches_padacioso_underscore_label():
    dt = "ovos-skill-date-time.openvoiceos"
    obs = StepObservation()
    obs.add_intent(f"{dt}:what.time.is.it.intent")
    assert evaluate(ScriptStep("what time is it", "en-us", dt, "what_time_is_it"), obs).status == PASS


def test_runner_ignores_messages_from_other_sessions():
    def reply(r, step):
        other = {"session": {"session_id": "default"}}
        mine = {"session": {"session_id": r.session_id}}
        r.feed("ovos-skill-wikipedia.openvoiceos:wiki", {}, other)
        r.feed("speak", {"utterance": "long long ago"}, other)
        r.feed(f"{WEATHER}:weather.intent", {}, mine)
        r.feed("ovos.utterance.handled", {}, mine)

    obs_seen = []
    runner, done = _runner([_step()], reply)
    runner._on_step_done = lambda i, n, s, res, obs: obs_seen.append((res.status, obs.intents, obs.spoke))
    runner.run()
    assert obs_seen == [("pass", [f"{WEATHER}:weather.intent"], [])]
    assert runner.session_id.startswith("ovos-tui-test-")



def test_an_end_marker_from_another_session_does_not_end_the_step():
    """ovos-core timing out an earlier step's handler (a quiz waiting 300 s
    for an answer) emits error + utterance.handled in THAT step's session.
    Seen live: it ended the current step before its own match arrived."""
    def reply(r, step):
        old = {"session": {"session_id": "ovos-tui-test-earlier"}}
        r.feed("ovos.intent.handler.error", {}, old)
        r.feed("mycroft.skill.handler.error", {}, old)
        r.feed("ovos.utterance.handled", {}, old)
        mine = {"session": {"session_id": r.session_id}}

        def later():
            r.feed(f"{WEATHER}:weather.intent", {}, mine)
            r.feed("ovos.utterance.handled", {}, mine)
        threading.Timer(0.3, later).start()

    seen = []
    runner, done = _runner([_step()], reply)
    runner._on_step_done = lambda i, n, s, res, obs: seen.append((res.status, obs.intents))
    runner.run()
    assert seen == [("pass", [f"{WEATHER}:weather.intent"])]


def test_ocp_takeover_is_reported_not_a_timeout():
    obs = StepObservation()
    observe(obs, "ocp:play", {"query": "a metronome"}, {})
    step = ScriptStep("start a metronome", "en-us", "ovos-skill-metronome.andlo", None)
    result = evaluate(step, obs, timed_out=True)
    assert result.status == FAIL and "ocp:play" in result.detail


def test_run_summary_lists_every_session_used_even_when_stopped():
    def reply(r, step):
        r.feed(f"{WEATHER}:weather.intent")
        r.feed("ovos.utterance.handled")
        if len(r.session_ids) == 2:
            r.stop()

    runner, done = _runner([_step(), _step(), _step()], reply)
    summary = runner.run()
    assert summary.cancelled and len(summary.session_ids) == 2
    assert len(set(summary.session_ids)) == 2


# --- common-reading provider skills: the pipeline matches, a provider serves ---

READER = "ovos-common-reading-pipeline-plugin.andlo"
ANDERSEN = "ovos-skill-andersen-tales.andlo"


def test_the_provider_fetched_from_counts_not_the_pipeline_that_matched():
    obs = StepObservation()
    observe(obs, f"{READER}:read_by_collection", {}, {})
    observe(obs, "ovos.common_reading.search", {"query": "andersen"}, {})
    assert obs.awaiting_provider
    observe(obs, f"ovos.common_reading.fetch_content.{ANDERSEN}", {"content_id": "x"}, {})
    assert not obs.awaiting_provider and obs.provider == ANDERSEN
    step = ScriptStep("tell me a story from andersen", "en-us", ANDERSEN, None)
    assert evaluate(step, obs).status == PASS


def test_a_search_response_alone_does_not_make_every_provider_pass():
    obs = StepObservation()
    observe(obs, f"{READER}:read_by_collection", {}, {})
    observe(obs, "ovos.common_reading.search", {}, {})
    observe(obs, "ovos.common_reading.search.response", {"skill_id": ANDERSEN}, {})
    step = ScriptStep("tell me a story from andersen", "en-us", ANDERSEN, None)
    assert evaluate(step, obs).status == FAIL


def test_a_different_provider_fails_and_says_which_one_read():
    obs = StepObservation()
    observe(obs, f"{READER}:read_by_collection", {}, {})
    observe(obs, "ovos.common_reading.search", {}, {})
    observe(obs, "ovos.common_reading.fetch_content.ovos-skill-grimm-tales.andlo", {}, {})
    result = evaluate(ScriptStep("a story from andersen", "en-us", ANDERSEN, None), obs)
    assert result.status == FAIL
    assert "read from ovos-skill-grimm-tales.andlo" in result.detail


def test_runner_waits_for_the_fetch_that_comes_after_the_handler_is_done():
    import threading

    def reply(runner, step):
        runner.feed(f"{READER}:read_by_collection")
        runner.feed("ovos.common_reading.search")
        runner.feed("mycroft.skill.handler.complete")
        runner.feed("ovos.utterance.handled")
        # the pipeline fetches the chosen story a moment later (seen live)
        threading.Timer(0.3, runner.feed, args=(f"ovos.common_reading.fetch_content.{ANDERSEN}",)).start()

    runner, done = _runner([ScriptStep("a story from andersen", "en-us", ANDERSEN, None)], reply,
                           provider_wait=2)
    runner.run()
    assert done == [(1, PASS)]


def test_runner_does_not_wait_when_no_reading_search_happened():
    import time
    runner, done = _runner([ScriptStep("what's the weather", "en-us", WEATHER, "weather.intent")],
                           lambda r, s: (r.feed(f"{WEATHER}:weather.intent"), r.feed("ovos.utterance.handled")),
                           provider_wait=5)
    start = time.monotonic()
    runner.run()
    assert done == [(1, PASS)] and time.monotonic() - start < 2


# --- a story read by a provider is stopped once the step is judged ---

def test_a_story_is_stopped_in_its_own_session_after_the_verdict():
    import threading
    stopped = []

    def reply(runner, step):
        runner.feed(f"{READER}:read_by_collection")
        runner.feed("ovos.common_reading.search")
        runner.feed("ovos.utterance.handled")
        runner.feed(f"ovos.common_reading.fetch_content.{ANDERSEN}")
        # the story starts, and would go on for minutes
        threading.Timer(0.2, runner.feed, args=("recognizer_loop:audio_output_start",)).start()

    def stop(session_id):
        stopped.append(session_id)
        runner.feed("recognizer_loop:audio_output_end")

    runner, done = _runner([ScriptStep("a story from andersen", "en-us", ANDERSEN, None)], reply,
                           provider_wait=2, stop_session=stop, speech_timeout=30)
    import time
    start = time.monotonic()
    runner.run()
    assert done == [(1, PASS)]
    assert stopped == [runner.session_ids[0]]
    assert time.monotonic() - start < 5  # didn't sit through SPEECH_TIMEOUT


def test_a_story_is_stopped_even_if_its_reading_never_starts():
    stopped = []

    def reply(runner, step):
        runner.feed(f"{READER}:read_by_collection")
        runner.feed("ovos.common_reading.search")
        runner.feed("ovos.utterance.handled")
        runner.feed(f"ovos.common_reading.fetch_content.{ANDERSEN}")

    runner, done = _runner([ScriptStep("a story from andersen", "en-us", ANDERSEN, None)], reply,
                           provider_wait=1, stop_session=stopped.append,
                           story_start_wait=0.2, stop_wait=0.1)
    runner.run()
    assert stopped == runner.session_ids


def test_every_step_is_stopped_in_its_own_session_afterwards():
    # e.g. "count forever": short replies, one after another, never ending
    stopped = []

    def reply(r, step):
        r.feed(f"{WEATHER}:weather.intent")
        r.feed("recognizer_loop:audio_output_start")
        r.feed("ovos.utterance.handled")
        r.feed("recognizer_loop:audio_output_end")

    runner, done = _runner([_step(), _step()], reply, stop_session=stopped.append)
    runner.run()
    assert done == [(1, PASS), (2, PASS)]
    assert stopped == runner.session_ids and len(set(stopped)) == 2


def test_speech_still_going_after_the_speech_timeout_is_stopped():
    stopped = []

    def reply(r, step):
        r.feed(f"{WEATHER}:weather.intent")
        r.feed("recognizer_loop:audio_output_start")
        r.feed("ovos.utterance.handled")

    runner, done = _runner([_step()], reply, stop_session=stopped.append,
                           speech_timeout=0.2, stop_wait=0.1)
    runner.run()
    assert stopped == runner.session_ids


def test_a_slow_announcement_before_the_fetch_does_not_fail_the_step():
    # seen live (Andersen on alpha): the pipeline matches and searches,
    # then its handler sits in a long wait for the announcement to be
    # spoken - silence on the bus - and only then fetches the story
    import threading

    def reply(runner, step):
        runner.feed(f"{READER}:read_by_collection")
        runner.feed("ovos.common_reading.search")

        def later():
            runner.feed("mycroft.skill.handler.complete")
            runner.feed("ovos.utterance.handled")
            runner.feed(f"ovos.common_reading.fetch_content.{ANDERSEN}")
        threading.Timer(0.8, later).start()

    runner, done = _runner([ScriptStep("a story from andersen", "en-us", ANDERSEN, None)], reply,
                           quiet_after_match=0.1, provider_wait=0.5, stop_session=lambda s: None,
                           story_start_wait=0.1, stop_wait=0.1)
    runner.run()
    assert done == [(1, PASS)]


def test_a_slow_announcement_before_the_fetch_does_not_fail_the_step():
    # seen live (Andersen on alpha): the pipeline matches and searches,
    # then its handler sits in a long wait for the announcement to be
    # spoken - silence on the bus - and only then fetches the story
    import threading

    def reply(runner, step):
        runner.feed(f"{READER}:read_by_collection")
        runner.feed("ovos.common_reading.search")

        def later():
            runner.feed("mycroft.skill.handler.complete")
            runner.feed("ovos.utterance.handled")
            runner.feed(f"ovos.common_reading.fetch_content.{ANDERSEN}")
        threading.Timer(0.8, later).start()

    runner, done = _runner([ScriptStep("a story from andersen", "en-us", ANDERSEN, None)], reply,
                           quiet_after_match=0.1, provider_wait=0.5, stop_session=lambda s: None,
                           story_start_wait=0.1, stop_wait=0.1)
    runner.run()
    assert done == [(1, PASS)]


def test_observe_counts_the_spec_speak_message_and_one_dual_emit():
    from ovos_tui_client.scripts import StepObservation, observe
    obs = StepObservation()
    observe(obs, "ovos.utterance.speak", {"utterance": "It is nine"}, {})
    assert obs.spoke == ["It is nine"]
    observe(obs, "speak", {"utterance": "It is nine"}, {})   # legacy copy of the same
    assert obs.spoke == ["It is nine"]
    observe(obs, "ovos.utterance.speak", {"utterance": "Anything else?"}, {})
    assert obs.spoke == ["It is nine", "Anything else?"]


# --- a timed-out step: wait for OVOS to finish it before the next one ---

def test_timeout_waits_for_core_and_says_how_long_it_took():
    import threading
    busy = []

    def reply(r, step):
        if len(r.session_ids) == 1:
            # core answers long after the step timeout (queued behind a slow fallback)
            def late():
                r.feed(f"{WEATHER}:weather.intent")
                r.feed("ovos.utterance.handled")
            threading.Timer(0.5, late).start()
        else:
            r.feed(f"{WEATHER}:weather.intent")
            r.feed("ovos.utterance.handled")

    runner, done = _runner([_step(), _step()], reply, step_timeout=0.2, busy_wait=5,
                           on_busy=lambda i, n, s, w: busy.append(i))
    summary = runner.run()
    assert busy == [1]
    first = summary.results[0][2]
    assert first.status == TIMEOUT and "OVOS finished it after" in first.detail
    assert "handled by" in first.detail
    # the next step was sent only after core was done, so it passes
    assert summary.results[1][2].status == PASS


def test_timeout_gives_up_waiting_after_busy_wait():
    runner, done = _runner([_step()], lambda r, s: None, step_timeout=0.1, busy_wait=0.3)
    summary = runner.run()
    result = summary.results[0][2]
    assert result.status == TIMEOUT and "still busy" in result.detail


def test_norm_intent_treats_camelcase_like_snake_case():
    # ovos-skill-alerts 0.1.28 (testing channel) dispatches 'CancelAlert';
    # the golden file on its default branch expects 'cancel_alert'
    from ovos_tui_client.scripts import _norm_intent
    assert _norm_intent("ovos-skill-alerts.openvoiceos:CancelAlert") == \
        _norm_intent("ovos-skill-alerts.openvoiceos:cancel_alert")
    assert _norm_intent("x.y:what.time.is.it.intent") == _norm_intent("x.y:what_time_is_it")
    assert _norm_intent("x.y:CancelAlert") != _norm_intent("x.y:ListAlerts")


def test_step_left_in_get_response_is_answered_cancel_in_its_own_session():
    # py-spy on a hung ovos-core: 8/8 handler threads waiting forever in
    # ask_yesno for sessions no one would ever answer
    answered, stopped = [], []
    holder = {}

    def reply(r, step):
        r.feed(f"{WEATHER}:weather.intent")
        r.feed("skill.converse.get_response.enable", {"skill_id": WEATHER},
               {"session": {"session_id": r.session_id, "response_mode": [WEATHER]}})
        r.feed("ovos.utterance.handled")

    def answer(session, text, lang):
        # the full session the skill serialized, not just its id
        assert session.get("response_mode") == [WEATHER]
        answered.append((session["session_id"], text, lang))
        # the skill gets its answer: cancel -> get_response returns
        holder["r"].feed(f"{WEATHER}.converse.get_response", {})
        holder["r"].feed("skill.converse.get_response.disable", {"skill_id": WEATHER})

    runner, done = _runner([_step(), _step()], reply, stop_session=stopped.append, answer=answer)
    holder["r"] = runner
    runner.run()
    assert done == [(1, PASS), (2, PASS)]    # the cancel traffic doesn't change the verdict
    assert [a[0] for a in answered] == runner.session_ids
    assert all(a[1] == "cancel" and a[2] == "en-us" for a in answered)
    assert runner.released_responses == 2
    assert stopped == runner.session_ids


def test_no_cancel_sent_when_nothing_waits_for_an_answer():
    answered = []

    def reply(r, step):
        r.feed(f"{WEATHER}:weather.intent")
        r.feed("skill.converse.get_response.enable", {"skill_id": WEATHER})
        r.feed("skill.converse.get_response.disable", {"skill_id": WEATHER})
        r.feed("ovos.utterance.handled")

    runner, done = _runner([_step()], reply, stop_session=lambda s: None,
                           answer=lambda *a: answered.append(a))
    runner.run()
    assert done == [(1, PASS)] and answered == [] and runner.released_responses == 0
