"""The shareable test report (#51): one JSON document, `ovos-test-report/1`.

Every saved result is two files (results.py): a readable .md, and this
report as .report.json. The report is the file meant to be handed to
someone else: a skill store, an issue, a maintainer. It is deliberately
not tied to any store. ovos-tui-client writes it to a file or prints it
for copy-paste, and a store decides for itself what it accepts.

Contents: the manifest (manifest.py: channel, versions, routing config,
machine type), a summary, and one row per step (what was said, what was
expected, what handled it, whether OVOS answered).

Private by default:
* no hostname, IP address, user name or home path (the manifest has none);
* OVOS's replies are NOT included, only whether it answered. Replies can
  hold personal data ("it's 14 degrees in <your town>"). --report-replies
  adds them for a report you only keep yourself.
"""
import json
import urllib.parse
from typing import Dict, Optional

from ovos_tui_client.scripts import FAIL, PASS, SENT, TIMEOUT, RunSummary

SCHEMA = "ovos-test-report/1"

# Browsers and servers commonly cut URLs around 8 KB.
MAX_SUBMIT_URL = 8000


def build_report(summary: RunSummary, manifest: Dict, include_replies: bool = False,
                 notes: Optional[str] = None) -> Dict:
    steps = []
    for i, step, result in summary.results:
        expected = step.skill_id or None
        if step.skill_id and step.intent_label:
            expected = f"{step.skill_id}:{step.intent_label}"
        replies = summary.replies.get(i, [])
        row = {
            "i": i,
            "utterance": step.utterance,
            "lang": step.lang,
            "expected": expected,
            "status": result.status,
            "handled_by": summary.handled_by.get(i) or None,
            "answered": bool(replies),
        }
        if result.status in (FAIL, TIMEOUT):
            row["detail"] = result.detail
        if include_replies:
            row["replies"] = replies
        steps.append(row)

    checked = len(summary.results) - summary.count(SENT)
    report = {
        "schema": SCHEMA,
        "title": summary.title,
        "manifest": manifest,
        "summary": {
            "steps": len(summary.results),
            "planned": summary.total,
            "checked": checked,
            "passed": summary.count(PASS),
            "failed": summary.count(FAIL),
            "timed_out": summary.count(TIMEOUT),
            "sent_without_check": summary.count(SENT),
            "answered": sum(1 for s in steps if s["answered"]),
            "cancelled": summary.cancelled,
            "duration_s": round(summary.duration, 1),
        },
        "steps": steps,
    }
    if notes:
        report["notes"] = notes.strip()[:1000]
    return report


def report_json(report: Dict, compact: bool = False) -> str:
    if compact:
        return json.dumps(report, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(report, ensure_ascii=False, indent=2) + "\n"


def submit_url(template: str, report: Dict) -> Optional[str]:
    """Fills a store-provided link template, or None if the result would be
    too long for a URL (then the report has to be pasted by hand).

    Placeholders (each URL-encoded): {report} the compact report JSON,
    {skill_id} the tested skill when there is exactly one, {channel},
    {title}. ovos-tui-client knows no store: the template comes from the
    user (--submit-url, or "submit_url" in the config file), typically
    copied from the store's own instructions."""
    skills = list((report.get("manifest") or {}).get("skills") or {})
    values = {
        "report": report_json(report, compact=True),
        "skill_id": skills[0] if len(skills) == 1 else "",
        "channel": (report.get("manifest") or {}).get("channel") or "",
        "title": report.get("title") or "",
    }
    url = template
    for key, value in values.items():
        url = url.replace("{" + key + "}", urllib.parse.quote(value, safe=""))
    return url if len(url) <= MAX_SUBMIT_URL else None
