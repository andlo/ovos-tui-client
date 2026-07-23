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
    than raising - callers should treat that as 'nothing to show'."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "list-units", "ovos-*", "--plain", "--no-legend"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return []
    if result.returncode != 0:
        return []
    names = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        unit_name = line.split()[0]
        if unit_name.endswith(".service"):
            names.append(unit_name)
    return sorted(names)


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
