# Your own scripts

A script is a file with sentences to send to OVOS, one after the other,
exactly like a [skill test](testing.md). Use scripts for your own
phrasings, for a fixed set to run before a release, or to combine
several skills' tests into one run.

## Where they go

Put scripts in `~/.config/ovos-tui-client/scripts/`, or in the folder
given with `--scripts-dir`. `Ctrl+P` → **`Script: Where do scripts go?`**
prints the folder and a short format reminder.

Every file there shows up in the palette as **`Script: <file name>`**.
New files appear without a restart.

## Plain text: `*.txt`

One sentence per line. Lines starting with `#` are comments.

```text
# morning check
what time is it
what's the weather like
read me the news
```

The sentences are sent and OVOS's replies shown. There is nothing to
check, so each step ends with `→` and whatever handled it, e.g.
`→ ovos-skill-date-time.openvoiceos:what_time_is_it`.

## With checks: `*.jsonl`

One JSON object per line, in the same format as the skills' golden
files. `skill_id` and `intent_label` are optional. Leave them out and
the step is just sent. Give `skill_id` alone and only the skill is
checked.

```jsonl
{"utterance": "what time is it", "skill_id": "ovos-skill-date-time.openvoiceos", "intent_label": "what_time_is_it"}
{"utterance": "how hot is it", "skill_id": "ovos-skill-weather.openvoiceos"}
{"utterance": "tell me a joke"}
```

Intent labels are compared loosely: `what.time.is.it.intent` and
`what_time_is_it` count as the same.

### Include a skill's tests

A line with `{"golden": "<skill_id>"}` pulls in that skill's whole
golden set, found the same way as for `Test: <Skill> - All`:

```jsonl
# before a release: my own phrasings plus two skills' own tests
{"utterance": "hvad er klokken", "skill_id": "ovos-skill-date-time.openvoiceos", "intent_label": "what_time_is_it"}
{"utterance": "hvornår går solen ned"}
{"golden": "ovos-skill-weather.openvoiceos"}
{"golden": "ovos-skill-naptime.openvoiceos"}
```

### Language

A line can carry `"lang": "da-dk"`. Lines in another language than the
TUI's `--lang` are skipped, so one file can hold several languages.

## Save a selection as a script

After `Test: <Skill> - Choose`, pick
**`Test: <Skill> - Save last selection as script`**. The chosen
sentences are written to `<skill>-selection.jsonl` (or `-2`, `-3`, …
if that name is taken), with their expected skill and intent, and show up as `Script: <skill>-selection`.

## Examples

**Check a new phrasing for collisions.** You're adding "is it cold
outside" to the weather skill. Put it in a script with the phrasings
closest to it that should go elsewhere:

```jsonl
{"utterance": "is it cold outside", "skill_id": "ovos-skill-weather.openvoiceos"}
{"utterance": "is it cold in the fridge", "skill_id": "ovos-skill-wikipedia.openvoiceos"}
{"utterance": "what is cold", "skill_id": "ovos-skill-wordnet.openvoiceos"}
```

**Smoke test after an update.** One sentence per skill you care about,
run after every `pip install -U` of OVOS:

```jsonl
{"utterance": "what time is it", "skill_id": "ovos-skill-date-time.openvoiceos"}
{"utterance": "set a timer for one minute", "skill_id": "ovos-skill-alerts.openvoiceos"}
{"utterance": "cancel all timers", "skill_id": "ovos-skill-alerts.openvoiceos"}
{"utterance": "what's the weather like", "skill_id": "ovos-skill-weather.openvoiceos"}
{"utterance": "tell me about the eiffel tower"}
```

Remember the last-but-one line: a script that sets a timer should also
cancel it. The TUI sends `stop` at the end of every run, but a timer is
not something `stop` removes.
