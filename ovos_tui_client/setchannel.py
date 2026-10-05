"""Make this install the release channel (#65): `ovos-tui --set-channel`.

An install is not always exactly the channel it was made for: the OVOS
installer resolves in separate batches, and on alpha it allows
pre-releases for everything, so a device can end up below the channel's
versions, with third-party betas (httpx 1.0.dev6 once kept the intent
pipeline from loading, OpenVoiceOS/ovos-installer#635), or with plugins
that conflict with the core.

The rules are the ones the channel's own tests use (ovos-test-harness's
test/channel_compat/install_channel.sh, also behind Klondike's CI):

1. Channel stack: the packages the harness's requirements name that the
   channel's constraints also name, installed BY NAME under -c constraints
   (--upgrade) - limited to what this install has or its intent pipeline
   names (a headless box gets no ovos-gui; a pipeline stage whose plugin is
   missing, like padatious, gets it). pip runs without --pre: a constraint
   line that names a pre-release already lets pip take it, --pre takes
   pre-releases of everything.
2. Lock: LOCKED_STACK pinned to what step 1 gave.
3. Everything else on the install that the channel names, upgraded under
   the constraints AND the lock. A package that only installs by moving
   the core is reported and left as it is, never forced in.
4. Pre-releases the channel does not name that only came in as
   dependencies go to the newest final release below them, with every
   dependent's own specifiers in the request (pip does not check installed
   dependents when asked for "x<v"). One a dependent asks for is kept.

Constraints and the harness stack are read live, never kept in this code.
With dry_run, pip's own `--dry-run --report` says what each step would do,
and nothing is changed: that is the health check from #64.

Pure functions first (tested without pip or network), then run().
"""
import json
import re
import subprocess
import sys
import time
from importlib.metadata import distributions
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from ovos_tui_client import channel as channel_mod

CHANNELS = channel_mod.CHANNELS
HARNESS_REQUIREMENTS_URL = ("https://raw.githubusercontent.com/OpenVoiceOS/ovos-test-harness/"
                            "dev/requirements.txt")
WORK_ROOT = Path.home() / ".cache" / "ovos-tui-client" / "set-channel"

# The packages that define a channel, locked once step 1 installed them.
# Same list as Klondike's run_shard.LOCKED_STACK (the store's CI).
LOCKED_STACK = ("ovos-core", "ovos-workshop", "ovos-bus-client", "ovos-plugin-manager",
                "ovos-config", "ovos-utils")
# The harness's own test tooling is not part of a device stack.
TEST_TOOLS = frozenset({"ovoscope", "pytest", "pytest-json-report", "pytest-timeout",
                        "ovos-spec-tools"})
# Repos whose distribution name is not the repo name (as in the harness's
# test/channel_compat/resolve.py).
DIST_NAME_OVERRIDES = {
    "ovos-adapt-pipeline-plugin": "ovos-adapt-parser",
    "ovos-padatious-pipeline-plugin": "ovos-padatious",
}
STAGE_SUFFIX = re.compile(r"-(high|medium|low)$")
NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")

normalize = channel_mod.normalize


# --- pure parts -------------------------------------------------------------

