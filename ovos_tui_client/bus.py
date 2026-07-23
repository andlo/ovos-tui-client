"""Wraps ovos_bus_client for the TUI's needs: sending an utterance as
if it came from STT, and a callback-based interface for incoming
'speak' events (OVOS's response) - decoupled from Textual itself so
this module has no UI framework dependency and can be tested without
spinning up a real App."""
import threading
import uuid

from ovos_bus_client import MessageBusClient, Message

from ovos_tui_client.activity import summarize_message


class OVOSBusConnection:
    def __init__(self, host="127.0.0.1", port=8181, lang="en-us", client=None):
        """`client` is injectable for testing - defaults to a real
        MessageBusClient against (host, port)."""
        self.lang = lang
        self._client = client or MessageBusClient(host=host, port=port)
        self._speak_handlers = []
        self._activity_handlers = []

    def connect(self):
        self._client.on("speak", self._on_speak)
        self._client.on("message", self._on_raw_message)
        self._client.run_in_thread()

    def _on_speak(self, message):
        utterance = message.data.get("utterance", "")
        for handler in self._speak_handlers:
            handler(utterance)

    def _on_raw_message(self, raw):
        """The bus's 'message' catch-all event emits the raw serialized
        JSON string, NOT a parsed Message object (confirmed by reading
        ovos_bus_client.client.MessageBusClient.on_message's source -
        it does `self.emitter.emit('message', message)` with the raw
        string, separately from `self.emitter.emit(parsed_message.msg_type,
        parsed_message)` for the real object). Deserializing here was
        the missing step that silently made the whole activity pane a
        no-op."""
        try:
            message = Message.deserialize(raw)
        except Exception:
            return
        self._on_any_message(message)

    def _on_any_message(self, message):
        """Routes every bus message through the activity summarizer -
        most are skipped (summarize_message returns None), only the
        curated subset worth showing reaches the activity handlers."""
        line = summarize_message(message.msg_type, message.data)
        if line is None:
            return
        for handler in self._activity_handlers:
            handler(line)

    def on_speak(self, handler):
        """Registers a callback(utterance: str) called whenever OVOS
        speaks. Multiple handlers can be registered (e.g. the
        conversation pane AND a transcript logger)."""
        self._speak_handlers.append(handler)

    def on_activity(self, handler):
        """Registers a callback(summary_line: str) called for every
        bus message the activity summarizer considers worth showing -
        see activity.py for the curated list."""
        self._activity_handlers.append(handler)

    def send_utterance(self, text):
        """Simulates what a real STT pipeline would emit after hearing
        speech - the standard event every OVOS intent/pipeline handler
        listens for, regardless of how the text arrived."""
        self._client.emit(Message("recognizer_loop:utterance", {
            "utterances": [text],
            "lang": self.lang,
            "utterance_id": str(uuid.uuid4()),
        }))

    def list_skills(self, callback, timeout=5, timer_factory=None):
        """Requests the list of currently loaded skills via the classic
        mycroft-core 'skillmanager.list' -> 'mycroft.skills.list'
        bus convention (OVOS maintains backward compatibility with
        most mycroft-core bus messages). Calls callback(skills) once
        a response arrives, or callback(None) if nothing arrives within
        `timeout` seconds.

        `skills` is a dict of skill_id -> active (True/False/None -
        None means the skill reported an unknown/unset state, not that
        it's inactive). Confirmed directly against a live OVOS
        instance: the real response shape is
        {"skill_id": {"active": bool_or_none, "id": "skill_id"}, ...},
        keyed by skill_id - not a flat list under a "skills" key as
        the mycroft-core docs alone would suggest. Handled here so
        callers just get a clean skill_id -> active mapping.

        `timer_factory` is injectable for testing (defaults to
        threading.Timer) so tests don't have to sleep for real."""
        state = {"received": False}

        def _on_response(message):
            state["received"] = True
            raw = message.data.get("skills")
            if raw is None:
                raw = message.data
            if isinstance(raw, dict):
                skills = {
                    skill_id: (info.get("active") if isinstance(info, dict) else None)
                    for skill_id, info in raw.items()
                }
            else:
                # defensive fallback if some OVOS version really does
                # respond with a flat list instead - active state
                # simply isn't available in that shape
                skills = {skill_id: None for skill_id in raw}
            callback(skills)

        self._client.once("mycroft.skills.list", _on_response)
        self._client.emit(Message("skillmanager.list"))

        def _timeout_check():
            if not state["received"]:
                callback(None)

        timer_factory = timer_factory or threading.Timer
        timer_factory(timeout, _timeout_check).start()

    def activate_skill(self, skill_id: str):
        """Re-enables a previously deactivated skill via the classic
        mycroft-core 'skillmanager.activate' bus convention - the
        sibling message to 'skillmanager.list' (used by list_skills()
        above), from the same mycroft-core SkillManager source. Fire-
        and-forget: unlike list_skills(), there's no documented
        response event to wait for, so this doesn't take a callback.

        The exact payload key ('skill') is based on the documented
        mycroft-core convention, not verified against a live modern
        OVOS instance - same honesty caveat as list_skills()."""
        self._client.emit(Message("skillmanager.activate", {"skill": skill_id}))

    def deactivate_skill(self, skill_id: str):
        """Disables a skill via 'skillmanager.deactivate' - see
        activate_skill()'s docstring for the same caveats."""
        self._client.emit(Message("skillmanager.deactivate", {"skill": skill_id}))
