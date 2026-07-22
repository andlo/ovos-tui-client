"""Wraps ovos_bus_client for the TUI's needs: sending an utterance as
if it came from STT, and a callback-based interface for incoming
'speak' events (OVOS's response) - decoupled from Textual itself so
this module has no UI framework dependency and can be tested without
spinning up a real App."""
import uuid

from ovos_bus_client import MessageBusClient, Message


class OVOSBusConnection:
    def __init__(self, host="127.0.0.1", port=8181, lang="en-us", client=None):
        """`client` is injectable for testing - defaults to a real
        MessageBusClient against (host, port)."""
        self.lang = lang
        self._client = client or MessageBusClient(host=host, port=port)
        self._speak_handlers = []

    def connect(self):
        self._client.on("speak", self._on_speak)
        self._client.run_in_thread()

    def _on_speak(self, message):
        utterance = message.data.get("utterance", "")
        for handler in self._speak_handlers:
            handler(utterance)

    def on_speak(self, handler):
        """Registers a callback(utterance: str) called whenever OVOS
        speaks. Multiple handlers can be registered (e.g. the
        conversation pane AND a transcript logger)."""
        self._speak_handlers.append(handler)

    def send_utterance(self, text):
        """Simulates what a real STT pipeline would emit after hearing
        speech - the standard event every OVOS intent/pipeline handler
        listens for, regardless of how the text arrived."""
        self._client.emit(Message("recognizer_loop:utterance", {
            "utterances": [text],
            "lang": self.lang,
            "utterance_id": str(uuid.uuid4()),
        }))