def requirement_names(text: str) -> List[str]:
    """Distribution names a requirements/constraints file names, in order.
    git+https://github.com/OpenVoiceOS/ovos-core@dev -> ovos-core."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("git+"):
            repo = line.split("#", 1)[0].rsplit("/", 1)[-1].split("@", 1)[0]
            repo = normalize(repo[:-4] if repo.endswith(".git") else repo)
            out.append(DIST_NAME_OVERRIDES.get(repo, repo))
            continue
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        m = NAME.match(line)
        if m:
            out.append(normalize(m.group(1)))
    return list(dict.fromkeys(out))


def pipeline_packages(stages: Iterable[str]) -> set:
    """ovos-padatious-pipeline-plugin-high -> ovos-padatious."""
    out = set()
    for stage in stages or []:
        plugin = normalize(STAGE_SUFFIX.sub("", str(stage)))
        out.add(DIST_NAME_OVERRIDES.get(plugin, plugin))
    return out


def plan(channel_names: Iterable[str], harness_names: Iterable[str],
         installed: Iterable[str], pipeline: Iterable[str]) -> Dict[str, List[str]]:
    """Which packages each step touches.

    stack: the harness stack the channel names, limited to what is installed
    or the pipeline names; added: the part of it not installed yet;
    skipped: harness stack this install doesn't have; rest: everything else
    installed that the channel names."""
    channel = set(channel_names)
    installed = set(installed)
    pipeline = set(pipeline)
    harness = [n for n in harness_names if n in channel and n not in TEST_TOOLS]
    stack = [n for n in harness if n in installed or n in pipeline]
    return {
        "stack": stack,
        "added": [n for n in stack if n not in installed],
        "skipped": [n for n in harness if n not in stack],
        "rest": sorted(n for n in installed & channel if n not in harness and n not in TEST_TOOLS),
    }


def lock_lines(versions: Dict[str, str]) -> List[str]:
    """ovos-core==3.7.2a2 ... for the LOCKED_STACK packages present."""
    v = {normalize(k): val for k, val in versions.items()}
    return [f"{p}=={v[p]}" for p in LOCKED_STACK if v.get(p)]


def _is_pre(spec: str) -> bool:
    from packaging.version import InvalidVersion, Version
    bare = re.sub(r"^[<>=!~]+", "", spec.strip())
    if bare.endswith(".*"):
        bare = bare[:-2]
    try:
        return Version(bare).is_prerelease
    except InvalidVersion:
        return False


def prerelease_moves(dists: Iterable[Tuple[str, str, List[str]]],
                     channel_names: Iterable[str]) -> List[Tuple[str, str, Optional[str]]]:
    """(name, version, request) for each pre-release outside the channel that
    something depends on. request is "name<base,<dependents' specifiers>"
    (no pre-release in it, so pip stays on final releases), or None when a
    dependent asks for a pre-release itself (keep it).

    dists: (name, version, requires) for every installed distribution."""
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.version import InvalidVersion, Version
    named = set(channel_names)
    dists = list(dists)
    asked: Dict[str, List[str]] = {}
    for _name, _ver, requires in dists:
        for r in requires or []:
            try:
                req = Requirement(r)
            except InvalidRequirement:
                continue
            if req.marker and "extra" in str(req.marker):
                continue
            asked.setdefault(normalize(req.name), []).extend(str(s) for s in req.specifier)
    out = []
    for name, ver, _req in dists:
        n = normalize(name)
        try:
            v = Version(ver)
        except InvalidVersion:
            continue
        if not v.is_prerelease or n in named or n not in asked:
            continue
        specs = sorted(set(asked[n]))
        if any(_is_pre(s) for s in specs):
            out.append((n, ver, None))
        else:
            out.append((n, ver, n + ",".join([f"<{v.base_version}"] + specs)))
    return out


def conflict_reason(pip_output: str) -> str:
    """pip's "The conflict is caused by:" lines, deduplicated, on one line."""
    lines = [l.strip() for l in (pip_output or "").splitlines()]
    if "The conflict is caused by:" not in lines:
        tail = [l for l in lines if l.startswith("ERROR:")]
        return tail[-1][len("ERROR:"):].strip() if tail else "see the pip log"
    i = lines.index("The conflict is caused by:")
    seen, out = set(), []
    for l in lines[i + 1:]:
        if not l or l.startswith(("Additionally", "To fix")):
            break
        if l not in seen:
            seen.add(l)
            out.append(l)
    return "; ".join(out)[:300]


def report_changes(report: Dict) -> Dict[str, str]:
    """{name: version} pip's --report says it would install."""
    out = {}
    for item in (report or {}).get("install") or []:
        meta = item.get("metadata") or {}
        if meta.get("name"):
            out[normalize(meta["name"])] = meta.get("version", "")
    return out


