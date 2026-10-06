"""Wraps ovos_bus_client for the TUI's needs: sending an utterance as
if it came from STT, and a callback-based interface for incoming
'speak' events (OVOS's response) - decoupled from Textual itself so
this module has no UI framework dependency and can be tested without
spinning up a real App."""
import socket
import threading
import time
import uuid

from ovos_bus_client import MessageBusClient, Message

from ovos_tui_client.activity import summarize_message

# Context key every ovos-tui-client puts on what it sends (#32), so each
# instance can tell its own utterances/events from another TUI's.
# Namespaced on purpose: never collides with OVOS's own routing keys
# (source/destination/session/client_name).
TUI_CONTEXT_KEY = "ovos_tui_client"
TUI_EVENT_PREFIX = "ovos.tui."

# ovos-dinkum-listener's own client_name (confirmed in its source:
# service.py builds the utterance context with this client_name and
# source="audio").
LISTENER_CLIENT_NAMES = {"ovos_dinkum_listener", "ovos_listener", "mycroft_listener"}

# What OVOS says. 'speak' is the classic message; newer cores (the alpha
# channel, ovos-core 3.x) emit the spec name 'ovos.utterance.speak' instead,
# with no legacy copy - seen live: a date-time answer arrived only as
# ovos.utterance.speak. A core in a dual-emit transition may send both, so
# listeners take either and drop the immediate duplicate.
SPEAK_TYPES = ("speak", "ovos.utterance.speak")
DUPLICATE_SPEAK_WINDOW = 0.5  # seconds


def describe_speaker(context: dict) -> str:
    """Short label for who said an utterance, from its bus context."""
    context = context or {}
    tui = context.get(TUI_CONTEXT_KEY)
    if isinstance(tui, dict):
        return f"💻 {tui.get('host') or 'another TUI'}"
    client = context.get("client_name")
    if client in LISTENER_CLIENT_NAMES:
        return "🎤 Mic"
    session = context.get("session")
    session_id = session.get("session_id") if isinstance(session, dict) else None
    label = client or (context.get("source") if isinstance(context.get("source"), str) else None)
    if session_id and session_id != "default":
        label = f"{label} · {session_id}" if label else session_id
    return f"🗣 {label or 'someone'}"


