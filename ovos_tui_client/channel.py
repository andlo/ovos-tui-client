"""Which OVOS release channel an install runs: stable, testing or alpha.

Not every install is made by the OVOS installer (raspOVOS images,
Buildroot, ovos-docker, a hand-made venv ...), and the installer's own
state file is often not readable by the user OVOS runs as. So the channel
is found in two ways, and both are shown:

* declared: what the install itself says it follows - the installer's
  state file (~/.local/state/ovos/installer.json) or raspOVOS's
  /opt/ovos/tag;
* from the versions: which channel's constraints file, as it is TODAY,
  allows the installed core packages. The files move over time, so they
  are always fetched live from OpenVoiceOS/ovos-releases (the source the
  installer, raspOVOS and ovos-docker all use), never kept in this code.

alpha's constraints are floors only (ovos-core>=...), so any newer install
matches them. For a floor, the installed major version must also be the
newest one on PyPI, which is what alpha installs.

A store that receives a report checks the channel again on its side; this
is for the person at the keyboard.
"""
import json
import re
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

CHANNELS = ("stable", "testing", "alpha")
CONSTRAINTS_URL = "https://raw.githubusercontent.com/OpenVoiceOS/ovos-releases/main/constraints-{channel}.txt"
PYPI_URL = "https://pypi.org/pypi/{package}/json"
RASPOVOS_TAG_FILE = Path("/opt/ovos/tag")

# The packages whose versions decide which channel an install is on.
CORE_PACKAGES = ("ovos-core", "ovos-workshop", "ovos-padatious", "ovos-bus-client",
                 "ovos-plugin-manager")

TIMEOUT = 8


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", str(name or "")).lower()