def health(constraints_text: str, dists: Iterable[Tuple[str, str, List[str]]],
           pip_check_lines: Iterable[str] = ()) -> Dict:
    """How clean an install is on a channel (#64), cheaply: no pip resolve.

    behind:      {package: (installed, channel's specifier)} for packages the
                 channel names whose installed version it doesn't allow
    prereleases: {package: version} pre-releases outside the channel that
                 nothing asks for (what --set-channel would move)
    conflicts:   pip check's lines (a package's requirements not met)"""
    from packaging.specifiers import InvalidSpecifier, SpecifierSet
    from packaging.version import InvalidVersion, Version
    pins = channel_mod.parse_constraints(constraints_text or "")
    dists = list(dists)
    behind = {}
    for name, ver, _req in dists:
        n = normalize(name)
        spec = pins.get(n)
        if not spec or spec.startswith("@"):  # unpinned or a git/url line
            continue
        try:
            if Version(ver) not in SpecifierSet(spec, prereleases=True):
                behind[n] = (ver, spec)
        except (InvalidVersion, InvalidSpecifier):
            continue
    pre = {n: v for n, v, request in prerelease_moves(dists, pins.keys()) if request}
    return {"behind": behind, "prereleases": pre, "conflicts": [l for l in pip_check_lines if l.strip()]}


def health_counts(h: Optional[Dict]) -> List[str]:
    """['3 behind', '6 pre-releases', '3 conflicts'] - only the non-zero ones."""
    if not h:
        return []
    out = []
    for key, one, many in (("behind", "behind", "behind"), ("prereleases", "pre-release", "pre-releases"),
                           ("conflicts", "conflict", "conflicts")):
        n = len(h.get(key) or [])
        if n:
            out.append(f"{n} {one if n == 1 else many}")
    return out


def health_markdown(h: Optional[Dict], channel: str) -> str:
    """The 'How clean is this install' part of the Release channel window."""
    if h is None:
        return ""
    lines = ["## How clean this install is", ""]
    counts = health_counts(h)
    if not counts:
        lines += [f"Clean: every package {channel} names is at a version {channel} allows, there are "
                  "no pre-releases outside the channel that nothing asks for, and pip finds no "
                  "conflicts.", ""]
        return "\n".join(lines)
    lines += [f"Not quite {channel}: {', '.join(counts)}. **Set channel: {channel}…** below shows "
              "what it would change (a dry run first); conflicts that come from a plugin's own "
              "upper bound can't be fixed from here.", ""]
    if h["behind"]:
        lines += ["**Not at the channel's versions:**", ""]
        lines += [f"- {n} {v} (the channel says {spec})" for n, (v, spec) in sorted(h["behind"].items())] + [""]
    if h["prereleases"]:
        lines += ["**Pre-releases outside the channel that nothing asks for:**", ""]
        lines += [f"- {n} {v}" for n, v in sorted(h["prereleases"].items())] + [""]
    if h["conflicts"]:
        lines += ["**pip check:**", ""] + [f"- {l}" for l in h["conflicts"]] + [""]
    return "\n".join(lines)


# --- the device -------------------------------------------------------------

def installed_dists() -> List[Tuple[str, str, List[str]]]:
    return [(d.metadata["Name"], d.version, list(d.requires or [])) for d in distributions()
            if d.metadata["Name"]]


def installed_versions() -> Dict[str, str]:
    return {normalize(n): v for n, v, _ in installed_dists()}


def configured_pipeline() -> List[str]:
    try:
        from ovos_config import Configuration
        return list((Configuration().get("intents") or {}).get("pipeline") or [])
    except Exception:  # noqa: BLE001 - no config: only what is installed
        return []


def read_source(source: str, fetch: Callable = channel_mod.fetch_text) -> Optional[str]:
    """A constraints file from a path or an http(s) URL."""
    if re.match(r"^https?://", source or ""):
        return fetch(source)
    try:
        return Path(source).expanduser().read_text(encoding="utf-8")
    except OSError:
        return None


