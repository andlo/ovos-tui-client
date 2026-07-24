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
- **Skills:** - click it, or press Enter when it's focused - opens the
  Command Palette, where "Log: Skill" filters the log display
  by skill_id (dynamically discovered the first time a skill_id is
  seen in the log text - best-effort, not every skill-related line
  mentions its own skill_id explicitly; unchecked by default, unlike
  Sources/Levels, since this list is open-ended rather than short and
  fixed). No modal for this anymore - see the Command Palette section
  below.

  **Filter semantics:** an unchecked box does NOT mean "hidden" - it
  means "not specifically filtered to". With nothing checked in a
  category, nothing in that category is restricted and everything
  shows. Checking one or more boxes restricts that category to only
  the checked ones - independently per category, applying
  retroactively to already-received lines too, same as the free-text
  filter. Choices persist across sessions (`~/.config/ovos-tui-client/
  state.json`), saved on quit.
- **F1**: toggles Textual's own built-in help panel - a genuine side
  panel docked to the right, not a popup - combining this project's
  own explanatory text (filter semantics, scroll behavior, the
  no-popup-windows design) with a live, always-accurate list of every
  current keybinding. No custom-built help screen to maintain
  separately and let go stale. **F5-F8**: jump focus straight to
  Logs / Conversation / Activity / the utterance input. There's no
  F2/Services, F3/Skills, or F4/Skill-filter shortcut anymore -
  service management, installed-skill listing/activation, AND the
  log-display skill filter all moved entirely into the Command
  Palette (below), with results written to the **conversation pane**
  (dim/grey text) instead of a popup - this tool avoids modal windows
  wherever an action doesn't inherently need its own screen.
