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
│ Sources: [ ]bus [ ]skills [ ]audio ...    │
│ Log Levels: [ ]DEBUG [ ]INFO ... Skills:0/0│
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

- **Logs**: tails every recognized OVOS service log file it finds
  (`bus.log`, `skills.log`, `audio.log`/`media.log`, `voice.log`,
  `gui.log`, `enclosure.log`, `phal.log`), each with its own color.
  OVOS's own `TIMESTAMP - COMPONENT - ` prefix is stripped, and every
  `[source]` tag is padded to the same width so message text lines up
  in one column. Lines containing `ERROR` are bolded.
- **Sources** and **Log Levels** (DEBUG/INFO/WARNING/ERROR/CRITICAL)
  are compact checkboxes directly in the main view, no modal needed -
  each category on its own single line.
- **F4**: opens a panel to filter by **skill** (dynamically discovered
  the first time a skill_id is seen in the log text - best-effort, not
  every skill-related line mentions its own skill_id explicitly). Kept
  in a modal rather than inline since this list can grow arbitrarily
  long as more skills are discovered, unlike sources/levels which are
  short and fixed.

  **Filter semantics - unchecked-by-default, checking narrows:**
  an unchecked box does NOT mean "hidden" - it means "not specifically
  filtered to". With nothing checked in a category, nothing in that
  category is restricted and everything shows (the default, fully-open
  state). Checking one or more boxes restricts that category to only
  the checked ones. Applies independently per category (source/level/
  skill), and applies retroactively to already-received lines, not
  just new ones, same as the free-text filter.

  This filtering UI has gone through several designs based on real
  user feedback - see git history for the always-checked full rows,
  then oversized-checkbox modals, then the checked=shown inversion.
- **F2**: opens a services panel listing discovered `ovos-*.service`
  systemd --user units - select one and press Enter to restart it.
- **F3**: opens a panel listing currently loaded skills (requested via
  the bus).
- **Conversation**: what you typed (green, full line) and what OVOS
  said back (blue, full line), auto-scrolling to the newest message.
- **Activity**: a curated, simplified feed of what's happening on the
  bus right now - which skill is handling the request, and for
  `ovos.common_reading.*` traffic specifically, which providers
  answered, at what confidence, and whether content fetch succeeded.
- **Input**: type what you'd say to OVOS, press Enter. Up/Down arrows
  browse previously submitted utterances, shell-history style.

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
