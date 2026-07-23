"""Tests for ServiceCommandProvider - the Command Palette's real
autocomplete-as-you-type over discovered services (issue #3).
services.py's own discover/restart/stop/start functions are mocked
throughout; this file tests the Provider glue, not systemctl."""
from unittest.mock import MagicMock, patch

import pytest

from ovos_tui_client.app import OVOSTUIApp, ServiceCommandProvider


def _app_with_fake_bus(tmp_path):
    (tmp_path / "skills.log").write_text("")
    app = OVOSTUIApp(log_dir_override=str(tmp_path))
    app.bus = MagicMock()
    return app


async def _collect_hits(provider, query):
    return [hit async for hit in provider.search(query)]


@pytest.mark.asyncio
async def test_search_yields_a_hit_per_service_per_action(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        provider = ServiceCommandProvider(app.screen)
        with patch("ovos_tui_client.app.discover_services", return_value=["ovos-core.service"]):
            hits = await _collect_hits(provider, "ovos-core")

        texts = [str(h.match_display) for h in hits]
        assert any("Restart service: ovos-core.service" in t for t in texts)
        assert any("Stop service: ovos-core.service" in t for t in texts)
        assert any("Start service: ovos-core.service" in t for t in texts)


@pytest.mark.asyncio
async def test_search_matches_partial_queries(tmp_path):
    """Confirms fuzzy autocomplete actually narrows by what's typed,
    not just returning everything regardless of query."""
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        provider = ServiceCommandProvider(app.screen)
        with patch("ovos_tui_client.app.discover_services",
                   return_value=["ovos-core.service", "ovos-audio.service"]):
            hits = await _collect_hits(provider, "restart audio")

        texts = [str(h.match_display) for h in hits]
        assert any("ovos-audio.service" in t for t in texts)
        assert not any("ovos-core.service" in t and "Restart" in t for t in texts)


@pytest.mark.asyncio
async def test_selecting_a_restart_hit_calls_restart_service(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        provider = ServiceCommandProvider(app.screen)
        with patch("ovos_tui_client.app.discover_services", return_value=["ovos-core.service"]), \
             patch("ovos_tui_client.app.restart_service", return_value=(True, "ovos-core.service: restarted")) as mock_restart:
            hits = await _collect_hits(provider, "Restart service: ovos-core.service")
            hit = next(h for h in hits if "Restart" in str(h.match_display))
            hit.command()
            await pilot.pause()

            mock_restart.assert_called_once_with("ovos-core.service")


@pytest.mark.asyncio
async def test_selecting_a_stop_hit_calls_stop_service(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        provider = ServiceCommandProvider(app.screen)
        with patch("ovos_tui_client.app.discover_services", return_value=["ovos-core.service"]), \
             patch("ovos_tui_client.app.stop_service", return_value=(True, "ovos-core.service: stopped")) as mock_stop:
            hits = await _collect_hits(provider, "Stop service: ovos-core.service")
            hit = next(h for h in hits if "Stop" in str(h.match_display))
            hit.command()
            await pilot.pause()

            mock_stop.assert_called_once_with("ovos-core.service")


@pytest.mark.asyncio
async def test_no_services_means_no_hits(tmp_path):
    app = _app_with_fake_bus(tmp_path)
    async with app.run_test() as pilot:
        provider = ServiceCommandProvider(app.screen)
        with patch("ovos_tui_client.app.discover_services", return_value=[]):
            hits = await _collect_hits(provider, "restart")

        assert hits == []
