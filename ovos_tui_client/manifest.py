"""What a test run ran against (#51): the manifest.

A result is only useful if it says exactly which install produced it, so
every headless run records:

* the release channel (stable / testing / alpha), unless given with
  --channel: what the install declares (the OVOS installer's state file,
  raspOVOS's /opt/ovos/tag), checked against the installed versions and
  the channels' live constraints files, or found from the versions alone
  when nothing is declared (see channel.py);
* the versions of the packages that decide how skills load and route
  (ovos-core, ovos-workshop, the intent engines, the bus client, ...) and
  of every skill that was tested;
* the effective routing config: lang, secondary_langs, pipeline order;
* the machine type (architecture, and the board model when the kernel
  reports one, e.g. "Raspberry Pi 5" or a Mark II);
* when, and which ovos-tui-client made it.

Versions are read from THIS Python environment. The OVOS installer puts
ovos-tui-client in the same venv as OVOS (~/.venvs/ovos), so on a device
they are OVOS's own versions. When the bus is on another machine they
would describe the wrong install, so they are left out and the manifest
says so (`versions_from: "unavailable"`) instead of guessing.

Nothing private goes in: no hostname, no IP address, no user name or
home path, and nothing from skill settings (API keys live there). The
bus address is recorded only as "local" or "remote".
"""
import importlib.metadata
import json
import platform
import time
from pathlib import Path
from typing import Dict, Iterable, Optional

from ovos_tui_client import channel as channel_mod
from ovos_tui_client.skill_examples import find_skill_distribution

# Written by ovos-installer (tui/channels.sh): {"channel": "testing"|"alpha", ...}
INSTALLER_STATE_FILE = Path("~/.local/state/ovos/installer.json").expanduser()

# The packages whose versions decide whether a skill loads and where an
# utterance goes. Kept short on purpose: this is what a reader compares.
KEY_PACKAGES = (
    "ovos-core", "ovos-workshop", "ovos-bus-client", "ovos-plugin-manager",
    "ovos-config", "ovos-utils", "ovos-padatious", "ovos-adapt-parser",
    "padacioso", "ovos-m2v-pipeline", "ovos-common-query-pipeline-plugin",
    "ovos-ocp-pipeline-plugin", "ovos-persona",
)

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}

DEVICE_MODEL_FILES = ("/proc/device-tree/model", "/sys/firmware/devicetree/base/model")


def installer_channel(state_file: Path = INSTALLER_STATE_FILE) -> Optional[str]:
    """The channel ovos-installer installed from, or None when unknown."""
    return read_installer_channel(state_file)[0]


def read_installer_channel(state_file: Path = INSTALLER_STATE_FILE):
    """(channel, why it is unknown). ovos-installer can leave this file
    owned by root with mode 600 (seen on real installs), so the ovos user
    that OVOS and ovos-tui-client run as cannot read it. Say so rather
    than guess the channel from version numbers."""
    path = Path(state_file)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "no ovos-installer state file"
    except PermissionError:
        return None, f"ovos-installer's state file is not readable by this user ({path.name})"
    except (OSError, ValueError):
        return None, "ovos-installer's state file could not be read"
    channel = data.get("channel") if isinstance(data, dict) else None
    return (str(channel), None) if channel else (None, "ovos-installer's state file has no channel")


def package_version(name: str) -> Optional[str]:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def device_model(files: Iterable[str] = DEVICE_MODEL_FILES) -> Optional[str]:
    """Board model from the device tree (Raspberry Pis, Mark II ...);
    None on a normal PC or VM."""
    for path in files:
        try:
            text = Path(path).read_bytes().decode("utf-8", "replace").strip("\x00 \n")
        except OSError:
            continue
        if text:
            return text[:80]
    return None


def _module(conf: Dict, section: str) -> Optional[str]:
    """The plugin name OVOS uses for `section` ("stt", "tts"), and nothing
    else from that section: the plugin's own settings live next to it and
    can hold API keys or server addresses."""
    value = (conf.get(section) or {}).get("module")
    return str(value)[:80] if value else None


