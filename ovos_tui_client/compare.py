"""Compare two test results step by step (#49).

Two `.report.json` files from the same test set, typically one from a
testing install and one from alpha. Each step is paired by what was said,
in which language and what was expected; for each pair the verdict, what
handled it, the pipeline plugin, the slots, padatious' score and the
failure diagnosis (#48) are compared. Unchanged steps are counted, not
listed. Each difference gets a class the tester can change:

* fix        - failed on A, passes on B (an expected improvement)
* regression - passed on A, fails on B
* unclear    - anything else that changed (another stage, other slots ...)

The two manifests are compared too, so the comparison says what it
compared: channel, the core versions, the pipeline plugins, install health.

Output: `ovos-test-comparison/1` JSON and a readable `.md`.
"""
import json
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

COMPARE_SCHEMA = "ovos-test-comparison/1"
FIX, REGRESSION, UNCLEAR = "fix", "regression", "unclear"
CLASSES = (FIX, REGRESSION, UNCLEAR)
CONF_STEP = 0.05   # padatious scores closer than this count as the same
KEY_PACKAGES = ("ovos-core", "ovos-workshop", "ovos-bus-client", "ovos-plugin-manager",
                "ovos-padatious", "ovos-adapt-parser", "ovos-m2v-pipeline", "ovos-config")


class NotAReport(ValueError):
    pass


def load_report(path) -> Dict:
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise NotAReport(f"{path}: {e}") from e
    if not str(data.get("schema", "")).startswith("ovos-test-report/"):
        raise NotAReport(f"{path}: not an ovos-test-report (schema {data.get('schema')!r})")
    return data


def side(report: Dict, path: Optional[str] = None) -> Dict:
    m = report.get("manifest") or {}
    core = (m.get("stack") or {}).get("ovos-core")
    label = " · ".join(x for x in (m.get("channel") or "channel unknown",
                                   f"ovos-core {core}" if core else "",
                                   (m.get("machine") or {}).get("model") or "") if x)
    return {"label": label, "title": report.get("title"), "created_at": m.get("created_at"),
            "channel": m.get("channel"), "file": str(path) if path else None,
            "summary": report.get("summary") or {}}


def _health_counts(m: Dict) -> str:
    h = m.get("channel_health")
    if not isinstance(h, dict):
        return "not recorded"
    parts = [f"{len(h.get(k) or [])} {k}" for k in ("behind", "prereleases", "conflicts") if h.get(k)]
    return ", ".join(parts) or "clean"


def manifest_differences(a: Dict, b: Dict) -> List[Dict]:
    """What differs between the two installs, as [{what, a, b}]."""
    ma, mb = a.get("manifest") or {}, b.get("manifest") or {}
    out = []

    def add(what, va, vb):
        if va != vb:
            out.append({"what": what, "a": va, "b": vb})
    add("channel", ma.get("channel"), mb.get("channel"))
    add("lang", ma.get("lang"), mb.get("lang"))
    # the stack and the pipeline plugins: {package: version} each
    va = {**(ma.get("stack") or {}), **(_as_versions(ma.get("pipeline_plugins")))}
    vb = {**(mb.get("stack") or {}), **(_as_versions(mb.get("pipeline_plugins")))}
    for pkg in KEY_PACKAGES:
        add(pkg, va.get(pkg), vb.get(pkg))
    for pkg in sorted((set(va) | set(vb)) - set(KEY_PACKAGES)):
        add(pkg, va.get(pkg), vb.get(pkg))
    ca, cb = ma.get("config") or {}, mb.get("config") or {}
    add("secondary langs", ", ".join(ca.get("secondary_langs") or []) or None,
        ", ".join(cb.get("secondary_langs") or []) or None)
    add("pipeline", " → ".join(ca.get("pipeline") or []) or None,
        " → ".join(cb.get("pipeline") or []) or None)
    add("install health", _health_counts(ma), _health_counts(mb))
    return out


def _as_versions(plugins) -> Dict[str, str]:
    """pipeline_plugins as {package: version}, whatever shape a report has."""
    if isinstance(plugins, dict):
        return {str(k): str(v) for k, v in plugins.items()}
    out = {}
    for p in plugins or []:
        if isinstance(p, dict) and p.get("name"):
            out[str(p["name"])] = str(p.get("version") or "")
    return out


def _key(row: Dict) -> Tuple[str, str, str]:
    return (str(row.get("utterance", "")).strip().lower(), str(row.get("lang") or "").lower(),
            str(row.get("expected") or ""))


