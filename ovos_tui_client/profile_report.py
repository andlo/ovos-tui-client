"""The profile report (#62): what passes on this install, per profile.

Three profiles that build on each other, like a skill store's:

  default  the OVOS installer's default skills (read live from
           ovos-installer) and the intent pipeline this install runs
  extra    + the installer's "extra skills"
  custom   + a requirements file given with --profile (e.g. a store's
           curated list); ovos-tui-client knows no store

Each entry is a skill or a pipeline plugin, keyed by its runtime id
(skill_id, pipeline plugin id), with a level measured on this device:

  1  installed     the package is in this Python environment
  2  loads         the skill is in OVOS' skill list; a pipeline plugin is
                   in the pipeline and OVOS didn't leave it out
  3  routes        its golden utterances (en-US) reach it, >= 80 %
                   (only with routes=True: it runs every golden utterance)

The JSON is `ovos-profile-report/1`, the format a store's own profile
report uses, with the same rules for state and label, so a device's report
compares row by row with a store's. The store-only fields (store_id, the
curated-profile counts and badge) are left out: the counts here are this
device's, with everything it has installed loaded, and say so.
"""
import re
import time
from typing import Callable, Dict, Iterable, List, Optional, Tuple

SCHEMA = "ovos-profile-report/1"
INSTALLER_TEMPLATES = ("https://raw.githubusercontent.com/OpenVoiceOS/ovos-installer/main/"
                       "ansible/roles/ovos_virtualenv/templates/virtualenv/{name}")
DEFAULT_TEMPLATE = "skills-requirements.txt.j2"
EXTRA_TEMPLATE = "extra-skills-requirements.txt.j2"
SKILL_GROUPS = ("ovos.plugin.skill", "opm.skill")
PIPELINE_GROUP = "opm.pipeline"
LEVEL3_RATIO = 0.8      # the same as a store's: below this a skill "loads", it doesn't route
LEVEL3_LANG = "en-US"
STAGE_SUFFIX = re.compile(r"-(high|medium|low)$")
_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[([^\]]*)\])?")


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name or "").lower()


# --- which packages a profile has --------------------------------------------------

def template_lines(text: str, server: bool = False) -> List[str]:
    """Requirement lines of an ovos-installer Jinja template, for a
    non-server install with the optional features off: a block guarded
    only by the profile is kept, any other condition (ggwave ...) isn't."""
    out, keep = [], [True]
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("{% if"):
            cond = line[5:].strip(" %}")
            only_profile = re.fullmatch(r'ovos_installer_profile\s*!=\s*"server"', cond) is not None
            keep.append(keep[-1] and only_profile and not server)
            continue
        if line.startswith("{% endif"):
            if len(keep) > 1:
                keep.pop()
            continue
        if not line or line.startswith("#") or "{%" in line or not keep[-1]:
            continue
        out.append(line)
    return out


def requirement_file_lines(text: str) -> List[str]:
    """A pip requirements file (a --profile): the requirement lines."""
    out = []
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            out.append(line)
    return out


def expand(lines: Iterable[str], extra_of: Callable[[str, str], List[str]]) -> List[str]:
    """Package names a list of requirement lines installs, with extras of a
    package (ovos-core[skills-media]) expanded to the packages they add.
    extra_of(package, extra) -> package names. A git URL counts by its
    repo name."""
    out = []
    for line in lines:
        if line.startswith("git+"):
            repo = line.split("#", 1)[0].rstrip("/").rsplit("/", 1)[-1].split("@", 1)[0]
            out.append(normalize(repo[:-4] if repo.endswith(".git") else repo))
            continue
        m = _NAME.match(line)
        if not m:
            continue
        pkg, extras = normalize(m.group(1)), [e.strip() for e in (m.group(3) or "").split(",") if e.strip()]
        if extras:
            for extra in extras:
                out += [normalize(p) for p in extra_of(pkg, extra)]
        else:
            out.append(pkg)
    return list(dict.fromkeys(out))