class Pip:
    """pip in this Python environment (the OVOS venv ovos-tui runs in)."""

    def __init__(self, python: str = sys.executable, log_dir: Optional[Path] = None):
        self.python = python
        self.log_dir = log_dir

    def install(self, args: List[str], dry_run: bool, log_name: str) -> Tuple[bool, str, Dict]:
        cmd = [self.python, "-m", "pip", "install", "--disable-pip-version-check",
               "--progress-bar", "off"] + args
        report_file = None
        if dry_run:
            report_file = (self.log_dir or Path(".")) / f"{log_name}.report.json"
            cmd += ["--dry-run", "--report", str(report_file)]
        p = subprocess.run(cmd, capture_output=True, text=True)
        out = (p.stdout or "") + (p.stderr or "")
        if self.log_dir:
            (self.log_dir / f"{log_name}.log").write_text(" ".join(cmd) + "\n\n" + out)
        report = {}
        if report_file and report_file.exists():
            try:
                report = json.loads(report_file.read_text())
            except ValueError:
                report = {}
        return p.returncode == 0, out, report

    def freeze(self) -> str:
        p = subprocess.run([self.python, "-m", "pip", "freeze", "--disable-pip-version-check"],
                           capture_output=True, text=True)
        return p.stdout or ""

    def check(self) -> List[str]:
        p = subprocess.run([self.python, "-m", "pip", "check", "--disable-pip-version-check"],
                           capture_output=True, text=True)
        return [l for l in (p.stdout or "").splitlines()
                if l.strip() and not l.startswith("No broken requirements")]


def run(channel: str, constraints: Optional[str] = None, dry_run: bool = False,
        out: Callable[[str], None] = print, pip: Optional[Pip] = None,
        fetch: Callable = channel_mod.fetch_text, work_root: Path = WORK_ROOT) -> Dict:
    """Make this install `channel` (or only show what that would change).
    Returns a result dict; `out` gets the progress lines."""
    if channel not in CHANNELS:
        raise ValueError(f"unknown channel {channel!r} (want {', '.join(CHANNELS)})")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    work = Path(work_root) / channel / stamp
    work.mkdir(parents=True, exist_ok=True)
    pip = pip or Pip(log_dir=work)
    pip.log_dir = pip.log_dir or work
    res = {"channel": channel, "dry_run": dry_run, "work_dir": str(work), "changed": {},
           "added": [], "skipped": [], "cannot_follow": {}, "prereleases_moved": {},
           "prereleases_kept": {}, "pip_check": [], "error": None}

    source = constraints or channel_mod.CONSTRAINTS_URL.format(channel=channel)
    ctext = read_source(source, fetch)
    if not ctext:
        res["error"] = f"could not read the constraints from {source}"
        return res
    htext = fetch(HARNESS_REQUIREMENTS_URL)
    if not htext:
        res["error"] = f"could not fetch the channel stack from {HARNESS_REQUIREMENTS_URL}"
        return res
    cfile = work / "constraints.txt"
    cfile.write_text(ctext)
    res["constraints"] = source
    out(f"Constraints: {source}")

    before = installed_versions()
    (work / "freeze-before.txt").write_text(pip.freeze())
    p = plan(requirement_names(ctext), requirement_names(htext), before,
             pipeline_packages(configured_pipeline()))
    res["added"], res["skipped"] = p["added"], p["skipped"]
    out(f"[1/4] channel stack: {len(p['stack'])} packages"
        + (f", adding {', '.join(p['added'])} for the intent pipeline" if p["added"] else ""))

    ok, log, report = pip.install(["--upgrade", "-c", str(cfile)] + p["stack"], dry_run, "step1-stack")
    if not ok:
        res["error"] = "the channel stack does not install: " + conflict_reason(log)
        return res
    would = dict(before)
    would.update(report_changes(report))

    lock = lock_lines(would if dry_run else installed_versions())
    lfile = work / "lock.txt"
    lfile.write_text("\n".join(lock) + "\n")
    out("[2/4] core locked: " + ", ".join(lock))

    out(f"[3/4] {len(p['rest'])} other packages the channel names")
    c = ["-c", str(cfile), "-c", str(lfile)]
    ok, log, report = pip.install(["--upgrade"] + c + p["rest"], dry_run, "step3-rest")
    if ok:
        would.update(report_changes(report))
    else:
        for name in p["rest"]:
            ok1, log1, rep1 = pip.install(["--upgrade"] + c + [name], dry_run, f"step3-{name}")
            if ok1:
                would.update(report_changes(rep1))
            else:
                res["cannot_follow"][name] = conflict_reason(log1)

    out("[4/4] pre-releases nothing asks for")
    dists = installed_dists()
    if dry_run:  # judge the versions the steps above would leave
        dists = [(n, would.get(normalize(n), v), r) for n, v, r in dists]
    for name, ver, request in prerelease_moves(dists, requirement_names(ctext)):
        if request is None:
            res["prereleases_kept"][name] = f"{ver} (a dependent asks for a pre-release)"
            continue
        ok, log, report = pip.install(c + [request], dry_run, f"step4-{name}")
        if ok:
            got = report_changes(report).get(name) if dry_run else installed_versions().get(name)
            res["prereleases_moved"][name] = f"{ver} -> {got or '?'}"
            if dry_run and got:
                would[name] = got
        else:
            res["prereleases_kept"][name] = f"{ver} (no final release fits)"

    after = would if dry_run else installed_versions()
    res["changed"] = {n: (before.get(n), after[n]) for n in sorted(after)
                      if before.get(n) != after[n]}
    if not dry_run:
        (work / "freeze-after.txt").write_text(pip.freeze())
    res["pip_check"] = pip.check()  # on a dry run: the install as it is now
    return res