def align(a_steps: List[Dict], b_steps: List[Dict]) -> List[Tuple[Optional[Dict], Optional[Dict]]]:
    """Pairs the steps by (utterance, lang, expected); a sentence that occurs
    twice is paired by occurrence. Keeps A's order, then what only B has."""
    seen: Dict[Tuple, int] = {}
    b_index: Dict[Tuple, List[Dict]] = {}
    for row in b_steps:
        b_index.setdefault(_key(row), []).append(row)
    used = set()
    pairs = []
    for row in a_steps:
        k = _key(row)
        n = seen.get(k, 0)
        seen[k] = n + 1
        rows = b_index.get(k, [])
        other = rows[n] if n < len(rows) else None
        if other is not None:
            used.add(id(other))
        pairs.append((row, other))
    pairs += [(None, row) for row in b_steps if id(row) not in used]
    return pairs


def _match(row: Dict) -> Dict:
    return row.get("match") or {}


_INTENT_REF = re.compile(r"([A-Za-z0-9_.\-]+):([A-Za-z0-9_.\-]+)")


def norm_handled(text: Optional[str]) -> str:
    """'handled by' with every '<skill>:<intent>' in one naming."""
    from ovos_routing_judge import normalize_intent
    return _INTENT_REF.sub(lambda m: normalize_intent(m.group(0)), str(text or ""))


def step_changes(ra: Dict, rb: Dict) -> List[Dict]:
    """[{what, a, b}] for each thing that differs between two runs of one step."""
    out = []

    def add(what, va, vb):
        if va != vb:
            out.append({"what": what, "a": va, "b": vb})
    add("result", ra.get("status"), rb.get("status"))
    # ovos-core 2.x names an intent 'skill:what.time.is.it.intent', 3.x
    # 'skill:what_time_is_it': the same intent, not a difference
    if norm_handled(ra.get("handled_by")) != norm_handled(rb.get("handled_by")):
        out.append({"what": "handled by", "a": ra.get("handled_by"), "b": rb.get("handled_by")})
    ma, mb = _match(ra), _match(rb)
    if ma.get("stage") and mb.get("stage"):   # 2.x doesn't name the stage
        add("stage", ma.get("stage"), mb.get("stage"))
    add("slots", ma.get("slots") or {}, mb.get("slots") or {})
    ca, cb = ma.get("conf"), mb.get("conf")
    if ca is not None and cb is not None and abs(float(ca) - float(cb)) >= CONF_STEP:
        add("padatious score", round(float(ca), 2), round(float(cb), 2))
    da, db = (ra.get("diagnosis") or {}).get("category"), (rb.get("diagnosis") or {}).get("category")
    if da or db:
        add("why it failed", da, db)
    return out


def suggest(ra: Dict, rb: Dict) -> str:
    ok_a, ok_b = ra.get("status") == "pass", rb.get("status") == "pass"
    checked = ra.get("status") in ("pass", "fail", "timeout") and rb.get("status") in ("pass", "fail", "timeout")
    if checked and not ok_a and ok_b:
        return FIX
    if checked and ok_a and not ok_b:
        return REGRESSION
    return UNCLEAR


def compare(a: Dict, b: Dict, a_path: Optional[str] = None, b_path: Optional[str] = None,
            now: Optional[float] = None) -> Dict:
    diffs, only_a, only_b, same = [], [], [], 0
    for ra, rb in align(a.get("steps") or [], b.get("steps") or []):
        if rb is None:
            only_a.append({"utterance": ra.get("utterance"), "lang": ra.get("lang"),
                           "expected": ra.get("expected"), "status": ra.get("status")})
            continue
        if ra is None:
            only_b.append({"utterance": rb.get("utterance"), "lang": rb.get("lang"),
                           "expected": rb.get("expected"), "status": rb.get("status")})
            continue
        changes = step_changes(ra, rb)
        if not changes:
            same += 1
            continue
        cls = suggest(ra, rb)
        diffs.append({
            "utterance": ra.get("utterance"), "lang": ra.get("lang"), "expected": ra.get("expected"),
            "a": {"i": ra.get("i"), "status": ra.get("status"), "handled_by": ra.get("handled_by"),
                  "match": _match(ra) or None, "diagnosis": ra.get("diagnosis")},
            "b": {"i": rb.get("i"), "status": rb.get("status"), "handled_by": rb.get("handled_by"),
                  "match": _match(rb) or None, "diagnosis": rb.get("diagnosis")},
            "changes": changes, "suggested": cls, "class": cls,
        })
    result = {
        "schema": COMPARE_SCHEMA,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now or time.time())),
        "a": side(a, a_path), "b": side(b, b_path),
        "installs": manifest_differences(a, b),
        "differences": diffs, "only_a": only_a, "only_b": only_b,
    }
    result["summary"] = summarize(result, same)
    return result


