"""#62: the profile report - the same format and rules as a store's
(ovos-profile-report/1), for this device."""
from ovos_tui_client import profile_report as pr

SKILLS_J2 = """ovos-core[skills-essential,skills-internet]

{% if ovos_installer_profile != "server" %}
ovos-core[skills-audio]
{% endif %}

{% if ovos_installer_enable_ggwave and (ovos_installer_profile != "server") %}
ovos-skill-ggwave
{% endif %}
"""
EXTRA_J2 = """ovos-core[skills-media]
ovos-skill-icanhazdadjokes

#{% if ovos_installer_profile != "server" %}
#git+https://github.com/OpenVoiceOS/ovos-skill-easter-eggs.git
#{% endif %}
"""


def test_installer_templates_for_a_default_install():
    assert pr.template_lines(SKILLS_J2) == ["ovos-core[skills-essential,skills-internet]", "ovos-core[skills-audio]"]
    assert pr.template_lines(SKILLS_J2, server=True) == ["ovos-core[skills-essential,skills-internet]"]
    assert pr.template_lines(EXTRA_J2) == ["ovos-core[skills-media]", "ovos-skill-icanhazdadjokes"]


def test_expand_extras_and_git_lines():
    extras = {("ovos-core", "skills-essential"): ["ovos-skill-alerts", "ovos-skill-date-time"],
              ("ovos-core", "skills-audio"): ["ovos-skill-volume"]}
    got = pr.expand(["ovos-core[skills-essential, skills-audio]", "ovos_skill_jokes",
                     "git+https://github.com/x/ovos-skill-foo.git@dev"],
                    lambda p, e: extras.get((p, e), []))
    assert got == ["ovos-skill-alerts", "ovos-skill-date-time", "ovos-skill-volume", "ovos-skill-jokes",
                   "ovos-skill-foo"]


def test_profile_file_is_a_requirements_file():
    text = "# Klondike profile\n#   pip install -r x\n\novos-core[skills-media]\n-c c.txt\novos-skill-a  # why\n"
    assert pr.requirement_file_lines(text) == ["ovos-core[skills-media]", "ovos-skill-a"]


def test_profiles_build_on_each_other_and_add_the_pipeline():
    ids = {"ovos-skill-a": [("ovos-skill-a.openvoiceos", "skill")],
           "ovos-skill-b": [("ovos-skill-b.openvoiceos", "skill")],
           "ovos-padatious": [("ovos-padatious-pipeline-plugin", "pipeline")]}
    profiles = pr.profile_members(["ovos-skill-a"], ["ovos-skill-a", "ovos-skill-b"], ["ovos-skill-c"],
                                  ["ovos-padatious-pipeline-plugin-high", "ovos-fallback-pipeline-plugin-low"],
                                  extra_of=lambda p, e: [], ids_of=lambda p: ids.get(p, []))
    d, e, c = profiles
    assert [m[0] for m in d["members"]] == ["ovos-skill-a.openvoiceos", "ovos-padatious-pipeline-plugin",
                                            "ovos-fallback-pipeline-plugin"]
    assert [m[0] for m in e["members"]] == ["ovos-skill-b.openvoiceos"]      # a is in default already
    assert c["members"] == [("ovos-skill-c", "skill", "ovos-skill-c")]       # not installed: its name
    assert (e["builds_on"], c["builds_on"]) == ("default", "extra")


def _e(rid="s.x", kind="skill", **kw):
    args = dict(installed=True, loaded={"s.x": True}, pipeline=["p-high"], left_out=[])
    args.update(kw)
    return pr.entry(rid, kind, "1.0", **args)


def test_states_and_labels_follow_a_stores_rules():
    assert (_e(installed=False)["state"], _e(installed=False)["label"]) == ("untested", "not installed here")
    assert (_e(loaded={})["label"], _e(loaded={})["level"]) == ("✗ doesn't load", 1)
    assert _e(loaded={"s.x": False})["label"] == "✓ loads · deactivated"
    assert (_e()["state"], _e()["label"], _e()["level"]) == ("pass", "✓ loads", 2)
    full = _e(routing={"s.x": (10, 10)})
    assert (full["state"], full["label"], full["level"]) == ("pass", "✓ 10/10 golden", 3)
    assert full["golden"] == {"hit": 10, "counted": 10, "langs": ["en-US"],
                              "by_lang": {"en-US": {"hit": 10, "counted": 10}}}
    assert "this device" in full["note"] and "klondike" not in full and "gold" not in full
    assert _e(routing={"s.x": (9, 10)})["state"] == "warn"                    # routes, not all
    low = _e(routing={"s.x": (7, 10)})
    assert (low["label"], low["level"]) == ("✓ loads · 7/10 golden", 2)       # below 80 %
    assert _e(routing={"s.x": (10, 10)}, keeps_talking={"s.x"})["label"] == "✓ 10/10 golden · doesn't stop"


