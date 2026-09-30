# Release channels

OVOS is released in three channels. Each is one constraints file in
[OpenVoiceOS/ovos-releases](https://github.com/OpenVoiceOS/ovos-releases),
used by the OVOS installer, raspOVOS and ovos-docker alike:

| Channel | What it is |
|---|---|
| **testing** | What the OVOS installer installs by default: versions picked for testing, with upper bounds. What most users run. |
| **alpha** | The newest releases, pre-releases included. raspOVOS images and ovos-docker's default tag use it, and so do Mark II/DevKit and macOS installs. |
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

## Changing channel

Back up `~/.config/mycroft` first. Moving to an older channel downgrades
packages.

**OVOS installer.** The channel is fixed once installed: running the
installer again upgrades within it. To switch, uninstall and install again,
and pick the channel in the installer (testing or alpha):

    sudo sh -c "$(curl -fsSL https://raw.githubusercontent.com/OpenVoiceOS/ovos-installer/main/installer.sh)" installer.sh --uninstall

Uninstalling removes configuration too. Unattended installs set `channel:`
in `~/.config/ovos-installer/scenario.yaml`.

**raspOVOS.** The channel is in `/opt/ovos/tag`, and `ovos-update` updates
from it:

    echo testing | sudo tee /opt/ovos/tag
    ovos-update

`ovos-update testing` updates from testing once, without changing the tag.

**ovos-docker, and Buildroot images** (which run ovos-docker's
containers). The image tag is the channel: set `VERSION=testing` (or
`alpha`, `stable`) in the compose `.env`, then
`docker compose pull && docker compose up -d`.

**Your own venv.** Upgrade the installed OVOS packages against the
channel's constraints file (add `--pre` for alpha):

    pip install -U -c https://raw.githubusercontent.com/OpenVoiceOS/ovos-releases/main/constraints-testing.txt \
        $(pip list --format=freeze | grep -E '^ovos-' | cut -d= -f1)
