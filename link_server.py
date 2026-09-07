#!/usr/bin/env python3
"""
link_server.py — the piece that makes "DEACTIVATE" actually work.

Run this ALONGSIDE bot.py (same machine, same DATA_FILE). Every link the bot
now hands out looks like:

    https://yourdomain.com/l/<image_links|file_links|video_links>/<link_id>/<view|dl>

When someone opens that link, THIS server (not uguu.se, not 0x0.st) is what
they hit first. It checks bot_data.json:
  - status == "active"   → 302 redirect to the real mirror URL
  - status == "inactive" → shows a small "link deactivated" page, no redirect

This is what actually fixes the "deactivated but still opens" problem —
because now YOUR server, not a random free file host, is the one deciding
whether the link works.

DEPLOY NOTES:
  - Needs to be reachable on the public internet (a VPS + your own domain,
    or a tunnel like ngrok/Cloudflare Tunnel for testing).
  - Set PUBLIC_BASE_URL in bot.py's environment to wherever this ends up
    (e.g. PUBLIC_BASE_URL=https://links.example.com), and restart the bot.
  - Both processes must read/write the SAME bot_data.json file (same folder,
    or share a volume if running in separate containers).

Run:
  pip install flask
  python link_server.py            # dev server on 0.0.0.0:8080
  # in production, put it behind gunicorn + nginx/caddy with HTTPS, e.g.:
  gunicorn -w 2 -b 0.0.0.0:8080 link_server:app
"""

import json
import os

from flask import Flask, abort, redirect, Response

DATA_FILE = os.getenv("DATA_FILE", "bot_data.json")
PORT = int(os.getenv("LINK_SERVER_PORT", "8080"))

app = Flask(__name__)

DEACTIVATED_HTML = """
<!doctype html><html><head><meta charset="utf-8">
<title>Link Deactivated</title>
<style>
body{{font-family:sans-serif;background:#111;color:#eee;display:flex;
height:100vh;align-items:center;justify-content:center;text-align:center}}
.box{{padding:2rem}}
h1{{color:#e74c3c;font-size:1.6rem}}
</style></head><body><div class="box">
<h1>🔴 This link has been deactivated</h1>
<p>The owner turned this link off. It's no longer available.</p>
<p><small>{filename}</small></p>
</div></body></html>
"""

NOT_FOUND_HTML = """
<!doctype html><html><head><meta charset="utf-8">
<title>Link Not Found</title>
<style>body{font-family:sans-serif;background:#111;color:#eee;display:flex;
height:100vh;align-items:center;justify-content:center;text-align:center}</style>
</head><body><h1>❌ Link not found</h1></body></html>
"""

VALID_KEYS = {"image_links", "file_links", "video_links"}
VALID_KINDS = {"view", "dl"}


def _load() -> dict:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


@app.route("/l/<key>/<link_id>/<kind>")
def serve_link(key: str, link_id: str, kind: str):
    if key not in VALID_KEYS or kind not in VALID_KINDS:
        abort(404)

    data = _load()
    entry = None
    for e in data.get(key, []):
        if e.get("id") == link_id:
            entry = e
            break

    if not entry:
        return Response(NOT_FOUND_HTML, status=404, mimetype="text/html")

    if entry.get("status", "active") != "active":
        fname = entry.get("filename", "")
        return Response(
            DEACTIVATED_HTML.format(filename=fname), status=410, mimetype="text/html"
        )

    target = entry.get("view_url") if kind == "view" else entry.get("dl_url")
    if not target:
        return Response(NOT_FOUND_HTML, status=404, mimetype="text/html")

    return redirect(target, code=302)


@app.route("/")
def health():
    return "link_server running"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
