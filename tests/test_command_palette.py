"""Tests for the Command Palette's Service: Restart/Stop/Start entries
(issue #3 follow-up) - the two-step flow where picking an action opens
ServicePickerScreen (see test_screens.py) to choose which service.
services.py's own discover/restart/stop/start functions are mocked
throughout; this file tests the palette-entry glue, not systemctl."""
from unittest.mock import MagicMock, patch

import pytest

from ovos_tui_client.app import OVOSTUIApp, ServicePickerScreen


def _app_with_fake_bus(tmp_path):
    (tmp_path / "skills.log").write_text("")
    app = OVOSTUIApp(log_dir_override=str(tmp_path))
    app.bus = MagicMock()
    return app


@pytest.mark.asyncio
async def test_system_commands_include_the_three_service_actions(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        titles = [cmd.title for cmd in app.get_system_commands(app.screen)]
        assert "Service: Restart..." in titles
        assert "Service: Stop..." in titles
        assert "Service: Start..." in titles


@pytest.mark.asyncio
async def test_service_titles_share_a_common_prefix_for_palette_grouping(tmp_path):
    """The whole point of the 'Service: ' prefix: typing 'service' in
    the palette should cluster all three actions together via fuzzy
    match, since Textual's palette has no native submenu support."""
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        titles = [cmd.title for cmd in app.get_system_commands(app.screen)]
        service_titles = [t for t in titles if t.startswith("Service: ")]
        assert len(service_titles) == 3


@pytest.mark.asyncio
async def test_log_toggle_titles_share_a_common_prefix_for_palette_grouping(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        titles = [cmd.title for cmd in app.get_system_commands(app.screen)]
        log_titles = [t for t in titles if t.startswith("Log: ")]
        assert any("Toggle source" in t for t in log_titles)
        assert any("Toggle level" in t for t in log_titles)


@pytest.mark.asyncio
async def test_selecting_service_restart_command_opens_the_picker_bound_to_restart(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        with patch("ovos_tui_client.app.discover_services", return_value=["ovos-core.service"]), \
             patch("ovos_tui_client.app.restart_service", return_value=(True, "ovos-core.service: restarted")) as mock_restart:
            commands = {cmd.title: cmd.callback for cmd in app.get_system_commands(app.screen)}
            commands["Service: Restart..."]()
            await pilot.pause()

            assert isinstance(app.screen, ServicePickerScreen)
            assert app.screen.action_label == "Restart"

            # confirm the bound action really is restart_service, not stop/start
            app.screen.action_fn("ovos-core.service")
            mock_restart.assert_called_once_with("ovos-core.service")


@pytest.mark.asyncio
async def test_selecting_service_stop_command_opens_the_picker_bound_to_stop(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        with patch("ovos_tui_client.app.discover_services", return_value=["ovos-core.service"]), \
             patch("ovos_tui_client.app.stop_service", return_value=(True, "ovos-core.service: stopped")) as mock_stop:
            commands = {cmd.title: cmd.callback for cmd in app.get_system_commands(app.screen)}
            commands["Service: Stop..."]()
            await pilot.pause()

            assert isinstance(app.screen, ServicePickerScreen)
            assert app.screen.action_label == "Stop"
            app.screen.action_fn("ovos-core.service")
            mock_stop.assert_called_once_with("ovos-core.service")


@pytest.mark.asyncio
async def test_selecting_service_start_command_opens_the_picker_bound_to_start(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        with patch("ovos_tui_client.app.discover_services", return_value=["ovos-core.service"]), \
             patch("ovos_tui_client.app.start_service", return_value=(True, "ovos-core.service: started")) as mock_start:
            commands = {cmd.title: cmd.callback for cmd in app.get_system_commands(app.screen)}
            commands["Service: Start..."]()
            await pilot.pause()

            assert isinstance(app.screen, ServicePickerScreen)
            assert app.screen.action_label == "Start"
            app.screen.action_fn("ovos-core.service")
            mock_start.assert_called_once_with("ovos-core.service")
