#!/usr/bin/env python3
"""Draws docs/images/share-page.png: the page a report link opens
(share.ReportShare), with the demo report from make_screenshots.py.
Needs Firefox (headless). See docs/maintaining.md."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from ovos_tui_client.share import ReportShare  # noqa: E402
from make_screenshots import DEMO_REPORT  # noqa: E402

STORE = "https://andlo.github.io/ovos-klondike-mercantile/detail.html?skill=x#report=..."


def main():
    out = ROOT / "docs" / "images" / "share-page.png"
    text = json.dumps(DEMO_REPORT, indent=2) + "\n"
    share = ReportShare(text, title="Test: Weather - All", store_link=STORE,
                        host="127.0.0.1", address="127.0.0.1", ttl=60)
    url = share.start()
    profile = tempfile.mkdtemp(prefix="ovos-tui-ff-")
    try:
        subprocess.run(["firefox", "--headless", "--no-remote", "--profile", profile,
                        "--window-size=1100,760", "--screenshot", str(out), url],
                       check=True, timeout=60, capture_output=True)
    finally:
        share.stop()
    print(f"  {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
