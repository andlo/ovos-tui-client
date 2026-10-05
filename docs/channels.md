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
  newest one on PyPI.

A declared channel must agree with the versions; if it doesn't, the window
shows both instead of guessing. Without network, a declared channel is
used as it is.

The window lists the core versions, and for each other channel why the
install isn't on it.

Only the core is compared. An install can have the channel's core and
still not be the channel: other packages below the channel's versions,
betas of third-party libraries, plugins that conflict with the core. See
the next section.

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

[`device_setup.sh`](https://github.com/andlo/ovos-klondike-mercantile/blob/main/scripts/compat/device_setup.sh)
does this for an installed device, in its OVOS venv, as the user that
owns it:

    curl -fsSLO https://raw.githubusercontent.com/andlo/ovos-klondike-mercantile/main/scripts/compat/device_setup.sh
    bash device_setup.sh testing        # or alpha, stable

It reports what it changed, what can't follow the channel, and
`pip check`, and never downgrades the core. Restart OVOS afterwards.
Running it again keeps the device on the channel as the channel moves.
To add a package later, install it under the same constraints and the
core lock the script leaves:

    cd ~/.cache/klondike-device/<channel>
    ~/.venvs/ovos/bin/pip install -c constraints.txt -c lock.txt <package>

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

then run `device_setup.sh <channel>` as above: it moves everything the
channel names onto the channel and locks the core. Don't upgrade with
`pip install -U` of every `ovos-*` package at once: a plugin with an old
upper bound can pull ovos-core back a major version, and the name filter
misses packages like `padacioso` or `skill-*`.
