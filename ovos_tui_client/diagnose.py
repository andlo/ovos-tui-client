"""Why a test step failed (#48): a diagnosis under the red line.

Stage 1 asks OVOS over the bus, right after the failed step and before the
next one starts (both checked on ovos-core 2.1 and 3.7):

* `intent.service.intent.get`: which stage and intent would match the
  utterance now, without running a handler;
* `intent.service.padatious.get`: padatious' best guess and its score. It
  answers with its best guess whatever the score (nonsense gets a 0.94
  from an intent with a catch-all slot), so a score only means something
  next to the thresholds of the padatious stages in the pipeline;
* `ovos.intent.list` (ovos-core 3.x): which intents are registered, per
  language. On 2.x the padatious manifest says which, not for which
  language.

That puts a failure in one category: skill not loaded, handler failed,
matched but silent, intent not registered (for this language), below the
threshold, another intent won, matches now, or unknown.

Stage 2 holds known causes, as rules. They read the skills.log, installed
versions and .intent files, so they only run when ovos-tui is on the OVOS
machine; over a remote bus the diagnosis says "cause unknown" and shows
what stage 1 found. Every new bug found becomes a rule.
"""
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from ovos_routing_judge import normalize_intent

# categories
NOT_LOADED = "skill_not_loaded"
HANDLER_FAILED = "handler_failed"
SILENT = "matched_but_silent"
NOT_REGISTERED = "intent_not_registered"
BELOW_THRESHOLD = "below_threshold"
OTHER_WON = "other_intent_won"
MATCHES_NOW = "matches_now"
UNKNOWN = "unknown"

PADATIOUS = "ovos-padatious-pipeline-plugin"
DEFAULT_THRESHOLDS = {"high": 0.95, "medium": 0.8, "low": 0.5}
PROBE_TIMEOUT = 8.0      # intent.get runs the pipeline; fallback/common query take a second or two
LOG_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

# Request: (message type, data, reply type, timeout) -> reply data or None
Request = Callable[[str, dict, str, float], Optional[dict]]


@dataclass
class Diagnosis:
    category: str
    lines: List[str] = field(default_factory=list)   # shown under the red line
    cause: Optional[str] = None                      # a known cause (stage 2)
    evidence: Dict = field(default_factory=dict)

    def as_dict(self) -> Dict:
        d = {"category": self.category, "lines": list(self.lines), "evidence": dict(self.evidence)}
        if self.cause:
            d["cause"] = self.cause
        return d


@dataclass
class Context:
    """What the diagnosis may use besides the bus."""
    installed: Dict[str, Optional[bool]] = field(default_factory=dict)  # skill_id -> active
    local: bool = False
    pipeline: List[str] = field(default_factory=list)          # intents.pipeline, local only
    thresholds: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_THRESHOLDS))
    log_dir: Optional[Path] = None
    versions: Dict[str, str] = field(default_factory=dict)     # normalized package -> version
    intent_lines: Callable[[str, str, str], Optional[List[str]]] = lambda skill, intent, lang: None


def short(intent: str) -> str:
    """'skill.id:name.intent' -> 'name'; the label the steps use."""
    name = intent.split(":", 1)[1] if ":" in intent else intent
    return normalize_intent(name)


def same_intent(a: Optional[str], b: Optional[str]) -> bool:
    return bool(a and b) and short(a) == short(b)


def padatious_stages(pipeline: Iterable[str]) -> List[str]:
    """['high'] for a pipeline with only ovos-padatious-pipeline-plugin-high."""
    return [s.rsplit("-", 1)[1] for s in pipeline if s.startswith(PADATIOUS + "-")]


# --- stage 1 ----------------------------------------------------------------

