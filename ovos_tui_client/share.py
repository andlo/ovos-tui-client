"""Getting a saved report off the device (#51).

Copy to the clipboard goes through the terminal (OSC 52), and many
terminals don't pass it on - GNOME Terminal / Ptyxis (VTE) among them, over
ssh or not. So a report can also be handed out as a short link: a tiny,
read-only web page served from the device for a few minutes, reachable
from the tester's own browser on the same network. The page shows the
report with Copy and Download, and - when the user has set a store's link
template - a button that opens the store's page with the report filled in
(report.submit_url). Nothing is sent anywhere by ovos-tui: the browser
fetches the page from the device, and the person submits on the store's
page themselves.

The link carries a random token, serves one report, and stops after
SHARE_TTL. The report holds no private data by design (report.py).
"""
import html
import json
import os
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

SHARE_TTL = 15 * 60  # seconds a report link stays up
ASK_SUBMIT_URL_TEXT = (
    "Where do you submit test reports? Paste the report link your skill store gives in its "
    "instructions (it contains {report_fragment} or {report}).\n"
    "ovos-tui never submits anything: it only builds a link that opens the store's page with "
    "your report filled in. You check it there and submit it yourself.")


def lan_address() -> str:
    """This machine's address on the network a browser would reach it by
    (the interface of the default route). No packet is sent."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))  # TEST-NET-1: routing lookup only
        return s.getsockname()[0]
    except OSError:
        return socket.gethostname()
    finally:
        s.close()


def scp_hint(path, address: Optional[str] = None) -> str:
    """A command to fetch the report file from another machine."""
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or "ovos"
    return f"scp {user}@{address or lan_address()}:{Path(path).expanduser().resolve()} ."


def _page(title: str, text: str, store_link: Optional[str]) -> str:
    try:
        summary = json.loads(text).get("summary") or {}
        line = (f"{summary.get('passed', 0)}/{summary.get('checked', 0)} passed · "
                f"{summary.get('failed', 0)} failed · {summary.get('timed_out', 0)} timed out")
    except (ValueError, AttributeError):
        line = ""
    store = ""
    if store_link:
        host = urlparse(store_link).netloc or "the store"
        store = (f'<a class="btn primary" href="{html.escape(store_link, quote=True)}" rel="noreferrer" '
                 f'title="The skill store\'s page at {html.escape(host, quote=True)}, with this report filled in">'
                 f'Open detailed page</a>')
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} - test report</title>
<style>
body{{font:15px/1.45 system-ui,sans-serif;margin:0;padding:16px;background:#f6f7f9;color:#1b1f24}}
main{{max-width:960px;margin:0 auto}} h1{{font-size:1.2rem;margin:.2rem 0}}
.muted{{color:#5b6470}} .row{{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}}
.btn{{border:1px solid #c9ced6;background:#fff;border-radius:8px;padding:8px 14px;font:inherit;
cursor:pointer;text-decoration:none;color:inherit}} .btn.primary{{background:#2457d6;border-color:#2457d6;color:#fff}}
textarea{{width:100%;box-sizing:border-box;height:60vh;font:12px/1.4 ui-monospace,monospace;
border:1px solid #c9ced6;border-radius:8px;padding:8px}}
@media (prefers-color-scheme:dark){{body{{background:#15181c;color:#e6e8eb}}.btn{{background:#23272e;border-color:#3a404a}}
textarea{{background:#1c2026;color:#e6e8eb;border-color:#3a404a}}.muted{{color:#9aa3ad}}}}
</style></head><body><main>
<h1>{html.escape(title)}</h1>
<div class="muted">{html.escape(line)}</div>
<div class="row">{store}
<button class="btn" id="copy">Copy report</button>
<a class="btn" href="report.json" download="report.json">Download report.json</a></div>
<p class="muted">Served from the OVOS device by ovos-tui for a few minutes. Nothing is sent anywhere
until you submit it yourself.</p>
<textarea id="report" readonly>{html.escape(text)}</textarea>
<script>
document.getElementById("copy").addEventListener("click", async () => {{
  const t = document.getElementById("report");
  try {{ await navigator.clipboard.writeText(t.value); }}
  catch (e) {{ t.focus(); t.select(); document.execCommand("copy"); }}
  document.getElementById("copy").textContent = "Copied";
}});
</script></main></body></html>"""


class ReportShare:
    """One report on a short, temporary link. start() -> the link."""

    def __init__(self, text: str, title: str = "Test report", store_link: Optional[str] = None,
                 ttl: float = SHARE_TTL, host: str = "0.0.0.0", port: int = 0,
                 address: Optional[str] = None):
        self.text = text
        self.title = title
        self.store_link = store_link
        self.ttl = ttl
        self.token = secrets.token_urlsafe(9)
        self._bind = (host, port)
        self._address = address
        self._server = None
        self._timer = None
        self.url = None

    def start(self) -> str:
        share = self
        page = _page(self.title, self.text, self.store_link).encode("utf-8")
        body = self.text.encode("utf-8")
        prefix = f"/{self.token}/"

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server API
                if self.path in (prefix, prefix + "index.html"):
                    self._send(page, "text/html; charset=utf-8")
                elif self.path == prefix + "report.json":
                    self._send(body, "application/json; charset=utf-8",
                               {"Content-Disposition": 'attachment; filename="report.json"'})
                else:
                    self.send_error(404)

            def _send(self, data, ctype, extra=None):
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):  # keep the terminal quiet
                pass

        self._server = ThreadingHTTPServer(self._bind, Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self._timer = threading.Timer(self.ttl, self.stop)
        self._timer.daemon = True
        self._timer.start()
        port = self._server.server_address[1]
        share.url = f"http://{self._address or lan_address()}:{port}{prefix}"
        return share.url

    def stop(self) -> None:
        if self._timer:
            self._timer.cancel()
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
