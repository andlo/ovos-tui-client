"""Tests for services.py - systemctl --user is mocked throughout, no
real service management is exercised here."""
from unittest.mock import MagicMock, patch

from ovos_tui_client.services import discover_services, discover_services_with_state, restart_service, stop_service, start_service, detect_container_runtime, start_container_log_bridges, stop_container_log_bridges, categorize_container_name


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


# --- detect_container_runtime() (confirmed against a real podman container during development) ---

def test_detect_container_runtime_finds_ovos_named_containers():
    fake_output = "ovos-core-test\nsome-other-container\nhivemind-relay\n"
    with patch("subprocess.run", return_value=_fake_completed(stdout=fake_output)):
        assert detect_container_runtime() == ["hivemind-relay", "ovos-core-test"]


def test_detect_container_runtime_returns_empty_when_no_matching_containers():
    fake_output = "some-other-container\nanother-one\n"
    with patch("subprocess.run", return_value=_fake_completed(stdout=fake_output)):
        assert detect_container_runtime() == []


def test_detect_container_runtime_returns_empty_when_neither_binary_available():
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        assert detect_container_runtime() == []


def test_detect_container_runtime_falls_back_from_docker_to_podman():
    """docker not installed (FileNotFoundError) but podman is and has a
    match - confirms the fallback actually tries the second binary
    rather than giving up after the first failure."""
    call_count = {"n": 0}

    def fake_run(cmd, **kwargs):
        call_count["n"] += 1
        if cmd[0] == "docker":
            raise FileNotFoundError()
        return _fake_completed(stdout="ovos-messagebus\n")

    with patch("subprocess.run", side_effect=fake_run):
        assert detect_container_runtime() == ["ovos-messagebus"]
    assert call_count["n"] == 2


# --- start_container_log_bridges()/stop_container_log_bridges() ---
# (mechanism confirmed working end-to-end against a real, locally-run
# podman container during development - these tests cover the
# subprocess-management logic itself, mocked)

# --- categorize_container_name(): maps real ovos-docker container
# names (confirmed against a real, running install) to the same
# category names file-based installs already use ---

def test_categorize_container_name_skills():
    assert categorize_container_name("ovos_skill_wikihow") == "skills"
    assert categorize_container_name("ovos_skill_date_time") == "skills"
    assert categorize_container_name("ovos_core") == "skills"


def test_categorize_container_name_audio():
    assert categorize_container_name("ovos_audio") == "audio"


def test_categorize_container_name_voice():
    assert categorize_container_name("ovos_listener") == "voice"


def test_categorize_container_name_bus():
    assert categorize_container_name("ovos_messagebus") == "bus"


def test_categorize_container_name_phal():
    assert categorize_container_name("ovos_phal") == "phal"
    assert categorize_container_name("ovos_phal_admin") == "phal"


def test_categorize_container_name_gui():
    assert categorize_container_name("ovos_gui_websocket") == "gui"


def test_categorize_container_name_falls_back_to_other():
    """Real container names seen on a live install that don't map to
    a known category - ovos_cli (a debug/interactive tool, not a
    logging service) and ovos_plugin_ggwave (an audio-data-over-sound
    plugin, not one of the core categories)."""
    assert categorize_container_name("ovos_cli") == "other"
    assert categorize_container_name("ovos_plugin_ggwave") == "other"


def test_start_container_log_bridges_spawns_one_process_per_container(tmp_path):
    with patch("subprocess.run", return_value=_fake_completed(returncode=0)):
        with patch("subprocess.Popen") as mock_popen:
            handles = start_container_log_bridges(["ovos_core", "ovos_audio"], tmp_path)
    assert mock_popen.call_count == 2
    assert len(handles) == 2


def test_start_container_log_bridges_groups_same_category_containers_into_one_file(tmp_path):
    """The actual point of this design: on a real ovos-docker install
    there can be 15-25 individual skill containers - all of them must
    append to the SAME skills.log, not create 15-25 separate files/
    checkboxes for what's conceptually one category."""
    with patch("subprocess.run", return_value=_fake_completed(returncode=0)):
        with patch("subprocess.Popen") as mock_popen:
            start_container_log_bridges(
                ["ovos_skill_wikihow", "ovos_skill_weather", "ovos_skill_wolfie"], tmp_path
            )
    # 3 separate `docker logs -f` processes (one per container - each
    # needs its own subprocess, docker can't merge streams itself)...
    assert mock_popen.call_count == 3
    # ...but all writing to the same single file
    stdout_targets = {call.kwargs["stdout"].name for call in mock_popen.call_args_list}
    assert stdout_targets == {str(tmp_path / "skills.log")}


def test_start_container_log_bridges_uses_docker_logs_dash_f_with_container_name(tmp_path):
    with patch("subprocess.run", return_value=_fake_completed(returncode=0)):
        with patch("subprocess.Popen") as mock_popen:
            start_container_log_bridges(["ovos_core"], tmp_path)
    cmd = mock_popen.call_args[0][0]
    assert cmd[0] in ("docker", "podman")
    assert "logs" in cmd
    assert "-f" in cmd
    assert cmd[-1] == "ovos_core"


def test_start_container_log_bridges_creates_the_target_directory(tmp_path):
    target = tmp_path / "not-yet-created"
    with patch("subprocess.run", return_value=_fake_completed(returncode=0)):
        with patch("subprocess.Popen"):
            start_container_log_bridges(["ovos_core"], target)
    assert target.is_dir()


def test_start_container_log_bridges_returns_empty_when_no_binary_available():
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        handles = start_container_log_bridges(["ovos_core"], "/tmp/whatever")
    assert handles == []


def test_start_container_log_bridges_skips_a_container_on_popen_failure(tmp_path):
    """One container's docker/podman invocation fails to even start
    (e.g. a transient error) - shouldn't take down the others."""
    with patch("subprocess.run", return_value=_fake_completed(returncode=0)):
        with patch("subprocess.Popen", side_effect=[OSError("boom"), MagicMock()]):
            handles = start_container_log_bridges(["broken", "ovos_core"], tmp_path)
    assert len(handles) == 1


def test_stop_container_log_bridges_terminates_running_processes():
    proc = MagicMock()
    proc.poll.return_value = None  # still running
    stop_container_log_bridges([proc])
    proc.terminate.assert_called_once()
    proc.wait.assert_called_once()


def test_stop_container_log_bridges_skips_already_exited_processes():
    proc = MagicMock()
    proc.poll.return_value = 0  # already exited
    stop_container_log_bridges([proc])
    proc.terminate.assert_not_called()


def test_stop_container_log_bridges_force_kills_after_timeout():
    import subprocess as subprocess_module
    proc = MagicMock()
    proc.poll.return_value = None
    proc.wait.side_effect = subprocess_module.TimeoutExpired(cmd="x", timeout=3)
    stop_container_log_bridges([proc])
    proc.kill.assert_called_once()
