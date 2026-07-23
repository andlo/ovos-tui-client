"""Persists filter choices (Sources/Log Levels/Skills enabled states)
across sessions, so quitting and reopening the TUI doesn't reset
filters you'd already set up. Stored as JSON in a standard XDG-style
config location - deliberately separate from mycroft.conf/OVOS's own
config, since this is purely a preference of this tool, not OVOS
itself.
"""
import json
from pathlib import Path

STATE_FILE = Path("~/.config/ovos-tui-client/state.json").expanduser()


def load_filter_state():
    """Returns a dict with 'sources', 'levels', 'skills' keys (each a
    name->bool dict), or all-empty if no saved state exists yet or it
    can't be read - callers should fall back to their normal defaults
    in that case, not crash. A corrupt or partially-written file (e.g.
    from a crash mid-save) is treated the same as no file at all."""
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"sources": {}, "levels": {}, "skills": {}}
    if not isinstance(data, dict):
        return {"sources": {}, "levels": {}, "skills": {}}
    return {
        "sources": data.get("sources") or {},
        "levels": data.get("levels") or {},
        "skills": data.get("skills") or {},
    }


def save_filter_state(sources: dict, levels: dict, skills: dict) -> None:
    """Writes the current filter state to disk. Never raises - a
    failed save (read-only filesystem, permissions, disk full) is a
    minor inconvenience, not something that should crash the app on
    exit and lose the person's session."""
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump({"sources": sources, "levels": levels, "skills": skills}, f)
    except OSError:
        pass
