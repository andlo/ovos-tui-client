"""Autosave a run while it runs, and take a cut-short run up again.

A long run (every golden utterance of a profile: hours on a Mark II) can
end before it's done: the TUI closed, the device restarted, a skill that
wouldn't stop. While a run runs, what it has measured is saved every
SAVE_EVERY steps (and when it stops) as

    partial_<fingerprint>.report.json   the report so far (ovos-test-report/1,
                                        with a "partial" block)
    partial_<fingerprint>.md            the same, readable

next to the results. The fingerprint is a hash of the run's steps (what is
said, in which language, what is expected) and its title: starting the
same run again finds it and can go on from the next step. A partial run
of other steps is never resumed - that would mix two runs. A finished run
removes its partial files.
"""
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

from ovos_tui_client.report import build_report, report_json
from ovos_tui_client.results import report_markdown
from ovos_tui_client.scripts import RunSummary, StepResult

SAVE_EVERY = 10


def fingerprint(title: str, steps) -> str:
    """12 hex characters for these steps in this order."""
    data = [title] + [[s.utterance, (s.lang or "").lower(), s.skill_id or "", s.intent_label or ""] for s in steps]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()[:12]


def paths(directory, fp: str):
    d = Path(directory).expanduser()
    return d / f"partial_{fp}.report.json", d / f"partial_{fp}.md"


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)   # never a half-written file, even if the device dies mid-save


def save(summary: RunSummary, manifest: Dict, directory, fp: str, meta: Optional[Dict] = None) -> Optional[Path]:
    """Writes the run so far. Returns the JSON path, or None if it can't."""
    try:
        js, md = paths(directory, fp)
        js.parent.mkdir(parents=True, exist_ok=True)
        report = build_report(summary, manifest)
        report["partial"] = {"fingerprint": fp, "done": len(summary.results), "total": summary.total,
                             "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                             "started_at": summary.started_at, "resumed_at": list(summary.resumed_at)}
        _write(js, report_json(report))
        head = (f"> **Partial run:** {len(summary.results)}/{summary.total} steps, saved "
                f"{time.strftime('%Y-%m-%d %H:%M')}. Start the same run again to go on from step "
                f"{len(summary.results) + 1}.\n\n")
        _write(md, head + report_markdown(summary, meta or {}))
        return js
    except OSError:
        return None


def find(directory, fp: str) -> Optional[Dict]:
    """{'path', 'done', 'total', 'updated_at'} for a partial run of these steps, or None."""
    js, _ = paths(directory, fp)
    try:
        data = json.loads(js.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    p = data.get("partial") or {}
    if p.get("fingerprint") != fp or not p.get("done"):
        return None
    return {"path": js, "done": p["done"], "total": p.get("total"), "updated_at": p.get("updated_at")}


def load(path, title: str, steps: List) -> Optional[RunSummary]:
    """The saved run as a RunSummary to resume, or None if it doesn't fit
    these steps (each saved row must be the same sentence at that place)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    p = data.get("partial") or {}
    if p.get("fingerprint") != fingerprint(title, steps):
        return None
    summary = RunSummary(title=title, total=len(steps), started_at=p.get("started_at") or time.time())
    summary.resumed_at = list(p.get("resumed_at") or [])
    summary.duration = float((data.get("summary") or {}).get("duration_s") or 0)
    for row in data.get("steps") or []:
        i = int(row.get("i") or 0)
        if not 1 <= i <= len(steps) or row.get("utterance") != steps[i - 1].utterance:
            return None
        summary.results.append((i, steps[i - 1], StepResult(
            row.get("status", "fail"), row.get("detail") or "", diagnosis=row.get("diagnosis"),
            notes=list(row.get("notes") or []))))
        if row.get("handled_by"):
            summary.handled_by[i] = row["handled_by"]
        if row.get("match"):
            summary.matches[i] = row["match"]
    return summary


def remove(directory, fp: str) -> None:
    for path in paths(directory, fp):
        try:
            path.unlink()
        except OSError:
            pass