def routing_config(mycroft_conf_override: Optional[str] = None) -> Dict:
    """lang, secondary_langs and the intent pipeline, as OVOS will use them,
    plus the STT and TTS plugin names (a report is easier to compare when
    it says "whisper · piper"; only the names, never their settings)."""
    try:
        if mycroft_conf_override:
            from json_database.utils import load_commented_json
            conf = load_commented_json(mycroft_conf_override) or {}
        else:
            from ovos_config.config import Configuration
            conf = Configuration()
    except Exception:  # noqa: BLE001 - a manifest must never break a run
        return {}
    return {
        "lang": conf.get("lang"),
        "secondary_langs": list(conf.get("secondary_langs") or []),
        "pipeline": list((conf.get("intents") or {}).get("pipeline") or []),
        "stt": _module(conf, "stt"),
        "tts": _module(conf, "tts"),
    }


def local_stack() -> Dict[str, str]:
    return {name: v for name in KEY_PACKAGES if (v := package_version(name))}


def channel_check(stack: Dict[str, str], fetch=None) -> Dict:
    """channel.detect() for this device: declared channel + live check."""
    declared = channel_mod.declared_channel(lambda: read_installer_channel(INSTALLER_STATE_FILE))
    return channel_mod.detect(stack, declared, fetch=fetch or channel_mod.fetch_text)


def detect_channel(stack: Dict[str, str], fetch=None):
    """(channel, source, note) for a manifest. Never raises."""
    try:
        res = channel_check(stack, fetch)
    except Exception as e:  # noqa: BLE001 - a manifest must never break a run
        return None, None, f"the channel check failed ({e.__class__.__name__})"
    if res["channel"]:
        return res["channel"], res["source"], None
    if res["declared"]:
        note = f"{res['declared_source']} says {res['declared']}, but the installed versions don't match it"
    elif len(res["matches"]) > 1:
        note = "the installed versions fit " + " and ".join(res["matches"])
    elif res["checked"]:
        note = "the installed versions match no channel as it is today"
    else:
        note = res.get("declared_note") or "the channels' constraints could not be fetched"
    return None, None, note


def build_manifest(host: str, lang: str, tested_skill_ids: Iterable[str],
                   installed_skills: Optional[Dict[str, Optional[bool]]] = None,
                   channel: Optional[str] = None, mycroft_conf_override: Optional[str] = None,
                   tool_version: str = "unknown", now: Optional[float] = None,
                   fetch=None, channel_result: Optional[tuple] = None) -> Dict:
    """channel_result: (channel, source, note) already settled by the
    caller (the TUI's report window), so nothing is checked again."""
    local = (host or "").strip().lower() in LOCAL_HOSTS
    tested = sorted(set(tested_skill_ids))
    stack = local_stack() if local else {}
    if channel:
        detected, channel_source, channel_note = channel, "argument", None
    elif channel_result is not None:
        detected, channel_source, channel_note = channel_result
    elif local:
        detected, channel_source, channel_note = detect_channel(stack, fetch)
    else:
        detected, channel_source, channel_note = None, None, "OVOS is on another machine"
    manifest = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now or time.time())),
        "tool": f"ovos-tui-client {tool_version}",
        "bus": "local" if local else "remote",
        "channel": detected,
        "channel_source": channel_source,
        "lang": lang,
        "machine": {"arch": platform.machine() or None, "model": device_model() if local else None,
                    "python": platform.python_version()},
        "versions_from": "this environment" if local else "unavailable",
        "stack": {},
        "skills": {},
        "config": routing_config(mycroft_conf_override) if local or mycroft_conf_override else {},
    }
    if channel_note:
        manifest["channel_note"] = channel_note
    if local:
        manifest["stack"] = stack
        for skill_id in tested:
            dist = find_skill_distribution(skill_id)
            manifest["skills"][skill_id] = {
                "package": dist[0] if dist else None,
                "version": dist[1] if dist else None,
                "active": (installed_skills or {}).get(skill_id),
            }
    else:
        manifest["skills"] = {skill_id: {"package": None, "version": None,
                                         "active": (installed_skills or {}).get(skill_id)}
                              for skill_id in tested}
    if installed_skills is not None:
        manifest["installed_skills"] = len(installed_skills)
    return manifest
