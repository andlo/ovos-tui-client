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

    # #48: why a step failed. On the OVOS machine its logs, versions and
    # intent files can be read too; over a remote bus only the bus probes.
    from ovos_tui_client.diagnose import diagnose, local_context, padatious_conf, remote_context
    from ovos_tui_client.logs import find_log_dir
    from ovos_tui_client.manifest import LOCAL_HOSTS
    local = (args.host or "").strip().lower() in LOCAL_HOSTS
    ctx = (local_context(installed, find_log_dir(is_local=True)) if local
           else remote_context(installed))

    saver = PartialRun(args, title, steps, installed, log)

    def _step_done(i, n, step, result, obs):
        saver.step_done(runner, result)
        log(f"[{i}/{n}] {_MARK.get(result.status, '?')} \"{step.utterance}\"  {result.detail}")
        for line in (result.diagnosis or {}).get("lines") or []:
            log(f"      ↳ {line}")
        for note in result.notes or []:
            log(f"      ⚠ {note}")

    runner = ScriptRunner(
        steps, title,
        send=lambda i, n, step: bus.send_utterance(
            step.utterance, session_id=runner.session_id, script={"title": title, "i": i, "n": n}),
        on_step_done=_step_done,
        diagnose=lambda step, result, obs, since: diagnose(
            step, result, obs, bus.request, ctx, since).as_dict(),
        match_conf=lambda step, obs: padatious_conf(step, obs, bus.request),
        on_stuck=lambda i, n, step, skill: log(
            f"[{i}/{n}] ⚠ {skill} is still speaking after stop; the steps run while it talks are marked "
            "'possibly affected' (Ctrl+C stops after the current step)"),
        known_skills=lambda: list(installed),
        stop_session=bus.stop_session,
        stop_all=getattr(bus, "stop_all", None),
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
        summary = runner.run(resume=saver.resume)
    finally:
        signal.signal(signal.SIGINT, previous)
    saver.finish(summary)
    log(f"{title}: {' · '.join(summary_parts(summary))}")
    if summary.halted_by:
        log(f"Stopped early: {summary.halted_by} kept talking after a stop to its session and a stop for "
            "everything. If it still talks, restart ovos-core.")
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


# --------------------------------------------------------------------
# The profile report (#62)
# --------------------------------------------------------------------

def profile_inputs(profile_path: Optional[str], fetch=None, log: Callable[[str], None] = print):
    """(default lines, extra lines, custom lines or None, sources) - the
    installer's templates live, and the --profile file."""
    from ovos_tui_client import profile_report as pr
    from ovos_tui_client.channel import fetch_text
    fetch = fetch or fetch_text
    lines, sources = {}, {}
    for pid, name in (("default", pr.DEFAULT_TEMPLATE), ("extra", pr.EXTRA_TEMPLATE)):
        url = pr.INSTALLER_TEMPLATES.format(name=name)
        text = fetch(url)
        if text is None:
            log(f"Could not fetch the OVOS installer's {name} - the {pid} profile is empty (no network?).")
        lines[pid] = pr.template_lines(text or "")
        sources[pid] = f"OpenVoiceOS/ovos-installer@main ({name})"
    custom = None
    if profile_path:
        from ovos_tui_client.setchannel import read_source
        text = read_source(profile_path, fetch)
        if text is None:
            log(f"Could not read the profile {profile_path}.")
        else:
            custom = pr.requirement_file_lines(text)
            sources["custom"] = f"{profile_path}"
    sources["default"] += " and this install's intent pipeline"
    return lines["default"], lines["extra"], custom, sources


def routing_from(summary) -> tuple:
    """({skill_id: (hit, counted)}, {skills that kept talking after stop})
    from a run of golden utterances (#62's level 3, #74's 'doesn't stop')."""
    routing, talking = {}, set()
    for _, step, result in summary.results:
        if not step.skill_id or not step.has_expectation:
            continue
        hit, counted = routing.get(step.skill_id, (0, 0))
        routing[step.skill_id] = (hit + (result.status == PASS), counted + 1)
        for note in getattr(result, "notes", None) or []:
            if note.startswith("did not stop: "):
                talking.add(note[len("did not stop: "):].split(" ", 1)[0])
    return routing, talking


def run_profile_report(args, bus_factory=OVOSBusConnection, out=sys.stdout, err=sys.stderr,
                       tool_version: str = "unknown", fetch=None) -> int:
    """`ovos-tui --profile-report [--profile FILE] [--routes]`."""
    from ovos_tui_client import profile_report as pr
    from ovos_tui_client.channel import CONSTRAINTS_URL
    from ovos_tui_client.diagnose import local_context
    from ovos_tui_client.logs import find_log_dir
    from ovos_tui_client.manifest import LOCAL_HOSTS

    def log(line: str) -> None:
        print(line, file=err, flush=True)

    if (args.host or "").strip().lower() not in LOCAL_HOSTS:
        log("The profile report reads this machine's packages: run it on the device itself.")
        return EXIT_CANNOT_RUN
    _quiet_ovos_logs()
    bus = bus_factory(host=args.host, port=args.port, lang=args.lang)
    try:
        bus.connect()
    except Exception as e:  # noqa: BLE001
        log(f"Could not connect to the OVOS messagebus at {args.host}:{args.port}: {e}")
        return EXIT_CANNOT_RUN
    installed = wait_for_skills(bus, log=log)
    if installed is None:
        log("OVOS never answered the skill list - is ovos-core running? Nothing reported.")
        _close(bus)
        return EXIT_CANNOT_RUN

    manifest = build_manifest(args.host, bus.lang, [], installed_skills=installed, channel=args.channel,
                              mycroft_conf_override=args.mycroft_conf, tool_version=tool_version)
    channel = manifest.get("channel")
    if channel not in ("stable", "testing", "alpha"):
        log(f"The release channel is unknown ({manifest.get('channel_note')}); add --channel testing "
            "(or alpha, stable). A profile report is per channel.")
        _close(bus)
        return EXIT_CANNOT_RUN

    log_dir = find_log_dir(is_local=True)
    ctx = local_context(installed, log_dir)
    left_out = pr.left_out_stages(_log_lines(log_dir))
    default, extra, custom, sources = profile_inputs(args.profile, fetch, log)
    profiles = pr.profile_members(default, extra, custom, ctx.pipeline)

    routing, talking = None, set()
    if args.routes:
        routing, talking = _route_profiles(args, bus, installed, profiles, log)

    report = pr.build_report(channel, profiles, loaded=installed, pipeline=ctx.pipeline, left_out=left_out,
                             routing=routing, keeps_talking=talking,
                             constraints_url=CONSTRAINTS_URL.format(channel=channel), sources=sources,
                             manifest=manifest, tool=f"ovos-tui-client {tool_version}")
    _close(bus)
    md = pr.report_markdown(report)
    print(md, file=out)
    output_dir = Path(args.output).expanduser() if args.output else RESULTS_DIR
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        base = output_dir / f"{time.strftime('%Y-%m-%d_%H%M%S')}_profile-report_{channel}"
        base.with_suffix(".md").write_text(md, encoding="utf-8")
        js = Path(str(base) + ".profile-report.json")
        js.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log(f"Saved {base}.md and {js.name}")
    except OSError as e:
        log(f"Could not save the report in {output_dir}: {e}")
    fails = sum(p["summary"]["fails"] for p in report["channels"][channel]["profiles"])
    return EXIT_FAILED if fails else EXIT_OK


def _log_lines(log_dir) -> List[str]:
    if not log_dir:
        return []
    try:
        return (Path(log_dir) / "skills.log").read_text(errors="replace").splitlines()[-20000:]
    except OSError:
        return []


def _route_profiles(args, bus, installed, profiles, log):
    """Level 3: every loaded skill's golden utterances, in LEVEL3_LANG."""
    from ovos_tui_client import profile_report as pr
    skills = [rid for p in profiles for rid, kind, _ in p["members"]
              if kind == "skill" and installed.get(rid) is not False and rid in installed]
    steps, sources = [], {}
    for skill_id in skills:
        _, s, _ = resolve_steps(skill_id, installed, pr.LEVEL3_LANG, args.golden_dir, log=lambda *a: None,
                                sources=sources)
        steps += [x for x in s if x.has_expectation]
    if not steps:
        log("No golden utterances found for the profiles' skills - level 3 not measured.")
        return {}, set()
    log(f"Routes: {len(steps)} golden utterances for {len(skills)} skills - this takes a while "
        "(Ctrl+C stops after the current step).")
    saver = PartialRun(args, "Profile report: routes", steps, installed, log)

    def _step_done(i, n, step, result, obs):
        saver.step_done(runner, result)
        if i % 25 == 0 or i == n:
            log(f"  {i}/{n} done")

    runner = ScriptRunner(
        steps, "Profile report: routes",
        send=lambda i, n, step: bus.send_utterance(step.utterance, session_id=runner.session_id, lang=step.lang,
                                                   script={"title": "Profile report", "i": i, "n": n}),
        on_step_done=_step_done,
        known_skills=lambda: list(installed), stop_session=bus.stop_session, stop_all=getattr(bus, "stop_all", None),
        answer=lambda session, text, lang: bus.send_utterance(text, lang=lang, session_id=session),
    )
    bus.on_message(runner.feed)
    previous = signal.getsignal(signal.SIGINT)

    def _interrupt(signum, frame):
        log("Stopping after the current step...")
        runner.stop()
        signal.signal(signal.SIGINT, previous)
    signal.signal(signal.SIGINT, _interrupt)
    try:
        summary = runner.run(resume=saver.resume)
    finally:
        signal.signal(signal.SIGINT, previous)
    saver.finish(summary)
    if summary.halted_by:
        log(f"Routes stopped early, after {len(summary.results)}/{len(steps)} steps: {summary.halted_by} kept "
            "talking after stop. Level 3 is from the steps run so far. If it still talks, restart ovos-core.")
    routing, talking = routing_from(summary)
    if summary.halted_by:
        talking.add(summary.halted_by)
    return routing, talking


class PartialRun:
    """Autosave for a headless run, and --resume (see partial.py)."""

    def __init__(self, args, title: str, steps, installed: Dict, log: Callable[[str], None]):
        from ovos_tui_client import partial
        self._p = partial
        self.log = log
        self.dir = Path(args.output).expanduser() if args.output else RESULTS_DIR
        self.fp = partial.fingerprint(title, steps)
        self.manifest = {}
        try:
            self.manifest = build_manifest(args.host, args.lang, [], installed_skills=installed,
                                           channel=args.channel)
        except Exception:  # noqa: BLE001 - only what the partial file says it ran against
            pass
        self.resume = None
        found = partial.find(self.dir, self.fp)
        if found and getattr(args, "resume", False):
            self.resume = partial.load(found["path"], title, steps)
            if self.resume is None:
                log("The saved part of this run doesn't fit its steps any more; starting over.")
            else:
                log(f"Resuming: {found['done']}/{found['total']} steps from the saved run "
                    f"({found['path'].name}), going on from step {found['done'] + 1}.")
        elif found:
            log(f"A run of these steps was cut short at {found['done']}/{found['total']} "
                f"({found['path'].name}); add --resume to go on from step {found['done'] + 1}. "
                "Starting over.")

    def step_done(self, runner, result) -> None:
        so_far = runner.summary
        if so_far is not None and (len(so_far.results) % self._p.SAVE_EVERY == 0 or result.notes):
            self._p.save(so_far, self.manifest, self.dir, self.fp)

    def finish(self, summary) -> None:
        if len(summary.results) < summary.total:
            if self._p.save(summary, self.manifest, self.dir, self.fp):
                self.log(f"Saved the run so far ({len(summary.results)}/{summary.total}): run it again with "
                         f"--resume to go on from step {len(summary.results) + 1}.")
        else:
            self._p.remove(self.dir, self.fp)
