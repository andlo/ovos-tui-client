"""Tests for state.py - filter persistence across sessions."""
import pytest

from ovos_tui_client.state import load_filter_state, save_filter_state


def test_load_returns_empty_structure_when_no_file_exists(tmp_path, monkeypatch):
    from ovos_tui_client import state
    monkeypatch.setattr(state, "STATE_FILE", tmp_path / "nonexistent" / "state.json")
    result = state.load_filter_state()
    assert result == {"sources": {}, "levels": {}, "skills": {}}


def test_save_then_load_round_trips(tmp_path, monkeypatch):
    from ovos_tui_client import state
    monkeypatch.setattr(state, "STATE_FILE", tmp_path / "sub" / "state.json")

    state.save_filter_state(
        sources={"bus": False, "skills": True},
        levels={"DEBUG": False},
        skills={"ovos-skill-grimm-tales.andlo": True},
    )
    result = state.load_filter_state()

    assert result == {
        "sources": {"bus": False, "skills": True},
        "levels": {"DEBUG": False},
        "skills": {"ovos-skill-grimm-tales.andlo": True},
    }


def test_load_handles_corrupt_json_gracefully(tmp_path, monkeypatch):
    from ovos_tui_client import state
    bad_file = tmp_path / "state.json"
    bad_file.write_text("{not valid json")
    monkeypatch.setattr(state, "STATE_FILE", bad_file)

    result = state.load_filter_state()
    assert result == {"sources": {}, "levels": {}, "skills": {}}


def test_load_handles_non_dict_json_gracefully(tmp_path, monkeypatch):
    from ovos_tui_client import state
    weird_file = tmp_path / "state.json"
    weird_file.write_text("[1, 2, 3]")
    monkeypatch.setattr(state, "STATE_FILE", weird_file)

    result = state.load_filter_state()
    assert result == {"sources": {}, "levels": {}, "skills": {}}


def test_save_never_raises_on_unwritable_path(tmp_path, monkeypatch):
    from ovos_tui_client import state
    # a path under a file (not a directory) is never writable as a dir
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    monkeypatch.setattr(state, "STATE_FILE", blocker / "sub" / "state.json")

    state.save_filter_state(sources={}, levels={}, skills={})  # must not raise


# --- integration with OVOSTUIApp ---

@pytest.mark.asyncio
async def test_app_restores_saved_filter_state_on_init(tmp_path, monkeypatch):
    import pytest as _pytest  # noqa: F401 (already imported at module scope, kept explicit for clarity)
    from unittest.mock import MagicMock
    from ovos_tui_client import state
    from ovos_tui_client.app import OVOSTUIApp

    state_file = tmp_path / "state.json"
    monkeypatch.setattr(state, "STATE_FILE", state_file)
    monkeypatch.setattr("ovos_tui_client.app.load_filter_state", state.load_filter_state)

    (tmp_path / "skills.log").write_text("")
    (tmp_path / "bus.log").write_text("")
    state.save_filter_state(
        sources={"skills": False},
        levels={"DEBUG": False},
        skills={"ovos-skill-grimm-tales.andlo": True},
    )

    app = OVOSTUIApp(log_dir_override=str(tmp_path))
    app.bus = MagicMock()

    skills_source = next(s for s in app.log_sources if s.name == "skills")
    assert skills_source.enabled is False
    assert app.level_enabled["DEBUG"] is False
    assert app.skill_enabled == {"ovos-skill-grimm-tales.andlo": True}