def probe(step, request: Request) -> Dict:
    """Asks OVOS about the step's utterance. Every answer may be None
    (an older core, a plugin that isn't installed, a timeout)."""
    data = {"utterance": step.utterance, "lang": step.lang}
    out = {}
    r = request("intent.service.intent.get", data, "intent.service.intent.reply", PROBE_TIMEOUT)
    out["would_match"] = (r or {}).get("intent") if r is not None else "no reply"
    r = request("intent.service.padatious.get", data, "intent.service.padatious.reply", 3.0)
    out["padatious"] = (r or {}).get("intent") if r is not None else "no reply"
    r = request("ovos.intent.list", {}, "ovos.intent.list.response", 3.0)
    if r is not None and r.get("ok", True):
        out["registered"] = [i for i in r.get("intents") or []
                             if i.get("skill_id") == step.skill_id]
    else:  # ovos-core 2.x: no per-language list
        r = request("intent.service.padatious.manifest.get", {"lang": step.lang},
                    "intent.service.padatious.manifest", 3.0)
        if r is not None:
            out["padatious_manifest"] = [i for i in r.get("intents") or []
                                         if str(i).startswith(f"{step.skill_id}:")]
    return out


def stage1(step, result, obs, facts: Dict, ctx: Context) -> Diagnosis:
    skill, want, lang = step.skill_id, step.intent_label, step.lang
    errors = list(getattr(obs, "errors", []) or [])
    would = facts.get("would_match")
    pad = facts.get("padatious")
    ev = {"would_match": would if isinstance(would, dict) else None,
          "padatious": pad if isinstance(pad, dict) else None}
    lines: List[str] = []

    if skill and ctx.installed and skill not in ctx.installed:
        return Diagnosis(NOT_LOADED, [f"{skill} is not loaded (not in OVOS' skill list)"], evidence=ev)
    if skill and ctx.installed.get(skill) is False:
        return Diagnosis(NOT_LOADED, [f"{skill} is loaded but deactivated"], evidence=ev)

    handled_expected = skill and skill in (getattr(obs, "skills", None) or [])
    if errors and (handled_expected or not skill):
        ev["errors"] = errors[:3]
        return Diagnosis(HANDLER_FAILED, [f"the handler failed: {errors[0]}"], evidence=ev)
    if handled_expected and not getattr(obs, "spoke", None):
        return Diagnosis(SILENT, [f"{skill} took it but said nothing" +
                                  (" before the time limit" if result.status == "timeout" else "")],
                         evidence=ev)

    # registered for this language?
    registered = facts.get("registered")
    if skill and registered is not None:
        langs = {i.get("lang", "").lower() for i in registered}
        here = [i for i in registered if i.get("lang", "").lower() == (lang or "").lower()]
        ev["registered"] = sorted({short(i.get("intent_name", "")) for i in here})
        if registered and not here:
            return Diagnosis(NOT_REGISTERED, [f"{skill} registers no intents for {lang} "
                                              f"(only {', '.join(sorted(langs))}): is {lang} in "
                                              "secondary_langs?"], evidence=ev)
        if want and not any(same_intent(i.get("intent_name"), want) for i in here):
            names = ", ".join(ev["registered"][:6]) or "none"
            return Diagnosis(NOT_REGISTERED, [f"{want} is not registered for {lang} "
                                              f"({skill} registers: {names})"], evidence=ev)
    elif skill and want and facts.get("padatious_manifest") is not None:
        names = facts["padatious_manifest"]
        ev["registered"] = sorted({short(n) for n in names})
        if names and not any(same_intent(n, want) for n in names):
            # adapt intents aren't in the padatious manifest: only say so if
            # the skill registers padatious intents at all
            lines.append(f"{want} is not among {skill}'s padatious intents "
                         "(ovos-core 2.x can't say for which language)")

    # padatious' score next to the pipeline's padatious stages
    if isinstance(pad, dict) and want and same_intent(pad.get("name"), want):
        conf = float(pad.get("conf") or 0)
        stages = padatious_stages(ctx.pipeline) if ctx.pipeline else []
        needed = min((ctx.thresholds.get(s, DEFAULT_THRESHOLDS.get(s, 1.0)) for s in stages), default=None)
        if needed is not None and conf < needed:
            only = " and ".join(f"{PADATIOUS}-{s} (≥{ctx.thresholds.get(s, DEFAULT_THRESHOLDS[s])})"
                                for s in stages)
            return Diagnosis(BELOW_THRESHOLD, lines + [
                f"{want} is registered, but padatious scores it {conf:.2f}; "
                f"the pipeline has only {only}"], evidence=ev)
        if needed is None and conf < DEFAULT_THRESHOLDS["high"]:
            lines.append(f"padatious scores {want} {conf:.2f} (below {DEFAULT_THRESHOLDS['high']} for "
                         "padatious-high; this pipeline can't be read from here)")

    if isinstance(would, dict):
        name, stage = would.get("intent_name") or "", would.get("intent_service") or ""
        if (skill and would.get("skill_id") == skill) and (not want or same_intent(name, want)):
            return Diagnosis(MATCHES_NOW, lines + [f"it matches now ({stage}): timing, or "
                                                   "something earlier in the run got in the way"], evidence=ev)
        who = would.get("skill_id") or "fallback"
        what = short(name) if ":" in name else name
        lines.append(f"{who} wins it: {what} via {stage}")
        if isinstance(pad, dict) and pad.get("name") and not same_intent(pad.get("name"), want):
            lines.append(f"padatious' best guess: {pad['name']} ({float(pad.get('conf') or 0):.2f})")
        return Diagnosis(OTHER_WON, lines, evidence=ev)
    if would is None:
        lines.append("nothing matches it now either")
    return Diagnosis(UNKNOWN, lines or ["OVOS didn't say (no reply to the probes)"], evidence=ev)


