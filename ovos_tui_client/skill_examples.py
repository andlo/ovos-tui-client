"""Finds real example utterances from installed skills' own skill.json
files, for ExampleCommandProvider (app.py) - "Example: read me the
story about the little mermaid" style Command Palette entries pulled
from skills actually installed, not made up.

Only works for local/venv/systemd installs where skills are regular,
importable Python packages on this same machine - confirmed directly
against a real install (37/37 skill_ids correctly resolved via the
heuristic below). Does NOT work for skills running in separate Docker/
Podman containers (see #28) - skill.json lives inside each skill's own
container there, inaccessible the same way regular file-based OVOS
resources are for that install type. find_skill_examples() returns []
in that case (a skill_id that isn't importable on THIS machine), not
an error - callers should treat an empty result as "no examples known"
rather than "something's broken".
"""
import importlib.util
import json
import os

# Locale directory naming isn't perfectly consistent across every
# skill package - confirmed directly: most use the full "xx-xx" form
# (en-us, da-dk), but at least one real, installed skill only ships a
# bare "da" directory, not "da-dk". Tried in order: the exact
# requested language, then just its prefix before the first "-", then
# "en-us" as a last-resort default (almost every skill has this one,
# even if it lacks the requester's own language).
_FALLBACK_LANG = "en-us"


def guess_module_name(skill_id: str) -> str:
    """skill_id -> best-effort importable module name, e.g.
    "ovos-skill-weather.openvoiceos" -> "ovos_skill_weather". Strips a
    trailing ".<author>" segment (skill_id's own convention) and swaps
    hyphens for underscores (Python package naming convention) - not
    guaranteed to be correct for every possible skill (some may use a
    different scheme entirely), but confirmed matching 37/37 real,
    diverse skill_ids on a live install, a strong enough hit rate to
    be worth trying rather than not offering this feature at all."""
    base = skill_id.rsplit(".", 1)[0] if "." in skill_id else skill_id
    return base.replace("-", "_")


def short_skill_name(skill_id: str) -> str:
    """skill_id -> a short, readable name for display, e.g.
    "ovos-skill-naptime.openvoiceos" -> "naptime". Strips the same
    trailing ".<author>" segment as guess_module_name() above, plus a
    leading "ovos-skill-"/"ovos_skill_" prefix if present - just for
    how this shows up in the Command Palette ("Example: naptime: Go to
    sleep" reads a lot better than repeating the full skill_id for
    every single example), not used for the actual module lookup
    (guess_module_name() still does that separately, on the full
    skill_id, since stripping the prefix here is purely cosmetic and
    shouldn't risk feeding a mangled name into an import lookup)."""
    base = skill_id.rsplit(".", 1)[0] if "." in skill_id else skill_id
    for prefix in ("ovos-skill-", "ovos_skill_"):
        if base.lower().startswith(prefix):
            return base[len(prefix):]
    return base


def find_skill_examples(skill_id: str, lang: str = "en-us") -> list:
    """Returns the "examples" list from skill_id's own skill.json, or
    [] if the skill isn't importable on this machine (Docker/Podman
    install, or the naming heuristic genuinely didn't match this
    particular skill), it has no skill.json, or the file has no
    examples. Never raises - any failure here is "no examples known
    for this skill", not something that should interrupt whatever
    called this."""
    try:
        module_name = guess_module_name(skill_id)
        spec = importlib.util.find_spec(module_name)
        if not spec or not spec.origin:
            return []
        pkg_dir = os.path.dirname(spec.origin)
    except (ImportError, ValueError, ModuleNotFoundError):
        return []

    lang_candidates = [lang]
    if "-" in lang:
        lang_candidates.append(lang.split("-", 1)[0])
    if _FALLBACK_LANG not in lang_candidates:
        lang_candidates.append(_FALLBACK_LANG)

    for candidate_lang in lang_candidates:
        skill_json_path = os.path.join(pkg_dir, "locale", candidate_lang, "skill.json")
        try:
            with open(skill_json_path) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            continue
        examples = data.get("examples")
        if isinstance(examples, list):
            return [e for e in examples if isinstance(e, str)]
        return []
    return []
