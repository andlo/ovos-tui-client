"""Translates raw OVOS bus messages into short, human-readable activity
lines - "what's happening right now", distinct from the verbose raw
logs. Deliberately curated: most message types return None (skipped),
since the whole point is a simplified summary, not a second log feed.

Pure function, no UI/bus dependency, so it's testable without a real
connection - see bus.py's on_activity() for how this gets wired up.
"""

FETCH_CONTENT_PREFIX = "ovos.common_reading.fetch_content."
FALLBACK_PREFIX = "ovos.skills.fallback."
OCP_PREFIX = "ovos.common_play."


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

    # The COMMON QUERY path (question:query / question:query.response):
    # a third, separate pipeline from fallback and this project's own
    # common-reading one, used for factual/knowledge questions ("what
    # is the capital of France") - broadcasts to every registered
    # answering skill (wikipedia, duckduckgo, wolframalpha, etc) and
    # collects whichever responses come back. Confirmed directly
    # against a live OVOS instance.
    #
    # Every skill responds TWICE: once with searching: true (still
    # working - a transient "I'm on it" phase, sometimes even sent
    # more than once by the same skill in the live capture), then once
    # with searching: false (done) - only the final searching: false
    # responses are shown here, same reasoning as skipping fallback's
    # ping/pong.
    #
    # Unlike an earlier draft of this, a searching: false response
    # WITHOUT an answer is still shown (as "no answer"), not skipped -
    # for consistency with how this project's own common-reading pong/
    # search.response already shows every candidate regardless of
    # confidence, and how fetch_content.response shows "empty response
    # (fetch failed)" rather than staying silent. A skill trying and
    # visibly coming up empty is exactly the kind of thing worth
    # seeing when debugging why a question didn't get answered.
    if msg_type == "question:query":
        return f'🔍 asking all skills: "{data.get("phrase", "?")}"'

    if msg_type == "question:query.response":
        if data.get("searching"):
            return None
        skill_id = data.get("skill_id", "?")
        answer = data.get("answer")
        return f'📥 {skill_id}: "{answer}"' if answer else f"✗ {skill_id}: no answer"

    # question:action - the actual "this one won" signal, fired once
    # the winning answer among all the candidates above has been
    # selected. Documented in the official OVOS message specification
    # (openvoiceos.github.io/message_spec/ovos_core/, CommonQAService):
    # "'phrase': str, 'skill_id': str, 'callback_data': dict - Trigger
    # skill callback after answer was selected". Handling it here is
    # correct if/when it fires, but honesty note: it did NOT actually
    # appear in live testing (several real questions asked, full
    # activity buffer checked, several second wait) - unlike the other
    # additions in this file, this one is implemented from the
    # documented spec alone, not confirmed by observing it happen. May
    # be version-dependent, or only fire under conditions not
    # triggered by the questions tried so far.
    if msg_type == "question:action":
        return f"🏆 {data.get('skill_id', '?')} selected to answer"

    # The OCP (OVOS Common Play - media playback: music, radio, etc)
    # search path. Structurally different from fallback/common-reading/
    # common-query above: a single skill can report MANY candidates via
    # repeated ovos.common_play.query.response calls (10+ for one
    # search, confirmed via live capture) rather than one verdict each -
    # this project's summarize_message() is a pure, stateless function
    # (see module docstring), so there's no way to aggregate those N
    # responses into one line without a bigger architecture change.
    # Showing all of them would flood the pane, so query.response is
    # deliberately NOT shown here, unlike the "show every final
    # outcome" approach used for common-reading/common-query. Only the
    # search kickoff and each skill's search_end (one line per skill,
    # not per candidate) are shown - skill.search_start and every raw
    # query.response are skipped, along with playback_time (fires
    # continuously during actual playback - pure noise) and all gui.*
    # bookkeeping messages.
    #
    # NOT YET CONFIRMED: which specific skill actually gets selected to
    # play (as opposed to merely offering candidates) - every live
    # capture attempt on this box hit an external API failure
    # (YouTube/Pyradios) before reaching that point. Tracked as a
    # follow-up rather than guessed at.
    if msg_type == "ocp:play":
        return f'🎵 OCP: searching for "{data.get("query", "?")}"'

    if msg_type == OCP_PREFIX + "skill.search_end":
        return f"✓ {data.get('skill_id', '?')}: search complete"

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