# --- stage 2: known causes ----------------------------------------------------

def log_lines_since(log_dir: Optional[Path], since: float, name: str = "skills.log",
                    max_lines: int = 3000) -> List[str]:
    """skills.log lines stamped at or after `since` (a time.time())."""
    if not log_dir:
        return []
    path = Path(log_dir) / name
    try:
        tail = path.read_text(errors="replace").splitlines()[-max_lines:]
    except OSError:
        return []
    start = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(since))
    out, keep = [], False
    for line in tail:
        m = LOG_LINE.match(line)
        if m:
            keep = m.group(1) >= start
        if keep:
            out.append(line)
    return out


def _version_lt(v: Optional[str], major: int) -> bool:
    try:
        return int(str(v).split(".")[0]) < major
    except (TypeError, ValueError):
        return False


def _version_ge(v: Optional[str], major: int) -> bool:
    try:
        return int(str(v).split(".")[0]) >= major
    except (TypeError, ValueError):
        return False


SLOT_END = re.compile(r"\{[^}]+\}\s*[.?!]?\s*$")
ADJACENT_SLOTS = re.compile(r"\}\s*\{")


def known_causes(step, diag: Diagnosis, obs, ctx: Context, log: List[str]) -> Optional[str]:
    """The first known cause that fits, or None. Each rule names its issue."""
    skill, want, lang = step.skill_id, step.intent_label, step.lang
    if any("All OVOS Translate servers are down" in l for l in log):
        return ("the OVOS translate servers are down (skills.log); queries that need translation "
                "hang or fail until they are back")
    if skill and any("can_stop" in l and "NotImplementedError" in l for l in log) \
            and _version_ge(ctx.versions.get("ovos-workshop"), 9):
        return (f"a skill implements stop without can_stop, which ovos-workshop 9 rejects: it keeps "
                "talking after stop, and later steps may be affected")
    would = diag.evidence.get("would_match") or {}
    if str(would.get("intent_service", "")).startswith("ovos-m2v-pipeline") \
            and "common_query" in str(would.get("intent_name", "")) and not getattr(obs, "spoke", None):
        return ("ovos-m2v-pipeline matched common_query.question, but the winning answer is never "
                "spoken (OpenVoiceOS/ovos-m2v-pipeline#68)")
    if want and diag.category in (BELOW_THRESHOLD, OTHER_WON, NOT_REGISTERED):
        lines = ctx.intent_lines(skill, want, lang) or []
        samples = [l for l in lines if l.strip() and not l.lstrip().startswith("#")]
        if samples:
            ending = sum(1 for l in samples if SLOT_END.search(l))
            if ending and _version_lt(ctx.versions.get("ovos-padatious"), 2):
                return (f"{ending}/{len(samples)} lines of {want}.intent end in a slot, and "
                        f"ovos-padatious {ctx.versions.get('ovos-padatious')} strips the '}}' there "
                        "(known bug, OpenVoiceOS/ovos-padatious-pipeline-plugin#175)")
            adjacent = sum(1 for l in samples if ADJACENT_SLOTS.search(l))
            if adjacent and _version_ge(ctx.versions.get("ovos-workshop"), 9):
                return (f"{adjacent}/{len(samples)} lines of {want}.intent have two slots side by side "
                        f"({{a}} {{b}}), which ovos-workshop {ctx.versions.get('ovos-workshop')} drops "
                        "at registration")
    return None


