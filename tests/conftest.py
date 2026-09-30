import pytest


@pytest.fixture(autouse=True)
def _no_channel_network(monkeypatch, tmp_path):
    """The release-channel check fetches constraints files live; tests never
    touch the network or this machine's /opt/ovos/tag. Tests of the check
    itself pass their own fetch."""
    monkeypatch.setattr("ovos_tui_client.channel.fetch_text", lambda *a, **k: None)
    monkeypatch.setattr("ovos_tui_client.channel.RASPOVOS_TAG_FILE", tmp_path / "no-raspovos-tag")
    monkeypatch.setattr("ovos_tui_client.manifest.INSTALLER_STATE_FILE", tmp_path / "no-installer.json")


@pytest.fixture(autouse=True)
def _no_busy_wait(monkeypatch):
    """After a timeout the runner waits for OVOS to finish (BUSY_WAIT, minutes).
    Off in tests unless a test passes busy_wait itself."""
    monkeypatch.setattr("ovos_tui_client.scripts.BUSY_WAIT", 0)