def test_pipeline_plugins():
    assert _e("p", "pipeline")["label"] == "✓ loads"
    assert _e("p", "pipeline", left_out=["p"])["label"] == "✗ doesn't load"
    assert _e("q", "pipeline")["label"] == "✓ installed · not in the pipeline"


def test_left_out_stages_only_since_ovos_last_started():
    lines = ["... Requested some invalid pipeline components! filtered: ['ovos-padatious-pipeline-plugin-high']",
             "... ovos-core is ready! additional skills can now be loaded",
             "... Requested some invalid pipeline components! filtered: ['ovos-m2v-pipeline-high']"]
    assert pr.left_out_stages(lines) == ["ovos-m2v-pipeline"]
    assert pr.left_out_stages(lines[:2]) == []


def test_the_report_is_an_ovos_profile_report():
    profiles = [{"id": "default", "name": "Default", "builds_on": None,
                 "members": [("s.x", "skill", "s"), ("t.x", "skill", "t")]}]
    r = pr.build_report("alpha", profiles, loaded={"s.x": True}, pipeline=[], left_out=[],
                        installed=lambda pkg: True, version_of=lambda rid, pkg: "1.0", now=0)
    assert r["schema"] == "ovos-profile-report/1" and list(r["channels"]) == ["alpha"]
    p = r["channels"]["alpha"]["profiles"][0]
    assert p["summary"] == {"total": 2, "loads": 1, "fails": 1, "not_testable": 0, "untested": 0, "routes": 0}
    assert "| s.x | skill | 2 | ✓ loads | 1.0 |" in pr.report_markdown(r)


def test_routing_from_a_run():
    from ovos_tui_client.headless import routing_from
    from ovos_tui_client.scripts import FAIL, PASS, RunSummary, ScriptStep, StepResult
    s = RunSummary(title="t", total=3)
    step = ScriptStep(utterance="u", skill_id="s.x", intent_label="i", lang="en-US")
    s.results = [(1, step, StepResult(PASS, "ok")), (2, step, StepResult(FAIL, "x")),
                 (3, step, StepResult(PASS, "ok", notes=["did not stop: s.x was still speaking 5 s after stop"]))]
    assert routing_from(s) == ({"s.x": (2, 3)}, {"s.x"})


# --- the palette ---------------------------------------------------------------------

import pytest  # noqa: E402
from unittest.mock import MagicMock, patch  # noqa: E402


@pytest.mark.asyncio
async def test_palette_profile_report_saves_and_shows_it(tmp_path):
    from ovos_tui_client.app import OVOSTUIApp
    (tmp_path / "skills.log").write_text("")
    app = OVOSTUIApp(log_dir_override=str(tmp_path))
    app.bus = MagicMock()
    app.results_dir = tmp_path / "results"
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        app.channel_result = {"channel": "alpha"}
        app.installed_skills = {"ovos-skill-a.openvoiceos": True}
        templates = {"skills-requirements.txt.j2": "ovos-skill-a\n", "extra-skills-requirements.txt.j2": ""}
        with patch("ovos_tui_client.channel.fetch_text",
                   side_effect=lambda url, *a, **k: next((t for n, t in templates.items() if url.endswith(n)), None)), \
                patch("ovos_tui_client.profile_report.runtime_ids",
                      side_effect=lambda p: [("ovos-skill-a.openvoiceos", "skill")] if p == "ovos-skill-a" else []), \
                patch("ovos_tui_client.profile_report.package_version", return_value="1.0"), \
                patch("ovos_tui_client.app.build_manifest", return_value={"channel": "alpha"}):
            app.profile_report(False)
            await app.workers.wait_for_complete()
            await pilot.pause()
        assert type(app.screen).__name__ == "TextAboutScreen"
    saved = list((tmp_path / "results").glob("*.profile-report.json"))
    assert len(saved) == 1
    import json
    rep = json.loads(saved[0].read_text())
    row = rep["channels"]["alpha"]["profiles"][0]["entries"][0]
    assert (row["runtime_id"], row["label"]) == ("ovos-skill-a.openvoiceos", "✓ loads")


@pytest.mark.asyncio
async def test_palette_profile_report_needs_the_channel(tmp_path):
    from textual.widgets import RichLog
    from ovos_tui_client.app import OVOSTUIApp
    (tmp_path / "skills.log").write_text("")
    app = OVOSTUIApp(log_dir_override=str(tmp_path))
    app.bus = MagicMock()
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        app.channel_result = None
        app.profile_report(False)
        await pilot.pause()
        text = "\n".join(str(l) for l in app.query_one("#conversation", RichLog).lines)
        assert "A profile report is per release channel" in text