def read_raspovos_tag(path: Optional[Path] = None) -> Optional[str]:
    try:
        text = Path(path or RASPOVOS_TAG_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text if text in CHANNELS else None


def declared_channel(installer_reader: Callable, raspovos_tag: Optional[Path] = None):
    """(channel, source, note): what the install says it follows.
    installer_reader is manifest.read_installer_channel (kept there, as
    older code imports it from there)."""
    channel, note = installer_reader()
    if channel:
        return channel, "ovos-installer", None
    tag = read_raspovos_tag(raspovos_tag)
    if tag:
        return tag, "raspOVOS", None
    return None, None, note


def parse_constraints(text: str) -> Dict[str, str]:
    """package -> specifier string, from a constraints-<channel>.txt."""
    pins = {}
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-") or " @ " in line:
            continue
        m = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*([^;]*)", line)
        if m:
            pins[normalize(m.group(1))] = m.group(3).strip()
    return pins


def is_floor(spec: str) -> bool:
    """True for a lower bound only (">=2.2.4a1"), which any newer version meets."""
    parts = [p.strip() for p in (spec or "").split(",") if p.strip()]
    return bool(parts) and all(p.startswith((">=", ">")) for p in parts)


def fetch_text(url: str, timeout: float = TIMEOUT) -> Optional[str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - fixed https URLs
            return resp.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - offline must never break anything
        return None


def newest_version(package: str, fetch: Callable = fetch_text) -> Optional[str]:
    """The newest release on PyPI, pre-releases included (what alpha installs)."""
    from packaging.version import InvalidVersion, Version
    text = fetch(PYPI_URL.format(package=package))
    try:
        releases = json.loads(text or "{}").get("releases") or {}
    except ValueError:
        return None
    best = None
    for v, files in releases.items():
        if files and all(f.get("yanked") for f in files):
            continue
        try:
            parsed = Version(v)
        except InvalidVersion:
            continue
        if best is None or parsed > best:
            best = parsed
    return str(best) if best else None


def channel_problems(stack: Dict[str, str], channel: str, constraints_text: str,
                     newest: Callable[[str], Optional[str]]) -> List[str]:
    """Why the installed core versions are not on `channel` today ([] = they are)."""
    from packaging.specifiers import InvalidSpecifier, SpecifierSet
    from packaging.version import InvalidVersion, Version
    out = []
    pins = parse_constraints(constraints_text)
    for pkg in CORE_PACKAGES:
        have = stack.get(normalize(pkg))
        spec = pins.get(normalize(pkg))
        if not have or not spec:
            continue
        try:
            allowed = Version(have) in SpecifierSet(spec, prereleases=True)
        except (InvalidVersion, InvalidSpecifier):
            allowed = False
        if not allowed:
            out.append(f"{pkg} {have} is outside {channel}'s {spec}")
            continue
        if is_floor(spec):
            latest = newest(pkg)
            try:
                if latest and Version(have).major != Version(latest).major:
                    out.append(f"{pkg} {have} is older than what {channel} installs now ({latest})")
            except InvalidVersion:
                pass
    return out


def detect(stack: Dict[str, str], declared: Tuple = (None, None, None),
           fetch: Callable = fetch_text, newest: Optional[Callable] = None) -> Dict:
    """Everything the 'OVOS: Release channel' window and a report need.

    channel: the declared channel when the versions agree with it (or could
    not be checked), else the one channel the versions match, else None.
    """
    stack = {normalize(k): v for k, v in (stack or {}).items()}
    newest = newest or (lambda pkg: newest_version(pkg, fetch))
    cache: Dict[str, Optional[str]] = {}

    def _newest(pkg):
        if pkg not in cache:
            cache[pkg] = newest(pkg)
        return cache[pkg]

    d_channel, d_source, d_note = declared
    result = {"declared": d_channel, "declared_source": d_source, "declared_note": d_note,
              "checked": [], "unreachable": [], "problems": {}, "matches": [],
              "channel": None, "source": None}
    if stack.get("ovos-core"):
        for ch in CHANNELS:
            text = fetch(CONSTRAINTS_URL.format(channel=ch))
            if text is None:
                result["unreachable"].append(ch)
                continue
            result["checked"].append(ch)
            problems = channel_problems(stack, ch, text, _newest)
            result["problems"][ch] = problems
            if not problems:
                result["matches"].append(ch)
    matches = result["matches"]
    if d_channel and (d_channel in matches or d_channel not in result["checked"]):
        result["channel"], result["source"] = d_channel, d_source
    elif d_channel:
        pass  # declared, but the versions say otherwise: shown, not guessed
    elif len(matches) == 1:
        result["channel"], result["source"] = matches[0], "installed versions"
    return result


CHANNELS_TEXT = """\
## The channels

| Channel | What it is |
|---|---|
| **testing** | What the OVOS installer installs by default: versions picked for testing, with upper bounds. What most users run. |
| **alpha** | The newest releases, pre-releases included. raspOVOS images, ovos-docker's default tag and Mark II/DevKit and macOS installs use it. |
| **stable** | The last stable release. Changes rarely. The installer doesn't offer it. |

Each channel is one constraints file in OpenVoiceOS/ovos-releases, and the
versions in it move over time. That is why the channel is worked out
against the files as they are today.
"""

SWITCH_TEXT = """\
## Changing channel

Back up `~/.config/mycroft` first. Moving to an older channel downgrades
packages.

**OVOS installer.** The channel is fixed once installed: running the
installer again upgrades within it. To switch, uninstall and install again,
and pick the channel in the installer (testing or alpha):

    sudo sh -c "$(curl -fsSL https://raw.githubusercontent.com/OpenVoiceOS/ovos-installer/main/installer.sh)" installer.sh --uninstall

Uninstalling removes configuration too. Unattended installs set
`channel:` in `~/.config/ovos-installer/scenario.yaml`.

**raspOVOS.** The channel is in `/opt/ovos/tag`, and `ovos-update`
updates from it:

    echo testing | sudo tee /opt/ovos/tag
    ovos-update

(`ovos-update testing` updates from testing once, without changing the tag.)

**ovos-docker, and Buildroot images (which run ovos-docker's containers).**
The image tag is the channel: set `VERSION=testing` (or `alpha`, `stable`)
in the compose `.env`, then `docker compose pull && docker compose up -d`.

**Your own venv.** Upgrade the installed OVOS packages against the
channel's constraints file (add `--pre` for alpha):

    pip install -U -c https://raw.githubusercontent.com/OpenVoiceOS/ovos-releases/main/constraints-testing.txt \\
        $(pip list --format=freeze | grep -E '^ovos-' | cut -d= -f1)
"""


def channel_markdown(result: Dict, stack: Dict[str, str], remote: bool = False) -> str:
    lines = ["# OVOS release channel", ""]
    if remote:
        lines += ["OVOS is on another machine, so its versions can't be read from here. "
                  "Run ovos-tui-client on the device itself to see its channel.", ""]
    else:
        ch = result.get("channel")
        if ch:
            how = {"ovos-installer": "the OVOS installer's state file says so",
                   "raspOVOS": "raspOVOS's /opt/ovos/tag says so",
                   "installed versions": "worked out from the installed versions"}.get(result.get("source"), "")
            agree = " and the installed versions agree" if result.get("declared") and ch in result.get("matches", []) else ""
            lines += [f"This install runs **{ch}** ({how}{agree}).", ""]
        elif result.get("declared"):
            lines += [f"The install says **{result['declared']}** ({result.get('declared_source')}), "
                      "but its versions are not what that channel installs today:", ""]
            lines += [f"- {p}" for p in result.get("problems", {}).get(result["declared"], [])] + [""]
        elif result.get("matches"):
            lines += ["The installed versions fit more than one channel: "
                      f"{', '.join(result['matches'])}. Pick the right one when you make a report.", ""]
        elif result.get("checked"):
            lines += ["The installed versions match no channel as it is today "
                      "(an older install, or packages installed by hand).", ""]
        else:
            lines += ["The channel could not be worked out.", ""]
        if result.get("declared_note") and not result.get("declared"):
            lines += [f"Not declared by the install: {result['declared_note']}.", ""]
        if result.get("unreachable"):
            lines += [f"Could not fetch the constraints for {', '.join(result['unreachable'])} "
                      "(no network?).", ""]
        for other in result.get("checked", []):
            probs = result.get("problems", {}).get(other) or []
            if other != result.get("channel") and probs and other != result.get("declared"):
                lines += [f"Not **{other}**: {probs[0]}."]
        lines += [""]
        if stack:
            lines += ["| Package | Installed |", "|---|---|"]
            lines += [f"| {pkg} | {v} |" for pkg, v in sorted(stack.items())]
            lines += [""]
    return "\n".join(lines) + "\n" + CHANNELS_TEXT + "\n" + SWITCH_TEXT