class OVOSBusConnection:
    def __init__(self, host="127.0.0.1", port=8181, lang="en-us", client=None):
        """`client` is injectable for testing - defaults to a real
        MessageBusClient against (host, port)."""
        self.lang = lang
        self._client = client or MessageBusClient(host=host, port=port)
        self._speak_handlers = []
        self._activity_handlers = []
        self._message_handlers = []
        self._heard_handlers = []
        self._tui_event_handlers = []
        self.instance_id = uuid.uuid4().hex[:8]
        try:
            self.host = socket.gethostname()
        except OSError:
            self.host = "unknown"

    def connect(self):
        for msg_type in SPEAK_TYPES:
            self._client.on(msg_type, self._on_speak)
        self._client.on("message", self._on_raw_message)
        self._client.run_in_thread()

    def _on_speak(self, message):
        utterance = message.data.get("utterance", "")
        now = time.monotonic()
        last = getattr(self, "_last_speak", None)
        if last and last[0] == utterance and now - last[1] < DUPLICATE_SPEAK_WINDOW:
            return  # the same sentence under the other name (dual emit)
        self._last_speak = (utterance, now)
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
        curated subset worth showing reaches the activity handlers.
        Raw-message handlers (on_message(), used by scripted test runs)
        see every message first, unfiltered."""
        for handler in self._message_handlers:
            try:
                handler(message.msg_type, message.data or {}, message.context or {})
            except Exception:
                pass
        self._route_others(message)
        line = summarize_message(message.msg_type, message.data)
        if line is None:
            return
        for handler in self._activity_handlers:
            handler(line)

    def is_own(self, context: dict) -> bool:
        tui = (context or {}).get(TUI_CONTEXT_KEY)
        return isinstance(tui, dict) and tui.get("instance") == self.instance_id

    def _route_others(self, message):
        """#32: utterances and TUI script events that did NOT come from
        this instance - the mic, HiveMind, another TUI - go to the
        heard/tui-event handlers so the conversation pane can show them."""
        context = message.context or {}
        if self.is_own(context):
            return
        if message.msg_type == "recognizer_loop:utterance":
            utterances = (message.data or {}).get("utterances") or []
            text = utterances[0] if utterances else ""
            if not text:
                return
            for handler in self._heard_handlers:
                try:
                    handler(text, context)
                except Exception:
                    pass
        elif message.msg_type.startswith(TUI_EVENT_PREFIX):
            for handler in self._tui_event_handlers:
                try:
                    handler(message.msg_type, message.data or {}, context)
                except Exception:
                    pass

    def on_heard(self, handler):
        """callback(text, context) for utterances sent by anyone else."""
        self._heard_handlers.append(handler)

    def on_tui_event(self, handler):
        """callback(msg_type, data, context) for other TUIs' ovos.tui.* events."""
        self._tui_event_handlers.append(handler)

    def _tui_context(self, script=None) -> dict:
        marker = {"instance": self.instance_id, "host": self.host}
        if script:
            marker["script"] = script
        return {TUI_CONTEXT_KEY: marker}

    def emit_tui_event(self, name: str, data: dict):
        """Announces something this TUI is doing (e.g. a script run) so
        other TUIs on the same bus can show it - see #32."""
        self._client.emit(Message(TUI_EVENT_PREFIX + name, data, self._tui_context()))

    def request(self, msg_type: str, data: dict, reply_type: str, timeout: float = 5.0):
        """Asks OVOS and waits for the reply's data, or None (no reply, an
        older core without that handler). For #48's diagnosis probes,
        which never run a handler."""
        try:
            reply = self._client.wait_for_response(Message(msg_type, data or {}, self._tui_context()),
                                                   reply_type=reply_type, timeout=timeout)
        except Exception:  # noqa: BLE001 - a probe never breaks anything
            return None
        return reply.data if reply is not None else None

    def stop_all(self):
        """mycroft.stop with no session: what saying 'stop' does. For a skill
        that ignored the stop to its own session (#74)."""
        self._client.emit(Message("mycroft.stop", {}, self._tui_context()))

    def stop_session(self, session_id: str):
        """mycroft.stop scoped to one session - stops whatever a test step
        started there. A stop in the default session doesn't reach it."""
        context = self._tui_context()
        # a full serialized session carries the active skills the stop
        # service asks - with just the id nobody in it is stopped
        context["session"] = dict(session_id) if isinstance(session_id, dict) else {"session_id": session_id}
        self._client.emit(Message("mycroft.stop", {}, context))

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

    def on_message(self, handler):
        """Registers a callback(msg_type, data, context) called for
        EVERY bus message, unfiltered - for scripted test runs
        (scripts.ScriptRunner.feed), which need to see intent
        dispatches and end-markers the activity summarizer skips."""
        self._message_handlers.append(handler)

    def send_utterance(self, text, lang=None, session_id=None, script=None):
        """Simulates what a real STT pipeline would emit after hearing
        speech - the standard event every OVOS intent/pipeline handler
        listens for, regardless of how the text arrived. `lang`
        overrides the connection's default language for this one
        utterance (golden-utterance rows carry their own).

        `session_id` sends it in its own OVOS session instead of the
        default one - used by scripted test runs so each step starts
        clean: a skill left waiting in get_response() in the default
        session (or a previous step's "shall I read this one?") can't
        capture the next step. Same isolation ovoscope's golden tests
        use (one session per row); confirmed live that a fresh session
        is routed normally while the default one was captured."""
        context = self._tui_context(script)
        if isinstance(session_id, dict):
            # a full serialized session (e.g. one a skill left waiting in
            # get_response) - its state has to travel with the utterance
            context["session"] = dict(session_id)
        elif session_id:
            context["session"] = {"session_id": session_id}
        self._client.emit(Message("recognizer_loop:utterance", {
            "utterances": [text],
            "lang": lang or self.lang,
            "utterance_id": str(uuid.uuid4()),
        }, context))

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
