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
    """A single tailable log file with a toggleable visibility state."""
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
    user for --log-dir' rather than crashing."""
    if log_dir is None:
        return []
    sources = []
    for name in KNOWN_LOG_NAMES:
        path = log_dir / f"{name}.log"
        if path.exists():
            sources.append(LogSource(name=name, path=path))
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
