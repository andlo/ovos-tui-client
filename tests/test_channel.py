"""channel.py: which release channel an install runs, against live-shaped
constraints files (served from a dict here, never the network)."""
import json

from ovos_tui_client import channel as ch
from ovos_tui_client import manifest as manifest_mod

CONSTRAINTS = {
    "stable": "ovos-core>=1.3.0,<1.4.0\novos-workshop>=0.1.0,<1.0.0\n",
    "testing": "# testing\novos-core[skills-essential]>=2.0.0,<3.0.0\novos-workshop>=7.0.0,<8.0.0\n",
    "alpha": "ovos-core>=2.2.4a1\novos-workshop>=7.0.0a1\n",
}
NEWEST = {"ovos-core": "3.7.2a3", "ovos-workshop": "8.1.0a2"}


def fetch_from(constraints=CONSTRAINTS):
    def fetch(url):
        for name, text in constraints.items():
            if url.endswith(f"constraints-{name}.txt"):
                return text
        return None
    return fetch


def detect(stack, declared=(None, None, None), constraints=CONSTRAINTS):
    return ch.detect(stack, declared, fetch=fetch_from(constraints), newest=NEWEST.get)


def test_testing_install_is_not_mistaken_for_alpha():
    # 2.x meets alpha's floor too, but alpha installs 3.x today
    r = detect({"ovos-core": "2.3.0", "ovos-workshop": "7.4.1"})
    assert r["matches"] == ["testing"] and r["channel"] == "testing"
    assert r["source"] == "installed versions"
    assert "older than what alpha installs now" in r["problems"]["alpha"][0]


def test_alpha_and_stable_from_versions():
    assert detect({"ovos-core": "3.7.1", "ovos-workshop": "8.0.0"})["channel"] == "alpha"
    assert detect({"ovos-core": "1.3.2", "ovos-workshop": "0.1.9"})["channel"] == "stable"


def test_declared_channel_must_agree_with_versions():
    r = detect({"ovos-core": "3.7.1"}, declared=("testing", "ovos-installer", None))
    assert r["channel"] is None and r["declared"] == "testing"
    assert "outside testing's" in r["problems"]["testing"][0]
    r = detect({"ovos-core": "2.1.0"}, declared=("testing", "ovos-installer", None))
    assert r["channel"] == "testing" and r["source"] == "ovos-installer"


def test_offline_keeps_the_declared_channel():
    r = detect({"ovos-core": "3.7.1"}, declared=("alpha", "raspOVOS", None), constraints={})
    assert r["channel"] == "alpha" and r["unreachable"] == ["stable", "testing", "alpha"]
    assert detect({"ovos-core": "3.7.1"}, constraints={})["channel"] is None


def test_versions_moving_with_the_files():
    # the same install stops matching testing once testing moves on
    moved = dict(CONSTRAINTS, testing="ovos-core>=2.5.0,<3.0.0\n")
    assert detect({"ovos-core": "2.1.0"}, constraints=moved)["channel"] is None


def test_raspovos_tag_is_a_declaration(tmp_path):
    tag = tmp_path / "tag"
    tag.write_text("alpha\n")
    assert ch.declared_channel(lambda: (None, "no ovos-installer state file"), tag) == ("alpha", "raspOVOS", None)
    tag.write_text("something-else")
    assert ch.declared_channel(lambda: (None, "why"), tag) == (None, None, "why")


def test_newest_version_skips_yanked_and_counts_prereleases():
    body = json.dumps({"releases": {"3.7.1": [{"yanked": False}], "3.8.0a1": [{"yanked": False}],
                                    "4.0.0": [{"yanked": True}], "bad": [{}]}})
    assert ch.newest_version("ovos-core", fetch=lambda url: body) == "3.8.0a1"


def test_manifest_uses_the_versions_when_nothing_is_declared(monkeypatch, tmp_path):
    monkeypatch.setattr(manifest_mod, "INSTALLER_STATE_FILE", tmp_path / "none.json")
    monkeypatch.setattr(ch, "RASPOVOS_TAG_FILE", tmp_path / "no-tag")
    monkeypatch.setattr(manifest_mod, "local_stack", lambda: {"ovos-core": "2.1.0", "ovos-workshop": "7.4.1"})
    monkeypatch.setattr(ch, "newest_version", lambda pkg, fetch=None, allowed=None: NEWEST.get(pkg))
    m = manifest_mod.build_manifest("127.0.0.1", "en-us", [], fetch=fetch_from())
    assert m["channel"] == "testing" and m["channel_source"] == "installed versions"


