"""Tests for the bus-message -> activity-line translator."""
from ovos_tui_client.activity import summarize_message


def test_utterance_heard():
    line = summarize_message("recognizer_loop:utterance", {"utterances": ["read me a grimm story"]})
    assert line == '→ heard: "read me a grimm story"'


def test_skill_handler_start_prefers_name_over_skill_id():
    line = summarize_message("mycroft.skill.handler.start", {"name": "GrimmTales", "skill_id": "ovos-skill-grimm-tales.andlo"})
    assert line == "▶ GrimmTales is handling this"


def test_skill_handler_start_falls_back_to_skill_id():
    line = summarize_message("mycroft.skill.handler.start", {"skill_id": "ovos-skill-grimm-tales.andlo"})
    assert line == "▶ ovos-skill-grimm-tales.andlo is handling this"


def test_skill_handler_complete():
    line = summarize_message("mycroft.skill.handler.complete", {"skill_id": "ovos-skill-grimm-tales.andlo"})
    assert line == "✓ ovos-skill-grimm-tales.andlo finished"


def test_intent_failure():
    assert summarize_message("intent_failure", {}) == "✗ no skill matched"
    assert summarize_message("complete_intent_failure", {}) == "✗ no skill matched"


def test_common_reading_search_broadcast():
    line = summarize_message("ovos.common_reading.search", {"phrase": "a lighthouse"})
    assert line == "🔍 pipeline: asking all providers to search"


def test_common_reading_search_response_with_confidence():
    line = summarize_message("ovos.common_reading.search.response", {
        "skill_id": "ovos-skill-grimm-tales.andlo", "title": "Cinderella", "confidence": 0.91,
    })
    assert line == '📥 ovos-skill-grimm-tales.andlo: "Cinderella" (0.91)'


def test_common_reading_search_response_without_confidence():
    line = summarize_message("ovos.common_reading.search.response", {
        "skill_id": "ovos-skill-grimm-tales.andlo", "title": "Cinderella",
    })
    assert line == '📥 ovos-skill-grimm-tales.andlo: "Cinderella"'


def test_common_reading_ping():
    assert summarize_message("ovos.common_reading.ping", {}) == "📡 pipeline: pinging providers (0 candidates so far)"


def test_common_reading_pong():
    line = summarize_message("ovos.common_reading.pong", {"skill_id": "ovos-skill-grimm-tales.andlo"})
    assert line == "📡 ovos-skill-grimm-tales.andlo: I'm here"


def test_fetch_content_request_extracts_skill_id_from_message_type():
    line = summarize_message("ovos.common_reading.fetch_content.ovos-skill-grimm-tales.andlo",
                              {"content_id": "Cinderella"})
    assert line == '📖 pipeline: fetching "Cinderella" from ovos-skill-grimm-tales.andlo'


def test_fetch_content_response_with_paragraphs():
    line = summarize_message("ovos.common_reading.fetch_content.response", {"paragraphs": ["p1", "p2", "p3"]})
    assert line == "✓ received 3 paragraph(s)"


def test_fetch_content_response_empty_means_failure():
    line = summarize_message("ovos.common_reading.fetch_content.response", {"paragraphs": []})
    assert line == "✗ empty response (fetch failed)"


def test_unrecognized_message_types_are_skipped():
    """The whole point is a curated, simplified summary - most bus
    traffic should NOT show up here."""
    assert summarize_message("some.random.internal.event", {"foo": "bar"}) is None
    assert summarize_message("speak", {"utterance": "hello"}) is None  # already shown in conversation pane
    assert summarize_message("gui.page.show", {}) is None


def test_wakeword_detected():
    assert summarize_message("recognizer_loop:wakeword") == "👂 wake word detected"


def test_speech_start():
    assert summarize_message("mycroft.audio.speech.start") == "🔊 speaking..."


def test_speech_stop():
    assert summarize_message("mycroft.audio.speech.stop") == "🔇 done speaking"


def test_global_stop():
    assert summarize_message("mycroft.stop") == "⏹ global stop triggered"


# --- fallback skill path (confirmed via live capture) ---

def test_fallback_skill_start():
    line = summarize_message(
        "ovos.skills.fallback.ovos-skill-fallback-unknown.openvoiceos.start", {}
    )
    assert line == "▶ ovos-skill-fallback-unknown.openvoiceos (fallback) is handling this"


def test_fallback_skill_response_success():
    line = summarize_message(
        "ovos.skills.fallback.ovos-skill-spelling.openvoiceos.response",
        {"result": True, "fallback_handler": "SpellingSkill.handle_fallback"},
    )
    assert line == "✓ ovos-skill-spelling.openvoiceos (fallback) finished"


