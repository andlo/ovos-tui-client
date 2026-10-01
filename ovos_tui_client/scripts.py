"""Scripted test runs (#30): replay a list of utterances against the
live OVOS install, one at a time, and judge each against what it was
expected to trigger - repeatable, instead of retyping things by hand.

Two sources of steps, ONE file format:

- Skills' own golden utterances, ``test/end2end/golden_utterances_<lang>.jsonl``
  (the rows ovoscope asserts on in each skill's CI). ``test/`` is NOT
  part of an installed wheel - setup.py only packages the skill module
  itself - so skill_examples.py's installed-package lookup (#28) can't
  find them. They're read from a local source checkout (``--golden-dir``)
  or fetched from the skill's own GitHub repo (found via the installed
  distribution's metadata), with a local cache as fallback.
- User scripts in ``~/.config/ovos-tui-client/scripts/`` - the same
  JSONL rows (expectations optional), or plain text with one utterance
  per line. A ``{"golden": "<skill_id>"}`` row pulls in that skill's
  whole golden set, so a script can be "these three skills' tests".

Everything here is UI-free (see app.py for the palette entries and
conversation-pane output) so it's testable without a running App, same
split as bus.py/activity.py.

What's judged is ROUTING only - which skill/intent handled the
utterance - never the wording of the reply. Several bus signals are
accepted for "what matched", because they differ across ovos-core
versions: ``ovos.intent.matched`` (newer core), the dispatched
``<skill_id>:<intent>`` message itself (every version), and skill-level
signals (handler start context, fallback response, common_query's
``question:action``).
"""
import importlib.metadata
import json
import re
import threading
import time
import uuid
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from ovos_tui_client.skill_examples import find_skill_examples, guess_module_name

SCRIPTS_DIR = Path("~/.config/ovos-tui-client/scripts").expanduser()
GOLDEN_CACHE_DIR = Path("~/.cache/ovos-tui-client/golden").expanduser()
GOLDEN_SUBPATH = "test/end2end"
SCRIPT_SUFFIXES = (".jsonl", ".txt")

# Per-step limits. STEP_TIMEOUT: how long to wait for OVOS to finish
# handling one utterance (some skills do network lookups). SPEECH_TIMEOUT:
# how long to wait for TTS playback to end once it has started, so the
# next step doesn't talk over the previous reply. LATE_HANDLED_WAIT: after
# a handler-complete, how long to still wait for newer core's
# ovos.utterance.handled end-marker. SETTLE: a short pause between steps
# for late 'speak' messages.
STEP_TIMEOUT = 30.0
SPEECH_TIMEOUT = 30.0
LATE_HANDLED_WAIT = 1.5
SETTLE = 0.5
# ovos-core < 2.3 has no ovos.utterance.handled, and several real paths
# (pipeline plugins, a converse/get_response capture) never emit
# mycroft.skill.handler.complete either - confirmed on a live 2.1.1
# install. So once SOMETHING has matched, the step also ends after this
# many seconds without any further bus traffic (and no TTS playing).
QUIET_AFTER_MATCH = 3.0
# The common-reading pipeline searches its provider skills inside the
# intent handler, but fetches the chosen story AFTER the handler has
# finished (seen live: handler.complete / utterance.handled arrive first,
# 'ovos.common_reading.fetch_content.<provider>' a moment later). Once a
# search has been seen, the step waits up to this long for the fetch
# that names the provider that actually answered.
#
# The fetch can come well over 10 s after the match (seen live on alpha
# with Andersen): the pipeline's handler first speaks an announcement
# ("here is ... by ...") and waits for it to be spoken - up to its own
# timeout when the audio end isn't reported for the session - and only
# then fetches. So the step doesn't end on bus silence while a search
# is waiting for its fetch (the handler is just blocked in that wait),
# and PROVIDER_WAIT is generous.
PROVIDER_WAIT = 30.0
# A provider skill's story can go on for minutes, far past SPEECH_TIMEOUT,
# and the reading pipeline speaks it in parts - so the runner used to move
# on while the story kept being read under the next steps (seen live with
# 365tomorrows). Once the provider is known the step's verdict is too:
# the runner waits up to STORY_START_WAIT for the reading to start, stops
# it (mycroft.stop in the step's own session) and gives the audio up to
# STOP_WAIT to go quiet.
STORY_START_WAIT = 10.0
STOP_WAIT = 5.0
# ovos-core handles one utterance at a time. When a step times out, core
# is usually still working on it (seen live on testing: a fallback solver
# waiting minutes for the OVOS translate servers), and every step sent
# meanwhile just queues behind it and times out too. So after a timeout
# the runner waits up to BUSY_WAIT for core to finish that utterance
# before it sends the next one: one slow sentence is one timeout, not a
# cascade. The step still counts as a timeout; its detail says how long
# core took and what handled it in the end.
BUSY_WAIT = 300.0

# A step whose skill is left waiting in get_response()/ask_yesno() (e.g.
# date-time's "did you mean <timezone>?") is answered "cancel" in its
# own session before the next step. ovos-workshop 7.x waits for that
# answer forever (num_retries=-1), each wait holds one of the bus
# client's 8 handler threads, and after 8 of them ovos-core stops
# handling anything - seen live on the testing channel (py-spy dump:
# 6x date-time ask_yesno, alerts _ocp_query, reading-pipeline ask_yesno).
RESPONSE_RELEASE_WAIT = 5.0
CANCEL_UTTERANCE = "cancel"
RELEASE_ROUNDS = 5
RELEASE_SETTLE = 1.0   # time for a handler to ask its next question after a cancel