def installed_extra_of(package: str, extra: str) -> List[str]:
    """The packages an extra adds, from the installed package's metadata."""
    from importlib.metadata import PackageNotFoundError, requires
    from packaging.requirements import InvalidRequirement, Requirement
    try:
        reqs = requires(package) or []
    except PackageNotFoundError:
        return []
    out = []
    for r in reqs:
        try:
            req = Requirement(r)
        except InvalidRequirement:
            continue
        if req.marker and req.marker.evaluate({"extra": extra}) and "extra" in str(req.marker):
            out.append(req.name)
    return out


def runtime_ids(package: str) -> List[Tuple[str, str]]:
    """[(runtime id, 'skill'|'pipeline')] an installed package registers."""
    from importlib.metadata import PackageNotFoundError, distribution
    try:
        dist = distribution(package)
    except PackageNotFoundError:
        return []
    out = []
    for ep in dist.entry_points:
        if ep.group in SKILL_GROUPS:
            out.append((ep.name, "skill"))
        elif ep.group == PIPELINE_GROUP:
            out.append((ep.name, "pipeline"))
    return list(dict.fromkeys(out))


def package_version(package: str) -> Optional[str]:
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version(package)
    except PackageNotFoundError:
        return None


def pipeline_plugins(stages: Iterable[str]) -> List[str]:
    """ovos-padatious-pipeline-plugin-high -> ovos-padatious-pipeline-plugin."""
    return list(dict.fromkeys(STAGE_SUFFIX.sub("", s) for s in stages or []))


# --- one entry -----------------------------------------------------------------------

DEVICE_NOTE = "measured on this device, with everything it has installed loaded"
LAST_RESORT_NOTE = ("a fallback of last resort: on this install its golden utterances reach other "
                    "skills and fallbacks first, as they should, so they don't measure it")


def entry(runtime_id: str, kind: str, version: Optional[str], *, installed: bool,
          loaded: Dict[str, Optional[bool]], pipeline: Iterable[str], left_out: Iterable[str],
          routing: Optional[Dict[str, Tuple[int, int]]] = None, keeps_talking: Iterable[str] = (),
          last_resort: Iterable[str] = (), now: Optional[str] = None) -> Dict:
    """One row, with a store's rules for state and label."""
    row = {"runtime_id": runtime_id, "kind": kind}
    if version:
        row["version"] = version
    if now:
        row["tested_at"] = now
    if not installed:
        return {**row, "state": "untested", "label": "not installed here", "level": None}
    if kind == "pipeline":
        plugins = set(pipeline_plugins(pipeline))
        if runtime_id in set(left_out):
            return {**row, "state": "fail", "label": "✗ doesn't load", "level": 1,
                    "note": "in the pipeline, but OVOS left it out"}
        if runtime_id not in plugins:
            return {**row, "state": "warn", "label": "✓ installed · not in the pipeline", "level": 1}
        return {**row, "state": "pass", "label": "✓ loads", "level": 2}
    # a skill
    if runtime_id not in loaded:
        return {**row, "state": "fail", "label": "✗ doesn't load", "level": 1}
    if loaded.get(runtime_id) is False:
        return {**row, "state": "warn", "label": "✓ loads · deactivated", "level": 2}
    if runtime_id in set(last_resort):
        # e.g. fallback-unknown ("I don't know"): its golden utterances only
        # reach it when it's the only fallback, never on a real install
        return {**row, "state": "pass", "label": "✓ loads · last-resort fallback", "level": 2,
                "note": LAST_RESORT_NOTE}
    counts = (routing or {}).get(runtime_id)
    if not counts or not counts[1]:
        return {**row, "state": "pass", "label": "✓ loads", "level": 2}
    hit, counted = counts
    row["golden"] = {"hit": hit, "counted": counted, "langs": [LEVEL3_LANG],
                     "by_lang": {LEVEL3_LANG: {"hit": hit, "counted": counted}}}
    row["note"] = DEVICE_NOTE
    if hit / counted < LEVEL3_RATIO:
        return {**row, "state": "warn", "label": f"✓ loads · {hit}/{counted} golden", "level": 2}
    if runtime_id in set(keeps_talking):
        return {**row, "state": "warn", "label": f"✓ {hit}/{counted} golden · doesn't stop", "level": 3}
    return {**row, "state": "pass" if hit == counted else "warn",
            "label": f"✓ {hit}/{counted} golden", "level": 3}