def test_fallback_skill_response_could_not_resolve():
    """The real, common case: ovos-skill-fallback-unknown always
    reports result: false (it's specifically the catch-nothing-else-
    could skill) - confirmed via live capture."""
    line = summarize_message(
        "ovos.skills.fallback.ovos-skill-fallback-unknown.openvoiceos.response",
        {"result": False, "fallback_handler": "UnknownSkill.handle_fallback"},
    )
    assert line == "✗ ovos-skill-fallback-unknown.openvoiceos (fallback) could not resolve"


def test_fallback_skill_id_with_dots_extracted_correctly():
    """skill_ids routinely contain dots themselves (author.name
    convention) - suffix-stripping, not dot-splitting, must handle
    this correctly."""
    line = summarize_message("ovos.skills.fallback.ovos-skill-wolfie.openvoiceos.start", {})
    assert "ovos-skill-wolfie.openvoiceos" in line
    assert line.startswith("▶ ovos-skill-wolfie.openvoiceos")


def test_fallback_ping_pong_and_request_are_not_shown():
    """Deliberately skipped - pong fires once per skill that merely
    CLAIMS capability (often several), not the one actually invoked;
    showing every pong would overstate how many skills did something."""
    assert summarize_message("ovos.skills.fallback.ping", {"utterances": ["x"]}) is None
    assert summarize_message(
        "ovos.skills.fallback.pong", {"skill_id": "ovos-skill-wolfie.openvoiceos", "can_handle": True}
    ) is None
    assert summarize_message(
        "ovos.skills.fallback.ovos-skill-fallback-unknown.openvoiceos.request", {}
    ) is None


# --- Common Query path (confirmed via live capture) ---

def test_common_query_question():
    line = summarize_message("question:query", {"phrase": "what is the capital of france"})
    assert line == '🔍 asking all skills: "what is the capital of france"'


def test_common_query_response_with_answer():
    line = summarize_message("question:query.response", {
        "phrase": "what is the capital of france",
        "skill_id": "ovos-skill-wikipedia.openvoiceos",
        "searching": False,
        "answer": "Its capital, largest city and main cultural...",
    })
    assert line == '📥 ovos-skill-wikipedia.openvoiceos: "Its capital, largest city and main cultural..."'


def test_common_query_response_without_answer_still_shown():
    """Consistency with common-reading's own pong/search.response
    (shown regardless of confidence) and fetch_content.response's
    "empty response" - a skill visibly coming up empty is worth
    seeing, not silently dropped."""
    line = summarize_message("question:query.response", {
        "phrase": "what is the capital of france",
        "skill_id": "ovos-skill-ddg.openvoiceos",
        "searching": False,
    })
    assert line == "✗ ovos-skill-ddg.openvoiceos: no answer"


def test_common_query_response_searching_true_is_not_shown():
    """The transient "still working" phase - same reasoning as
    skipping fallback's ping/pong."""
    line = summarize_message("question:query.response", {
        "phrase": "what is the capital of france",
        "skill_id": "ovos-skill-wikihow.openvoiceos",
        "searching": True,
    })
    assert line is None


# --- question:action (confirmed via official OVOS message spec) ---

def test_common_query_action_selected():
    line = summarize_message("question:action", {
        "phrase": "what is the capital of france",
        "skill_id": "ovos-skill-wikipedia.openvoiceos",
        "callback_data": {},
    })
    assert line == "🏆 ovos-skill-wikipedia.openvoiceos selected to answer"


# --- OCP (media playback) search path (confirmed via live capture) ---

def test_ocp_play_kickoff():
    line = summarize_message("ocp:play", {"query": "jazz music", "media_type": 2})
    assert line == '🎵 OCP: searching for "jazz music"'


def test_ocp_skill_search_end():
    line = summarize_message("ovos.common_play.skill.search_end", {"skill_id": "ovos-skill-pyradios.openvoiceos"})
    assert line == "✓ ovos-skill-pyradios.openvoiceos: search complete"


def test_ocp_skill_search_start_not_shown():
    """Transient "I'm looking" phase - same reasoning as fallback's
    ping/pong."""
    line = summarize_message("ovos.common_play.skill.search_start", {"skill_id": "ovos-skill-pyradios.openvoiceos"})
    assert line is None


def test_ocp_query_response_not_shown():
    """Deliberately NOT shown - a single skill can send many of these
    per search (confirmed via live capture: youtube-music sent 10 for
    one query), and summarize_message() is a stateless pure function
    with no way to aggregate them into one line."""
    line = summarize_message("ovos.common_play.query.response", {
        "phrase": "jazz music", "skill_id": "ovos-skill-youtube-music.openvoiceos",
    })
    assert line is None


def test_ocp_playback_time_not_shown():
    """Fires continuously during actual playback - pure noise."""
    line = summarize_message("ovos.common_play.playback_time", {"position": 16.1, "length": 4590281.0})
    assert line is None


def test_ocp_gui_messages_not_shown():
    assert summarize_message("gui.value.set", {"footer_text": "Searching..."}) is None
    assert summarize_message("gui.page.show", {"page_names": ["busy"]}) is None
    assert summarize_message("gui.status.request", {}) is None
