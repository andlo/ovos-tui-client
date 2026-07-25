"""Persists filter choices (Sources/Log Levels/Skills enabled states)
and utterance input history across sessions, so quitting and reopening
the TUI doesn't reset filters you'd already set up, or lose what
you've typed before. Stored as JSON in a standard XDG-style config
location - deliberately separate from mycroft.conf/OVOS's own config,
since this is purely a preference of this tool, not OVOS itself.
"""
import json
from pathlib import Path

STATE_FILE = Path("~/.config/ovos-tui-client/state.json").expanduser()

# Cap for saved input history - deliberately much smaller than
# LOG_BUFFER_SIZE (app.py, 5000): that's disposable log lines meant
# for re-filtering a live session, this is utterances the person
# actually typed, one heavier text line per entry, and Up/Down-arrow
# browsing a saved history that's thousands of entries deep stops
# being practically useful long before it stops being technically
# possible. 500 comfortably covers many sessions of real testing
# without the state file or the browsing experience growing unwieldy.
INPUT_HISTORY_CAP = 500


def load_filter_state():
    """Returns a dict with 'sources', 'levels', 'skills' keys (each a
    name->bool dict), or all-empty if no saved state exists yet or it
    can't be read - callers should fall back to their normal defaults
    in that case, not crash. A corrupt or partially-written file (e.g.
    from a crash mid-save) is treated the same as no file at all."""
    data = _load_raw()
    return {
        "sources": data.get("sources") or {},
        "levels": data.get("levels") or {},
        "skills": data.get("skills") or {},
    }


def save_filter_state(sources: dict, levels: dict, skills: dict) -> None:
    """Writes the current filter state to disk - preserves whatever
    input history is already saved (a separate concern, saved
    separately via save_input_history()) rather than clobbering it,
    since both functions write into the same underlying file."""
    data = _load_raw()
    data["sources"] = sources
    data["levels"] = levels
    data["skills"] = skills
    _save_raw(data)


def load_input_history() -> list:
    """Returns the saved utterance input history (oldest first, same
    order Up/Down-arrow browsing expects), or [] if none is saved yet
    or it can't be read."""
    data = _load_raw()
    history = data.get("input_history")
    if not isinstance(history, list):
        return []
    return [line for line in history if isinstance(line, str)]


def save_input_history(history: list) -> None:
    """Writes the utterance input history to disk, keeping only the
    most recent INPUT_HISTORY_CAP entries - preserves whatever filter
    state is already saved rather than clobbering it, same reasoning
    as save_filter_state()."""
    data = _load_raw()
    data["input_history"] = history[-INPUT_HISTORY_CAP:]
    _save_raw(data)


def _load_raw() -> dict:
    """Shared read used by both the filter-state and input-history
    functions above, since they live in the same file - a corrupt or
    partially-written file (e.g. from a crash mid-save) is treated the
    same as no file at all, callers get an empty dict to fall back
    from."""
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_raw(data: dict) -> None:
    """Shared write used by both the filter-state and input-history
    functions above. Never raises - a failed save (read-only
    filesystem, permissions, disk full) is a minor inconvenience, not
    something that should crash the app on exit and lose the person's
    session."""
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(data, f)
    except OSError:
        pass
