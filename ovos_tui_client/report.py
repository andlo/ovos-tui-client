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
import base64
import gzip
import json
import urllib.parse
from typing import Dict, Optional

from ovos_tui_client.scripts import FAIL, PASS, SENT, TIMEOUT, RunSummary

SCHEMA = "ovos-test-report/1"

# Browsers and servers commonly cut URLs around 8 KB.
MAX_SUBMIT_URL = 8000
# ...but the part after '#' never goes to a server; browsers take MBs there.
MAX_FRAGMENT_URL = 1_000_000


def report_fragment(report: Dict) -> str:
    """The report packed for a URL fragment: base64url(gzip(compact JSON)),
    without padding. A store's page unpacks it in the browser (e.g. with
    DecompressionStream("gzip")); ~2-5 KB for a one-skill report."""
    raw = report_json(report, compact=True).encode("utf-8")
    return base64.urlsafe_b64encode(gzip.compress(raw, 9)).decode("ascii").rstrip("=")


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
        match = summary.matches.get(i)
        if match and (match.get("stage") or match.get("slots") or match.get("conf") is not None):
            # #49: the pipeline plugin, the slots and padatious' score, for
            # comparing two installs (slot values come from the utterance)
            row["match"] = {k: v for k, v in match.items() if v not in (None, {}, "")}
        if result.status in (FAIL, TIMEOUT):
            row["detail"] = result.detail
            if getattr(result, "diagnosis", None):
                # #48: category, the lines shown under the red line, a known
                # cause, and the probes' answers (intent names and scores
                # only - nothing OVOS said)
                row["diagnosis"] = result.diagnosis
        if getattr(result, "notes", None):
            # #74: e.g. a skill still speaking after stop, and the steps
            # that ran while it did
            row["notes"] = list(result.notes)
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
    {report_fragment} the report packed for a '#' fragment (see
    report_fragment(); much shorter, and never sent to a server),
    {skill_id} the tested skill when there is exactly one, {channel},
    {title}. ovos-tui-client knows no store: the template comes from the
    user (--submit-url, or "submit_url" in the config file), typically
    copied from the store's own instructions."""
    skills = list((report.get("manifest") or {}).get("skills") or {})
    values = {
        "report": report_json(report, compact=True) if "{report}" in template else "",
        "report_fragment": report_fragment(report) if "{report_fragment}" in template else "",
        "skill_id": skills[0] if len(skills) == 1 else "",
        "channel": (report.get("manifest") or {}).get("channel") or "",
        "title": report.get("title") or "",
    }
    url = template
    for key, value in values.items():
        url = url.replace("{" + key + "}", urllib.parse.quote(value, safe=""))
    if "{report}" in template:
        return url if len(url) <= MAX_SUBMIT_URL else None
    return url if len(url) <= MAX_FRAGMENT_URL else None
