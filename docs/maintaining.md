# Maintaining these docs

The manual lives in `docs/` as Markdown and is built with
[MkDocs](https://www.mkdocs.org/) and the Material theme. GitHub
Actions publishes it to GitHub Pages on every push to `main`.

## Layout

| Path | |
|---|---|
| `mkdocs.yml` | Site settings and the page order (`nav:`) |
| `docs/*.md` | The pages |
| `docs/images/*.svg` | Screenshots, **generated** - don't edit by hand |
| `scripts/make_screenshots.py` | Draws the screenshots |
| `scripts/make_share_page.py` | Draws `share-page.png`, the page a report link opens (needs Firefox) |
| `scripts/build_docs.sh` | Screenshots + build, in one go |
| `.github/workflows/docs.yml` | Checks docs on PRs, publishes on `main` |

## Preview locally

```bash
pip install -e .[docs]
scripts/build_docs.sh serve
```

Open <http://127.0.0.1:8000>. Pages reload as you save.

## Screenshots

The screenshots are real ovos-tui-client screens, drawn headlessly by
Textual with a fake messagebus and fixed demo data. No OVOS is needed,
and the same code gives the same pictures, so a change to the UI shows
up as a diff in `docs/images/`.

```bash
python scripts/make_screenshots.py            # all of them
python scripts/make_screenshots.py picker     # just one
python scripts/make_screenshots.py --list     # scene names
```

Regenerate them whenever the UI changes, and commit the SVGs with the
change. One picture is a PNG from a real browser instead:
`share-page.png`, the page a report link opens. Redraw it with
`python scripts/make_share_page.py` (needs Firefox) when `share.py`'s page
changes; the docs workflow doesn't regenerate it. The docs workflow regenerates them on every PR and warns if
they differ from the committed ones. The published site always uses
freshly generated screenshots, so it never shows a stale UI.

### Adding a screenshot

1. Write a `scene_<name>(app, pilot)` function in
   `scripts/make_screenshots.py`. It gets a running app with the demo
   logs and conversation already loaded. Drive it the way a user would
   (`app.show_installed_skills()`, `await pilot.press("down")`), then
   `await _settle(pilot)`.
2. Add it to `SCENES`.
3. Run `python scripts/make_screenshots.py <name>` and reference
   `images/<name>.svg` from a page.

The demo data (skills, log lines, golden utterances, the weather
skill's `skill.json`) is at the top of the script. Keep it believable:
the pictures are what people will compare their own screen with.

Anything that differs between runs (time, temp paths, the home folder)
must be fixed or replaced in the script, otherwise the check fails on
every PR.

## Writing style

- Write for someone running OVOS at home, not for developers of this
  tool. Say what to type and what they will see.
- Use the names exactly as shown in the palette: `Test: Weather - All`,
  `About: Installed skills`.
- One page per task. Link to other pages instead of repeating them.
- Update the manual in the same PR as the feature.

## Publishing

Pushing to `main` builds and publishes the site. The first time, GitHub
Pages must be set to **Source: GitHub Actions** (repository
*Settings → Pages*), or with the GitHub CLI:

```bash
gh api -X POST repos/andlo/ovos-tui-client/pages -f build_type=workflow
```

The site is then at <https://andlo.github.io/ovos-tui-client/>.