- **Ctrl+P**: Textual's command palette - meant as a way to talk to/
  control OVOS directly ("bagom"/behind the scenes), not just a
  launcher for this tool's own popup screens. Every F1/F5-F8 action is
  there too, fuzzy-searchable, but it also goes further. The palette
  itself has no native grouping/submenus (confirmed - it's a flat
  fuzzy-matched list in every command-palette implementation, not just
  Textual's), so related commands share a literal prefix instead, so
  typing that prefix clusters them together - and every action below
  runs immediately, filtered in place as you type, with its result
  written to the conversation pane - **no popup windows**:
  - **`Log: `** - toggle any source/level/skill directly, e.g. "Log:
    Source: skills", "Log: Level: ERROR", "Log:
    Skill: ovos-skill-horoscope..." - same effect as clicking the
    checkbox, without leaving the palette. Once at least one skill_id
    has been seen, "Log: Select all skills" / "Log: Deselect all
    skills" are also available for bulk toggling.
  - **`Service: `** - "Service: Restart ovos-core.service", "Service:
    Stop ...", "Service: Start ..." - one hit per discovered
    `ovos-*.service` unit, but **only for actions that make sense for
    its current state**: a running service offers Stop/Restart but
    not Start; a stopped one offers only Start (determined from
    `systemctl`'s own ACTIVE column). Fuzzy-matched as you type (e.g.
    "restart co" narrows straight to `ovos-core.service`). Selecting
    one runs it immediately; the result ("ovos-core.service:
    restarted", or the failure reason) appears in the conversation
    pane.
  - **`Skill: `** - one entry per known skill, current state shown in
    the title - "Skill: skill_id (Active)" or "Skill: skill_id
    (Inactive)" - selecting it toggles (Deactivate if currently
    Active, Activate if currently Inactive), same "state in title,
    selecting toggles" convention as `Log:` above. An unknown state
    shows both Activate and Deactivate explicitly instead, since
    there's no current state to toggle from. The list is populated
    once at startup via the bus (`skillmanager.list` - confirmed
    directly against a live OVOS instance that its response really
    does carry per-skill active state, not just names), not refetched
    per keystroke. Fire-and-forget (see `bus.py`'s honesty note on
    `activate_skill()`/`deactivate_skill()` - based on the documented
    mycroft-core convention) - the conversation-pane line confirms the
    request was *sent*, not that OVOS applied it; the local cache
    updates optimistically so the next search reflects the change
    immediately. No separate "list installed skills" command anymore -
    typing "skill" already shows every one of them.
  - **`Pipeline: `** - one entry per stage in `mycroft.conf`'s
    `intents.pipeline` array, numbered in order - "Pipeline: 1.
    stop_high", "Pipeline: 2. ovos-common-reading-pipeline-plugin",
    etc, via `ovos-config` (respects config layering). Read-only -
    pipeline order is static config, not something with a live bus
    toggle; selecting an entry just confirms which stage you found.
  - Textual's own default **"Screenshot"** and **"Keys"** commands are
    filtered out - Screenshot isn't useful for this tool; Keys is
    Textual's own default trigger for exactly the same show-help-panel
    action "Help: Toggle panel" already calls, so keeping both would
    just be two entries doing the same thing.
  **Tab/Shift+Tab**: cycle focus across everything (checkboxes, panes,
  input) - a Textual built-in, no custom code needed. **Escape**:
  closes whatever modal is open. Every palette entry is a single line
  - no separate description text underneath; where a command has
  state worth showing (checked/unchecked, active/inactive), it's
  embedded right in the title instead.
- Typing a plain character while focus is on Logs/Conversation/
  Activity (none of which are normally typable) redirects that
  keystroke to the utterance input instead of doing nothing - almost
  always what was actually meant.
- **Conversation**: what you typed (green), what OVOS said back
  (blue), and dim/grey status lines for everything above (startup
  connection info, service actions, skill list/activate/deactivate
  results) - distinct styling so status lines don't compete for
  attention with the actual conversation. Startup itself narrates as
  a compact boot sequence - version number, which log sources were
  found, a "Services:" block listing each `ovos-*.service` unit and
  whether it's Active/Inactive, and a skill count split into
  active/inactive - ending in "OK ready." only once both of the
  async/background startup steps (service-state check, skill lookup)
  have genuinely finished, not just been kicked off, so it doesn't
  appear before its own result does. The UI itself is
  interactive from the moment those steps are kicked off, not after -
  the service-state check in particular runs on a background thread
  precisely so a slow `systemctl` call can never freeze the whole app.
- **Activity**: a curated, simplified feed of what's happening on the
  bus right now - which skill is handling the request, wake word/
  speech start-stop, global stop, which fallback skill handled a
  genuinely unrecognized utterance (and whether it actually resolved
  anything - "could not resolve" for the common "sorry, I don't
  understand" case), and for `ovos.common_reading.*` traffic
  specifically, which providers answered, at what confidence, and
  whether content fetch succeeded.
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
ovos-tui --host 192.168.1.50 --port 8181 --lang da-dk --log-dir ~/.local/state/mycroft --mycroft-conf ~/ovos/config/mycroft.conf
```

- `--log-dir`: the log directory is auto-detected against a list of
  known candidate paths (which vary by OVOS install method). If nothing
  is found, the logs pane says so - pass this to point at the right
  directory explicitly.
- `--mycroft-conf`: path to a specific `mycroft.conf` for the `Pipeline: `
  palette entries to read. Only needed on Docker/Podman installs
  (`ovos-installer`'s "containers" method, or `ovos-docker` directly) -
  confirmed via `ovos-docker`'s own compose files that the real, live
  config commonly lives at a host path like `~/ovos/config/mycroft.conf`
  (configurable per-install), not the standard
  `~/.config/mycroft/mycroft.conf` `ovos-config` looks for by default.
  Without this, `Pipeline: ` may read the wrong file or find nothing on
  a Docker install - it won't crash, but it won't be accurate either.
  This direct-read path tolerates the same JSON5-style `//` comments a
  real `mycroft.conf` has, but skips `ovos-config`'s own config-layering
  (system/user/web-cache merge) - a reasonable trade-off for a quick
  lookup.

### Docker/Podman installs

`ovos-tui-client` itself runs on the host, not inside the same
containers, so a few things behave differently on a Docker/Podman
install (confirmed against `ovos-docker`'s own documentation and a
locally-run container, not just inferred):

- **Logs**: usually fine without any extra flags - `ovos-docker`
  installs commonly volume-mount the same conventional
  `~/.local/state/mycroft` path this tool already checks first, and
  the official `ovos-logs` debugging tool relies on that same
  assumption. If the install was configured with `"logs": {"path":
  "stdout"}` (a documented `ovos-docker` recommendation for
  container-log-based debugging) instead, there are no log FILES at
  all - this tool will correctly report "no logs found", and
  `docker compose logs` / `docker logs <container>` are the right
  tools for that case instead.
- **Services**: `systemctl --user` genuinely has nothing to find,
  since OVOS runs as containers, not systemd units, in this mode. The
  boot sequence detects this (checking `docker ps` / `podman ps` for
  OVOS-named containers) and says so explicitly rather than showing a
  bare, unexplained "none found" - but starting/stopping/restarting a
  container from here isn't supported yet (see the open issue for
  this).
- **Pipeline**: see `--mycroft-conf` above.

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