def summary(rows: List[Dict]) -> Dict:
    loads = [r for r in rows if r["state"] in ("pass", "warn")]
    return {
        "total": len(rows),
        "loads": sum(1 for r in loads if (r.get("level") or 0) >= 2),
        "fails": sum(1 for r in rows if r["state"] == "fail"),
        "not_testable": sum(1 for r in rows if r["state"] == "unsupported"),
        "untested": sum(1 for r in rows if r["state"] == "untested"),
        "routes": sum(1 for r in loads if (r.get("level") or 0) >= 3),
    }


# --- the profiles ------------------------------------------------------------------

def profile_members(default_lines: List[str], extra_lines: List[str], custom_lines: Optional[List[str]],
                    pipeline: Iterable[str],
                    extra_of: Optional[Callable[[str, str], List[str]]] = None,
                    ids_of: Optional[Callable[[str], List[Tuple[str, str]]]] = None) -> List[Dict]:
    """[{id, name, builds_on, packages, members: [(runtime_id, kind, package)]}]; a
    runtime id is in the first profile that has it. A package that isn't
    installed has no known runtime id, so it stands in with its own name."""
    extra_of = extra_of or installed_extra_of
    ids_of = ids_of or runtime_ids
    specs = [("default", "Default", None, default_lines), ("extra", "Extra", "default", extra_lines)]
    if custom_lines is not None:
        specs.append(("custom", "Custom", "extra", custom_lines))
    seen, profiles = set(), []
    for pid, name, builds_on, lines in specs:
        members = []
        for pkg in expand(lines, extra_of):
            ids = ids_of(pkg) or [(pkg, "pipeline" if "pipeline" in pkg else "skill")]
            for rid, kind in ids:
                if rid not in seen:
                    seen.add(rid)
                    members.append((rid, kind, pkg))
        if pid == "default":
            # the pipeline this install runs is part of what it was installed as
            for plugin in pipeline_plugins(pipeline):
                if plugin not in seen:
                    seen.add(plugin)
                    members.append((plugin, "pipeline", None))
        profiles.append({"id": pid, "name": name, "builds_on": builds_on, "members": members})
    return profiles


# --- the report --------------------------------------------------------------------

def plugin_package(plugin_id: str) -> Optional[str]:
    """The installed package that registers a pipeline plugin."""
    from importlib.metadata import distributions
    for d in distributions():
        for ep in d.entry_points:
            if ep.group == PIPELINE_GROUP and ep.name == plugin_id:
                return d.metadata["Name"]
    return None


def left_out_stages(log_lines: Iterable[str]) -> List[str]:
    """Pipeline plugins OVOS leaves out: ovos-core logs 'Requested some
    invalid pipeline components! filtered: [...]' (skills.log) each time it
    builds the pipeline. Only lines since OVOS last started count; an older
    one may be from before the plugin was installed."""
    last = None
    for line in log_lines:
        if "ovos-core is ready" in line or "Skills Manager is ready" in line:
            last = None
        elif "invalid pipeline" in line:
            last = line
    if not last:
        return []
    return pipeline_plugins(re.findall(r"'([A-Za-z0-9_.\-]+)'", last.split("invalid pipeline", 1)[1]))


def fallback_skills(log_lines: Iterable[str]) -> set:
    """Skills that registered a fallback handler since OVOS last started
    ('registering fallback handler -> ovos.skills.fallback.<skill_id>')."""
    out = set()
    for line in log_lines:
        if "ovos-core is ready" in line or "Skills Manager is ready" in line:
            continue   # registrations come before 'ready'; keep them
        m = re.search(r"registering fallback handler -> ovos\.skills\.fallback\.(\S+)", line)
        if m:
            out.add(m.group(1))
    return out