def render(res: Dict) -> str:
    """The result as Markdown, for the terminal and the TUI window."""
    ch = res.get("channel")
    title = f"# {'Dry run: s' if res.get('dry_run') else 'S'}et channel {ch}"
    lines = [title, ""]
    if res.get("error"):
        return "\n".join(lines + [f"**Stopped:** {res['error']}", ""])
    if res.get("dry_run") and not (res["changed"] or res["prereleases_moved"]):
        lines += [f"**Nothing to do: this install already follows {ch}**, as far as its "
                  "packages can (what can't follow is listed below, with why). There is "
                  "nothing to apply; run it again when the channel moves.", ""]
    lines += [f"Constraints: {res.get('constraints')}", ""]
    verb = "would change" if res.get("dry_run") else "changed"
    lines += [f"## {len(res['changed'])} packages {verb}", ""]
    if res["changed"]:
        lines += ["| Package | Now | " + ("Would be" if res.get("dry_run") else "After") + " |", "|---|---|---|"]
        lines += [f"| {n} | {a or '-'} | {b} |" for n, (a, b) in res["changed"].items()]
    else:
        lines += ["Everything the channel names that can follow it already does."]
    lines += [""]
    if res.get("added"):
        lines += [f"Added for the intent pipeline: {', '.join(res['added'])}.", ""]
    lines += ["## Can't follow the channel (left as installed)", ""]
    lines += [f"- **{n}**: {why}" for n, why in res["cannot_follow"].items()] or ["None."]
    lines += ["", "## Pre-releases outside the channel", ""]
    moved = [f"- {n}: {v}" for n, v in res["prereleases_moved"].items()]
    kept = [f"- {n} {v}, kept" for n, v in res["prereleases_kept"].items()]
    lines += (moved + kept) or ["None."]
    lines += ["", "## pip check" + (" (as installed now)" if res.get("dry_run") else ""), ""]
    lines += [f"- {l}" for l in res["pip_check"]] or ["No broken requirements."]
    lines += ["", f"Logs, constraints, lock and pip freezes: `{res['work_dir']}`", ""]
    if not res.get("dry_run") and res["changed"]:
        lines += ["**Restart OVOS** to load the new versions.", ""]
    return "\n".join(lines)


def cli(args) -> int:
    """`ovos-tui --set-channel <channel> [--dry-run] [--constraints FILE|URL]`."""
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("--set-channel changes the Python environment ovos-tui runs in; run it on the "
              "device itself, not against a remote bus.", file=sys.stderr)
        return 2
    try:
        res = run(args.set_channel, constraints=args.constraints, dry_run=args.dry_run,
                  out=lambda s: print(s, file=sys.stderr, flush=True))
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(render(res))
    if res.get("error"):
        return 2
    # A dry run is a health check: 1 when the install isn't the channel yet.
    # Packages that can't follow the channel are upstream caps, not a failure.
    return 1 if res.get("dry_run") and (res["changed"] or res["prereleases_moved"]) else 0
