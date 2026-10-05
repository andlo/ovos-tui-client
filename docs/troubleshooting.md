# Troubleshooting

## Logs

**"Logs found and loaded: none".** The log folder differs between
install methods. Point at it with `--log-dir`, e.g.
`--log-dir ~/.local/state/mycroft`. On Docker/Podman installs the logs
may only exist as container output - see
[Web and Docker](web-and-docker.md#dockerpodman-installs).

**No `bus` source.** On some installs `ovos-messagebus` logs to stdout
or the systemd journal instead of a file. That's the install, not this
tool.

## Tests

**A step fails with "expected …, got ovos-common-query-pipeline…" or a
reading plugin.** A pipeline plugin earlier in the pipeline matched the
sentence before the skill got it. Type `pipeline` in the palette to see
the order. That is a real finding about your install, not a test
error.

**Every step reports the same skill, "captured by its pending get_response".** A skill is
waiting for an answer (`get_response()`) and swallows everything. Each
test step uses its own session, which avoids most of this, but a skill
stuck in the default session can still capture what you type yourself.
Say "stop", or restart `ovos-core` from the palette (`service`).

**Steps time out.** Is the skill active? Is `ovos-core` running? A
skill that takes more than 30 seconds to answer (slow web lookups) will
also time out.

**`Test: <Skill>` is missing from the palette.** The skill has no
golden utterances for your `--lang` on GitHub and no `skill.json`
examples, or the TUI can't find its repository. Use `--golden-dir` with
a local checkout.

**Something the test started is still going.** The TUI sends `stop` to
every session the run used when it finishes. A skill that doesn't
implement stop, or a timer or alarm (which stop doesn't cancel), keeps
going. Cancel it by typing, e.g. "cancel all timers".

## Skills

**Activate/deactivate says "OVOS reports it is still …".** Two seconds
after the request, OVOS still lists the skill in its old state. The
skill may have failed to load or unload; check the `skills` log. "couldn't
confirm the change" means OVOS didn't answer the skill-list request at
all.

**The skills list is empty on Docker.** On a one-container-per-skill
install, `skillmanager.list` only reports skills in the same container.
See [Web and Docker](web-and-docker.md#dockerpodman-installs).

## Services

**`Services: none found`.** ovos-tui looks for `ovos-*` systemd user units
first and, when there are none, system units. An install with neither runs
OVOS some other way: in containers (then it says so), or by hand.

**`… is a system service and sudo wants a password`.** OVOS runs as system
units here (some OVOS installer setups, e.g. a Mark II), and starting,
stopping or restarting those needs root. ovos-tui uses `sudo -n` so a
password prompt can never appear inside it; run the command the message
shows, or allow the OVOS user to run `systemctl` for the `ovos-*` units
without a password.

## Reporting a problem

Open an issue on
[GitHub](https://github.com/andlo/ovos-tui-client/issues) with the
version from `About: ovos-tui-client`, your ovos-core version and what
the conversation and activity panes showed.
