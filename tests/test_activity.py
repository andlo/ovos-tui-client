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
