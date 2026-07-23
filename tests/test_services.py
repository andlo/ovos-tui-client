"""Tests for services.py - systemctl --user is mocked throughout, no
real service management is exercised here."""
from unittest.mock import MagicMock, patch

from ovos_tui_client.services import discover_services, discover_services_with_state, restart_service, stop_service, start_service


def _fake_completed(stdout="", stderr="", returncode=0):
    r = MagicMock()
    r.stdout = stdout
    r.stderr = stderr
    r.returncode = returncode
    return r


def test_discover_services_parses_unit_names():
    fake_output = (
        "ovos-core.service       loaded active running Open Voice OS - Core (skills)\n"
        "ovos-audio.service      loaded active running Open Voice OS - Audio\n"
        "ovos-messagebus.service loaded active running Open Voice OS - Message bus service\n"
    )
    with patch("subprocess.run", return_value=_fake_completed(stdout=fake_output)):
        services = discover_services()

    assert services == ["ovos-audio.service", "ovos-core.service", "ovos-messagebus.service"]


def test_discover_services_returns_empty_list_on_nonzero_exit():
    with patch("subprocess.run", return_value=_fake_completed(returncode=1)):
        assert discover_services() == []


def test_discover_services_returns_empty_list_when_systemctl_missing():
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        assert discover_services() == []


def test_discover_services_ignores_blank_lines():
    fake_output = "ovos-core.service loaded active running X\n\n\n"
    with patch("subprocess.run", return_value=_fake_completed(stdout=fake_output)):
        assert discover_services() == ["ovos-core.service"]


def test_restart_service_success():
    with patch("subprocess.run", return_value=_fake_completed(returncode=0)):
        ok, msg = restart_service("ovos-core.service")

    assert ok is True
    assert "restarted" in msg


def test_restart_service_failure_includes_stderr():
    with patch("subprocess.run", return_value=_fake_completed(returncode=1, stderr="Unit not found.")):
        ok, msg = restart_service("ovos-bogus.service")

    assert ok is False
    assert "Unit not found." in msg


def test_restart_service_timeout_reported_not_raised():
    import subprocess as sp
    with patch("subprocess.run", side_effect=sp.TimeoutExpired(cmd="systemctl", timeout=30)):
        ok, msg = restart_service("ovos-core.service")

    assert ok is False
    assert "timed out" in msg


def test_restart_service_missing_systemctl_reported_not_raised():
    with patch("subprocess.run", side_effect=FileNotFoundError("no systemctl")):
        ok, msg = restart_service("ovos-core.service")

    assert ok is False


def test_stop_service_success():
    with patch("subprocess.run", return_value=_fake_completed(returncode=0)) as mock_run:
        ok, msg = stop_service("ovos-core.service")

    assert ok is True
    assert "stopped" in msg
    assert mock_run.call_args[0][0] == ["systemctl", "--user", "stop", "ovos-core.service"]


def test_stop_service_failure_includes_stderr():
    with patch("subprocess.run", return_value=_fake_completed(returncode=1, stderr="Permission denied.")):
        ok, msg = stop_service("ovos-core.service")

    assert ok is False
    assert "Permission denied." in msg


def test_start_service_success():
    with patch("subprocess.run", return_value=_fake_completed(returncode=0)) as mock_run:
        ok, msg = start_service("ovos-core.service")

    assert ok is True
    assert "started" in msg
    assert mock_run.call_args[0][0] == ["systemctl", "--user", "start", "ovos-core.service"]


def test_start_service_timeout_reported_not_raised():
    import subprocess as sp
    with patch("subprocess.run", side_effect=sp.TimeoutExpired(cmd="systemctl", timeout=30)):
        ok, msg = start_service("ovos-core.service")

    assert ok is False
    assert "timed out" in msg


def test_stop_service_missing_systemctl_reported_not_raised():
    with patch("subprocess.run", side_effect=FileNotFoundError("no systemctl")):
        ok, msg = stop_service("ovos-core.service")

    assert ok is False


def test_discover_services_with_state_parses_active_column():
    fake_output = (
        "ovos-core.service       loaded active   running Open Voice OS - Core (skills)\n"
        "ovos-audio.service      loaded inactive dead    Open Voice OS - Audio\n"
    )
    with patch("subprocess.run", return_value=_fake_completed(stdout=fake_output)):
        services = discover_services_with_state()

    assert services == [
        ("ovos-audio.service", False),
        ("ovos-core.service", True),
    ]


def test_discover_services_with_state_returns_empty_list_on_failure():
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        assert discover_services_with_state() == []


def test_discover_services_still_returns_name_only_list():
    """Backward compatibility for existing callers/tests."""
    fake_output = "ovos-core.service loaded active running X\n"
    with patch("subprocess.run", return_value=_fake_completed(stdout=fake_output)):
        assert discover_services() == ["ovos-core.service"]
