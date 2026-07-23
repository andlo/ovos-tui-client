# ovos-tui-client

**🚧 Actively under development - expect frequent changes.** This is a
young project with ongoing design iteration (see git history/tags for
the pace of change); pin a specific version if you need stability, or
just `pip install --upgrade` regularly to stay current.

A split-pane terminal UI for testing OVOS without a mic/speaker - a
lightweight replacement for the unmaintained `ovos-cli-client` and the
broken `neon-cli-client` (whose `pyyaml~=5.4` dependency fails to build
on modern Python/setuptools).

[![Tests](https://github.com/andlo/ovos-tui-client/actions/workflows/test.yml/badge.svg)](https://github.com/andlo/ovos-tui-client/actions/workflows/test.yml)
[![PyPI version](https://img.shields.io/pypi/v/ovos-tui-client.svg)](https://pypi.org/project/ovos-tui-client/)
[![ovos-cli-client](https://img.shields.io/pypi/v/ovos-cli-client.svg?label=ovos-cli-client)](https://pypi.org/project/ovos-cli-client/)

## Layout

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

- **Logs**: tails every recognized OVOS service log file it finds
  (`bus.log`, `skills.log`, `audio.log`/`media.log`, `voice.log`,
  `gui.log`, `enclosure.log`, `phal.log`), each with its own color.
  OVOS's own `TIMESTAMP - COMPONENT - ` prefix is stripped, and every
  `[source]` tag is padded to the same width so message text lines up
  in one column. Lines containing `ERROR` are bolded. Scrolled-up panes
  (logs, conversation, activity) are never yanked back to the bottom by
  incoming content - auto-scroll only re-engages once you're back at
  the bottom yourself.
- **Sources** and **Log Levels** (DEBUG/INFO/WARNING/ERROR/CRITICAL)
  are compact checkboxes directly in the main view, checked by
  default, no modal needed - each category on its own single line.
- **Skills:** - click it, or press **F4** - opens a panel to filter by
  skill (dynamically discovered the first time a skill_id is seen in
  the log text - best-effort, not every skill-related line mentions
  its own skill_id explicitly; unchecked by default, unlike Sources/
  Levels, since this list is open-ended rather than short and fixed).
  Kept in a modal rather than inline for that reason.

  **Filter semantics:** an unchecked box does NOT mean "hidden" - it
  means "not specifically filtered to". With nothing checked in a
  category, nothing in that category is restricted and everything
  shows. Checking one or more boxes restricts that category to only
  the checked ones - independently per category, applying
  retroactively to already-received lines too, same as the free-text
  filter. Choices persist across sessions (`~/.config/ovos-tui-client/
  state.json`), saved on quit.
- **F1**: keybinding reference. **F2**: services panel (restart an
  `ovos-*.service` unit). **F3**: currently loaded skills (from the
  bus). **F5-F8**: jump focus straight to Logs / Conversation /
  Activity / the utterance input. **Ctrl+P**: Textual's built-in
  command palette - every action above is also there, fuzzy-searchable,
  for anyone who'd rather type a command than remember an F-key.
  **Tab/Shift+Tab**: cycle focus across everything (checkboxes, panes,
  input) - a Textual built-in, no custom code needed. **Escape**:
  closes whatever modal is open.
- Typing a plain character while focus is on Logs/Conversation/
  Activity (none of which are normally typable) redirects that
  keystroke to the utterance input instead of doing nothing - almost
  always what was actually meant.
- **Conversation**: what you typed (green, full line) and what OVOS
  said back (blue, full line).
- **Activity**: a curated, simplified feed of what's happening on the
  bus right now - which skill is handling the request, wake word/
  speech start-stop, global stop, and for `ovos.common_reading.*`
  traffic specifically, which providers answered, at what confidence,
  and whether content fetch succeeded.
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

Building this tool instead avoids both dependency chains (just
`textual` + `ovos-bus-client`, both actively maintained), and adds
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
