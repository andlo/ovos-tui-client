"""Saving the result of a test run ('Test: Save result', and every headless run).

Two files per run, side by side in RESULTS_DIR:

* ``<date>_<time>_<title>.md`` - a readable report: when, where, the
  summary line, the failures, and every step with what handled it and
  what OVOS said. For you, or to paste into an issue.
* ``<date>_<time>_<title>.report.json`` - the shareable report
  (report.py, ``ovos-test-report/1``): what was tested against (channel,
  versions, machine) and one row per step. Also what to compare two runs
  with (stable vs alpha, before vs after a fix).
"""
import re
import time
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from ovos_tui_client.scripts import PASS, FAIL, TIMEOUT, SENT, RunSummary

RESULTS_DIR = Path("~/.local/share/ovos-tui-client/results").expanduser()

_MARK = {PASS: "✓", FAIL: "✗", TIMEOUT: "⏱", SENT: "→"}


def summary_parts(summary: RunSummary) -> list:
    """The parts of the one-line summary ('40/42 passed · 2 failed · 125s')."""
    passed, failed = summary.count(PASS), summary.count(FAIL)
    timeouts, sent = summary.count(TIMEOUT), summary.count(SENT)
    done = len(summary.results)
    parts = []
    checked = done - sent
    if checked:
        parts.append(f"{passed}/{checked} passed")
    if failed:
        parts.append(f"{failed} failed")
    if timeouts:
        parts.append(f"{timeouts} timed out")
    if sent:
        parts.append(f"{sent} sent without a check")
    if summary.cancelled:
        parts.append(f"stopped after {done}/{summary.total}")
    parts.append(f"{summary.duration:.0f}s")
    return parts


def _slug(text: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:60] or "test"


def result_basename(summary: RunSummary) -> str:
    when = time.localtime(summary.started_at or time.time())
    return f"{time.strftime('%Y-%m-%d_%H%M%S', when)}_{_slug(summary.title)}"


def _cell(text) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ").strip()


def report_markdown(summary: RunSummary, meta: Dict[str, str],
                    versions: Optional[Dict[str, Optional[str]]] = None) -> str:
    """meta: e.g. {'OVOS': 'host:port', 'Language': 'en-us',
    'ovos-tui-client': '0.2.0a7'}. versions: skill_id -> installed version."""
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(summary.started_at or time.time()))
    state = "stopped" if summary.cancelled else "finished"
    lines = [f"# {summary.title}", "",
             f"- **Result:** {state}: {' · '.join(summary_parts(summary))}",
             f"- **Started:** {when}"]
    lines += [f"- **{k}:** {v}" for k, v in meta.items() if v]
    lines.append("")

    failures = summary.failures
    if failures:
        lines += ["## Failures", ""]
        for i, step, result in failures:
            lines.append(f"- {_MARK[result.status]} [{i}] \"{step.utterance}\" → {result.detail}")
            for d in (getattr(result, "diagnosis", None) or {}).get("lines") or []:
                lines.append(f"  - ↳ {d}")
        lines.append("")

    lines += ["## All steps", "",
              "| # | | Utterance | Expected | Handled by | OVOS said |",
              "|---|---|---|---|---|---|"]
    for i, step, result in summary.results:
        expected = step.expected_intent or step.skill_id or ""
        handled = summary.handled_by.get(i, "")
        if result.status in (FAIL, TIMEOUT):
            handled = result.detail
        said = " / ".join(summary.replies.get(i, []))
        if len(said) > 200:
            said = said[:197] + "..."
        lines.append(f"| {i} | {_MARK.get(result.status, '')} | {_cell(step.utterance)} | "
                     f"{_cell(expected)} | {_cell(handled)} | {_cell(said)} |")
    lines.append("")

    skills = sorted({step.skill_id for _, step, _ in summary.results if step.skill_id})
    if skills:
        lines += ["## Skills tested", ""]
        for skill_id in skills:
            version = (versions or {}).get(skill_id)
            lines.append(f"- {skill_id}" + (f" {version}" if version else ""))
        lines.append("")
    return "\n".join(lines)


def markdown_meta(manifest: Dict) -> Tuple[Dict[str, str], Dict[str, Optional[str]]]:
    """The .md header lines and skill versions, from a report's manifest."""
    channel = manifest.get("channel") or "unknown"
    if manifest.get("channel") and manifest.get("channel_source"):
        channel += f" ({manifest['channel_source']})"
    meta = {"OVOS": manifest.get("bus"), "Channel": channel, "Language": manifest.get("lang"),
            "Tool": manifest.get("tool")}
    versions = {sid: (info or {}).get("version") for sid, info in (manifest.get("skills") or {}).items()}
    return meta, versions


def save_result(summary: RunSummary, meta: Dict[str, str],
                versions: Optional[Dict[str, Optional[str]]] = None,
                directory: Path = RESULTS_DIR, report_text: Optional[str] = None) -> Tuple[Path, Optional[Path]]:
    """Writes the .md and (given report_text) the .report.json; returns both paths."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    base = result_basename(summary)
    md, rep = directory / f"{base}.md", directory / f"{base}.report.json"
    n = 2
    while md.exists() or rep.exists():
        md, rep = directory / f"{base}-{n}.md", directory / f"{base}-{n}.report.json"
        n += 1
    md.write_text(report_markdown(summary, meta, versions), encoding="utf-8")
    if report_text is None:
        return md, None
    rep.write_text(report_text, encoding="utf-8")
    return md, rep
