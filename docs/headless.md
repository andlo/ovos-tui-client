# Headless test runs and reports

The same tests you run from the palette ([Testing skills](testing.md)) can
run **without the terminal UI**, from the command line. Use it for a
scheduled run (cron, a systemd timer), for CI, or to make a **report** you
can share: with a skill's maintainer, in an issue, or with a skill store.

```bash
ovos-tui --run all
ovos-tui --run ovos-skill-weather.openvoiceos
ovos-tui --run ~/.config/ovos-tui-client/scripts/smoke.jsonl
```

!!! warning "These are real utterances"
    Exactly as in the UI: the sentences go to your live OVOS, so timers,
    alarms and media really happen. Every step runs in its own session,
    and that session is stopped afterwards.

## What it does

1. Connects to the messagebus (`--host`, `--port`, default `127.0.0.1:8181`).
2. Waits for OVOS to list its skills, asking again a few times if it is
   busy (same as the UI). If it never answers, nothing is tested.
3. Looks up the golden utterances for what you asked for, in your
   `--lang`: `all` means every installed, active skill; a `skill_id` means
   that skill; a `.jsonl`/`.txt` file is one of [your own scripts](scripts.md).
4. Runs every step and prints one line each:

    ```
    [1/4] ✓ "what's the weather"  ovos-skill-weather.openvoiceos:weather.intent
    [2/4] ✗ "will it rain"  expected ovos-skill-weather.openvoiceos, got ovos-skill-wolfie.openvoiceos
    Test: ovos-skill-weather.openvoiceos: 3/4 passed · 1 failed · 21s
    ```

5. Saves the result, like **Test: Save last result**: a readable `.md`,
   the `.jsonl` rows, and a `.manifest.json` saying what was tested against.

**Exit code:** `0` every checked step passed, `1` something failed or timed
out (or the run was stopped with Ctrl+C), `2` it could not run: no bus, no
skill list, or nothing to test.

## Options

| Option | |
|---|---|
| `--run TARGET` | `all`, a skill_id, or a script file. |
| `--lang` | The language of the utterances (default `en-us`). |
| `--output DIR` | Where the `.md`, `.jsonl` and `.manifest.json` go (default `~/.local/share/ovos-tui-client/results`). |
| `--report FILE` | Also write one shareable report (below). `-` prints it, for copy-paste; progress then goes to stderr. |
| `--report-replies` | Include what OVOS said in the report. Off by default. |
| `--channel NAME` | The release channel of this install, if it can't be worked out automatically (below). |
| `--notes TEXT` | Free text for the report, e.g. what the skill needs: `"OpenWeather API key set"`, `"Mark II"`. |
| `--submit-url TEMPLATE` | Print a link to submit the report (below). |
| `--golden-dir DIR` | Local skill checkouts to take golden utterances from, before GitHub. |

## What was tested against: the manifest

A result is only useful if it says exactly which install produced it. The
manifest records:

- **channel:** the OVOS release channel (`stable`, `testing`, `alpha`) and
  how it was found (`channel_source`). What the install declares (the OVOS
  installer's state file, raspOVOS's `/opt/ovos/tag`) is checked against the
  installed versions and each channel's constraints file as it is today;
  with nothing declared, as on Docker, Buildroot or a hand-made venv, the
  versions alone decide. `--channel` overrides it. See
  [Release channels](channels.md).
- **stack:** the versions of the packages that decide how skills load and
  where an utterance goes: ovos-core, ovos-workshop, ovos-bus-client,
  ovos-plugin-manager, the intent engines, the pipeline plugins.
- **skills:** each tested skill's package and version.
- **config:** `lang`, `secondary_langs`, the intent pipeline order, and the STT and TTS plugin names (only the names: a plugin's own settings can hold keys or server addresses, so they are never read into the report).
- **machine:** architecture, and the board model when there is one
  (`Raspberry Pi 5`, a Mark II ...).
- when, and which ovos-tui-client version.

!!! note "Run it on the device itself"
    The versions are read from the Python environment ovos-tui-client runs
    in. The OVOS installer puts ovos-tui-client in the same environment as
    OVOS, so on the device they are OVOS's own. Pointed at a bus on another
    machine (`--host`), they would describe the wrong install, so they are
    left out and the manifest says `"versions_from": "unavailable"`.

## The shareable report

`--report` writes one JSON document, `ovos-test-report/1`, with the manifest,
a summary and one row per step:

```json
{
  "schema": "ovos-test-report/1",
  "title": "Test: ovos-skill-weather.openvoiceos",
  "manifest": {
    "channel": "testing", "channel_source": "ovos-installer", "bus": "local",
    "stack": { "ovos-core": "2.1.0", "ovos-workshop": "7.0.6", "...": "..." },
    "skills": { "ovos-skill-weather.openvoiceos": { "package": "ovos-skill-weather", "version": "1.2.0" } },
    "config": { "lang": "en-us", "secondary_langs": ["da-dk"], "pipeline": ["..."],
                "stt": "ovos-stt-plugin-server", "tts": "ovos-tts-plugin-piper" },
    "machine": { "arch": "aarch64", "model": "Raspberry Pi 5 Model B Rev 1.0" },
    "created_at": "2026-10-04T18:12:00Z", "tool": "ovos-tui-client 0.3.0"
  },
  "summary": { "steps": 4, "checked": 4, "passed": 3, "failed": 1, "answered": 4, "...": "..." },
  "steps": [
    { "i": 1, "utterance": "what's the weather", "lang": "en-us",
      "expected": "ovos-skill-weather.openvoiceos:weather.intent", "status": "pass",
      "handled_by": "ovos-skill-weather.openvoiceos:weather.intent", "answered": true }
  ],
  "notes": "OpenWeather API key set"
}
```

**Private by default.** The report holds no hostname, IP address, user name
or home path, and nothing from any skill's settings, where API keys live.
OVOS's replies are left out too, because they can contain personal data
("it's 14 degrees in *your town*"); the report only says whether OVOS
answered. Add them with `--report-replies` for a report you keep yourself.
Read the report before you share it; it is plain JSON.

## Sending a report somewhere

ovos-tui-client does not know about any skill store or service, and sends
nothing anywhere by itself. You decide where a report goes:

- **Copy-paste:** `--report -` prints it; paste it where you want it.
- **A file:** `--report report.json`, then attach or upload it.
- **A link:** if a store or project tells you to, give its link template
  with `--submit-url`, or set it once in `~/.config/ovos-tui-client/config.json`:

    ```json
    { "submit_url": "https://example.org/submit?skill={skill_id}&report={report}" }
    ```

    After the run, ovos-tui-client prints the link with the placeholders
    filled in (each URL-encoded): `{report}` the report, `{skill_id}` the
    tested skill (when there is one), `{channel}`, `{title}`. You open the
    link yourself. If the report is too long for a link (about 8 KB),
    it says so and you paste the report instead.

A report can also be made from the TUI after an ordinary run: `Ctrl+P` →
`Test: Create shareable report`. See [Testing skills](testing.md#share-the-result-as-a-report).

## Scheduled runs

A nightly run of everything, keeping the results, for example with cron:

```cron
30 3 * * *  ~/.venvs/ovos/bin/ovos-tui --run all --output ~/ovos-test-results >> ~/ovos-test-results/cron.log 2>&1
```

The exit code tells a timer or CI job whether everything passed.
