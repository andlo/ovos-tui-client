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

Output: the same two files as 'Test: Save result' in the TUI -
DIR/<date>_<title>.md (readable) and .report.json (the shareable
`ovos-test-report/1`, report.py). --report also puts a copy of the report
in FILE, or on stdout with `-` for copy-paste; progress lines then go to
stderr so stdout holds only the report.

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
from ovos_tui_client.share import ASK_SUBMIT_URL_TEXT, SHARE_TTL, ReportShare, scp_hint
from ovos_tui_client.results import RESULTS_DIR, markdown_meta, save_result, summary_parts
from ovos_tui_client.scripts import (FAIL, PASS, SENT, TIMEOUT, ScriptRunner, expand_includes,
                                     load_golden, parse_script)

EXIT_OK, EXIT_FAILED, EXIT_CANNOT_RUN = 0, 1, 2

# Same patience as the UI's automatic retries (#50), but bounded: a
# scheduled run must end even when OVOS never answers.
SKILL_LIST_RETRY_DELAYS = (5, 10, 20, 30, 30)
# A core that was just (re)started answers the skill list while it is
# still loading skills (seen live: 36 of 60) - ask again until the count
# holds still, so a run doesn't start before the skill it tests is there.
SKILL_SETTLE_INTERVAL = 5.0
SKILL_SETTLE_CHECKS = 2      # the same count this many times in a row
SKILL_SETTLE_MAX = 180.0

CONFIG_FILE = Path("~/.config/ovos-tui-client/config.json").expanduser()

_MARK = {PASS: "✓", FAIL: "✗", TIMEOUT: "⏱", SENT: "→"}


def save_config(values: Dict, path: Path = CONFIG_FILE) -> None:
    """Merges values into the config file (best effort)."""
    data = load_config(path)
    data.update(values)
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def ask_submit_url(log: Callable[[str], None], ask: Callable[[str], str] = input,
                   path: Path = CONFIG_FILE) -> Optional[str]:
    """Asks (from a terminal) for a store's report link template and keeps
    it in the config file. An empty answer isn't kept: it's asked again next
    time (set it any time with --submit-url, or in the TUI: Ctrl+P ->
    'Settings: Skill store report link')."""
    log("")
    for line in ASK_SUBMIT_URL_TEXT.splitlines():
        log(line)
    log("Press Enter to skip for now (asked again next time; or set it with --submit-url, "
        "or in the TUI: Ctrl+P → 'Settings: Skill store report link').")
    try:
        answer = ask("Store report link: ").strip()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer and "{report" not in answer:
        log("That link has no {report_fragment} or {report} in it, so it can't carry the report - not saved.")
        answer = ""
    if answer:
        save_config({"submit_url": answer}, path)
        log(f"Saved in {path}.")
    return answer or None


def share_report(text: str, title: str, path, store_link: Optional[str], log: Callable[[str], None],
                 wait: Optional[Callable[[str], str]] = input, ttl: float = SHARE_TTL,
                 share_cls=None) -> None:
    """Puts the report on a short temporary link and says how to get it."""
    share_cls = share_cls or ReportShare
    share = share_cls(text, title=title, store_link=store_link, ttl=ttl)
    try:
        url = share.start()
    except OSError as e:
        log(f"Could not serve the report on a link ({e}).")
        url = None
    if url:
        log("")
        log(f"Open the report in your browser (Ctrl+click): {url}")
        log("  " + ("Copy, Download, and 'Open detailed page' (the store's page with the report filled in)." if store_link
                    else "Copy and Download there.") + f" The link works for {ttl / 60:.0f} min.")
    if path:
        log(f"Or fetch the file: {scp_hint(path)}")
    if url:
        if wait is None:
            try:
                threading.Event().wait(ttl)
            except KeyboardInterrupt:
                pass
        else:
            try:
                wait("Press Enter when you're done with the link... ")
            except (EOFError, KeyboardInterrupt):
                pass
        share.stop()


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
            return _settle(bus, box["skills"], sleep, log)
        if attempt < len(delays):
            log(f"Skill list: no answer from OVOS yet, asking again in {delays[attempt]} s")
            sleep(delays[attempt])
    return None


def _ask_skills(bus, timeout=15) -> Optional[Dict]:
    done, box = threading.Event(), {}

    def _on_result(skills):
        box["skills"] = skills
        done.set()

    bus.list_skills(_on_result)
    done.wait(timeout)
    return box.get("skills")


def _settle(bus, skills: Dict, sleep, log) -> Dict:
    """Ask again until the number of loaded skills stops changing."""
    same, waited = 0, 0.0
    while same < SKILL_SETTLE_CHECKS and waited < SKILL_SETTLE_MAX:
        sleep(SKILL_SETTLE_INTERVAL)
        waited += SKILL_SETTLE_INTERVAL
        again = _ask_skills(bus)
        if again is None:
            continue
        if len(again) == len(skills):
            same += 1
        else:
            log(f"Skills still loading ({len(skills)} -> {len(again)}), waiting...")
            same = 0
        skills = again
    return skills