FALLBACK_PREFIX = "ovos.skills.fallback."
READING_FETCH_PREFIX = "ovos.common_reading.fetch_content."
READING_SEARCH = "ovos.common_reading.search"
OCP_PLAY = "ovos.common_play.play"   # OCP starts playing the best search result
OCP_ID = "ovos.common_play"


# --------------------------------------------------------------------
# Steps and parsing
# --------------------------------------------------------------------

@dataclass
class ScriptStep:
    utterance: str
    lang: Optional[str] = None
    skill_id: Optional[str] = None
    intent_label: Optional[str] = None

    @property
    def has_expectation(self) -> bool:
        return bool(self.skill_id)

    @property
    def expected_intent(self) -> Optional[str]:
        if self.skill_id and self.intent_label:
            return f"{self.skill_id}:{self.intent_label}"
        return None


@dataclass
class GoldenInclude:
    """A ``{"golden": "<skill_id>"}`` row in a user script - expanded by
    expand_includes() into that skill's golden utterances."""
    skill_id: str


def normalize_lang(lang: Optional[str]) -> str:
    return (lang or "").strip().replace("_", "-").lower()


def lang_matches(row_lang: Optional[str], wanted: Optional[str]) -> bool:
    """'da-DK' matches 'da-dk'; a bare 'da' on either side matches any
    'da-XX' - golden files use 'da-DK', the TUI's --lang is usually
    'da-dk', and a few skills only ship bare language codes."""
    a, b = normalize_lang(row_lang), normalize_lang(wanted)
    if not a or not b:
        return True
    if a == b:
        return True
    if "-" not in a or "-" not in b:
        return a.split("-")[0] == b.split("-")[0]
    return False


def parse_script(text: str, lang: Optional[str] = None, default_skill_id: Optional[str] = None) -> list:
    """Parses a script/golden file into ScriptStep (and GoldenInclude)
    items. Blank lines and '#' comments are skipped. A line starting
    with '{' is a JSON row; anything else is a plain utterance with no
    expectation. JSON rows with needs_manual=true, or a 'lang' that
    doesn't match `lang`, are skipped - exactly the rows ovoscope's own
    golden tests skip. Malformed JSON rows are skipped rather than
    aborting the whole script."""
    items = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not line.startswith("{"):
            items.append(ScriptStep(utterance=line, lang=lang))
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if row.get("golden"):
            items.append(GoldenInclude(skill_id=str(row["golden"])))
            continue
        if row.get("needs_manual"):
            continue
        utterance = row.get("utterance")
        if not isinstance(utterance, str) or not utterance.strip():
            continue
        row_lang = row.get("lang")
        if lang and not lang_matches(row_lang, lang):
            continue
        items.append(ScriptStep(
            utterance=utterance.strip(),
            lang=row_lang or lang,
            skill_id=row.get("skill_id") or default_skill_id,
            intent_label=row.get("intent_label") or None,
        ))
    return items


def expand_includes(items: list, loader: Callable[[str], list]) -> List[ScriptStep]:
    """Replaces every GoldenInclude with loader(skill_id)'s steps."""
    steps = []
    for item in items:
        if isinstance(item, GoldenInclude):
            steps.extend(loader(item.skill_id))
        else:
            steps.append(item)
    return steps


# --------------------------------------------------------------------
# Finding golden utterances
# --------------------------------------------------------------------

def golden_filenames(lang: str) -> List[str]:
    """Candidate filenames for `lang`, most specific first: skills name
    them 'golden_utterances_da-DK.jsonl' (region upper-cased), and a
    few languages have no region at all ('..._kab.jsonl')."""
    parts = normalize_lang(lang).split("-")
    names = []
    if len(parts) >= 2:
        names.append("-".join([parts[0]] + [p.upper() for p in parts[1:]]))
    names.append(parts[0])
    return [f"golden_utterances_{n}.jsonl" for n in dict.fromkeys(names)]


def repo_dir_candidates(skill_id: str) -> List[str]:
    """Directory names a local checkout of skill_id might have -
    'ovos-skill-weather.openvoiceos' -> ['ovos-skill-weather', ...]."""
    base = skill_id.rsplit(".", 1)[0] if "." in skill_id else skill_id
    return list(dict.fromkeys([base, base.replace("_", "-"), base.replace("-", "_")]))


_GITHUB_RE = re.compile(r"github\.com[/:]([^/\s]+)/([^/\s#?]+)", re.IGNORECASE)


def parse_github_repo(url: str) -> Optional[tuple]:
    m = _GITHUB_RE.search(url or "")
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    if repo.endswith(".git"):
        repo = repo[:-4]
    return owner, repo


