# ovos-tui-client

A split-pane terminal UI for talking to, debugging and testing [OpenVoiceOS](https://www.openvoiceos.org/) without a microphone or speaker - type what you'd say, read what OVOS says back, and watch exactly what's happening on the message bus while it happens.

**📖 Manual: <https://andlo.github.io/ovos-tui-client/>** - with screenshots and examples.

[![Tests](https://github.com/andlo/ovos-tui-client/actions/workflows/test.yml/badge.svg)](https://github.com/andlo/ovos-tui-client/actions/workflows/test.yml)
[![Docs](https://github.com/andlo/ovos-tui-client/actions/workflows/docs.yml/badge.svg)](https://andlo.github.io/ovos-tui-client/)
[![PyPI version](https://img.shields.io/pypi/v/ovos-tui-client.svg)](https://pypi.org/project/ovos-tui-client/)

![ovos-tui-client screenshot](https://raw.githubusercontent.com/andlo/ovos-tui-client/main/ovos-tui-client.png)

Actively maintained, and a working replacement for the old CLI clients - see [below](#why-not-just-fix-ovos-cli-client--neon-cli-client).

## Install

```bash
pip install ovos-tui-client
ovos-tui                                  # connects to 127.0.0.1:8181
ovos-tui --host 192.168.1.50 --lang da-dk
```

A container image is published on every release: `docker run -it --rm --network host ghcr.io/andlo/ovos-tui-client:latest`. `pip install ovos-tui-client[web]` and `ovos-tui --web` serve it in a browser. See [Web and Docker](https://andlo.github.io/ovos-tui-client/web-and-docker/).

## What it does

- **Logs** - every OVOS service log it can find, colour-coded, filterable by source, level, skill and free text, live.
- **Conversation** - what you typed and what OVOS said, plus what *anyone else* said to OVOS: the microphone, HiveMind clients, other ovos-tui-clients - including their test runs.
- **Activity** - a readable feed of what happens behind the scenes: which skill is handling it, which fallback caught it, which answers came back.
- **Command palette** (`Ctrl+P`) - restart services, activate/deactivate skills, show the intent pipeline, send example phrases, filter logs, clear panes - all searchable by typing.
- **Scripted test runs** - replay a skill's own golden test utterances (`Test: <Skill> - All`, or pick some with `- Choose`), or your own scripts, against your live OVOS, with a ✓/✗ per step and a summary. Catches the intent collisions an isolated skill test never sees. [Testing skills](https://andlo.github.io/ovos-tui-client/testing/)
- **Headless runs and reports** - `ovos-tui --run all|<skill>|<script>` runs the same tests from the command line (cron, CI) and can write a shareable, store-agnostic JSON report of exactly what was tested against. [Headless runs](https://andlo.github.io/ovos-tui-client/headless/)
- **Release channels** - which OVOS channel (stable, testing, alpha) the install runs, and `ovos-tui --set-channel <channel>` (or `Ctrl+P` → `OVOS: Set channel: <channel>…`) to make it exactly that channel, the way the channel's own tests install it: dry run first, never downgrades the core. [Release channels](https://andlo.github.io/ovos-tui-client/channels/)
- **About windows** - a skill's description, examples, version, source and test coverage, with test and activate/deactivate buttons; a checklist of all installed skills.

Everything is in the [manual](https://andlo.github.io/ovos-tui-client/): [getting started](https://andlo.github.io/ovos-tui-client/getting-started/), [testing](https://andlo.github.io/ovos-tui-client/testing/), [scripts](https://andlo.github.io/ovos-tui-client/scripts/), [skills](https://andlo.github.io/ovos-tui-client/skills/), [troubleshooting](https://andlo.github.io/ovos-tui-client/troubleshooting/).

## Why this is worth having

Testing OVOS by voice means dealing with wake-word misfires, STT mistakes, and no visibility into *why* something did or didn't happen. Typing directly and watching the activity feed skips all of that: see which skill actually answered and which ones gave up, catch vocabulary gaps as you find them, understand fallback behaviour, check the pipeline order without digging through config files, and re-run the same tests after every change.

## Contributing

Tests: `pip install -r requirements-test.txt && pytest`. The manual lives in `docs/` and is built with MkDocs; screenshots are generated, not hand-made - see [Maintaining these docs](https://andlo.github.io/ovos-tui-client/maintaining/).

## Why not just fix ovos-cli-client / neon-cli-client?

`ovos-cli-client` (last released March 2022) installs cleanly via pip,
but crashes immediately on launch on a fresh install:
`ModuleNotFoundError: No module named 'ovos_utils.configuration'` -
its `ovos_utils` dependency is unpinned, and the module it imports
from has since been removed/relocated in current `ovos_utils`
releases. It was never updated to match. Confirmed directly (`pip
install ovos-cli-client && ovos-cli-client`) rather than assumed.

`neon-cli-client` pulls in `neon-utils`, which pins `pyyaml~=5.4` - a
version with no prebuilt wheel for modern Python and a build script
incompatible with current `setuptools` (workaround: pin
`setuptools<58` first).

Building this tool instead avoids both dependency chains, and adds
genuinely useful features - toggleable/filterable logs, service
restart, a simplified activity feed - neither of the above has.

No existing project fills this specific niche as of writing (checked
the OpenVoiceOS GitHub org's repositories and general TUI project
listings) - if that's changed by the time you're reading this, please
open an issue and point at it.

## Category
**Development Tools**

## Tags
#ovos #tui #testing #cli #development
