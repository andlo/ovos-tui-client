# Release channels

OVOS is released in three channels. Each is one constraints file in
[OpenVoiceOS/OpenVoiceOS](https://github.com/OpenVoiceOS/OpenVoiceOS)
(formerly `ovos-releases`), used by the OVOS installer, raspOVOS and
ovos-docker alike:

| Channel | What it is |
|---|---|
| **testing** | What the OVOS installer installs by default: versions picked for testing, with upper bounds. What most users run. |
| **alpha** | The newest OVOS releases: minimum versions for OVOS's own packages, usually pre-releases. Everything else stays on final releases unless an OVOS package asks for more. raspOVOS images and ovos-docker's default tag use it, and so do Mark II/DevKit and macOS installs. |
| **stable** | The last stable release. Changes rarely. The installer doesn't offer it. |

## Which one am I running?

The startup lines say it, e.g. `OVOS: testing · ovos-core 2.1.1 (from the
installed versions)`, and so does the header (`OVOS testing`). When the
installed versions are not what any channel installs today, a hand-made
mix or an older install, it says `not an official channel` instead of
guessing. `Ctrl+P` → **`OVOS: Release channel`** shows the details.

Not every install is made by the OVOS installer, and the installer's own
state file is often not readable by the user OVOS runs as, so
ovos-tui-client works the channel out in two ways:

- **Declared:** what the install says it follows: the OVOS installer's
  state file (`~/.local/state/ovos/installer.json`) or raspOVOS's
  `/opt/ovos/tag`.
- **From the versions:** which channel's constraints file, as it is
  today, allows the installed ovos-core, ovos-workshop, ovos-padatious,
  ovos-bus-client and ovos-plugin-manager. The files change over time, so
  they are fetched live, never kept in ovos-tui-client. alpha only sets
  lower bounds, so for alpha the installed major version must also be the
  newest one alpha can install: the newest on PyPI that the installed core
  packages themselves allow (ovos-core asking for
  `ovos-plugin-manager<3.0.0` means a 3.0.0 alpha on PyPI isn't on alpha
  yet).

A declared channel must agree with the versions; if it doesn't, the window
shows both instead of guessing. Without network, a declared channel is
used as it is.

The window lists the core versions, and for each other channel why the
install isn't on it.

The channel is worked out from the core. An install can have the
channel's core and still not be the channel: other packages below the
channel's versions, betas of third-party libraries, plugins that conflict
with the core. So right after the channel, ovos-tui checks how clean the
install is on it, without changing anything and in seconds:

- **behind:** packages the channel names whose installed version it
  doesn't allow
- **pre-releases:** pre-releases the channel doesn't name that nothing
  asks for
- **conflicts:** what `pip check` reports

The header then says e.g. `OVOS alpha · 3 conflicts` (just `OVOS alpha`
when it's clean), the startup lines say what's not quite right, and the
'OVOS: Release channel' window lists each package. A saved test report
carries the same (`channel_health` in its manifest), so whoever reads it
can tell a failure on a clean channel from one on a drifted install. The
next section is how to fix it; conflicts that come from a plugin's own
upper bound can't be fixed from the device.

## A clean install on a channel

An install is not always exactly the channel it was made for. The OVOS
installer resolves in separate batches, and on alpha it allows
pre-releases for everything, so a fresh alpha install can have packages
below alpha's versions and third-party betas (httpx 1.0.dev6 once broke
huggingface_hub, so the intent pipeline didn't load and the device
answered nothing:
[ovos-installer#635](https://github.com/OpenVoiceOS/ovos-installer/issues/635)).

A clean channel follows the rules the channel's own tests use
([ovos-test-harness](https://github.com/OpenVoiceOS/ovos-test-harness)'s
channel install):

1. Every OVOS package the channel names is installed **by name** under
   the channel's constraints, fetched live: `pip install -c
   <constraints> <package>`.
2. **No `--pre`.** A constraint line that names a pre-release already
   lets pip take it; `--pre` makes pip take pre-releases of *everything*,
   which is how third-party betas get in.
3. **The core decides.** ovos-core, ovos-workshop, ovos-bus-client,
   ovos-plugin-manager, ovos-config and ovos-utils stay at the channel's
   versions. A plugin that only installs by moving them doesn't fit the
   channel; it is left out, not forced in.
4. **Pre-releases only where asked for.** On an installed device,
   pre-releases the channel doesn't name that came in as dependencies go
   to the newest final release, unless something asks for the
   pre-release.

ovos-tui-client does this for the install it runs in (the OVOS venv, as
the user that owns it). On the device:

    ovos-tui --set-channel testing --dry-run    # what it would change (exit code 1 if anything)
    ovos-tui --set-channel testing              # do it; or alpha, stable

or in the TUI: the **`OVOS: Release channel`** window has a button per
channel (**Set channel: testing…** etc.), and `Ctrl+P` →
**`OVOS: Set channel: <channel>…`** does the same. Either shows the dry
run first; when something would change it asks before applying it, and
then offers to restart the OVOS services. When nothing would change, it
says so: the install already is the channel, and there is nothing to
apply.

It reports what changed, what can't follow the channel (with pip's
reason), the pre-releases kept and `pip check`, and never downgrades the
core. Restart OVOS afterwards. Running it again keeps the install on the
channel as the channel moves. Logs, the constraints used, the core lock
and a `pip freeze` from before and after are kept in
`~/.cache/ovos-tui-client/set-channel/<channel>/<time>/`; to add a package
later without moving the core, install it under the same two files:

    cd ~/.cache/ovos-tui-client/set-channel/<channel>/<time>
    pip install -c constraints.txt -c lock.txt <package>

`--constraints FILE|URL` uses another constraints file than the channel's
own, e.g. to try a pending change to OpenVoiceOS/OpenVoiceOS on a real
device before it is merged.

## Changing channel

Back up `~/.config/mycroft` first. Moving to an older channel downgrades
packages.

**OVOS installer.** The channel is fixed once installed: running the
installer again upgrades within it. To switch, uninstall and install again,
and pick the channel in the installer (testing or alpha):

    sudo sh -c "$(curl -fsSL https://raw.githubusercontent.com/OpenVoiceOS/ovos-installer/main/installer.sh)" installer.sh --uninstall

Uninstalling removes configuration too. Unattended installs set `channel:`
in `~/.config/ovos-installer/scenario.yaml`. Afterwards, make it a
[clean install](#a-clean-install-on-a-channel).

**raspOVOS.** The channel is in `/opt/ovos/tag`, and `ovos-update` updates
from it:

    echo testing | sudo tee /opt/ovos/tag
    ovos-update

`ovos-update testing` updates from testing once, without changing the tag.

**ovos-docker, and Buildroot images** (which run ovos-docker's
containers). The image tag is the channel: set `VERSION=testing` (or
`alpha`, `stable`) in the compose `.env`, then
`docker compose pull && docker compose up -d`.

**Your own venv.** Install what you want under the channel's constraints,
without `--pre`:

    pip install -c https://raw.githubusercontent.com/OpenVoiceOS/OpenVoiceOS/main/constraints-testing.txt ovos-core ...

then run `ovos-tui --set-channel <channel>` as above: it moves
everything the channel names onto the channel and locks the core. Don't upgrade with
`pip install -U` of every `ovos-*` package at once: a plugin with an old
upper bound can pull ovos-core back a major version, and the name filter
misses packages like `padacioso` or `skill-*`.
