"""Tests for skill_examples.py - the file-lookup logic is unit tested
against real, temporary package-like directory structures (not mocked
importlib internals) so the actual file-finding logic is genuinely
exercised, not just assumed correct. The skill_id -> module_name
heuristic itself was separately confirmed against a real, live
install (37/37 real skill_ids resolved correctly) - see the module's
own docstring."""
import json
import sys
from unittest.mock import MagicMock, patch

import pytest

from ovos_tui_client.skill_examples import guess_module_name, find_skill_examples, short_skill_name


# --- guess_module_name() ---

def test_guess_module_name_strips_author_suffix_and_swaps_hyphens():
    assert guess_module_name("ovos-skill-weather.openvoiceos") == "ovos_skill_weather"


def test_guess_module_name_handles_no_author_suffix():
    assert guess_module_name("ovos-skill-weather") == "ovos_skill_weather"


def test_guess_module_name_handles_multiple_dots():
    """rsplit with maxsplit=1 - only the LAST dot-segment is treated as
    the author suffix, not every dot in the skill_id."""
    assert guess_module_name("ovos.skill.weather.openvoiceos") == "ovos.skill.weather"


# --- find_skill_examples(): real temp-directory package structures ---

def _fake_installed_package(tmp_path, module_name, lang_to_examples: dict):
    """Builds a real directory structure matching what an installed
    skill package actually looks like (locale/<lang>/skill.json), and
    returns a fake importlib spec pointing at it - close enough to the
    real thing that find_skill_examples()'s actual file-reading logic
    is genuinely exercised, not mocked away."""
    pkg_dir = tmp_path / module_name
    pkg_dir.mkdir()
    init_file = pkg_dir / "__init__.py"
    init_file.write_text("")
    for lang, examples in lang_to_examples.items():
        locale_dir = pkg_dir / "locale" / lang
        locale_dir.mkdir(parents=True)
        (locale_dir / "skill.json").write_text(json.dumps({"examples": examples}))
    spec = MagicMock()
    spec.origin = str(init_file)
    return spec


def test_find_skill_examples_reads_the_requested_language(tmp_path):
    spec = _fake_installed_package(tmp_path, "ovos_skill_weather", {
        "en-us": ["what's the weather?"],
        "da-dk": ["hvordan er vejret?"],
    })
    with patch("importlib.util.find_spec", return_value=spec):
        result = find_skill_examples("ovos-skill-weather.openvoiceos", lang="da-dk")
    assert result == ["hvordan er vejret?"]


def test_find_skill_examples_falls_back_to_language_prefix(tmp_path):
    """Confirmed real-world case: at least one real installed skill
    only ships a bare "da" locale directory, not "da-dk"."""
    spec = _fake_installed_package(tmp_path, "ovos_skill_alerts", {
        "da": ["sæt en alarm"],
    })
    with patch("importlib.util.find_spec", return_value=spec):
        result = find_skill_examples("ovos-skill-alerts.openvoiceos", lang="da-dk")
    assert result == ["sæt en alarm"]


def test_find_skill_examples_falls_back_to_en_us_as_last_resort(tmp_path):
    spec = _fake_installed_package(tmp_path, "ovos_skill_weather", {
        "en-us": ["what's the weather?"],
    })
    with patch("importlib.util.find_spec", return_value=spec):
        result = find_skill_examples("ovos-skill-weather.openvoiceos", lang="fr-fr")
    assert result == ["what's the weather?"]


def test_find_skill_examples_returns_empty_when_module_not_found():
    with patch("importlib.util.find_spec", return_value=None):
        assert find_skill_examples("ovos-skill-docker-only.andlo") == []


def test_find_skill_examples_returns_empty_when_no_skill_json(tmp_path):
    pkg_dir = tmp_path / "ovos_skill_nolocale"
    pkg_dir.mkdir()
    init_file = pkg_dir / "__init__.py"
    init_file.write_text("")
    spec = MagicMock()
    spec.origin = str(init_file)
    with patch("importlib.util.find_spec", return_value=spec):
        assert find_skill_examples("ovos-skill-nolocale.andlo") == []


def test_find_skill_examples_returns_empty_on_corrupt_json(tmp_path):
    pkg_dir = tmp_path / "ovos_skill_corrupt"
    (pkg_dir / "locale" / "en-us").mkdir(parents=True)
    init_file = pkg_dir / "__init__.py"
    init_file.write_text("")
    (pkg_dir / "locale" / "en-us" / "skill.json").write_text("{not valid json")
    spec = MagicMock()
    spec.origin = str(init_file)
    with patch("importlib.util.find_spec", return_value=spec):
        assert find_skill_examples("ovos-skill-corrupt.andlo") == []


def test_find_skill_examples_returns_empty_when_examples_key_missing(tmp_path):
    pkg_dir = tmp_path / "ovos_skill_noexamples"
    (pkg_dir / "locale" / "en-us").mkdir(parents=True)
    init_file = pkg_dir / "__init__.py"
    init_file.write_text("")
    (pkg_dir / "locale" / "en-us" / "skill.json").write_text(json.dumps({"skill_id": "x"}))
    spec = MagicMock()
    spec.origin = str(init_file)
    with patch("importlib.util.find_spec", return_value=spec):
        assert find_skill_examples("ovos-skill-noexamples.andlo") == []


def test_find_skill_examples_filters_non_string_entries(tmp_path):
    spec = _fake_installed_package(tmp_path, "ovos_skill_weird", {
        "en-us": ["a real example", 123, None, "another real one"],
    })
    with patch("importlib.util.find_spec", return_value=spec):
        result = find_skill_examples("ovos-skill-weird.andlo")
    assert result == ["a real example", "another real one"]


def test_find_skill_examples_never_raises_on_unexpected_import_error():
    with patch("importlib.util.find_spec", side_effect=ImportError("boom")):
        assert find_skill_examples("ovos-skill-broken.andlo") == []


# --- short_skill_name() ---

def test_short_skill_name_strips_ovos_skill_prefix_and_author_suffix():
    assert short_skill_name("ovos-skill-naptime.openvoiceos") == "naptime"


def test_short_skill_name_handles_multi_word_skill_names():
    assert short_skill_name("ovos-skill-andersen-tales.andlo") == "andersen-tales"


def test_short_skill_name_leaves_non_matching_prefixes_alone():
    """Not every skill necessarily follows the "ovos-skill-" naming
    convention - if it doesn't, this should still return something
    sensible (the author-suffix-stripped skill_id as-is) rather than
    mangling it by stripping a prefix that was never actually there."""
    assert short_skill_name("weird-custom-name.andlo") == "weird-custom-name"


def test_short_skill_name_handles_no_author_suffix():
    assert short_skill_name("ovos-skill-naptime") == "naptime"