def summarize(c: Dict, same: Optional[int] = None) -> Dict:
    if same is None:
        same = (c.get("summary") or {}).get("identical", 0)
    diffs = c.get("differences") or []
    return {"compared": same + len(diffs), "identical": same, "changed": len(diffs),
            **{cls: sum(1 for d in diffs if d.get("class") == cls) for cls in CLASSES},
            "only_a": len(c.get("only_a") or []), "only_b": len(c.get("only_b") or [])}


def set_class(c: Dict, index: int, cls: str) -> None:
    """The tester's own class for difference `index`."""
    if cls not in CLASSES:
        raise ValueError(f"class must be one of {', '.join(CLASSES)}")
    c["differences"][index]["class"] = cls
    c["summary"] = summarize(c)


# --- output -----------------------------------------------------------------

def _fmt(v) -> str:
    if v is None or v == {}:
        return "-"
    if isinstance(v, dict):
        return ", ".join(f"{k}={val}" for k, val in v.items())
    return str(v)


def comparison_markdown(c: Dict) -> str:
    a, b, s = c["a"], c["b"], c["summary"]
    lines = [f"# Comparison: {a.get('title') or '?'}", "",
             f"- **A:** {a['label']} ({a.get('created_at') or '?'})",
             f"- **B:** {b['label']} ({b.get('created_at') or '?'})",
             f"- **Steps compared:** {s['compared']} · identical {s['identical']} · changed {s['changed']} "
             f"(fix {s['fix']}, regression {s['regression']}, unclear {s['unclear']})"
             + (f" · only on A {s['only_a']}" if s["only_a"] else "")
             + (f" · only on B {s['only_b']}" if s["only_b"] else ""), ""]
    if c.get("installs"):
        lines += ["## What differs between the installs", "", "| | A | B |", "|---|---|---|"]
        lines += [f"| {d['what']} | {_fmt(d['a'])} | {_fmt(d['b'])} |" for d in c["installs"]]
        lines += [""]
    for cls, heading in ((REGRESSION, "Regressions"), (FIX, "Fixes"), (UNCLEAR, "Unclear")):
        rows = [d for d in c.get("differences") or [] if d.get("class") == cls]
        if not rows:
            continue
        lines += [f"## {heading} ({len(rows)})", ""]
        for d in rows:
            note = "" if d["class"] == d["suggested"] else f" (suggested: {d['suggested']})"
            lines.append(f"- \"{d['utterance']}\" ({d.get('lang') or '?'}), expected "
                         f"{d.get('expected') or '-'}{note}")
            for ch in d["changes"]:
                lines.append(f"  - {ch['what']}: {_fmt(ch['a'])} → {_fmt(ch['b'])}")
            for label, sd in (("A", d["a"]), ("B", d["b"])):
                for why in ((sd.get("diagnosis") or {}).get("lines") or [])[:3]:
                    lines.append(f"  - ↳ {label}: {why}")
        lines += [""]
    for key, heading in (("only_a", "Only on A"), ("only_b", "Only on B")):
        if c.get(key):
            lines += [f"## {heading}", ""]
            lines += [f"- \"{r['utterance']}\" ({r.get('lang') or '?'}): {r.get('status')}" for r in c[key]]
            lines += [""]
    return "\n".join(lines)


def comparison_json(c: Dict) -> str:
    return json.dumps(c, ensure_ascii=False, indent=2) + "\n"


def cli(a_path: str, b_path: str, output: Optional[str] = None, out=sys.stdout, err=sys.stderr) -> int:
    """`ovos-tui --compare A.report.json B.report.json [--output DIR]`.
    Prints the comparison (Markdown) and saves .md + .comparison.json.
    Exit code 1 when there are regressions, 2 when a file can't be read."""
    from ovos_tui_client.results import RESULTS_DIR
    try:
        a, b = load_report(a_path), load_report(b_path)
    except NotAReport as e:
        print(str(e), file=err)
        return 2
    c = compare(a, b, a_path, b_path)
    print(comparison_markdown(c), file=out)
    md, js = save_comparison(c, output or RESULTS_DIR)
    print(f"Saved {md} and {js.name}", file=err)
    return 1 if c["summary"][REGRESSION] else 0


def save_comparison(c: Dict, directory) -> Tuple[Path, Path]:
    directory = Path(directory).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    slug = "".join(ch if ch.isalnum() else "-" for ch in (c["a"].get("title") or "results").lower()).strip("-")[:40]
    base = f"{stamp}_compare_{slug or 'results'}"
    md, js = directory / f"{base}.md", directory / f"{base}.comparison.json"
    md.write_text(comparison_markdown(c), encoding="utf-8")
    js.write_text(comparison_json(c), encoding="utf-8")
    return md, js