def find_repo_url(skill_id: str) -> Optional[str]:
    """The skill's GitHub repo, read from its installed distribution's
    metadata (setup.py's url= becomes 'Home-page'; pyproject's
    [project.urls] become 'Project-URL'). None if the skill isn't
    installed on this machine or declares no GitHub URL."""
    module = guess_module_name(skill_id)
    try:
        dists = list(importlib.metadata.packages_distributions().get(module, []))
    except Exception:
        dists = []
    dists += repo_dir_candidates(skill_id)
    for dist in dict.fromkeys(dists):
        try:
            md = importlib.metadata.metadata(dist)
        except Exception:
            continue
        urls = [md.get("Home-page") or ""]
        urls += [v.split(",", 1)[-1] for v in (md.get_all("Project-URL") or [])]
        for url in urls:
            parsed = parse_github_repo(url)
            if parsed:
                return f"https://github.com/{parsed[0]}/{parsed[1]}"
    return None


def github_raw_url(repo_url: str, path: str, ref: str = "HEAD") -> Optional[str]:
    parsed = parse_github_repo(repo_url)
    if not parsed:
        return None
    # HEAD resolves to the repo's default branch (dev for most OVOS
    # skills, main/master elsewhere) without having to know which.
    return f"https://raw.githubusercontent.com/{parsed[0]}/{parsed[1]}/{ref}/{path}"


def find_installed_version(skill_id: str) -> Optional[str]:
    """The installed version of skill_id's distribution, found the same
    way as find_repo_url(). None if it isn't installed here."""
    module = guess_module_name(skill_id)
    try:
        dists = list(importlib.metadata.packages_distributions().get(module, []))
    except Exception:
        dists = []
    dists += repo_dir_candidates(skill_id)
    for dist in dict.fromkeys(dists):
        try:
            return importlib.metadata.version(dist)
        except Exception:
            continue
    return None


_TAG_RE = re.compile(r"refs/tags/([^\s^\x00]+)")
_TAGS_CACHE: dict = {}


def list_repo_tags(repo_url: str, timeout: float = 10.0) -> Optional[List[str]]:
    """The repo's tag names, from git's own ref advertisement
    (<repo>.git/info/refs). One request per repo, no GitHub API rate
    limit, no git binary. None when it can't be read (offline)."""
    parsed = parse_github_repo(repo_url)
    if not parsed:
        return None
    key = (parsed[0].lower(), parsed[1].lower())
    if key in _TAGS_CACHE:
        return _TAGS_CACHE[key]
    url = f"https://github.com/{parsed[0]}/{parsed[1]}.git/info/refs?service=git-upload-pack"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError):
        return None
    tags = list(dict.fromkeys(_TAG_RE.findall(body)))
    _TAGS_CACHE[key] = tags
    return tags


def _normalize_version(v: str) -> str:
    v = (v or "").strip()
    if v[:1] in ("v", "V"):
        v = v[1:]
    try:
        from packaging.version import Version
        return str(Version(v))
    except Exception:
        return v.lower()


def tag_for_version(tags: Iterable[str], version: str) -> Optional[str]:
    """The tag that names `version`: 'V0.4.20', 'v0.4.20' or '0.4.20'
    (also 0.2.0a3 vs 0.2.0.a3 and the like, compared as versions)."""
    if not version:
        return None
    want = _normalize_version(version)
    for tag in tags or ():
        if _normalize_version(tag) == want:
            return tag
    return None


