#!/usr/bin/env bash
# Regenerate the screenshots and build (or serve) the manual.
#
#   scripts/build_docs.sh          screenshots + strict build into site/
#   scripts/build_docs.sh serve    screenshots + live preview on :8000
#   scripts/build_docs.sh check    as build, then warn if the screenshots
#                                  differ from the committed ones (CI).
#                                  A warning, not a failure: a newer Textual
#                                  can shift a pixel without anything being
#                                  wrong, and the published site always uses
#                                  freshly generated screenshots anyway.
#
# Needs: pip install -e .[docs]
set -euo pipefail
cd "$(dirname "$0")/.."

mode="${1:-build}"

echo "== screenshots"
python scripts/make_screenshots.py

case "$mode" in
  serve)
    exec mkdocs serve
    ;;
  build|check)
    echo "== mkdocs build --strict"
    mkdocs build --strict
    ;;
  *)
    echo "usage: $0 [build|serve|check]" >&2
    exit 2
    ;;
esac

if [ "$mode" = check ]; then
  changed="$(git status --porcelain -- docs/images)"
  if [ -n "$changed" ]; then
    echo "::warning::screenshots in docs/images changed - run scripts/build_docs.sh and commit the SVGs"
    echo "$changed"
  else
    echo "screenshots up to date"
  fi
fi