# --- a last-resort fallback --------------------------------------------------------

def test_registered_fallbacks_from_the_log():
    lines = ["... register_fallback:170 - INFO - registering fallback handler -> "
             "ovos.skills.fallback.ovos-skill-ddg.openvoiceos",
             "... registering fallback handler -> ovos.skills.fallback.ovos-skill-fallback-unknown.openvoiceos",
             "... ovos-core is ready! additional skills can now be loaded"]
    assert pr.fallback_skills(lines) == {"ovos-skill-ddg.openvoiceos", "ovos-skill-fallback-unknown.openvoiceos"}


PRIORITIES = {"unknown.x": 100, "ddg.x": 90, "launcher.x": 4, "wolfie.x": 91}


def test_last_resort_is_a_fallback_whose_golden_went_to_another_fallback():
    registered = {"unknown.x", "ddg.x"}
    taken = {"unknown.x": (7, 9), "ddg.x": (0, 3), "weather.x": (9, 9)}
    # weather.x isn't a fallback skill; ddg.x routes itself
    assert pr.last_resort_fallbacks(registered, taken, PRIORITIES.get) == {"unknown.x"}


def test_last_resort_must_be_in_the_low_fallback_band():
    """#83: application-launcher (priority 4) losing all 16 to spelling is
    intent theft and stays shown; only the low band (> 90) is a last resort."""
    registered = {"unknown.x", "ddg.x", "launcher.x", "wolfie.x", "nobody-knows.x"}
    taken = {s: (9, 9) for s in registered}
    got = pr.last_resort_fallbacks(registered, taken, PRIORITIES.get)
    assert got == {"unknown.x", "wolfie.x"}          # 100 and 91; not 90, not 4, not unknown


def test_priority_from_the_skills_code():
    assert pr.priority_in_code("@fallback_handler(priority=100)\ndef handle(...)") == 100
    assert pr.priority_in_code("self.register_fallback(self.handle_fallback, 91)") == 91
    assert pr.priority_in_code("self.register_fallback(self.handle, priority=4)") == 4
    assert pr.priority_in_code("@fallback_handler(priority=50)\n@fallback_handler(priority=95)") is None
    assert pr.priority_in_code("class X(OVOSSkill): pass") is None


def test_the_mycroft_conf_override_wins(monkeypatch):
    monkeypatch.setattr(pr, "priority_override", lambda s: 95 if s == "ddg.x" else None)
    monkeypatch.setattr(pr, "skill_source", lambda s: "@fallback_handler(priority=90)")
    assert pr.fallback_priority("ddg.x") == 95
    assert pr.fallback_priority("other.x") == 90


def test_a_last_resort_fallback_is_graded_by_level_2():
    e = _e("s.x", routing={"s.x": (0, 9)}, last_resort={"s.x"})
    assert (e["state"], e["label"], e["level"]) == ("pass", "✓ loads · last-resort fallback", 2)
    assert "golden" not in e and "a fallback of last resort" in e["note"]
    # level 2 still decides: one that doesn't load still fails
    assert _e("s.x", loaded={}, last_resort={"s.x"})["label"] == "✗ doesn't load"


def test_fallback_taken_reads_the_diagnosis():
    from ovos_tui_client.headless import fallback_taken
    from ovos_tui_client.scripts import FAIL, PASS, RunSummary, ScriptStep, StepResult
    step = ScriptStep(utterance="what did", skill_id="unknown.x", intent_label="", lang="en-US")
    other = {"evidence": {"would_match": {"skill_id": "ddg.x",
                                          "intent_service": "ovos-fallback-pipeline-plugin-medium"}}}
    m2v = {"evidence": {"would_match": {"skill_id": "personal.x", "intent_service": "ovos-m2v-pipeline-high"}}}
    itself = {"evidence": {"would_match": {"skill_id": "unknown.x",
                                           "intent_service": "ovos-fallback-pipeline-plugin-low"}}}
    s = RunSummary(title="t", total=5)
    s.results = [(1, step, StepResult(FAIL, "x", diagnosis=other)),
                 (2, step, StepResult(FAIL, "x", diagnosis=m2v)),
                 (3, step, StepResult(FAIL, "x", diagnosis=itself)),     # broken, not outranked
                 (4, step, StepResult(FAIL, "x")),                       # no diagnosis: nothing known
                 (5, step, StepResult(PASS, "ok"))]
    assert fallback_taken(s) == {"unknown.x": (2, 5)}
