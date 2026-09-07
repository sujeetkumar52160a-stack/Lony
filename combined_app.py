#!/usr/bin/env python3
"""
combined_app.py — runs BOTH the Telegram bot AND the link server in ONE
process, so they share the same bot_data.json on disk. This is required for
a single free Render Web Service (Render doesn't give you two machines for
free, and the bot + the link server MUST see the same file for deactivation
to actually work).

Render setup:
  1. Push bot.py, link_server.py, combined_app.py, requirements.txt,
     render.yaml to a GitHub repo (private is fine).
  2. On Render: New → Blueprint → point at your repo (it reads render.yaml
     automatically). Or New → Web Service manually with:
       Build Command: pip install -r requirements.txt
       Start Command: python combined_app.py
  3. Once deployed, Render gives you a URL like:
       https://your-service.onrender.com
     Set that as PUBLIC_BASE_URL in bot.py (edit the file or set it as a
     Render environment variable) and redeploy.

⚠️ IMPORTANT — Render free tier sleeps after 15 min with no inbound HTTP
traffic, and that would kill your bot's polling loop too (not just the link
server). Use a free uptime pinger (e.g. UptimeRobot, cron-job.org) to hit
your Render URL ("/") every 5 minutes so it never sleeps.
"""

import os
import threading

import link_server
import bot as bot_module


def _run_link_server():
    port = int(os.getenv("PORT", "8080"))
    # Flask's built-in server — fine for this low-traffic redirect job.
    link_server.app.run(host="0.0.0.0", port=port)


def main():
    # Link server runs in a background thread...
    t = threading.Thread(target=_run_link_server, daemon=True)
    t.start()

    # ...and the Telegram bot's polling loop runs in the main thread
    # (must stay in the main thread — PTB installs signal handlers there).
    bot_module.main()


if __name__ == "__main__":
    main()
