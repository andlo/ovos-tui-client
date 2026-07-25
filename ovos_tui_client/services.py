"""Discovers and restarts OVOS's systemd --user services. Deliberately
scoped to user-level systemd (matching this project's assumption that
OVOS runs under a per-user venv install, see logs.py's module
docstring for the same reasoning) - not sudo/system-level services.

Like logs.py, this doesn't hardcode a fixed service-name list: service
names vary by install (we found 'ovos-core' handles skills, not
'ovos-skills', on a real system earlier in this project) - so services
are discovered by querying systemd directly for anything matching
'ovos-*', rather than guessed at.
"""
import subprocess


def discover_services():
    """Returns a sorted list of unit names (e.g. 'ovos-core.service')
    for every loaded systemd --user unit matching 'ovos-*'. Returns []
    on any failure (systemctl not found, no user session, etc) rather
    than raising - callers should treat that as 'nothing to show'.

    Kept as-is (name-only) for backward compatibility with existing
    callers/tests - see discover_services_with_state() below for the
    richer version that also reports whether each unit is running."""
    return [name for name, _ in discover_services_with_state()]


def discover_services_with_state():
    """Like discover_services(), but returns (unit_name, is_active)
    tuples - `systemctl --user list-units` already reports this in its
    3rd column (ACTIVE: active/inactive/failed/etc), which
    discover_services() was previously discarding. Added so the
    Command Palette can offer only the actions that make sense for a
    unit's current state (no point offering 'Start' on something
    already running, or 'Stop'/'Restart' on something that isn't)."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "list-units", "ovos-*", "--plain", "--no-legend"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return []
    if result.returncode != 0:
        return []
    services = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        columns = line.split()
        unit_name = columns[0]
        if not unit_name.endswith(".service"):
            continue
        # columns: UNIT LOAD ACTIVE SUB DESCRIPTION... - ACTIVE is
        # index 2 when present; be defensive about short/malformed
        # lines rather than raising on an unexpected systemctl format.
        is_active = len(columns) > 2 and columns[2] == "active"
        services.append((unit_name, is_active))
    return sorted(services)


def _systemctl_action(action: str, unit_name: str, timeout: int = 30):
    """Shared implementation for restart/stop/start - all three are the
    same shape (run systemctl --user <action> <unit>, never raise,
    return (success, message)), so this avoids repeating the
    try/except three times. `action` is a systemctl verb: 'restart',
    'stop', or 'start'."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", action, unit_name],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"{unit_name}: {action} timed out after {timeout}s"
    except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
        return False, f"{unit_name}: {e}"
    if result.returncode == 0:
        past_tense = {"restart": "restarted", "stop": "stopped", "start": "started"}[action]
        return True, f"{unit_name}: {past_tense}"
    return False, f"{unit_name}: {result.stderr.strip() or (action + ' failed')}"


def restart_service(unit_name):
    """Restarts a single systemd --user unit. Returns (success: bool,
    message: str) rather than raising, so the UI can show the result
    without a try/except at every call site."""
    return _systemctl_action("restart", unit_name)


def stop_service(unit_name):
    """Stops a single systemd --user unit. Same (success, message)
    contract as restart_service()."""
    return _systemctl_action("stop", unit_name)


def start_service(unit_name):
    """Starts a single systemd --user unit. Same (success, message)
    contract as restart_service()."""
    return _systemctl_action("start", unit_name)


