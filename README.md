# ovos-tui-client

A split-pane terminal UI for testing OVOS without a mic/speaker - a
lightweight replacement for the unmaintained `ovos-cli-client` and the
broken `neon-cli-client` (whose `pyyaml~=5.4` dependency fails to build
on modern Python/setuptools).

[![Tests](https://github.com/andlo/ovos-tui-client/actions/workflows/test.yml/badge.svg)](https://github.com/andlo/ovos-tui-client/actions/workflows/test.yml)
[![PyPI version](https://img.shields.io/pypi/v/ovos-tui-client.svg)](https://pypi.org/project/ovos-tui-client/)

## Layout

```
┌──────────────────────────────────────────┐
│ LOGS (top) - toggleable per source        │
│ [x]bus [x]skills [ ]audio        scroll↕  │
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

- **Logs**: tails every recognized OVOS service log file it finds
  (`bus.log`, `skills.log`, `audio.log`/`media.log`, `voice.log`,
  `gui.log`, `enclosure.log`, `phal.log`), with a checkbox per source to
  toggle it on/off, scrollable.
- **Conversation**: what you typed and what OVOS said back, in two
  distinct colors.
- **Activity**: a curated, simplified feed of what's happening on the
  bus right now - which skill is handling the request, and for
  `ovos.common_reading.*` traffic specifically, which providers
  answered, at what confidence, and whether content fetch succeeded.
- **Input**: type what you'd say to OVOS, press Enter.

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
ovos-tui --host 192.168.1.50 --port 8181 --lang da-dk --log-dir ~/.local/state/mycroft
```

- `--log-dir`: the log directory is auto-detected against a list of
  known candidate paths (which vary by OVOS install method). If nothing
  is found, the logs pane says so - pass this to point at the right
  directory explicitly.

## Why not just fix neon-cli-client?

`neon-cli-client` pulls in `neon-utils`, which pins `pyyaml~=5.4` - a
version with no prebuilt wheel for modern Python and a build script
incompatible with current `setuptools` (workaround: pin
`setuptools<58` first). Building this tool instead avoids that whole
dependency chain (just `textual` + `ovos-bus-client`, both actively
maintained), and adds genuinely useful features - toggleable logs and
a simplified activity feed - `neon-cli-client` doesn't have.

## Category
**Development Tools**

## Tags
#ovos #tui #testing #cli #development
