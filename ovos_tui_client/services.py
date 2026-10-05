"""Discovers and restarts OVOS's systemd services.

Most installs run OVOS as systemd --user units, but not all: the OVOS
installer can also set it up as system units running as the OVOS user
(`User=pi` on a Mark II), and then `systemctl --user` finds nothing
(#63). So user units are looked for first, and only when there are no
OVOS units there, system units. Each unit remembers which scope it was
found in; actions on a system unit go through `sudo -n` (never a
password prompt inside the UI) unless this already runs as root, and say
which command to run by hand when sudo wants a password.

Like logs.py, this doesn't hardcode a fixed service-name list: service
names vary by install (we found 'ovos-core' handles skills, not
'ovos-skills', on a real system earlier in this project) - so services
are discovered by querying systemd directly for anything matching
'ovos-*', rather than guessed at.
"""
import os
import subprocess

USER, SYSTEM = "user", "system"
# unit name -> the scope it was last found in; unknown units are user units
_UNIT_SCOPE = {}


def _list_units(scope):
    """[(unit, is_active)] for 'ovos-*' services in one scope; [] on any
    failure (systemctl missing, no user session ...)."""
    cmd = ["systemctl"] + (["--user"] if scope == USER else []) + \
        ["list-units", "ovos-*", "--plain", "--no-legend"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
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


def service_scope():
    """USER or SYSTEM for the services last discovered, None if none."""
    scopes = set(_UNIT_SCOPE.values())
    return scopes.pop() if len(scopes) == 1 else (USER if USER in scopes else None)


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
    already running, or 'Stop'/'Restart' on something that isn't).

    User units first; system units only when there are no OVOS user
    units (#63)."""
    services, scope = _list_units(USER), USER
    if not services:
        services, scope = _list_units(SYSTEM), SYSTEM
    _UNIT_SCOPE.clear()
    _UNIT_SCOPE.update({name: scope for name, _ in services})
    return services


def _is_root():
    try:
        return os.geteuid() == 0
    except AttributeError:  # not POSIX
        return False


def _systemctl_action(action: str, unit_name: str, timeout: int = 30):
    """Shared implementation for restart/stop/start - all three are the
    same shape (run systemctl <action> <unit> in the unit's scope, never
    raise, return (success, message)), so this avoids repeating the
    try/except three times. `action` is a systemctl verb: 'restart',
    'stop', or 'start'. A system unit goes through `sudo -n` (unless this
    runs as root): no password prompt can appear inside the UI, and when
    sudo needs one the message says what to run instead."""
    system = _UNIT_SCOPE.get(unit_name) == SYSTEM
    if not system:
        cmd = ["systemctl", "--user", action, unit_name]
    elif _is_root():
        cmd = ["systemctl", action, unit_name]
    else:
        cmd = ["sudo", "-n", "systemctl", action, unit_name]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"{unit_name}: {action} timed out after {timeout}s"
    except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
        return False, f"{unit_name}: {e}"
    if result.returncode == 0:
        past_tense = {"restart": "restarted", "stop": "stopped", "start": "started"}[action]
        return True, f"{unit_name}: {past_tense}"
    err = result.stderr.strip()
    if system and cmd[0] == "sudo" and ("password" in err.lower() or "sudo:" in err):
        return False, (f"{unit_name} is a system service and sudo wants a password: "
                       f"run `sudo systemctl {action} {unit_name}` yourself")
    return False, f"{unit_name}: {err or (action + ' failed')}"


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


def categorize_container_name(name):
    """Maps a Docker/Podman container name to the SAME category names
    file-based installs already use (see logs.py's KNOWN_LOG_NAMES) -
    "skills", "audio", "voice", "bus", "phal", "gui" - or "other" if it
    doesn't recognizably fit one of those.

    This exists so start_container_log_bridges() (below) can make
    Docker/Podman container logs land in the exact same small,
    familiar set of log files a normal systemd/venv install already
    produces - "skills.log", "audio.log", etc - rather than one file
    per container (which on a real ovos-docker install is easily 15-25
    separate files/checkboxes for what's conceptually still just a
    handful of categories). Confirmed directly against a real,
    running ovos-docker install's actual container names (ovos_core,
    ovos_audio, ovos_listener, ovos_messagebus, ovos_phal,
    ovos_phal_admin, ovos_skill_*, plus a few - ovos_cli,
    ovos_plugin_ggwave - that don't map to anything and land in
    "other").

    Pattern-matched, not an exhaustive lookup table - "ovos_skill_"
    (any suffix) always maps to "skills", matching how every
    individual skill already shares ONE skills.log file on a normal
    install, not one file per skill. "ovos_core" also maps to
    "skills" specifically because its own log content (intent-service/
    pipeline handling) is exactly what already lands in skills.log on
    a normal install - confirmed by comparing real log line prefixes
    between a systemd install and this container's own output."""
    n = name.lower()
    if n.startswith("ovos_skill") or n == "ovos_core":
        return "skills"
    if n == "ovos_audio":
        return "audio"
    if n == "ovos_listener":
        return "voice"
    if n == "ovos_messagebus":
        return "bus"
    if n.startswith("ovos_phal"):
        return "phal"
    if "gui" in n:
        return "gui"
    return "other"


def start_container_log_bridges(container_names, target_dir):
    """Bridges Docker/Podman container stdout into the SAME small set
    of log files a normal file-based install already produces
    (skills.log, audio.log, voice.log, bus.log, phal.log, plus
    other.log for anything uncategorized - see
    categorize_container_name() above) - not one file per container.
    Then that directory can be handed to discover_log_sources() (its
    DEFAULT KNOWN_LOG_NAMES, no override needed) and treated exactly
    like any other log directory, reusing 100% of the existing
    file-tailing/coloring/filtering machinery, including the Sources:
    checkboxes actually being a small, meaningful set instead of one
    checkbox per container.

    Multiple containers sharing a category (most commonly "skills" -
    every ovos_skill_* container) each get their OWN `docker logs -f`
    subprocess, but all of them append to the SAME shared file -
    concurrent appends from separate processes are safe here without
    explicit locking, since POSIX guarantees a single write() to a
    file opened with O_APPEND is atomic as long as it's smaller than
    PIPE_BUF (4096 bytes on Linux) - true for any normal single log
    line.

    This exists because a confirmed real gap (see the discussion that
    led here): on a Docker/Podman install following ovos-docker's own
    documented example config ("logs": {"path": "stdout"}), there are
    no log files anywhere on the host - only container stdout. This
    bridges that gap by making container stdout look like an ordinary
    log file, rather than teaching the app a second, parallel way to
    receive log lines.

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
        category = categorize_container_name(name)
        log_path = target_dir / f"{category}.log"
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
