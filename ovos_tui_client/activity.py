"""Translates raw OVOS bus messages into short, human-readable activity
lines - "what's happening right now", distinct from the verbose raw
logs. Deliberately curated: most message types return None (skipped),
since the whole point is a simplified summary, not a second log feed.

Pure function, no UI/bus dependency, so it's testable without a real
connection - see bus.py's on_activity() for how this gets wired up.
"""

FETCH_CONTENT_PREFIX = "ovos.common_reading.fetch_content."
FALLBACK_PREFIX = "ovos.skills.fallback."


def summarize_message(msg_type, data=None):
    """Returns a short status string for a bus message worth surfacing
    in the activity pane, or None if this message type should be
    skipped (the vast majority - this is a curated summary, not a
    second log)."""
    data = data or {}

    if msg_type == "recognizer_loop:utterance":
        utterances = data.get("utterances") or [""]
        return f'→ heard: "{utterances[0]}"'

    if msg_type == "mycroft.skill.handler.start":
        return f"▶ {_skill_label(data)} is handling this"

    if msg_type == "mycroft.skill.handler.complete":
        return f"✓ {_skill_label(data)} finished"

    if msg_type in ("intent_failure", "complete_intent_failure"):
        return "✗ no skill matched"

    if msg_type == "ovos.common_reading.search":
        return "🔍 pipeline: asking all providers to search"

    if msg_type == "ovos.common_reading.search.response":
        return _summarize_search_response(data)

    if msg_type == "ovos.common_reading.ping":
        return "📡 pipeline: pinging providers (0 candidates so far)"

    if msg_type == "ovos.common_reading.pong":
        return f"📡 {data.get('skill_id', '?')}: I'm here"

    if msg_type.startswith(FETCH_CONTENT_PREFIX) and not msg_type.endswith(".response"):
        skill_id = msg_type[len(FETCH_CONTENT_PREFIX):]
        content_id = data.get("content_id", "?")
        return f'📖 pipeline: fetching "{content_id}" from {skill_id}'

    if msg_type == "ovos.common_reading.fetch_content.response":
        n = len(data.get("paragraphs", []))
        return f"✓ received {n} paragraph(s)" if n else "✗ empty response (fetch failed)"

    # The FALLBACK path: when nothing else matches an utterance (a
    # very real, common case - garbled STT, genuinely unrecognized
    # phrasing), OVOS asks a chain of fallback skills whether they can
    # handle it. This was previously completely invisible in the
    # activity pane - it's a different, dynamic message-type family
    # (skill_id embedded in the type string itself, same pattern as
    # FETCH_CONTENT_PREFIX above), not the mycroft.skill.handler.*
    # pair the normal intent path uses. Confirmed directly against a
    # live OVOS instance by sending a nonsense utterance and capturing
    # the actual bus traffic: ping -> pong (per candidate skill,
    # {"skill_id", "can_handle"}) -> request -> start -> response
    # ({"result": bool, "fallback_handler": "Class.method"}).
    #
    # ping/pong are deliberately skipped: pong fires once per skill
    # that merely CLAIMS it's capable (often several, in priority
    # order), not the one that actually ends up handling it - showing
    # every pong would overstate how many skills did something. Only
    # .start/.response for whichever skill was actually invoked are
    # shown, mirroring the normal mycroft.skill.handler.start/complete
    # treatment above.
    if msg_type.startswith(FALLBACK_PREFIX):
        remainder = msg_type[len(FALLBACK_PREFIX):]
        if remainder.endswith(".start"):
            skill_id = remainder[:-len(".start")]
            return f"▶ {skill_id} (fallback) is handling this"
        if remainder.endswith(".response"):
            skill_id = remainder[:-len(".response")]
            if data.get("result"):
                return f"✓ {skill_id} (fallback) finished"
            return f"✗ {skill_id} (fallback) could not resolve"
        return None  # ping/pong/request - not shown, see comment above

    # A small, deliberately curated set of core lifecycle events -
    # picked for being both common and meaningful on their own (no
    # surrounding context needed to understand "wake word heard" or
    # "still speaking"), without turning this into a second log feed.
    if msg_type == "recognizer_loop:wakeword":
        return "👂 wake word detected"

    if msg_type == "mycroft.audio.speech.start":
        return "🔊 speaking..."

    if msg_type == "mycroft.audio.speech.stop":
        return "🔇 done speaking"

    if msg_type == "mycroft.stop":
        return "⏹ global stop triggered"

    return None


def _skill_label(data):
    return data.get("name") or data.get("skill_id") or "a skill"


def _summarize_search_response(data):
    skill_id = data.get("skill_id", "?")
    title = data.get("title", "?")
    confidence = data.get("confidence")
    if confidence is not None:
        return f'📥 {skill_id}: "{title}" ({confidence:.2f})'
    return f'📥 {skill_id}: "{title}"'
