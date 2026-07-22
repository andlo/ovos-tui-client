"""Tests for OVOSBusConnection using a fake MessageBusClient - no real
network connection needed."""
import json
from unittest.mock import MagicMock

from ovos_tui_client.bus import OVOSBusConnection


def _make_connection():
    fake_client = MagicMock()
    conn = OVOSBusConnection(client=fake_client)
    return conn, fake_client


def test_send_utterance_emits_recognizer_loop_utterance():
    conn, fake_client = _make_connection()

    conn.send_utterance("read me a grimm story")

    sent = fake_client.emit.call_args[0][0]
    assert sent.msg_type == "recognizer_loop:utterance"
    assert sent.data["utterances"] == ["read me a grimm story"]
    assert sent.data["lang"] == "en-us"
    assert "utterance_id" in sent.data


def test_send_utterance_uses_configured_lang():
    fake_client = MagicMock()
    conn = OVOSBusConnection(client=fake_client, lang="da-dk")

    conn.send_utterance("hej")

    sent = fake_client.emit.call_args[0][0]
    assert sent.data["lang"] == "da-dk"


def test_connect_registers_speak_handler_and_starts_thread():
    conn, fake_client = _make_connection()

    conn.connect()

    fake_client.on.assert_any_call("speak", conn._on_speak)
    fake_client.on.assert_any_call("message", conn._on_raw_message)
    fake_client.run_in_thread.assert_called_once()


def test_on_speak_calls_registered_handlers_with_utterance_text():
    conn, _ = _make_connection()
    received = []
    conn.on_speak(lambda text: received.append(text))

    fake_message = MagicMock()
    fake_message.data = {"utterance": "Here is Cinderella, by the Brothers Grimm"}
    conn._on_speak(fake_message)

    assert received == ["Here is Cinderella, by the Brothers Grimm"]


def test_on_speak_supports_multiple_handlers():
    conn, _ = _make_connection()
    calls = {"a": 0, "b": 0}
    conn.on_speak(lambda text: calls.__setitem__("a", calls["a"] + 1))
    conn.on_speak(lambda text: calls.__setitem__("b", calls["b"] + 1))

    fake_message = MagicMock()
    fake_message.data = {"utterance": "hello"}
    conn._on_speak(fake_message)

    assert calls == {"a": 1, "b": 1}


def test_on_speak_handles_missing_utterance_key_gracefully():
    conn, _ = _make_connection()
    received = []
    conn.on_speak(lambda text: received.append(text))

    fake_message = MagicMock()
    fake_message.data = {}
    conn._on_speak(fake_message)

    assert received == [""]


def test_on_activity_receives_summarized_lines():
    conn, _ = _make_connection()
    received = []
    conn.on_activity(lambda line: received.append(line))

    fake_message = MagicMock()
    fake_message.msg_type = "ovos.common_reading.ping"
    fake_message.data = {}
    conn._on_any_message(fake_message)

    assert received == ["📡 pipeline: pinging providers (0 candidates so far)"]


def test_on_activity_skips_unrecognized_message_types():
    conn, _ = _make_connection()
    received = []
    conn.on_activity(lambda line: received.append(line))

    fake_message = MagicMock()
    fake_message.msg_type = "some.internal.noise"
    fake_message.data = {}
    conn._on_any_message(fake_message)

    assert received == []


def test_on_activity_supports_multiple_handlers():
    conn, _ = _make_connection()
    calls = {"a": 0, "b": 0}
    conn.on_activity(lambda line: calls.__setitem__("a", calls["a"] + 1))
    conn.on_activity(lambda line: calls.__setitem__("b", calls["b"] + 1))

    fake_message = MagicMock()
    fake_message.msg_type = "intent_failure"
    fake_message.data = {}
    conn._on_any_message(fake_message)

    assert calls == {"a": 1, "b": 1}


