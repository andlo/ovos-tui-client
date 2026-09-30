"""Headless test runs (#51): `ovos-tui --run ...` without the terminal UI.

For cron, a systemd timer, CI, or a one-off check on a device:

    ovos-tui --run all                          # every installed skill with golden utterances
    ovos-tui --run ovos-skill-weather.openvoiceos
    ovos-tui --run ~/.config/ovos-tui-client/scripts/smoke.jsonl
             [--output DIR] [--report FILE|-] [--channel NAME] [--notes TEXT]
             [--submit-url TEMPLATE]

It does exactly what the Test/Script palette entries do: waits for the
skill list (with the same retries as the UI, #50), looks up golden
utterances, and runs every step in its own session through the same
ScriptRunner, stopping each step's session afterwards. Only the
presentation differs: one plain line per step instead of the screen.

Output:
* DIR/<date>_<title>.md and .jsonl, the same files as 'Test: Save last
  result' (#47), plus <...>.manifest.json (what was tested against);
* with --report, one shareable `ovos-test-report/1` JSON document
  (report.py) to a file, or to stdout with `-` for copy-paste. Progress
  lines then go to stderr so stdout holds only the report.

Exit code: 0 all checked steps passed, 1 something failed or timed out,
2 could not run (no bus, no skill list, nothing to test).

ovos-tui-client knows no skill store. A store that accepts these reports
can publish a link template for --submit-url; see report.submit_url().
"""
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from ovos_tui_client.bus import OVOSBusConnection
from ovos_tui_client.manifest import build_manifest
from ovos_tui_client.report import build_report, report_json, submit_url
from ovos_tui_client.results import RESULTS_DIR, save_result, summary_parts
from ovos_tui_client.scripts import (FAIL, PASS, SENT, TIMEOUT, ScriptRunner, expand_includes,
                                     load_golden, parse_script)

EXIT_OK, EXIT_FAILED, EXIT_CANNOT_RUN = 0, 1, 2

# Same patience as the UI's automatic retries (#50), but bounded: a
# scheduled run must end even when OVOS never answers.
SKILL_LIST_RETRY_DELAYS = (5, 10, 20, 30, 30)

CONFIG_FILE = Path("~/.config/ovos-tui-client/config.json").expanduser()

_MARK = {PASS: "✓", FAIL: "✗", TIMEOUT: "⏱", SENT: "→"}


def load_config(path: Path = CONFIG_FILE) -> Dict:
    """Optional settings for headless runs, e.g. {"submit_url": "..."}."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def wait_for_skills(bus, delays=None, sleep=time.sleep,
                    log: Callable[[str], None] = print) -> Optional[Dict]:
    """The installed skills (skill_id -> active), or None if OVOS never answers."""
    delays = SKILL_LIST_RETRY_DELAYS if delays is None else delays
    for attempt in range(len(delays) + 1):
        done = threading.Event()
        box = {}

        def _on_result(skills, box=box, done=done):
            box["skills"] = skills
            done.set()

        bus.list_skills(_on_result)
        done.wait(15)
        if box.get("skills") is not None:
            return box["skills"]
        if attempt < len(delays):
            log(f"Skill list: no answer from OVOS yet, asking again in {delays[attempt]} s")
            sleep(delays[attempt])
    return None


def resolve_steps(target: str, installed: Dict, lang: str, golden_dirs,
                  log: Callable[[str], None] = print, loader=None):
    """(title, steps, tested skill ids) for --run's target, or (title, [], ...)."""
    loader = loader or load_golden

    def golden(skill_id):
        result = loader(skill_id, lang, golden_dirs=golden_dirs)
        if result.steps:
            log(f"{skill_id}: {len(result.steps)} step(s) from {result.source}")
        return result.steps

    path = Path(target).expanduser()
    if target == "all":
        skill_ids = sorted(s for s, active in installed.items() if active is not False)
        steps = []
        for skill_id in skill_ids:
            steps.extend(golden(skill_id))
        return "Test: All installed skills", steps, sorted({s.skill_id for s in steps if s.skill_id})
    if path.suffix in (".jsonl", ".txt") and path.exists():
        items = parse_script(path.read_text(encoding="utf-8"), lang=lang)
        steps = expand_includes(items, golden)
        return f"Script: {path.stem}", steps, sorted({s.skill_id for s in steps if s.skill_id})
    if target not in installed:
        log(f"{target}: not an installed skill, and not a .jsonl/.txt script file")
        return f"Test: {target}", [], [target]
    return f"Test: {target}", golden(target), [target]


def _quiet_ovos_logs() -> None:
    """OVOS logs INFO to stdout; in a headless run that buries the ✓/✗ lines."""
    try:
        from ovos_utils.log import LOG
        LOG.set_level("WARNING")
    except Exception:  # noqa: BLE001
        pass


def _stdout_to_stderr():
    """For `--report -`: stdout must hold ONLY the report, but OVOS and its
    libraries log to stdout (a StreamHandler bound to sys.stdout, some
    created at import time). Point file descriptor 1 at stderr for the
    whole run, so every such write lands on stderr, and hand back a stream
    on the original stdout for the report itself."""
    sys.stdout.flush()
    saved = os.dup(1)
    os.dup2(2, 1)
    return os.fdopen(saved, "w", encoding="utf-8")