def http_get_text(url: str, timeout: float = 10.0) -> Optional[str]:
    """Returns the body, or None on 404/network failure - a missing
    golden file for one language is normal, not an error."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except (urllib.error.URLError, OSError, ValueError):
        return None


@dataclass
class GoldenResult:
    steps: List[ScriptStep]
    source: Optional[str] = None  # human-readable: path or URL it came from


def load_golden(skill_id: str, lang: str, golden_dirs: Iterable = (),
                fetch: Callable[[str], Optional[str]] = http_get_text,
                repo_url_finder: Callable[[str], Optional[str]] = find_repo_url,
                cache_dir: Path = GOLDEN_CACHE_DIR,
                examples_finder: Optional[Callable[[str, str], list]] = find_skill_examples,
                version_finder: Optional[Callable[[str], Optional[str]]] = find_installed_version,
                tag_lister: Optional[Callable[[str], Optional[List[str]]]] = list_repo_tags) -> GoldenResult:
    """Golden utterances for skill_id in lang. Lookup order:

    1. local checkouts: <golden_dir>/<repo-name>/test/end2end/<file>
    2. the skill's GitHub repo at the tag of the INSTALLED version, so
       the steps match the code that answers them. If that release
       ships no golden file, its skill.json examples are used (step 4)
       rather than the default branch's file, which describes another
       version. Only when the installed version has no tag (or it
       can't be found out) is the default branch (HEAD) used.
    3. the cache from an earlier successful fetch (offline fallback)
    4. the skill's own skill.json "examples" (#28) - many skills have
       no golden file but do ship examples. These carry no intent
       label, so they're checked at skill level only: "did THIS skill
       answer", not which of its intents.

    Returns GoldenResult([], None) when nothing is found anywhere."""
    names = golden_filenames(lang)

    for base in golden_dirs:
        base = Path(base).expanduser()
        for repo_dir in repo_dir_candidates(skill_id):
            for name in names:
                path = base / repo_dir / GOLDEN_SUBPATH / name
                if path.is_file():
                    try:
                        text = path.read_text(encoding="utf-8")
                    except OSError:
                        continue
                    return GoldenResult(parse_script(text, lang, skill_id), str(path))

    cache_base = Path(cache_dir) / skill_id
    repo_url = repo_url_finder(skill_id)

    def _examples(note: str) -> Optional[GoldenResult]:
        if examples_finder is None:
            return None
        try:
            examples = examples_finder(skill_id, normalize_lang(lang))
        except Exception:
            examples = []
        steps = [ScriptStep(e.strip(), lang, skill_id, None) for e in examples if isinstance(e, str) and e.strip()]
        return GoldenResult(steps, note) if steps else None

    tag = None
    if repo_url and version_finder is not None and tag_lister is not None:
        try:
            version = version_finder(skill_id)
            tag = tag_for_version(tag_lister(repo_url) or [], version) if version else None
        except Exception:
            tag = None
    if tag:
        tag_cache = cache_base / tag
        for name in names:
            url = github_raw_url(repo_url, f"{GOLDEN_SUBPATH}/{name}", ref=tag)
            text = fetch(url) if url else None
            if text is not None:
                try:
                    tag_cache.mkdir(parents=True, exist_ok=True)
                    (tag_cache / name).write_text(text, encoding="utf-8")
                except OSError:
                    pass
                return GoldenResult(parse_script(text, lang, skill_id), url)
        for name in names:
            path = tag_cache / name
            if path.is_file():
                try:
                    text = path.read_text(encoding="utf-8")
                except OSError:
                    continue
                return GoldenResult(parse_script(text, lang, skill_id), f"{path} (cached)")
        # The installed release has no golden file. The default branch's
        # describes another version (renamed or new intents), so don't
        # fall back to it.
        return _examples(f"skill.json examples (skill-level check only - release {tag} has no golden file)") \
            or GoldenResult([], None)

    if repo_url:
        for name in names:
            url = github_raw_url(repo_url, f"{GOLDEN_SUBPATH}/{name}")
            text = fetch(url) if url else None
            if text is not None:
                try:
                    cache_base.mkdir(parents=True, exist_ok=True)
                    (cache_base / name).write_text(text, encoding="utf-8")
                except OSError:
                    pass
                return GoldenResult(parse_script(text, lang, skill_id), url)

    for name in names:
        path = cache_base / name
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            return GoldenResult(parse_script(text, lang, skill_id), f"{path} (cached)")

    if examples_finder is not None:
        try:
            examples = examples_finder(skill_id, normalize_lang(lang))
        except Exception:
            examples = []
        steps = [ScriptStep(e.strip(), lang, skill_id, None) for e in examples if isinstance(e, str) and e.strip()]
        if steps:
            return GoldenResult(steps, "skill.json examples (skill-level check only - no golden file)")

    return GoldenResult([], None)


def list_user_scripts(scripts_dir: Path = SCRIPTS_DIR) -> List[Path]:
    try:
        return sorted(p for p in Path(scripts_dir).iterdir()
                      if p.is_file() and p.suffix in SCRIPT_SUFFIXES)
    except OSError:
        return []


# --------------------------------------------------------------------
# Observing and judging one step
# --------------------------------------------------------------------

@dataclass
class StepObservation:
    intents: List[str] = field(default_factory=list)  # "<skill_id>:<intent>"
    skills: List[str] = field(default_factory=list)
    failed: bool = False
    spoke: List[str] = field(default_factory=list)
    # a common-reading search went out and no provider has been fetched
    # from yet - see PROVIDER_WAIT
    awaiting_provider: bool = False
    provider: str = ""  # the provider skill the reading pipeline fetched from / OCP played
    provider_via: str = ""  # "the reading pipeline" or "OCP"
    last_speak_type: str = ""  # see observe(): drops a dual-emitted duplicate

    def _add(self, lst, value):
        if value and value not in lst:
            lst.append(value)

    def add_intent(self, name: str):
        self._add(self.intents, name)
        if ":" in name:
            self._add(self.skills, name.split(":", 1)[0])

    def add_skill(self, skill_id: str):
        self._add(self.skills, skill_id)


def observe(obs: StepObservation, msg_type: str, data: dict, context: dict,
            known_skills: Iterable[str] = ()) -> None:
    """Folds one bus message into obs. `known_skills` (installed skill
    ids plus the step's expected one) is what tells a dispatched
    '<skill_id>:<intent>' message apart from other colon-containing
    message types like 'recognizer_loop:utterance'."""
    data = data or {}
    context = context or {}
    known = set(known_skills)

    if msg_type == "ovos.intent.matched":
        name = data.get("intent_name") or data.get("intent_type") or data.get("match_type") or ""
        skill = data.get("skill_id") or context.get("skill_id")
        if name and ":" not in name and skill:
            name = f"{skill}:{name}"
        if name:
            obs.add_intent(name)
        if skill:
            obs.add_skill(skill)
        return

    if msg_type.startswith("ocp:"):
        # the OCP media pipeline took it ('start a metronome' -> ocp:play,
        # seen live) - there's no skill id at this point, only the action;
        # which media skill it picks comes with ovos.common_play.play
        obs.add_intent(msg_type)
        if msg_type == "ocp:play":
            obs.awaiting_provider = True
        return

    if ":" in msg_type:
        prefix = msg_type.split(":", 1)[0]
        if prefix in known or _looks_like_component_id(prefix):
            obs.add_intent(msg_type)
            return

    # A skill (or pipeline plugin) waiting in get_response()/converse
    # captures the utterance before any intent matching - dispatched as
    # '<id>.converse.get_response' (seen live on ovos-core 2.1.1, from
    # both a skill and ovos-common-reading-pipeline-plugin).
    if ".converse." in msg_type:
        owner, _, rest = msg_type.partition(".converse.")
        if owner in known or _looks_like_component_id(owner):
            obs.add_intent(f"{owner}:converse.{rest}")
            return

    if msg_type == "mycroft.skill.handler.start":
        obs.add_skill(context.get("skill_id") or data.get("skill_id"))
    elif msg_type.startswith(FALLBACK_PREFIX) and msg_type.endswith(".response"):
        if data.get("result"):
            obs.add_skill(msg_type[len(FALLBACK_PREFIX):-len(".response")])
    elif msg_type == "question:action":
        obs.add_skill(data.get("skill_id"))
    elif msg_type == OCP_PLAY:
        # OCP picked a result - its media carries the skill that serves it
        media = data.get("media") or {}
        skill = media.get("skill_id") if isinstance(media, dict) else None
        if skill and skill != OCP_ID:
            obs.provider, obs.provider_via = skill, "OCP"
            obs.add_skill(skill)
        obs.awaiting_provider = False
    elif msg_type == READING_SEARCH:
        obs.awaiting_provider = True
    elif msg_type.startswith(READING_FETCH_PREFIX) and not msg_type.endswith(".response"):
        # common-reading pipeline picked this provider skill's content
        obs.provider = msg_type[len(READING_FETCH_PREFIX):]
        obs.provider_via = "the reading pipeline"
        obs.add_skill(obs.provider)
        obs.awaiting_provider = False
    elif msg_type in ("intent_failure", "complete_intent_failure"):
        obs.failed = True
    elif msg_type in ("speak", "ovos.utterance.speak"):
        # both names: the classic one and the spec one newer cores use
        # (see bus.SPEAK_TYPES). A dual-emitting core sends the same
        # sentence under both, back to back - count it once.
        utterance = data.get("utterance")
        if utterance and not (obs.spoke and obs.spoke[-1] == utterance and obs.last_speak_type != msg_type):
            obs.spoke.append(utterance)
        obs.last_speak_type = msg_type


_NON_INTENT_PREFIXES = ("mycroft.", "ovos.common_play", "recognizer_loop", "ovos.utterance")


def _looks_like_component_id(prefix: str) -> bool:
    """'<something>.<author>' ids that aren't installed skills but still
    dispatch '<id>:<intent>' - pipeline plugins like
    'ovos-common-reading-pipeline-plugin.andlo:read_content' (seen live)."""
    return ("." in prefix and " " not in prefix and "/" not in prefix
            and not prefix.startswith(_NON_INTENT_PREFIXES))


def is_converse_capture(intent: str) -> bool:
    return ":converse." in intent


def _norm_intent(name: str) -> str:
    skill, _, label = (name or "").strip().partition(":")
    skill = skill.lower()
    # Older skill releases name their intents in CamelCase ('CancelAlert',
    # ovos-skill-alerts 0.1.28 on the testing channel) where the golden
    # file on the repo's default branch already says 'cancel_alert' -
    # split CamelCase before comparing so both count as the same intent.
    label = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", label).lower()
    if label.endswith(".intent"):
        label = label[:-len(".intent")]
    # Golden rows name padacioso intents 'what_time_is_it'; the same
    # intent dispatched via padatious is 'what.time.is.it.intent' (seen
    # live on ovos-skill-date-time) - treat '.', '_', '-' and ' ' alike.
    label = re.sub(r"[._\- ]+", "_", label)
    return f"{skill}:{label}"


PASS, FAIL, TIMEOUT, SENT = "pass", "fail", "timeout", "sent"


@dataclass
class StepResult:
    status: str
    detail: str


def describe(obs: StepObservation) -> str:
    captures = [i for i in obs.intents if is_converse_capture(i)]
    if captures:
        skill = captures[0].split(":", 1)[0]
        return f"{skill} (captured by its pending get_response/converse - the skill is waiting for an answer)"
    if obs.intents:
        text = ", ".join(obs.intents)
        # the reading pipeline matched - say whose story it read
        if obs.provider:
            verb = "played by" if obs.provider_via == "OCP" else "read from"
            return f"{text}, {verb} {obs.provider}"
        return text
    if obs.skills:
        return ", ".join(obs.skills)
    if obs.failed:
        return "no skill matched"
    return "nothing matched"


def evaluate(step: ScriptStep, obs: StepObservation, timed_out: bool = False) -> StepResult:
    """PASS when the expected intent (or, for rows without an
    intent_label, the expected skill) handled the utterance; FAIL when
    something else did; TIMEOUT when nothing at all happened in time;
    SENT for plain steps with no expectation (just shows what matched)."""
    if not step.has_expectation:
        if timed_out and not (obs.intents or obs.skills or obs.failed):
            return StepResult(TIMEOUT, "no response")
        return StepResult(SENT, describe(obs))

    expected = step.expected_intent
    # a pass names the skill too, like a failure does ("expected <skill>:<intent>")
    if expected and any(_norm_intent(i) == _norm_intent(expected) for i in obs.intents):
        return StepResult(PASS, expected)

    if step.skill_id in obs.skills:
        own = [i for i in obs.intents if i.split(":", 1)[0] == step.skill_id]
        if any(is_converse_capture(i) for i in own):
            return StepResult(FAIL, f"got {describe(obs)}")
        if expected and own:
            return StepResult(FAIL, f"expected {step.intent_label}, got {', '.join(i.split(':', 1)[1] for i in own)}")
        passed = expected or step.skill_id
        return StepResult(PASS, f"{passed}, via {obs.provider_via or 'the reading pipeline'}" if obs.provider else passed)

    if timed_out and not (obs.intents or obs.skills or obs.failed):
        return StepResult(TIMEOUT, "no response")
    return StepResult(FAIL, f"expected {expected or step.skill_id}, got {describe(obs)}")


# --------------------------------------------------------------------
# Running a script
# --------------------------------------------------------------------

@dataclass
class RunSummary:
    title: str
    total: int = 0
    results: list = field(default_factory=list)  # (index, step, StepResult)
    cancelled: bool = False
    duration: float = 0.0
    # Sessions the steps ran in. Anything a step started (a metronome,
    # a timer, a story being read) lives in THAT session, so a plain
    # mycroft.stop in the default session doesn't reach it - found live:
    # a metronome started by a test step kept ticking after the run.
    session_ids: list = field(default_factory=list)
    started_at: float = 0.0  # wall-clock time.time() when the run started
    # per step index: what handled it and what OVOS said - for saving the
    # result (results.py), since StepResult.detail alone is terse on a pass
    handled_by: dict = field(default_factory=dict)
    replies: dict = field(default_factory=dict)

    def count(self, status: str) -> int:
        return sum(1 for _, _, r in self.results if r.status == status)

    @property
    def failures(self) -> list:
        return [(i, s, r) for i, s, r in self.results if r.status in (FAIL, TIMEOUT)]


class ScriptRunner:
    """Sends steps one at a time and waits for each to finish before
    the next. Bus messages are pushed in via feed() (from the bus
    thread); run() blocks, so the caller runs it in a worker thread.

    A step is finished when: ovos.utterance.handled arrives (newer
    core's end-marker), OR a handler-complete / intent-failure arrives
    (older core - then briefly waits for a late utterance.handled too),
    OR STEP_TIMEOUT passes. If TTS playback started, it additionally
    waits for it to end, so replies don't overlap the next step."""

    TERMINAL = ("ovos.utterance.handled",)
    SOFT_TERMINAL = ("mycroft.skill.handler.complete", "mycroft.skill.handler.error",
                     "complete_intent_failure")

    def __init__(self, steps: List[ScriptStep], title: str,
                 send: Callable[[int, int, ScriptStep], None],
                 on_step_done: Callable[[int, int, ScriptStep, StepResult, StepObservation], None] = None,
                 known_skills: Callable[[], Iterable[str]] = lambda: (),
                 step_timeout: float = None, speech_timeout: float = None,
                 late_handled_wait: float = None, settle: float = None, quiet_after_match: float = None,
                 provider_wait: float = None,
                 stop_session: Callable[[str], None] = None,
                 story_start_wait: float = None, stop_wait: float = None,
                 busy_wait: float = None,
                 on_busy: Callable[[int, int, ScriptStep, float], None] = None,
                 answer: Callable[[str, str, Optional[str]], None] = None,
                 response_release_wait: float = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.steps = list(steps)
        self.title = title
        self._send = send
        self._on_step_done = on_step_done or (lambda *a: None)
        self._known_skills = known_skills
        # None -> the module-level defaults, read at construction time
        # (not bound at import) so they stay patchable in tests.
        self.step_timeout = STEP_TIMEOUT if step_timeout is None else step_timeout
        self.speech_timeout = SPEECH_TIMEOUT if speech_timeout is None else speech_timeout
        self.late_handled_wait = LATE_HANDLED_WAIT if late_handled_wait is None else late_handled_wait
        self.settle = SETTLE if settle is None else settle
        self.quiet_after_match = QUIET_AFTER_MATCH if quiet_after_match is None else quiet_after_match
        self.provider_wait = PROVIDER_WAIT if provider_wait is None else provider_wait
        self.story_start_wait = STORY_START_WAIT if story_start_wait is None else story_start_wait
        self.stop_wait = STOP_WAIT if stop_wait is None else stop_wait
        self.busy_wait = BUSY_WAIT if busy_wait is None else busy_wait
        self._on_busy = on_busy or (lambda *a: None)
        self._stop_session = stop_session
        # answer(session, text, lang) - sends an utterance into a step's
        # session; used to cancel a get_response the step left open.
        # `session` is the full serialized session from the skill's
        # get_response.enable message: a non-default session lives only
        # in message context, so the "waiting for an answer" state must
        # travel with the answer or core routes it as a new utterance
        # (seen live - a bare session_id left all 6 waits hanging).
        self._answer = answer
        self.response_release_wait = (RESPONSE_RELEASE_WAIT if response_release_wait is None
                                      else response_release_wait)
        self._pending_response = set()   # skills waiting in get_response in this step's session
        self._response_session = None    # that session, as the skill serialized it
        self._step_session = None        # latest full serialization of the step's session seen on the bus
        self._response_released = threading.Event()
        self._disable_seen = threading.Event()   # any get_response.disable since the last cancel
        self._releasing = False
        self.released_responses = 0      # how many get_response waits the run cancelled
        self._last_msg = 0.0
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._obs = None
        self._expected_skill = None
        self._known = set()
        self._handled = threading.Event()
        self._soft_done = threading.Event()
        self._speaking = False
        self._speech_seen = False
        self._speech_done = threading.Event()
        self._speech_started = threading.Event()
        self._provider_seen = threading.Event()
        self._cancel = threading.Event()
        self.current = 0
        self.session_id = None  # fresh per step, see _run_step()
        self.session_ids = []   # every session this run used - see RunSummary.session_ids

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def stop(self) -> None:
        self._cancel.set()
        self._provider_seen.set()
        self._handled.set()
        self._soft_done.set()
        self._speech_done.set()

    def feed(self, msg_type: str, data: dict = None, context: dict = None) -> None:
        context = context or {}
        with self._lock:
            if self._obs is None:
                return
            known = self._known | ({self._expected_skill} if self._expected_skill else set())
            msg_session = (context.get("session") or {}).get("session_id") if isinstance(context.get("session"), dict) else None
            other_session = bool(self.session_id and msg_session and msg_session != self.session_id)
            # Messages from OTHER sessions (something still going on in
            # the default session, e.g. a story being read) don't count
            # toward this step's result - seen live. Audio events are
            # still used for "is TTS playing" below, whatever session.
            if (not other_session and msg_session and isinstance(context.get("session"), dict)
                    and len(context["session"]) > 1):
                # the session as core/skills last serialized it (active
                # skills, response mode...) - a stop or answer sent with
                # just the id reaches nobody (seen live)
                self._step_session = dict(context["session"])
            if not other_session and msg_type == "skill.converse.get_response.enable":
                self._pending_response.add((data or {}).get("skill_id") or "?")
                if isinstance(context.get("session"), dict):
                    self._response_session = dict(context["session"])
                self._response_released.clear()
            elif not other_session and msg_type == "skill.converse.get_response.disable":
                self._pending_response.discard((data or {}).get("skill_id") or "?")
                self._disable_seen.set()
                if not self._pending_response:
                    self._response_released.set()
            if self._releasing:
                # traffic from our own "cancel" - not part of the step's result
                return
            if not other_session:
                observe(self._obs, msg_type, data or {}, context, known)
                if not self._obs.awaiting_provider:
                    self._provider_seen.set()
                else:
                    self._provider_seen.clear()
            self._last_msg = self._clock()
            if msg_type in ("mycroft.audio.speech.start", "recognizer_loop:audio_output_start"):
                self._speaking = True
                self._speech_seen = True
                self._speech_done.clear()
                self._speech_started.set()
            elif msg_type in ("mycroft.audio.speech.stop", "recognizer_loop:audio_output_end"):
                self._speaking = False
                self._speech_done.set()
        # An end-marker from another session belongs to something else -
        # e.g. ovos-core timing out a handler from an earlier step (a quiz
        # waiting 300 s for an answer) - and must not end this step before
        # its own match arrives. Seen live on alpha: 40 steps reported
        # "nothing matched" while core had matched them correctly.
        if other_session:
            return
        if msg_type in self.TERMINAL:
            self._handled.set()
        elif msg_type in self.SOFT_TERMINAL:
            self._soft_done.set()

    def _run_step(self, index: int, step: ScriptStep):
        total = len(self.steps)
        with self._lock:
            self._obs = StepObservation()
            # Each step gets its own OVOS session (the sender puts it in
            # the utterance's context - see app._script_send), so leftover
            # converse/get_response state can't capture it.
            self.session_id = f"ovos-tui-test-{uuid.uuid4().hex[:12]}"
            self.session_ids.append(self.session_id)
            self._expected_skill = step.skill_id
            self._known = set(self._known_skills() or ())
            self._speaking = False
            self._last_msg = self._clock()
            self._speech_seen = False
            self._pending_response = set()
            self._response_session = None
            self._step_session = None
            self._releasing = False
        self._response_released.set()
        self._handled.clear()
        self._soft_done.clear()
        self._speech_done.set()
        self._speech_started.clear()
        self._provider_seen.set()

        self._send(index, total, step)

        deadline = self._clock() + self.step_timeout
        timed_out = True
        while not self._cancel.is_set():
            remaining = deadline - self._clock()
            if remaining <= 0:
                break
            if self._handled.wait(min(remaining, 0.1)):
                timed_out = False
                break
            if self._soft_done.is_set():
                # older core has no utterance.handled - give it a moment
                self._handled.wait(self.late_handled_wait)
                timed_out = False
                break
            with self._lock:
                matched = bool(self._obs and (self._obs.intents or self._obs.skills or self._obs.failed))
                # a reading search waits for its fetch - bus silence then
                # just means the pipeline is busy announcing the story
                awaiting_provider = bool(self._obs and self._obs.awaiting_provider)
                quiet = self._clock() - self._last_msg
                speaking = self._speaking
                # replied but TTS hasn't started yet (synthesis can take a
                # few seconds) - wait longer before calling it done
                waiting_for_tts = bool(self._obs and self._obs.spoke) and not self._speech_seen
                # the skill took it and now waits for an answer: its handler
                # won't finish (no utterance.handled) until it gets one
                asking = bool(self._pending_response)
            needed = self.quiet_after_match * (3 if waiting_for_tts else 1)
            if matched and not speaking and not awaiting_provider and (quiet >= needed or asking):
                timed_out = False
                break

        late = None
        busy_gave_up = False
        silent = False  # nothing at all had happened when the step timed out
        if timed_out and not self._cancel.is_set():
            with self._lock:
                silent = not (self._obs and (self._obs.intents or self._obs.skills or self._obs.failed))
                asking = bool(self._pending_response)
            # a skill waiting for an answer isn't "core still busy": it
            # would wait out BUSY_WAIT for nothing - the cancel below ends it
            if not asking:
                late, busy_gave_up = self._wait_until_core_is_done(index, total, step)

        if not self._cancel.is_set():
            # the reading pipeline fetches from its provider after the
            # handler is done - wait for that before judging the step
            self._provider_seen.wait(self.provider_wait)
            with self._lock:
                story = bool(self._obs and self._obs.provider)
            if story:
                # verdict known - don't sit through (or leave running) a
                # story that can last minutes; see STORY_START_WAIT
                self._speech_started.wait(self.story_start_wait)
                self._end_step_session(step)
                self._speech_done.wait(self.stop_wait)
            else:
                with self._lock:
                    speaking = self._speaking
                if speaking:
                    self._speech_done.wait(self.speech_timeout)
                # Always stop the step's session before the next step, so
                # nothing it started (counting forever, a metronome, speech
                # past SPEECH_TIMEOUT) goes on under the next one. Only this
                # step's own session is touched.
                self._end_step_session(step)
                with self._lock:
                    speaking = self._speaking
                if speaking:
                    self._speech_done.wait(self.stop_wait)
            if self.settle:
                self._sleep(self.settle)

        with self._lock:
            obs, self._obs = self._obs, None
        result = evaluate(step, obs, timed_out=timed_out)
        if silent and late is not None:
            later = evaluate(step, obs)
            result = StepResult(TIMEOUT, f"no response in {self.step_timeout:.0f} s; OVOS finished it after "
                                         f"{late:.0f} s ({'handled by ' if later.status == PASS else ''}"
                                         f"{later.detail or later.status})")
        elif silent and busy_gave_up:
            result = StepResult(TIMEOUT, f"no response; OVOS was still busy with it after "
                                         f"{self.step_timeout + self.busy_wait:.0f} s")
        return result, obs

    def _wait_until_core_is_done(self, index: int, total: int, step: ScriptStep):
        """After a timeout: (seconds core took in all, or None; gave up?).
        See BUSY_WAIT."""
        if self.busy_wait <= 0:
            return None, False
        self._on_busy(index, total, step, self.busy_wait)
        started = self._clock() - self.step_timeout
        deadline = self._clock() + self.busy_wait
        while not self._cancel.is_set() and self._clock() < deadline:
            if self._handled.wait(0.2) or self._soft_done.is_set():
                return self._clock() - started, False
            with self._lock:
                matched = bool(self._obs and (self._obs.intents or self._obs.skills or self._obs.failed))
                quiet = self._clock() - self._last_msg
                speaking = self._speaking
            if matched and not speaking and quiet >= self.quiet_after_match:
                return self._clock() - started, False
        return None, not self._cancel.is_set()

    def _end_step_session(self, step: ScriptStep) -> None:
        self._release_pending_response(step)
        self._stop_step_session()

    def _release_pending_response(self, step: ScriptStep) -> None:
        """Answer "cancel" to a get_response the step left waiting, so the
        skill's handler thread is freed (see RESPONSE_RELEASE_WAIT)."""
        with self._lock:
            pending = bool(self._pending_response)
            if pending and self._answer and self.session_id and not self._cancel.is_set():
                self._releasing = True
                session = self._response_session or {"session_id": self.session_id}
            else:
                return
        # A quiz answers a cancel by asking its next question - answer
        # those too, a few rounds at most (seen live: geometry-practice).
        for _ in range(RELEASE_ROUNDS):
            self._disable_seen.clear()
            try:
                self._answer(session, CANCEL_UTTERANCE, step.lang)
            except Exception:
                break
            if not self._disable_seen.wait(self.response_release_wait):
                break
            self.released_responses += 1
            self._sleep(RELEASE_SETTLE)
            with self._lock:
                if not self._pending_response:
                    break
                session = self._response_session or session
        with self._lock:
            self._releasing = False

    def _stop_step_session(self) -> None:
        if self._stop_session and self.session_id and not self._cancel.is_set():
            with self._lock:
                session = self._step_session
            try:
                self._stop_session(session or self.session_id)
            except Exception:
                pass

    def run(self) -> RunSummary:
        summary = RunSummary(title=self.title, total=len(self.steps), started_at=time.time())
        start = self._clock()
        for i, step in enumerate(self.steps, start=1):
            if self._cancel.is_set():
                break
            self.current = i
            result, obs = self._run_step(i, step)
            if self._cancel.is_set():
                break
            summary.results.append((i, step, result))
            if obs is not None:
                summary.handled_by[i] = describe(obs)
                summary.replies[i] = list(obs.spoke)
            self._on_step_done(i, len(self.steps), step, result, obs)
        summary.cancelled = self._cancel.is_set()
        summary.duration = self._clock() - start
        summary.session_ids = list(self.session_ids)
        return summary