def last_resort_fallbacks(registered: Iterable[str], taken: Dict[str, Tuple[int, int]]) -> set:
    """Fallback skills whose golden utterances mostly went to something
    else: a last resort ("I don't know") on this install, so level 3 can't
    measure them. taken: {skill: (steps something else took, steps)}."""
    registered = set(registered)
    return {s for s, (by_fallback, counted) in taken.items()
            if s in registered and counted and by_fallback * 2 >= counted}


def build_report(channel: str, profiles: List[Dict], *, loaded: Dict[str, Optional[bool]],
                 pipeline: List[str], left_out: List[str], routing: Optional[Dict] = None,
                 keeps_talking: Iterable[str] = (), last_resort: Iterable[str] = (),
                 constraints_url: Optional[str] = None,
                 sources: Optional[Dict[str, str]] = None, manifest: Optional[Dict] = None,
                 tool: str = "ovos-tui-client", installed: Callable[[str], bool] = None,
                 version_of: Callable[[str, Optional[str]], Optional[str]] = None,
                 now: Optional[float] = None) -> Dict:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now or time.time()))
    installed = installed or (lambda pkg: package_version(pkg) is not None)
    version_of = version_of or (lambda rid, pkg: package_version(pkg or plugin_package(rid) or rid))
    out_profiles = []
    for p in profiles:
        rows = []
        for rid, kind, pkg in p["members"]:
            is_installed = installed(pkg) if pkg else plugin_package(rid) is not None
            rows.append(entry(rid, kind, version_of(rid, pkg) if is_installed else None,
                              installed=is_installed, loaded=loaded, pipeline=pipeline, left_out=left_out,
                              routing=routing, keeps_talking=keeps_talking, last_resort=last_resort,
                              now=stamp))
        out_profiles.append({"id": p["id"], "name": p["name"], "builds_on": p["builds_on"],
                             "source": (sources or {}).get(p["id"]), "entries": rows, "summary": summary(rows)})
    report = {
        "schema": SCHEMA, "generated_at": stamp, "tool": tool,
        "channels": {channel: {"run_at": stamp, "constraints_url": constraints_url,
                               "pipeline_not_loaded": list(left_out), "profiles": out_profiles}},
        "routes_measured": routing is not None,
    }
    if manifest:
        report["manifest"] = manifest
    return report


def report_markdown(report: Dict) -> str:
    ch, data = next(iter(report["channels"].items()))
    lines = [f"# Profile report: {ch}", "", f"- **Generated:** {report.get('generated_at')}",
             f"- **Tool:** {report.get('tool')}",
             "- **Levels:** 1 installed · 2 loads · 3 routes (golden utterances, en-US, "
             f">= {int(LEVEL3_RATIO * 100)} %)" + ("" if report.get("routes_measured") else
                                                   " - not measured in this run (--routes)"), ""]
    if data.get("pipeline_not_loaded"):
        lines += [f"- **Left out of the pipeline by OVOS:** {', '.join(data['pipeline_not_loaded'])}", ""]
    for p in data["profiles"]:
        s = p["summary"]
        lines += [f"## {p['name']}" + (f" (+ {p['builds_on']})" if p.get("builds_on") else ""), ""]
        if p.get("source"):
            lines += [f"From {p['source']}.", ""]
        lines += [f"{s['total']} entries: {s['loads']} load, {s['routes']} route, {s['fails']} fail, "
                  f"{s['untested']} not installed here.", "",
                  "| Entry | Kind | Level | Result | Version |", "|---|---|---|---|---|"]
        for e in p["entries"]:
            lines.append(f"| {e['runtime_id']} | {e['kind']} | {e.get('level') if e.get('level') is not None else '-'} "
                         f"| {e['label']} | {e.get('version') or '-'} |")
        lines.append("")
    return "\n".join(lines)