def run_headless(args, bus_factory=OVOSBusConnection, out=sys.stdout, err=sys.stderr,
                 config: Optional[Dict] = None, tool_version: str = "unknown") -> int:
    if args.report == "-" and out is sys.stdout:
        out = _stdout_to_stderr()
    _quiet_ovos_logs()
    # With the report on stdout, everything else goes to stderr.
    progress_stream = err if args.report == "-" else out

    def log(line: str) -> None:
        print(line, file=progress_stream, flush=True)

    config = load_config() if config is None else config
    bus = bus_factory(host=args.host, port=args.port, lang=args.lang)
    try:
        bus.connect()
    except Exception as e:  # noqa: BLE001
        log(f"Could not connect to the OVOS messagebus at {args.host}:{args.port}: {e}")
        return EXIT_CANNOT_RUN

    installed = wait_for_skills(bus, log=log)
    if installed is None:
        log("OVOS never answered the skill list - is ovos-core running? Nothing tested.")
        _close(bus)
        return EXIT_CANNOT_RUN
    log(f"Skills found: {len(installed)}")

    title, steps, tested = resolve_steps(args.run, installed, bus.lang, args.golden_dir, log=log)
    if not steps:
        log(f"{title}: nothing to test for {bus.lang} (no golden utterances or skill.json examples)")
        _close(bus)
        return EXIT_CANNOT_RUN

    runner = ScriptRunner(
        steps, title,
        send=lambda i, n, step: bus.send_utterance(
            step.utterance, session_id=runner.session_id, script={"title": title, "i": i, "n": n}),
        on_step_done=lambda i, n, step, result, obs: log(
            f"[{i}/{n}] {_MARK.get(result.status, '?')} \"{step.utterance}\"  {result.detail}"),
        known_skills=lambda: list(installed),
        stop_session=bus.stop_session,
        on_busy=lambda i, n, step, wait: log(
            f"[{i}/{n}] ⏳ no response yet; waiting up to {wait / 60:.0f} min for OVOS to finish it "
            "before the next step (OVOS handles one sentence at a time)"),
    )
    bus.on_message(runner.feed)

    previous = signal.getsignal(signal.SIGINT)

    def _interrupt(signum, frame):
        log("Stopping after the current step (Ctrl+C again to abort at once)...")
        runner.stop()
        signal.signal(signal.SIGINT, previous)

    signal.signal(signal.SIGINT, _interrupt)
    log(f"{title}: {len(steps)} step(s)")
    try:
        summary = runner.run()
    finally:
        signal.signal(signal.SIGINT, previous)
    log(f"{title}: {' · '.join(summary_parts(summary))}")

    manifest = build_manifest(args.host, bus.lang, tested, installed_skills=installed,
                              channel=args.channel, mycroft_conf_override=args.mycroft_conf,
                              tool_version=tool_version)
    if manifest["bus"] == "remote":
        log("Note: OVOS is on another machine, so its package versions and channel could not be "
            "read. Run on the device itself for a complete report.")
    elif not manifest.get("channel"):
        log(f"Note: the release channel is unknown ({manifest.get('channel_note')}). "
            "Add --channel testing (or alpha, stable) to record it.")
    else:
        log(f"Channel: {manifest['channel']} ({manifest.get('channel_source')})")

    meta = {"OVOS": "local" if manifest["bus"] == "local" else "remote",
            "Channel": manifest.get("channel") or "unknown", "Language": bus.lang,
            "ovos-tui-client": tool_version}
    versions = {sid: info.get("version") for sid, info in manifest["skills"].items()}
    output_dir = Path(args.output).expanduser() if args.output else RESULTS_DIR
    try:
        md, jsonl = save_result(summary, meta, versions, output_dir)
        manifest_path = md.with_suffix(".manifest.json")
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        log(f"Saved {md}, {jsonl.name} and {manifest_path.name}")
    except OSError as e:
        log(f"Could not save the result in {output_dir}: {e}")

    if args.report:
        report = build_report(summary, manifest, include_replies=args.report_replies, notes=args.notes)
        text = report_json(report)
        if args.report == "-":
            out.write(text)
            out.flush()
        else:
            path = Path(args.report).expanduser()
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
                log(f"Report written to {path}")
            except OSError as e:
                log(f"Could not write the report to {path}: {e}")
        template = args.submit_url or config.get("submit_url")
        if template:
            link = submit_url(template, report)
            log(f"Submit it here: {link}" if link else
                "The report is too long for a link - open the store's page and paste the report instead.")

    _close(bus)
    if summary.cancelled or summary.count(FAIL) or summary.count(TIMEOUT):
        return EXIT_FAILED
    return EXIT_OK


def _close(bus) -> None:
    client = getattr(bus, "_client", None)
    try:
        if client is not None and hasattr(client, "close"):
            client.close()
    except Exception:  # noqa: BLE001
        pass