def test_on_raw_message_deserializes_before_summarizing():
    """Regression guard for the actual bug found in testing: the bus's
    'message' catch-all emits a raw JSON STRING, not a Message object
    (confirmed by reading ovos_bus_client's source) - _on_raw_message
    must deserialize it first, or the activity pane silently never
    receives anything."""
    conn, _ = _make_connection()
    received = []
    conn.on_activity(lambda line: received.append(line))

    raw_json = json.dumps({"type": "ovos.common_reading.ping", "data": {}, "context": {}})
    conn._on_raw_message(raw_json)

    assert received == ["📡 pipeline: pinging providers (0 candidates so far)"]


def test_on_raw_message_ignores_malformed_json_without_crashing():
    conn, _ = _make_connection()
    received = []
    conn.on_activity(lambda line: received.append(line))

    conn._on_raw_message("not valid json at all {{{")

    assert received == []


def test_list_skills_sends_skillmanager_list_request():
    conn, fake_client = _make_connection()
    conn.list_skills(lambda skills: None, timer_factory=MagicMock())

    fake_client.emit.assert_called_once()
    sent = fake_client.emit.call_args[0][0]
    assert sent.msg_type == "skillmanager.list"


def test_list_skills_registers_a_once_handler():
    conn, fake_client = _make_connection()
    conn.list_skills(lambda skills: None, timer_factory=MagicMock())

    fake_client.once.assert_called_once()
    assert fake_client.once.call_args[0][0] == "mycroft.skills.list"


def test_list_skills_calls_callback_with_skills_on_response():
    conn, fake_client = _make_connection()
    received = []
    conn.list_skills(lambda skills: received.append(skills), timer_factory=MagicMock())

    response_handler = fake_client.once.call_args[0][1]
    fake_message = MagicMock()
    fake_message.data = {"skills": ["ovos-skill-grimm-tales.andlo", "ovos-skill-andersen-tales.andlo"]}
    response_handler(fake_message)

    assert received == [["ovos-skill-grimm-tales.andlo", "ovos-skill-andersen-tales.andlo"]]


def test_list_skills_falls_back_to_data_keys_if_no_skills_field():
    """Defensive fallback for the case where the response format
    differs from the assumed convention - see the honesty note in
    list_skills()'s docstring about this being unverified."""
    conn, fake_client = _make_connection()
    received = []
    conn.list_skills(lambda skills: received.append(skills), timer_factory=MagicMock())

    response_handler = fake_client.once.call_args[0][1]
    fake_message = MagicMock()
    fake_message.data = {"ovos-skill-grimm-tales.andlo": {}, "ovos-skill-andersen-tales.andlo": {}}
    response_handler(fake_message)

    assert set(received[0]) == {"ovos-skill-grimm-tales.andlo", "ovos-skill-andersen-tales.andlo"}


def test_list_skills_calls_callback_with_none_on_timeout():
    conn, fake_client = _make_connection()
    received = []

    class ImmediateTimer:
        def __init__(self, interval, function):
            self.function = function

        def start(self):
            self.function()  # fire immediately, simulating timeout

    conn.list_skills(lambda skills: received.append(skills), timer_factory=ImmediateTimer)

    assert received == [None]


def test_list_skills_timeout_does_not_fire_if_response_already_received():
    conn, fake_client = _make_connection()
    received = []
    fired_timer = {}

    class DeferredTimer:
        def __init__(self, interval, function):
            fired_timer["fn"] = function

        def start(self):
            pass  # deliberately doesn't fire yet - fired manually below

    conn.list_skills(lambda skills: received.append(skills), timer_factory=DeferredTimer)
    response_handler = fake_client.once.call_args[0][1]
    fake_message = MagicMock()
    fake_message.data = {"skills": ["ovos-skill-grimm-tales.andlo"]}

    response_handler(fake_message)  # the real response arrives first
    fired_timer["fn"]()  # then the timeout check runs afterward

    # only the real response should have reached the callback - the
    # timeout check must see state["received"] is already True and
    # skip calling back with None
    assert received == [["ovos-skill-grimm-tales.andlo"]]
