"""Log discovery and tailing for the OVOS TUI client.

Deliberately does NOT hardcode a single log directory - OVOS's actual
log location varies by install method (ovos-installer venv install,
Docker, raspOVOS, distro packaging) and has used different XDG-style
paths across versions. Instead this scans a list of known candidate
directories and only reports the ones that actually contain recognized
*.log files, with a CLI override always available for anything this
list doesn't anticipate.
"""
from pathlib import Path
from dataclasses import dataclass, field
import re

# Ordered by how likely each is on a modern (2026) ovos-installer
# venv-based install - see module docstring. Not exhaustive by design;
# --log-dir on the CLI covers anything this misses.
CANDIDATE_LOG_DIRS = [
    "~/.local/state/mycroft",
    "~/.local/state/ovos",
    "~/.cache/mycroft/log",
    "/var/log/mycroft",
]

# Known OVOS service log filenames (without .log suffix) - both the
# legacy "audio" name and the newer "media" replacement are included
# since which one exists depends on install age.
KNOWN_LOG_NAMES = [
    "bus", "skills", "audio", "media", "voice", "gui", "enclosure", "phal",
]


@dataclass
class LogSource:
    """A single tailable log file with a toggleable INCLUDE-filter
    state. Checked by default (unlike skill_enabled entries in app.py,
    which default unchecked) - Sources and Log Levels are short,
    fixed-length lists where "everything on, uncheck what you don't
    want" reads naturally; Skills is an open-ended, growing list where
    "check the few you care about" reads naturally instead. Either
    way, the underlying filter rule is the same: if NO source is
    checked, nothing is restricted and every source shows; checking
    one or more restricts the view to only those."""
    name: str
    path: Path
    enabled: bool = True
    _offset: int = field(default=0, repr=False)

    def read_new_lines(self):
        """Returns any lines appended since the last call. Handles the
        file being truncated/rotated (offset ahead of current size)
        by resetting to the start rather than raising."""
        if not self.path.exists():
            return []
        size = self.path.stat().st_size
        if size < self._offset:
            self._offset = 0  # file was rotated/truncated
        with open(self.path, "r", errors="replace") as f:
            f.seek(self._offset)
            new_data = f.read()
            self._offset = f.tell()
        if not new_data:
            return []
        return [line for line in new_data.splitlines() if line.strip()]


def find_log_dir(override=None):
    """Returns the first candidate directory that actually contains at
    least one recognized *.log file, or None if none do. `override`,
    if given, is trusted as-is without the "must contain a known log"
    check - the user explicitly pointed here."""
    if override:
        return Path(override).expanduser()
    for candidate in CANDIDATE_LOG_DIRS:
        path = Path(candidate).expanduser()
        if not path.is_dir():
            continue
        if any((path / f"{name}.log").exists() for name in KNOWN_LOG_NAMES):
            return path
    return None


def discover_log_sources(log_dir):
    """Builds a LogSource for every KNOWN_LOG_NAMES file that actually
    exists in log_dir. Returns [] if log_dir is None or contains none
    of them - callers should treat that as 'no logs found, ask the
    user for --log-dir' rather than crashing.

    Each LogSource starts its _offset at the file's CURRENT size (end
    of file), not 0 - a real bug found via testing: starting at 0
    meant the very first _poll_logs() tick read the entire pre-existing
    log file (potentially thousands of lines on a long-running OVOS
    install) and wrote every one of them to the RichLog widget
    synchronously on the main thread, genuinely freezing the UI for a
    noticeable stretch at startup - not just a perception issue.
    Seeking to the end first means only lines appended AFTER this tool
    starts are ever shown, same as `tail -f`'s default behavior - the
    UI is responsive immediately instead of only once that backlog
    finishes draining."""
    if log_dir is None:
        return []
    sources = []
    for name in KNOWN_LOG_NAMES:
        path = log_dir / f"{name}.log"
        if path.exists():
            sources.append(LogSource(name=name, path=path, _offset=path.stat().st_size))
    return sources


def line_matches_filter(line: str, filter_text: str) -> bool:
    """Case-insensitive free-text substring match. An empty filter
    matches everything - this is the 'no filter applied' state."""
    if not filter_text:
        return True
    return filter_text.lower() in line.lower()


# OVOS's own log lines look like:
#   2024-12-07 07:51:04.662 - bus - ovos_messagebus.__main__:main:46 - INFO - Starting...
# i.e. TIMESTAMP - COMPONENT - MODULE:FUNC:LINE - LEVEL - MESSAGE. Both
# the timestamp and the component name duplicate information the TUI
# already shows itself (live scroll position, and the [source] prefix
# format_log_line() adds) - stripping them here declutters the display
# without losing anything.
_LOG_PREFIX_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\s*-\s*[^-]+\s*-\s*"
)


def strip_log_prefix(line: str) -> str:
    """Removes the leading 'TIMESTAMP - COMPONENT - ' prefix if present;
    returns the line unchanged if it doesn't match (e.g. a continuation
    line of a multi-line traceback, which has no prefix of its own)."""
    return _LOG_PREFIX_RE.sub("", line, count=1)


KNOWN_LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

# After strip_log_prefix, a normal line looks like
#   MODULE:FUNC:LINE - LEVEL - MESSAGE
# LEVEL is a reliable, structured field (standard Python logging
# convention) - unlike skill identity below, this is a clean regex
# match, not a heuristic.
_LOG_LEVEL_RE = re.compile(r"-\s*(" + "|".join(KNOWN_LOG_LEVELS) + r")\s*-")


def extract_log_level(line: str):
    """Returns the log level (e.g. 'ERROR') found in a line already
    passed through strip_log_prefix(), or None if no recognized level
    token is present (e.g. a traceback continuation line)."""
    match = _LOG_LEVEL_RE.search(line)
    return match.group(1) if match else None


# Best-effort only, unlike extract_log_level above: OVOS doesn't log a
# structured "which skill" field on every skills.log line - many
# skill-related lines come from shared base-class code (ovos_workshop.
# skills.ovos:speak, etc) with no per-instance identity in the text at
# all. This only catches lines that happen to mention a skill_id
# explicitly (e.g. inside a dict repr like "'skill_id': 'x.andlo'", or
# a bare "skill_id=x.andlo") - it will miss plenty of genuinely
# skill-specific lines that don't happen to spell out their own
# skill_id. Still useful as an opt-in filter, just not a complete one.
_SKILL_ID_RE = re.compile(r"skill_id['\"]?\s*[:=]\s*['\"]?([\w.\-]+)['\"]?")


def extract_skill_id(line: str):
    """Returns a skill_id substring found in a line's text, or None.
    Best-effort - see module note above."""
    match = _SKILL_ID_RE.search(line)
    return match.group(1) if match else None