def resolve_steps(target: str, installed: Dict, lang: str, golden_dirs,
                  log: Callable[[str], None] = print, loader=None,
                  sources: Optional[Dict[str, str]] = None):
    """(title, steps, tested skill ids) for --run's target, or (title, [], ...).
    When given, `sources` is filled with where each skill's steps came from."""
    loader = loader or load_golden

    def golden(skill_id):
        result = loader(skill_id, lang, golden_dirs=golden_dirs)
        if result.steps:
            log(f"{skill_id}: {len(result.steps)} step(s) from {result.source}")
            if sources is not None and result.source:
                sources[skill_id] = result.source
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

    sources: Dict[str, str] = {}
    title, steps, tested = resolve_steps(args.run, installed, bus.lang, args.golden_dir, log=log,
                                         sources=sources)
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
        answer=lambda session, text, lang: bus.send_utterance(text, lang=lang, session_id=session),
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
    if runner.released_responses:
        log(f"Answered \"cancel\" to {runner.released_responses} question(s) a skill was left waiting on "
            "(get_response), so its handler thread was freed.")

    manifest = build_manifest(args.host, bus.lang, tested, installed_skills=installed,
                              channel=args.channel, mycroft_conf_override=args.mycroft_conf,
                              tool_version=tool_version)
    note_steps_sources(manifest, sources, log)
    if manifest["bus"] == "remote":
        log("Note: OVOS is on another machine, so its package versions and channel could not be "
            "read. Run on the device itself for a complete report.")
    elif not manifest.get("channel"):
        log(f"Note: the release channel is unknown ({manifest.get('channel_note')}). "
            "Add --channel testing (or alpha, stable) to record it.")
    else:
        log(f"Channel: {manifest['channel']} ({manifest.get('channel_source')})")

    report = build_report(summary, manifest, include_replies=args.report_replies, notes=args.notes)
    text = report_json(report)
    meta, versions = markdown_meta(manifest)
    output_dir = Path(args.output).expanduser() if args.output else RESULTS_DIR
    saved = None
    try:
        md, rep = save_result(summary, meta, versions, output_dir, report_text=text)
        saved = rep
        log(f"Saved {md} and {rep.name}")
    except OSError as e:
        log(f"Could not save the result in {output_dir}: {e}")

    if args.report:
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
    interactive = _interactive(out if args.report != "-" else err)
    template = args.submit_url or config.get("submit_url")
    if not template and interactive and not getattr(args, "no_share", False):
        template = ask_submit_url(log)
    link = submit_url(template, report) if template else None
    if template and not link:
        log("The report is too long for the store's link - open the store's page and paste or upload it instead.")
    if not getattr(args, "no_share", False) and (interactive or getattr(args, "share", False)):
        share_report(text, title, saved, link, log, wait=input if interactive else None)
    else:
        if link:
            log(f"Submit it here: {link}")
        if saved:
            log(f"Fetch the file with: {scp_hint(saved)}")

    _close(bus)
    if summary.cancelled or summary.count(FAIL) or summary.count(TIMEOUT):
        return EXIT_FAILED
    return EXIT_OK


def note_steps_sources(manifest: Dict, sources: Dict[str, str], log: Callable[[str], None] = print) -> None:
    """Record where each skill's test steps came from (manifest skills[id].steps_from).
    A golden file fetched from the repo's default branch (HEAD) can be newer
    than the installed release - intent names or sentences may have changed
    since - so say so, with the installed version, instead of letting those
    rows read as the skill's fault."""
    skills = manifest.setdefault("skills", {})
    newer = []
    for skill_id, source in sorted(sources.items()):
        info = skills.setdefault(skill_id, {"package": None, "version": None, "active": None})
        info["steps_from"] = source
        if "/HEAD/" in source:
            newer.append(f"{skill_id} {info.get('version') or '(version unknown)'}")
    if newer:
        manifest["steps_note"] = ("Golden files from the repos' default branch (HEAD) may be newer "
                                  "than the installed release: " + ", ".join(newer))
        log(f"Note: {len(newer)} skill(s) were tested with golden files from the repo's default "
            "branch, which may be newer than the installed release (see steps_from in the manifest).")


def _interactive(stream) -> bool:
    """Run by a person in a terminal (not cron/CI/a pipe)."""
    try:
        return sys.stdin.isatty() and stream.isatty()
    except (AttributeError, ValueError):
        return False


def _close(bus) -> None:
    client = getattr(bus, "_client", None)
    try:
        if client is not None and hasattr(client, "close"):
            client.close()
    except Exception:  # noqa: BLE001
        pass
