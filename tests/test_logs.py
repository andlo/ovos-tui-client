"""Tests for log discovery and tailing - the part of this tool most
worth being paranoid about, since the log directory location varies by
OVOS install method and isn't reliably documented."""
from ovos_tui_client.logs import (
    LogSource, find_log_dir, discover_log_sources, line_matches_filter,
    strip_log_prefix, CANDIDATE_LOG_DIRS,
)


def test_read_new_lines_returns_only_appended_content(tmp_path):
    log_file = tmp_path / "skills.log"
    log_file.write_text("line one\nline two\n")
    src = LogSource(name="skills", path=log_file)

    first = src.read_new_lines()
    assert first == ["line one", "line two"]

    # nothing new yet
    assert src.read_new_lines() == []

    with open(log_file, "a") as f:
        f.write("line three\n")
    assert src.read_new_lines() == ["line three"]


def test_read_new_lines_missing_file_returns_empty(tmp_path):
    src = LogSource(name="skills", path=tmp_path / "does_not_exist.log")
    assert src.read_new_lines() == []


def test_read_new_lines_handles_truncation(tmp_path):
    """A log file that got rotated/reduced (e.g. by ovos-logs reduce)
    should be read from the start again, not silently stop producing
    new lines forever because the recorded offset is now past EOF."""
    log_file = tmp_path / "bus.log"
    log_file.write_text("a" * 500 + "\n")
    src = LogSource(name="bus", path=log_file)
    src.read_new_lines()
    assert src._offset > 0

    log_file.write_text("short\n")  # simulates rotation/truncation
    lines = src.read_new_lines()
    assert lines == ["short"]


def test_find_log_dir_returns_first_candidate_with_known_logs(tmp_path, monkeypatch):
    fake_state_dir = tmp_path / "state" / "mycroft"
    fake_state_dir.mkdir(parents=True)
    (fake_state_dir / "skills.log").write_text("hi\n")

    monkeypatch.setattr(
        "ovos_tui_client.logs.CANDIDATE_LOG_DIRS",
        [str(tmp_path / "nonexistent"), str(fake_state_dir)],
    )

    assert find_log_dir() == fake_state_dir


def test_find_log_dir_returns_none_if_nothing_matches(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "ovos_tui_client.logs.CANDIDATE_LOG_DIRS",
        [str(tmp_path / "nowhere")],
    )
    assert find_log_dir() is None


def test_find_log_dir_trusts_explicit_override_unconditionally(tmp_path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    # no known logs here at all - override should still be returned
    assert find_log_dir(override=str(empty_dir)) == empty_dir


def test_discover_log_sources_only_includes_existing_files(tmp_path):
    (tmp_path / "skills.log").write_text("")
    (tmp_path / "bus.log").write_text("")
    # gui.log, audio.log etc intentionally absent

    sources = discover_log_sources(tmp_path)

    names = {s.name for s in sources}
    assert names == {"skills", "bus"}


def test_discover_log_sources_handles_none_dir():
    assert discover_log_sources(None) == []


def test_line_matches_filter_empty_filter_matches_everything():
    assert line_matches_filter("anything at all", "") is True


def test_line_matches_filter_case_insensitive_substring():
    assert line_matches_filter("Could not load ovos-skill-grimm-tales", "grimm") is True
    assert line_matches_filter("Could not load ovos-skill-grimm-tales", "GRIMM") is True
    assert line_matches_filter("Could not load ovos-skill-grimm-tales", "andersen") is False


def test_strip_log_prefix_removes_timestamp_and_component():
    line = "2024-12-07 07:51:04.662 - bus - ovos_messagebus.__main__:main:46 - INFO - Starting..."
    assert strip_log_prefix(line) == "ovos_messagebus.__main__:main:46 - INFO - Starting..."


def test_strip_log_prefix_works_for_any_component_name():
    line = "2026-07-22 21:13:03.456 - PHAL - some_module:func:12 - WARNING - device not found"
    assert strip_log_prefix(line) == "some_module:func:12 - WARNING - device not found"


def test_strip_log_prefix_leaves_non_matching_lines_unchanged():
    """Continuation lines of a multi-line traceback have no prefix of
    their own - these must pass through untouched, not get mangled."""
    line = "    File \"/home/ovos/skill.py\", line 42, in handle_search"
    assert strip_log_prefix(line) == line


def test_strip_log_prefix_handles_comma_millisecond_separator():
    """Some Python logging configs use a comma instead of a dot before
    milliseconds (the stdlib default asctime format)."""
    line = "2026-07-22 21:13:03,456 - skills - some_module:func:12 - INFO - hello"
    assert strip_log_prefix(line) == "some_module:func:12 - INFO - hello"