def test_markdown_explains_switching():
    md = ch.channel_markdown(detect({"ovos-core": "2.1.0"}), {"ovos-core": "2.1.0"})
    assert "runs **testing**" in md and "Changing channel" in md and "/opt/ovos/tag" in md
    assert "Not **alpha**" in md


def test_summary_lines():
    stack = {"ovos-core": "2.3.0"}
    short, line = ch.summary(detect({"ovos-core": "2.3.0"}), stack)
    assert short == "OVOS testing" and line.startswith("OVOS: testing · ovos-core 2.3.0 (from the installed versions")
    short, line = ch.summary(detect({"ovos-core": "0.9.0"}), {"ovos-core": "0.9.0"})
    assert short == "OVOS: not an official mix" and "not an official channel" in line
    short, line = ch.summary(detect({"ovos-core": "3.7.1"}, declared=("testing", "ovos-installer", None)),
                             {"ovos-core": "3.7.1"})
    assert short == "OVOS: not an official mix" and "ovos-installer says testing" in line
    short, line = ch.summary(detect({"ovos-core": "3.7.1"}, constraints={}), {"ovos-core": "3.7.1"})
    assert short == "OVOS: channel unknown" and "no network" in line
    assert ch.summary(None, {}, remote=True)[0] == "OVOS: channel unknown"


# --- alpha: the newest the installed core allows, not just the newest on PyPI ---

OPM_RELEASES = json.dumps({"releases": {"2.12.5a1": [{"yanked": False}], "3.0.0a1": [{"yanked": False}]}})


def test_newest_version_within_what_the_core_allows():
    assert ch.newest_version("ovos-plugin-manager", fetch=lambda url: OPM_RELEASES) == "3.0.0a1"
    assert ch.newest_version("ovos-plugin-manager", fetch=lambda url: OPM_RELEASES,
                             allowed="<3.0.0,>=2.12.0a1") == "2.12.5a1"


def test_stack_asks_only_the_core_packages(monkeypatch):
    reqs = {
        "ovos-core": ["ovos-plugin-manager<3.0.0,>=2.12.0a1", "ovos-workshop>=9.0.0a1",
                      'pytest; extra == "test"'],
        "ovos-workshop": ["ovos-plugin-manager>=2.11.0"],
        "some-old-plugin": ["ovos-plugin-manager<1.0"],    # not a core package: ignored
    }
    import importlib.metadata as md

    def fake_requires(name):
        if name not in reqs:
            raise md.PackageNotFoundError(name)
        return reqs[name]
    monkeypatch.setattr(md, "requires", fake_requires)
    assert ch.stack_asks("ovos-plugin-manager") == "<3.0.0,>=2.11.0,>=2.12.0a1"
    assert ch.stack_asks("pytest") == ""
    assert ch.stack_asks("ovos-core") == ""


def test_alpha_install_held_below_a_new_major_by_its_own_core_is_alpha(monkeypatch):
    """ovos-plugin-manager 3.0.0a1 is on PyPI, but ovos-core 3.7 asks for <3:
    a 2.12.5a1 install is what alpha installs (a Mark II, October 2026)."""
    alpha = "ovos-core>=2.2.4a1\novos-plugin-manager>=2.12.5a1\n"
    monkeypatch.setattr(ch, "stack_asks",
                        lambda pkg: "<3.0.0,>=2.12.0a1" if pkg == "ovos-plugin-manager" else "")
    pypi = {"ovos-core": json.dumps({"releases": {"3.7.2a2": [{}]}}), "ovos-plugin-manager": OPM_RELEASES}

    def fetch(url):
        if "constraints-alpha" in url:
            return alpha
        if "constraints-" in url:
            return "ovos-core>=2.1.1,<3.0.0\n"
        return next((body for pkg, body in pypi.items() if f"/{pkg}/" in url), None)
    res = ch.detect({"ovos-core": "3.7.2a2", "ovos-plugin-manager": "2.12.5a1"}, fetch=fetch)
    assert res["problems"]["alpha"] == [] and res["matches"] == ["alpha"] and res["channel"] == "alpha"