# --- reading the machine (local only) -------------------------------------------

def find_intent_lines(skill_id: str, intent: str, lang: str) -> Optional[List[str]]:
    """The lines of <intent>.intent for `lang` in the skill's installed
    package (locale/<lang>/... in any case and folder depth), or None."""
    import importlib.metadata
    from ovos_tui_client.skill_examples import find_skill_distribution
    dist = find_skill_distribution(skill_id)
    if not dist:
        return None
    try:
        files = importlib.metadata.files(dist[0]) or []
    except Exception:  # noqa: BLE001
        return None
    want_lang = (lang or "").lower()
    name = short(intent).lower()
    for f in files:
        parts = [p.lower() for p in Path(str(f)).parts]
        if not parts or not parts[-1].endswith(".intent"):
            continue
        stem = parts[-1][:-len(".intent")]
        if normalize_intent(stem).lower() != name or want_lang not in parts:
            continue
        try:
            return Path(f.locate()).read_text(errors="replace").splitlines()
        except OSError:
            return None
    return None


def local_context(installed: Dict, log_dir: Optional[Path]) -> Context:
    """The diagnosis context on the OVOS machine itself."""
    pipeline, thresholds = [], dict(DEFAULT_THRESHOLDS)
    try:
        from ovos_config import Configuration
        intents = Configuration().get("intents") or {}
        pipeline = list(intents.get("pipeline") or [])
        pad = intents.get(PADATIOUS) or intents.get("padatious") or {}
        for key, level in (("conf_high", "high"), ("conf_med", "medium"), ("conf_low", "low")):
            if pad.get(key):
                thresholds[level] = float(pad[key])
    except Exception:  # noqa: BLE001 - no config: defaults
        pass
    versions = {}
    try:
        import importlib.metadata
        for pkg in ("ovos-padatious", "ovos-workshop", "ovos-core", "ovos-m2v-pipeline"):
            try:
                versions[pkg] = importlib.metadata.version(pkg)
            except importlib.metadata.PackageNotFoundError:
                pass
    except Exception:  # noqa: BLE001
        pass
    return Context(installed=dict(installed or {}), local=True, pipeline=pipeline,
                   thresholds=thresholds, log_dir=Path(log_dir) if log_dir else None,
                   versions=versions, intent_lines=find_intent_lines)


def remote_context(installed: Dict) -> Context:
    return Context(installed=dict(installed or {}), local=False)


# --- together -------------------------------------------------------------------

def diagnose(step, result, obs, request: Request, ctx: Context, since: float) -> Diagnosis:
    """The whole diagnosis of one failed step. Never raises."""
    try:
        facts = probe(step, request)
    except Exception as e:  # noqa: BLE001 - a diagnosis must never break a run
        facts = {"would_match": "no reply", "error": str(e)}
    try:
        diag = stage1(step, result, obs, facts, ctx)
    except Exception as e:  # noqa: BLE001
        diag = Diagnosis(UNKNOWN, [f"could not work it out: {e}"])
    if ctx.local:
        try:
            diag.cause = known_causes(step, diag, obs, ctx, log_lines_since(ctx.log_dir, since))
        except Exception:  # noqa: BLE001
            diag.cause = None
        if diag.cause:
            diag.lines.append(f"Likely cause: {diag.cause}")
    else:
        diag.lines.append("cause unknown: OVOS is on another machine, so its logs, versions and "
                          "intent files can't be read from here")
    return diag
