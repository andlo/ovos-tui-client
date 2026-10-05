"""--set-channel's rules (#65), without pip, OVOS or the network."""
from pathlib import Path

from ovos_tui_client import setchannel as sc
from ovos_tui_client.app import build_arg_parser


HARNESS = """\
git+https://github.com/OpenVoiceOS/ovos-core@dev
git+https://github.com/OpenVoiceOS/ovos-workshop@dev
ovos-bus-client>=2.11.13a1
git+https://github.com/OpenVoiceOS/ovos-padatious-pipeline-plugin@dev
git+https://github.com/OpenVoiceOS/ovos-adapt-pipeline-plugin@dev
git+https://github.com/OpenVoiceOS/ovos-gui@dev
ovoscope>=1.6.23a1
pytest
"""
CONSTRAINTS = """\
# alpha
ovos-core>=2.2.4a1
ovos-workshop>=8.3.0a1
ovos-bus-client>=2.7.2a1
ovos-padatious>=2.2.0a1
ovos-adapt-parser>=1.0.0a1
ovos-gui>=1.0.0a1
ovoscope>=1.0.0a1
ovos-skill-date-time>=1.11.0a1
ovos-m2v-pipeline>=0.30.0a1
"""


def test_names_map_git_repos_to_distributions():
    names = sc.requirement_names(HARNESS)
    assert names[:3] == ["ovos-core", "ovos-workshop", "ovos-bus-client"]
    assert "ovos-padatious" in names and "ovos-adapt-parser" in names
    assert "ovos-padatious-pipeline-plugin" not in names


def test_pipeline_stages_to_packages():
    got = sc.pipeline_packages(["ovos-padatious-pipeline-plugin-high", "ovos-m2v-pipeline-medium",
                                "ovos-stop-pipeline-plugin-high"])
    assert got == {"ovos-padatious", "ovos-m2v-pipeline", "ovos-stop-pipeline-plugin"}


def test_plan_limits_the_stack_to_this_install():
    p = sc.plan(sc.requirement_names(CONSTRAINTS), sc.requirement_names(HARNESS),
                installed={"ovos-core", "ovos-workshop", "ovos-bus-client", "ovos-adapt-parser",
                           "ovos-skill-date-time", "ovos-m2v-pipeline", "ovoscope", "requests"},
                pipeline={"ovos-padatious", "ovos-m2v-pipeline"})
    assert "ovos-gui" not in p["stack"] and p["skipped"] == ["ovos-gui"]       # headless: no gui
    assert p["added"] == ["ovos-padatious"]                                    # pipeline asks for it
    assert "ovoscope" not in p["stack"] and "ovoscope" not in p["rest"]        # test tooling
    assert p["rest"] == ["ovos-m2v-pipeline", "ovos-skill-date-time"]          # not "requests"


def test_lock_is_the_stores_stack():
    assert sc.LOCKED_STACK == ("ovos-core", "ovos-workshop", "ovos-bus-client",
                               "ovos-plugin-manager", "ovos-config", "ovos-utils")
    lines = sc.lock_lines({"ovos_core": "3.7.2a2", "ovos-workshop": "9.8.14a1", "padacioso": "2.4.3a1"})
    assert lines == ["ovos-core==3.7.2a2", "ovos-workshop==9.8.14a1"]


def test_prereleases_nothing_asks_for_go_to_final_releases():
    dists = [
        ("huggingface_hub", "1.33.0", ["httpx<1,>=0.23.0"]),
        ("httpx", "1.0.dev6", []),
        ("ovos-workshop", "9.8.14a1", ["padacioso>=2.4.0a1", "pydantic>=2"]),
        ("padacioso", "2.4.3a1", []),
        ("pydantic", "2.14.0b2", []),
        ("ovos-tui-client", "0.3.0a1", []),            # a tool installed on purpose
        ("ovos-skill-date-time", "1.11.6a1", []),      # the channel names it
        ("lxml", "6.1.3", []),                         # a final release
    ]
    moves = {n: r for n, _v, r in sc.prerelease_moves(dists, ["ovos-skill-date-time", "ovos-workshop"])}
    assert moves["httpx"] == "httpx<1.0,<1,>=0.23.0"
    assert moves["pydantic"] == "pydantic<2.14.0,>=2"
    assert moves["padacioso"] is None                  # a dependent asks for a pre-release
    assert "ovos-tui-client" not in moves and "ovos-skill-date-time" not in moves and "lxml" not in moves
    for request in filter(None, moves.values()):       # never lets pip take a pre-release
        assert not any(sc._is_pre(s) for s in request.split(",")[1:])


def test_conflict_reason():
    log = """ERROR: Cannot install ovos-skill-homescreen because these package versions have conflicting dependencies.

The conflict is caused by:
    ovos-skill-homescreen 3.0.4a2 depends on ovos-workshop<9.0.0 and >=8.0.0
    ovos-skill-homescreen 3.0.4a2 depends on ovos-workshop<9.0.0 and >=8.0.0
    The user requested (constraint) ovos-workshop==9.8.14a1

To fix this you could try to:"""
    assert sc.conflict_reason(log) == ("ovos-skill-homescreen 3.0.4a2 depends on ovos-workshop<9.0.0 and >=8.0.0; "
                                       "The user requested (constraint) ovos-workshop==9.8.14a1")


def test_report_changes():
    rep = {"install": [{"metadata": {"name": "ovos_m2v_pipeline", "version": "0.30.0a1"}}]}
    assert sc.report_changes(rep) == {"ovos-m2v-pipeline": "0.30.0a1"}


def test_constraints_from_a_file(tmp_path: Path):
    f = tmp_path / "c.txt"
    f.write_text("ovos-core>=1\n")
    assert sc.read_source(str(f)) == "ovos-core>=1\n"
    assert sc.read_source("https://example.invalid/c.txt", fetch=lambda u: "x") == "x"


def test_cli_flags():
    a = build_arg_parser().parse_args(["--set-channel", "alpha", "--dry-run", "--constraints", "c.txt"])
    assert (a.set_channel, a.dry_run, a.constraints, a.channel) == ("alpha", True, "c.txt", None)


def test_render_dry_run():
    res = {"channel": "alpha", "dry_run": True, "constraints": "u", "work_dir": "/w",
           "changed": {"httpx": ("1.0.dev6", "0.28.1")}, "added": [], "skipped": [],
           "cannot_follow": {"ovos-skill-homescreen": "needs ovos-workshop<9"},
           "prereleases_moved": {"httpx": "1.0.dev6 -> 0.28.1"}, "prereleases_kept": {},
           "pip_check": [], "error": None}
    md = sc.render(res)
    assert "Dry run" in md and "| httpx | 1.0.dev6 | 0.28.1 |" in md and "homescreen" in md
    assert "Restart OVOS" not in md