@pytest.mark.asyncio
async def test_action_quit_saves_filter_state(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    from ovos_tui_client import state
    from ovos_tui_client.app import OVOSTUIApp

    state_file = tmp_path / "sub" / "state.json"
    monkeypatch.setattr(state, "STATE_FILE", state_file)
    monkeypatch.setattr("ovos_tui_client.app.save_filter_state", state.save_filter_state)

    (tmp_path / "skills.log").write_text("")
    app = OVOSTUIApp(log_dir_override=str(tmp_path))
    app.bus = MagicMock()
    async with app.run_test() as pilot:
        for src in app.log_sources:
            if src.name == "skills":
                src.enabled = False
        await app.action_quit()

    result = state.load_filter_state()
    assert result["sources"]["skills"] is False


# --- input history persistence (issue #12) ---

def test_load_input_history_returns_empty_list_when_no_file_exists(tmp_path, monkeypatch):
    from ovos_tui_client import state
    monkeypatch.setattr(state, "STATE_FILE", tmp_path / "nonexistent" / "state.json")
    assert state.load_input_history() == []


def test_save_then_load_input_history_round_trips(tmp_path, monkeypatch):
    from ovos_tui_client import state
    monkeypatch.setattr(state, "STATE_FILE", tmp_path / "sub" / "state.json")

    state.save_input_history(["tell me a joke", "what time is it"])
    assert state.load_input_history() == ["tell me a joke", "what time is it"]


def test_save_input_history_caps_at_the_configured_limit(tmp_path, monkeypatch):
    from ovos_tui_client import state
    monkeypatch.setattr(state, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(state, "INPUT_HISTORY_CAP", 3)

    state.save_input_history(["one", "two", "three", "four", "five"])

    # keeps the MOST RECENT entries (end of the list), drops the oldest
    assert state.load_input_history() == ["three", "four", "five"]


def test_load_input_history_ignores_non_string_entries(tmp_path, monkeypatch):
    from ovos_tui_client import state
    import json
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"input_history": ["ok", 123, None, "also ok"]}))
    monkeypatch.setattr(state, "STATE_FILE", state_file)

    assert state.load_input_history() == ["ok", "also ok"]


def test_saving_input_history_preserves_existing_filter_state(tmp_path, monkeypatch):
    """Both kinds of state live in the same file - saving one must not
    clobber the other."""
    from ovos_tui_client import state
    monkeypatch.setattr(state, "STATE_FILE", tmp_path / "state.json")

    state.save_filter_state(sources={"skills": False}, levels={}, skills={})
    state.save_input_history(["hello"])

    assert state.load_filter_state()["sources"] == {"skills": False}
    assert state.load_input_history() == ["hello"]


def test_saving_filter_state_preserves_existing_input_history(tmp_path, monkeypatch):
    from ovos_tui_client import state
    monkeypatch.setattr(state, "STATE_FILE", tmp_path / "state.json")

    state.save_input_history(["hello"])
    state.save_filter_state(sources={"skills": False}, levels={}, skills={})

    assert state.load_input_history() == ["hello"]
    assert state.load_filter_state()["sources"] == {"skills": False}


def test_load_input_history_handles_corrupt_json_gracefully(tmp_path, monkeypatch):
    from ovos_tui_client import state
    bad_file = tmp_path / "state.json"
    bad_file.write_text("{not valid json")
    monkeypatch.setattr(state, "STATE_FILE", bad_file)
    assert state.load_input_history() == []


def test_save_input_history_never_raises_on_unwritable_path(tmp_path, monkeypatch):
    from ovos_tui_client import state
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    monkeypatch.setattr(state, "STATE_FILE", blocker / "sub" / "state.json")
    state.save_input_history(["x"])  # must not raise


# --- integration with OVOSTUIApp ---

@pytest.mark.asyncio
async def test_app_restores_saved_input_history_on_init(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    from ovos_tui_client import state
    from ovos_tui_client.app import OVOSTUIApp

    monkeypatch.setattr(state, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr("ovos_tui_client.app.load_input_history", state.load_input_history)
    state.save_input_history(["read me a grimm story", "what time is it"])

    (tmp_path / "skills.log").write_text("")
    app = OVOSTUIApp(log_dir_override=str(tmp_path))
    app.bus = MagicMock()

    assert app.utterance_history == ["read me a grimm story", "what time is it"]


@pytest.mark.asyncio
async def test_action_quit_saves_input_history(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    from ovos_tui_client import state
    from ovos_tui_client.app import OVOSTUIApp

    monkeypatch.setattr(state, "STATE_FILE", tmp_path / "sub" / "state.json")
    monkeypatch.setattr("ovos_tui_client.app.save_input_history", state.save_input_history)

    (tmp_path / "skills.log").write_text("")
    app = OVOSTUIApp(log_dir_override=str(tmp_path))
    app.bus = MagicMock()
    async with app.run_test() as pilot:
        app.utterance_history.append("tell me a joke")
        await app.action_quit()

    assert state.load_input_history() == ["tell me a joke"]