def detect_container_runtime():
    """Best-effort detection of OVOS running under Docker/Podman
    instead of systemd - meant to be checked when
    discover_services_with_state() finds nothing, so the boot sequence
    can give an honest, specific explanation ("looks like a
    Docker/Podman install") instead of a bare "none found" that reads
    like something's broken.

    Confirmed via ovos-docker's own documentation
    (openvoiceos.github.io/ovos-docker) that OVOS services run as
    containers, not systemd units, when installed this way -
    `systemctl --user` genuinely has nothing to find in that case, so
    there's no bug to fix there, just a UI message worth improving.
    This function does NOT attempt to replace systemctl's start/stop/
    restart functionality for containers - that's real, separate work
    (different commands, different confirmation semantics, potentially
    needing the Docker/Podman socket mounted if ovos-tui-client itself
    ever runs containerized) tracked as its own follow-up rather than
    bolted on here as an afterthought.

    Tries `docker` first, then `podman` (whichever is actually
    installed) - returns a sorted list of container names that look
    OVOS-related (containing 'ovos' or 'hivemind', case-insensitive,
    matching ovos-docker's own naming convention), or [] if neither
    runtime is available or neither has any matching containers."""
    for binary in ("docker", "podman"):
        try:
            result = subprocess.run(
                [binary, "ps", "--format", "{{.Names}}"],
                capture_output=True, text=True, timeout=5,
            )
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            continue
        if result.returncode != 0:
            continue
        names = [n.strip() for n in result.stdout.splitlines() if n.strip()]
        matching = [n for n in names if "ovos" in n.lower() or "hivemind" in n.lower()]
        if matching:
            return sorted(matching)
    return []


def _find_container_binary():
    """Returns 'docker' or 'podman', whichever actually works, or None.
    Separate from detect_container_runtime() (which already does this
    same detection internally) because that function's return contract
    is just a list of names - existing callers/tests depend on that
    shape, so this doesn't change it, at the cost of re-doing the
    detection once more here."""
    for binary in ("docker", "podman"):
        try:
            result = subprocess.run([binary, "ps"], capture_output=True, timeout=5)
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            continue
        if result.returncode == 0:
            return binary
    return None


def start_container_log_bridges(container_names, target_dir):
    """For each container name, starts 'docker logs -f <name>' (or
    podman, whichever detect_container_runtime() would have used) with
    its combined stdout+stderr redirected into target_dir/<name>.log -
    then that directory can be handed to discover_log_sources() and
    treated exactly like any other log directory, reusing 100% of the
    existing file-tailing/filtering machinery. No new "log source"
    abstraction needed inside the app itself.

    This exists because a confirmed real gap (see the discussion that
    led here): on a Docker/Podman install following ovos-docker's own
    documented example config ("logs": {"path": "stdout"}), there are
    no log files anywhere on the host - only container stdout. This
    bridges that gap by making container stdout look like an ordinary
    log file, rather than teaching the app a second, parallel way to
    receive log lines.

    Log source NAMES intentionally come from the container names
    themselves (e.g. "ovos_core", "ovos_audio"), not a hardcoded
    container->service mapping - the exact mapping isn't fully
    confirmed for every core service (see issue #24's own notes on
    this), and guessing wrong would silently mislabel things. Using
    the container's own name sidesteps that entirely: whatever it's
    actually called is what shows up, no mapping table to get wrong or
    keep in sync as ovos-docker's own naming evolves.

    Returns a list of subprocess.Popen handles - the caller owns their
    lifecycle and MUST terminate them (e.g. on app quit); they are not
    cleaned up automatically here. Returns [] immediately (no
    processes started) if neither docker nor podman is available."""
    binary = _find_container_binary()
    if binary is None:
        return []
    target_dir.mkdir(parents=True, exist_ok=True)
    handles = []
    for name in container_names:
        log_path = target_dir / f"{name}.log"
        log_file = open(log_path, "a")
        try:
            proc = subprocess.Popen(
                [binary, "logs", "-f", "--tail", "0", name],
                stdout=log_file, stderr=subprocess.STDOUT,
            )
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            log_file.close()
            continue
        handles.append(proc)
    return handles


def stop_container_log_bridges(handles):
    """Terminates every subprocess started by start_container_log_bridges()
    - call this on app quit. Gives each a moment to exit cleanly before
    force-killing, and never raises even if a process already exited on
    its own (e.g. the container itself stopped)."""
    for proc in handles:
        if proc.poll() is not None:
            continue  # already exited
        proc.terminate()
    for proc in handles:
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
