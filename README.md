# ovos-tui-client

A split-pane terminal UI for talking to and debugging [OpenVoiceOS](https://www.openvoiceos.org/) without a microphone or speaker - type what you'd say, read what OVOS says back, and watch exactly what's happening on the message bus while it happens.

Actively maintained. The core experience is stable today and it's a solid, working replacement for the old CLI clients - see the [comparison](#why-not-just-fix-ovos-cli-client--neon-cli-client) below. It'll keep picking up refinements and fixes, but you don't need to wait for a "1.0" to get real use out of it.

[![Tests](https://github.com/andlo/ovos-tui-client/actions/workflows/test.yml/badge.svg)](https://github.com/andlo/ovos-tui-client/actions/workflows/test.yml)
[![PyPI version](https://img.shields.io/pypi/v/ovos-tui-client.svg)](https://pypi.org/project/ovos-tui-client/)
[![ovos-cli-client](https://img.shields.io/pypi/v/ovos-cli-client.svg?label=ovos-cli-client)](https://pypi.org/project/ovos-cli-client/)

## What it looks like

```
┌──────────────────────────────────────────┐
│ Sources: [X]bus [X]skills [X]audio ...    │
│ Log Levels: [X]DEBUG [X]INFO ... Skills:.. │
│ Filter logs (free text)...                │
│ LOGS                              scroll↕ │
├───────────────────────────┬───────────────┤
│ CONVERSATION (2/3)         │ ACTIVITY (1/3)│
│ You: read me a grimm story │ 🔍 pipeline:  │
│ OVOS: Here's Cinderella... │ asking all... │
│                             │ 📥 grimm-tale │
│                             │ s: "Cindere.. │
├───────────────────────────┴───────────────┤
│ > _                                        │
└──────────────────────────────────────────┘
```

Four panes at once: raw logs, a normal back-and-forth conversation, a live simplified feed of what's happening behind the scenes, and a text input that stands in for your voice. Everything updates in real time as OVOS processes what you type.

## What it does

- **Logs** - tails every OVOS service log it can find (bus, skills, audio, voice, GUI, PHAL, etc), color-coded by source, timestamps stripped for readability, errors bolded. Filter by source, log level, free text, or a specific skill - any combination, live, without restarting anything (with nothing checked in a category everything shows; checking one or more narrows to just those). Scroll up to read something and new lines won't yank you back down.
- **Conversation** - what you typed and what OVOS said back, plus quiet status lines for everything else this tool does (service restarts, skill toggles, startup info) kept visually distinct so they don't clutter the actual conversation.
- **Activity** - a simplified, human-readable feed of what's happening on the bus right now: which skill is handling the request, wake word and speech start/stop, which fallback skill caught something nothing else understood (and whether it actually resolved anything), and for content-reading requests specifically, which providers answered and at what confidence.
- **A searchable command palette** (`Ctrl+P`) for everything else - restart a stuck service, activate or deactivate a skill, check the intent pipeline order, or toggle any log filter - all searchable by typing, with results appearing right in the conversation pane instead of popup windows. A help panel (`F1`) covers the rest of the keybindings.
- Type what you'd say and press Enter, same as talking to a real OVOS device. Up/Down arrows browse what you've typed before, like shell history.

## Why this is worth having

Testing OVOS by voice means dealing with wake-word misfires, STT mistakes, and no visibility into *why* something did or didn't happen. Typing directly and watching the activity feed skips all of that - and makes some genuinely hard-to-see things visible:

- **See which skill actually answered - and which ones tried and gave up.** Ask a factual question and watch each candidate skill respond in real time, including the ones that came back empty - useful for figuring out why you got a weird or unhelpful answer instead of a good one.
- **Catch vocabulary gaps as you find them.** Type a phrasing you'd expect to work; if nothing responds, or the wrong skill claims it, that's immediately visible instead of a silent failure you'd only notice by accident.
- **Understand fallback behavior.** When nothing matches normally, OVOS asks a chain of fallback skills whether they can help - the activity feed shows exactly which one stepped in, and whether it actually resolved anything or just apologized.
- **Check the intent pipeline order without digging through config files.** Search "pipeline" in the command palette to see every matching stage in the exact order OVOS evaluates them.
- **Restart a stuck service in two keystrokes**, without switching to another terminal.

None of this requires working audio hardware, a wake word, or STT accuracy getting in the way - just type.

## Install

```bash
pip install ovos-tui-client
```

## Usage

```bash
ovos-tui
```

Connects to `127.0.0.1:8181` by default. Options:

```bash
ovos-tui --host 192.168.1.50 --port 8181 --lang da-dk --log-dir ~/.local/state/mycroft --mycroft-conf ~/ovos/config/mycroft.conf
```

- `--log-dir`: the log directory is auto-detected against a list of
  known candidate paths (which vary by OVOS install method). If nothing
  is found, the logs pane says so - pass this to point at the right
  directory explicitly.
- `--mycroft-conf`: path to a specific `mycroft.conf` for the pipeline
  view in the command palette to read. Only needed on Docker/Podman
  installs (see below) - without it, the pipeline view may read the
  wrong file or find nothing on those installs. It won't crash, but it
  won't be accurate either.

### Docker/Podman installs

This tool runs on the host, not inside the same containers OVOS runs
in, so a couple of things need extra attention on a Docker/Podman
install:

- **Logs** usually work without any extra flags - the common volume
  mount convention lines up with what this tool already looks for
  first. If the install is configured to send logs to the container's
  own stdout instead of a file (a documented option for
  container-log-based debugging), there are no log files to find at
  all - this tool will say so clearly, and `docker logs`/`docker
  compose logs` are the right tool for that case instead.
- **Services** run as containers, not background services this tool
  can query the usual way - it detects this and says so explicitly,
  listing the running containers, rather than just showing an
  unexplained empty result. Restarting a container from here isn't
  supported yet.
- **Pipeline** - see `--mycroft-conf` above.

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
