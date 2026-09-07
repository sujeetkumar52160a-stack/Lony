#!/usr/bin/env python3
"""
PREMIUM GEN IMAGE / FILE / VIDEO LINK BOT
Flow: Select Feature → Send Media → Choose VIEW or DOWNLOAD → ONE link
FIX: VIEW and DOWNLOAD generate proper separate links
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Tuple

import requests
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ===================== CONFIG =====================
TELEGRAM_TOKEN = os.getenv("BOT_TOKEN", "").strip() or "8671836041:AAH7QCMagW3YHjYpTtmAlCgugWKb4d3gm1A"
OWNER_ID = int(os.getenv("OWNER_ID", "0") or "0") or 8711563973
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "DGDRIFT")
DATA_FILE = os.getenv("DATA_FILE", "bot_data.json")

# ---- LARGE FILE SUPPORT (550 MB) ----
# IMPORTANT: the public api.telegram.org Bot API can only DOWNLOAD files up to
# 20 MB, regardless of this setting. To actually handle files up to 550 MB you
# must run Telegram's local Bot API server (telegram-bot-api) and point this
# bot at it — see BOT_API_BASE_URL below. Without that, uploads over 20 MB
# will fail at the Telegram layer before they ever reach this code.
MAX_IMAGE_SIZE_MB = int(os.getenv("MAX_IMAGE_SIZE_MB", "550"))
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "550"))
MAX_VIDEO_SIZE_MB = int(os.getenv("MAX_VIDEO_SIZE_MB", "550"))

# Point these at your local Bot API server (docker run aiogram/telegram-bot-api
# or the official telegram-bot-api binary) to unlock >20MB downloads.
# Leave blank to use the standard cloud API (20 MB download cap still applies).
BOT_API_BASE_URL = os.getenv("BOT_API_BASE_URL", "").strip()          # e.g. http://localhost:8081/bot
BOT_API_BASE_FILE_URL = os.getenv("BOT_API_BASE_FILE_URL", "").strip()  # e.g. http://localhost:8081/file/bot

# ---- REAL DEACTIVATION SUPPORT ----
# Free mirrors (uguu.se, file.io) have NO delete API, so a link straight to
# them can never be truly killed by this bot. Set PUBLIC_BASE_URL to the
# public address of link_server.py (run it alongside this bot, behind your
# own domain/reverse-proxy or an ngrok tunnel) and every link the bot hands
# out will point at YOUR server first — which checks bot_data.json and only
# redirects if the link is still "active". Deactivate it in the Owner Panel
# and it stops working immediately, for every mirror, no exceptions.
# Leave blank to fall back to raw mirror links (old behaviour — deactivation
# will only truly work for 0x0.st / temp.sh links, as before).
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")  # e.g. https://links.example.com

# Image: uguu.se gives viewer-friendly URL (good for VIEW)
# 0x0.st gives direct download (good for DOWNLOAD)
# NOTE ON REAL LIMITS (independent of MAX_*_SIZE_MB above):
#   0x0.st   ~ 512 MiB max
#   uguu.se  ~ 128 MB max
#   temp.sh  no fixed advertised cap, practically fine for large files
#   file.io  ~ 100 MB max on the free/anonymous tier
# A file over a given mirror's own cap will simply fail on that mirror and
# fall back to the next one in the list (see upload_media_dual below).
IMAGE_VIEW_MIRRORS   = ["https://uguu.se/upload"]          # browser-viewable
IMAGE_DL_MIRRORS     = ["https://0x0.st", "https://temp.sh/upload"]  # force-download
FILE_MIRRORS         = ["https://0x0.st", "https://file.io/", "https://temp.sh/upload"]
VIDEO_VIEW_MIRRORS   = ["https://uguu.se/upload"]          # can be played in browser
VIDEO_DL_MIRRORS     = ["https://0x0.st", "https://temp.sh/upload"]  # force download

# States
WAIT_MEDIA    = "wait_media"
WAIT_ACTION   = "wait_action"
WAIT_ADD_ADMIN = "wait_add_admin"
WAIT_REM_ADMIN = "wait_rem_admin"

# Main menu
BTN_IMG     = "📸 GEN IMAGE LINK"
BTN_FILE    = "📁 GEN FILE LINK"
BTN_VIDEO   = "🎬 GEN VIDEO LINK"
BTN_STATUS  = "🩺 SERVER STATUS"
BTN_ABOUT   = "ℹ️ ABOUT BOT"
BTN_CHANNEL = "📢 JOIN CHANNEL"
BTN_OWNER   = "👑 OWNER PANEL"
BTN_ADMIN   = "🔴 ADMIN PANEL"
BTN_BACK    = "🔙 MAIN MENU"

BTN_VIEW     = "👁 ONLY VIEW"
BTN_DOWNLOAD = "⬇️ DOWNLOAD"
BTN_CANCEL   = "❌ CANCEL"

BTN_IMG_LINKS  = "🖼 IMAGE LINKS"
BTN_FILE_LINKS = "📂 FILE LINKS"
BTN_VIDEO_LINKS = "🎬 VIDEO LINKS"
BTN_ACTIVE_LINKS = "🟢 ACTIVE LINKS"
BTN_ADD_ADMIN  = "➕ ADD ADMIN"
BTN_REM_ADMIN  = "➖ REMOVE ADMIN"
BTN_ADM_LIST   = "👥 ADMIN LIST"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "PremiumHostBot/3.0"})
_POOL = ThreadPoolExecutor(max_workers=6)


def to_bold_text(s: str) -> str:
    out = []
    for ch in s:
        o = ord(ch)
        if 65 <= o <= 90:
            out.append(chr(0x1D400 + (o - 65)))
        elif 97 <= o <= 122:
            out.append(chr(0x1D41A + (o - 97)))
        elif 48 <= o <= 57:
            out.append(chr(0x1D7CE + (o - 48)))
        else:
            out.append(ch)
    return "".join(out)


def _norm(s: str) -> str:
    out = []
    for ch in s or "":
        o = ord(ch)
        if 0x1D400 <= o <= 0x1D419:
            out.append(chr(ord("A") + (o - 0x1D400)))
        elif 0x1D41A <= o <= 0x1D433:
            out.append(chr(ord("a") + (o - 0x1D41A)))
        elif 0x1D7CE <= o <= 0x1D7D7:
            out.append(chr(ord("0") + (o - 0x1D7CE)))
        else:
            out.append(ch)
    return "".join(out).strip()


class CBtn(InlineKeyboardButton):
    def __init__(self, text: str, style: str = "", **kwargs):
        super().__init__(text, **kwargs)
        self._style = style or ""

    def to_dict(self, *args, **kwargs):
        try:
            d = super().to_dict(*args, **kwargs)
        except TypeError:
            d = super().to_dict()
        if self._style:
            d["style"] = self._style
        return d


class RBtn(KeyboardButton):
    def __init__(self, text: str, style: str = "", **kwargs):
        super().__init__(text, **kwargs)
        self._style = style or ""

    def to_dict(self, *args, **kwargs):
        try:
            d = super().to_dict(*args, **kwargs)
        except TypeError:
            d = super().to_dict()
        if self._style:
            d["style"] = self._style
        return d


def _rb(label: str, style: str = "") -> RBtn:
    return RBtn(to_bold_text(label), style=style)


def _ib(label: str, style: str = "", **kwargs) -> CBtn:
    return CBtn(to_bold_text(label), style=style, **kwargs)


# ==================== DATA ====================
def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"admins": [], "image_links": [], "file_links": [], "video_links": []}


def save_data(data: dict) -> None:
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"save_data: {e}")


def is_owner(uid: int) -> bool:
    return int(uid) == int(OWNER_ID)


def is_admin(uid: int) -> bool:
    if is_owner(uid):
        return True
    try:
        return int(uid) in [int(x) for x in load_data().get("admins", [])]
    except Exception:
        return False


def save_link(
    key: str, uid: int, uname: str, fname: str,
    view_url: str = "", dl_url: str = "",
    view_del_token: str = "", dl_del_token: str = "",
) -> str:
    """Saves ONE record per uploaded file (both view + download URLs together)
    and returns its short link_id. This id is what the owner deactivates —
    killing both the view and download link at once."""
    link_id = uuid.uuid4().hex[:8]
    data = load_data()
    data.setdefault(key, []).append(
        {
            "id": link_id,
            "view_url": view_url,
            "dl_url": dl_url,
            "view_del_token": view_del_token,  # only set for mirrors that support real deletion
            "dl_del_token": dl_del_token,
            "user_id": uid,
            "username": uname or "Unknown",
            "filename": fname,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "active",
        }
    )
    save_data(data)
    return link_id


def find_link(key: str, link_id: str) -> Optional[dict]:
    for e in load_data().get(key, []):
        if e.get("id") == link_id:
            return e
    return None


def public_link(key: str, link_id: str, kind: str) -> str:
    """Builds the link the user actually gets. If PUBLIC_BASE_URL is set,
    this points at OUR OWN link_server.py (real deactivation for every
    mirror). Otherwise it falls back to the raw mirror URL directly."""
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}/l/{key}/{link_id}/{kind}"
    e = find_link(key, link_id) or {}
    return (e.get("view_url") if kind == "view" else e.get("dl_url")) or ""


def deactivate_link(key: str, link_id: str) -> Tuple[bool, str]:
    """
    Marks a link inactive locally, and best-effort deletes it from the host
    on BOTH its view_url and dl_url.
      - If PUBLIC_BASE_URL is set: this alone is enough — link_server.py
        checks "status" before ever redirecting, so the link dies instantly
        no matter which mirror it points to.
      - Without PUBLIC_BASE_URL: real remote deletion only happens on
        mirrors that support it (0x0.st via stored token, temp.sh via HTTP
        DELETE). uguu.se / file.io have no delete API — those raw links may
        keep working until they expire on the mirror's own side.
    Returns (remote_deleted, note).
    """
    data = load_data()
    items = data.get(key, [])
    target = None
    for e in items:
        if e.get("id") == link_id:
            target = e
            break
    if not target:
        return False, "LINK NOT FOUND"

    remote_deleted = False
    notes = []

    for url_field, token_field, tag in (
        ("view_url", "view_del_token", "VIEW"),
        ("dl_url", "dl_del_token", "DOWNLOAD"),
    ):
        url = target.get(url_field, "") or ""
        token = target.get(token_field, "")
        try:
            if "0x0.st" in url and token:
                r = _SESSION.post(url, data={"token": token, "delete": ""}, timeout=15)
                if r.status_code == 200:
                    remote_deleted = True
                    notes.append(f"{tag}: DELETED FROM 0x0.st")
            elif "temp.sh" in url:
                r = _SESSION.delete(url, timeout=15)
                if r.status_code in (200, 204):
                    remote_deleted = True
                    notes.append(f"{tag}: DELETED FROM temp.sh")
        except Exception as e:
            notes.append(f"{tag}: REMOTE DELETE FAILED ({e})")

    if PUBLIC_BASE_URL:
        notes.append("PROXY LINK NOW BLOCKED (link_server.py)")
        remote_deleted = True
    elif not notes:
        notes.append("LOCAL ONLY — HOST HAS NO DELETE API (set PUBLIC_BASE_URL to fix this)")

    target["status"] = "inactive"
    save_data(data)
    return remote_deleted, " | ".join(notes)


# ==================== KEYBOARDS ====================
def main_reply_kb(uid: int) -> ReplyKeyboardMarkup:
    rows = [
        [_rb(BTN_IMG, "primary"), _rb(BTN_FILE, "primary")],
        [_rb(BTN_VIDEO, "success"), _rb(BTN_STATUS, "success")],
        [_rb(BTN_ABOUT, "primary"), _rb(BTN_CHANNEL, "primary")],
    ]
    if is_owner(uid):
        rows.append([_rb(BTN_OWNER, "danger")])
    elif is_admin(uid):
        rows.append([_rb(BTN_ADMIN, "danger")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


def action_reply_kb(mode: str) -> ReplyKeyboardMarkup:
    label_map = {
        "image": ("👁 GEN IMAGE VIEW", "⬇️ GEN IMAGE DOWNLOAD"),
        "file":  ("👁 GEN FILE VIEW",  "⬇️ GEN FILE DOWNLOAD"),
        "video": ("👁 GEN VIDEO VIEW", "⬇️ GEN VIDEO DOWNLOAD"),
    }
    v, d = label_map.get(mode, (BTN_VIEW, BTN_DOWNLOAD))
    return ReplyKeyboardMarkup(
        [
            [_rb(v, "primary"), _rb(d, "success")],
            [_rb(BTN_CANCEL, "danger")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def owner_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [_rb(BTN_IMG_LINKS, "primary"), _rb(BTN_FILE_LINKS, "primary")],
            [_rb(BTN_VIDEO_LINKS, "success"), _rb(BTN_ACTIVE_LINKS, "success")],
            [_rb(BTN_STATUS, "success"), _rb(BTN_ADM_LIST, "primary")],
            [_rb(BTN_ADD_ADMIN, "success"), _rb(BTN_REM_ADMIN, "danger")],
            [_rb(BTN_BACK, "primary")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def active_links_kb(page: int = 0, per_page: int = 8) -> Tuple[str, InlineKeyboardMarkup]:
    """Builds the text + inline keyboard for the owner's active-links panel.
    Each active link gets one button; tapping it deactivates that link."""
    data = load_data()
    all_active = []
    for key in ("image_links", "file_links", "video_links"):
        for e in data.get(key, []):
            if e.get("status", "active") == "active" and e.get("id"):
                all_active.append((key, e))

    if not all_active:
        return "<b>🟢 ACTIVE LINKS</b>\n\n<b>❌ ABHI KOI ACTIVE LINK NAHI.</b>", back_inline()

    start = page * per_page
    chunk = all_active[start:start + per_page]

    lines = [
        "<b>🟢 ACTIVE LINKS</b>",
        f"<b>TOTAL ACTIVE: {len(all_active)}</b>",
        "<b>━━━━━━━━━━━━━━━━━━━━━━━━</b>",
        "<b>👇 BUTTON DABAO = US LINK KO DEACTIVATE KARO</b>",
    ]
    rows = []
    kind_tag = {"image_links": "🖼", "file_links": "📂", "video_links": "🎬"}
    for key, e in chunk:
        label = f"{kind_tag.get(key,'🔗')} {e.get('filename','file')[:24]} ({e['id']})"
        rows.append([_ib(f"⛔ {label}", style="danger", callback_data=f"deact:{key}:{e['id']}")])

    nav = []
    if start > 0:
        nav.append(_ib("⬅️ PREV", style="primary", callback_data=f"actlinks:{page-1}"))
    if start + per_page < len(all_active):
        nav.append(_ib("NEXT ➡️", style="primary", callback_data=f"actlinks:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([_ib("⬅️ BACK", style="primary", callback_data="home")])

    return "\n".join(lines), InlineKeyboardMarkup(rows)


def admin_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [_rb(BTN_IMG_LINKS, "primary"), _rb(BTN_FILE_LINKS, "primary")],
            [_rb(BTN_VIDEO_LINKS, "success"), _rb(BTN_STATUS, "success")],
            [_rb(BTN_ADM_LIST, "primary"), _rb(BTN_BACK, "primary")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def cancel_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[_rb(BTN_CANCEL, "danger")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def back_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[_ib("⬅️ BACK", style="primary", callback_data="home")]])


def open_link_kb(url: str, label: str = "🔗 OPEN LINK") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [_ib(label, style="success", url=url)],
            [_ib("⬅️ BACK", style="primary", callback_data="home")],
        ]
    )


# ==================== TEXTS ====================
def home_text(name: str) -> str:
    n = html.escape((name or "USER").upper())
    return (
        "<b>╔══════════════════════════╗</b>\n"
        "<b>   🚀 PREMIUM GEN LINK BOT</b>\n"
        "<b>╚══════════════════════════╝</b>\n\n"
        f"<b>👋 WELCOME, {n}!</b>\n\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        "<b>1️⃣ FEATURE SELECT KARO</b>\n"
        "<b>2️⃣ IMAGE / FILE / VIDEO BHEJO</b>\n"
        "<b>3️⃣ VIEW YA DOWNLOAD CHUNO</b>\n"
        "<b>4️⃣ EK LINK MILEGA</b>\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        "<b>👇 NEECHE SE FEATURE CHUNO</b>"
    )


def prompt_media(mode: str) -> str:
    titles = {
        "image": ("📸 GEN IMAGE LINK", "IMAGE / PHOTO", MAX_IMAGE_SIZE_MB),
        "file":  ("📁 GEN FILE LINK",  "FILE (ZIP / PDF / APK…)", MAX_FILE_SIZE_MB),
        "video": ("🎬 GEN VIDEO LINK", "VIDEO", MAX_VIDEO_SIZE_MB),
    }
    title, what, mx = titles[mode]
    return (
        f"<b>{title}</b>\n"
        f"<b>━━━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        f"<b>AB APNI {what} BHEJO</b>\n"
        f"<b>MAX SIZE: {mx} MB</b>\n\n"
        f"<b>⚠️ SIRF {what} HI ACCEPT HOGI</b>\n"
        f"<b>GALAT TYPE → WARNING</b>\n\n"
        f"<b>💡 CANCEL → ❌ CANCEL</b>"
    )


def about_text() -> str:
    return (
        "<b>ℹ️ ABOUT THIS BOT</b>\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        "<b>🚀 GEN IMAGE • FILE • VIDEO LINK</b>\n"
        "<b>🔒 NO ACCOUNT NEEDED</b>\n"
        f"<b>🖼 IMAGE MAX: {MAX_IMAGE_SIZE_MB} MB</b>\n"
        f"<b>📦 FILE MAX: {MAX_FILE_SIZE_MB} MB</b>\n"
        f"<b>🎬 VIDEO MAX: {MAX_VIDEO_SIZE_MB} MB</b>\n\n"
        "<b>⚡ SELECT FEATURE → SEND MEDIA → VIEW/DOWNLOAD → LINK</b>"
    )


def owner_text() -> str:
    d = load_data()
    active_count = sum(
        1 for key in ("image_links", "file_links", "video_links")
        for e in d.get(key, []) if e.get("status", "active") == "active"
    )
    return (
        "<b>👑 OWNER PANEL</b>\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        f"<b>🖼 IMAGE LINKS: {len(d.get('image_links', []))}</b>\n"
        f"<b>📂 FILE LINKS: {len(d.get('file_links', []))}</b>\n"
        f"<b>🎬 VIDEO LINKS: {len(d.get('video_links', []))}</b>\n"
        f"<b>🟢 ACTIVE RIGHT NOW: {active_count}</b>\n"
        f"<b>👮 ADMINS: {len(d.get('admins', []))}</b>\n\n"
        "<b>🔒 ADD / REMOVE ADMIN — SIRF OWNER</b>\n"
        "<b>🟢 ACTIVE LINKS → DEACTIVATE ANY LINK</b>\n"
        "<b>👇 NEECHE SE OPTION CHUNO</b>"
    )


def admin_text() -> str:
    d = load_data()
    return (
        "<b>🔴 ADMIN PANEL</b>\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        f"<b>🖼 IMAGE LINKS: {len(d.get('image_links', []))}</b>\n"
        f"<b>📂 FILE LINKS: {len(d.get('file_links', []))}</b>\n"
        f"<b>🎬 VIDEO LINKS: {len(d.get('video_links', []))}</b>\n"
        f"<b>👮 ADMINS: {len(d.get('admins', []))}</b>\n\n"
        "<b>ℹ️ ADD ADMIN SIRF OWNER</b>\n"
        "<b>👇 NEECHE SE OPTION CHUNO</b>"
    )


def links_text(links: list, title: str, key: str = "") -> str:
    if not links:
        return f"<b>{html.escape(title)}</b>\n\n<b>❌ KOI LINK SAVE NAHI HUA.</b>"
    recent = links[-15:]
    lines = [f"<b>{html.escape(title)}</b>", "<b>━━━━━━━━━━━━━━━━━━━━━━━━</b>\n"]
    for i, e in enumerate(reversed(recent), 1):
        status = e.get("status", "active")
        status_tag = "<b>🟢 ACTIVE</b>" if status == "active" else "<b>🔴 DEACTIVATED</b>"
        lid = e.get("id", "")
        v_url = public_link(key, lid, "view") if key and lid else e.get("view_url", "")
        d_url = public_link(key, lid, "dl") if key and lid else e.get("dl_url", "")
        lines.append(
            f"<b>{i}.</b> <code>{html.escape(str(e.get('filename', 'file')))}</code>  {status_tag}\n"
            f"<b>👤</b> @{html.escape(str(e.get('username', '?')))}\n"
            f"<b>👁</b> <code>{html.escape(str(v_url or 'N/A'))}</code>\n"
            f"<b>⬇️</b> <code>{html.escape(str(d_url or 'N/A'))}</code>\n"
            f"<b>🕐</b> <b>{html.escape(str(e.get('time', '')))}</b>\n"
        )
    text = "\n".join(lines)
    return text[:4000] + ("\n\n<b>...AUR BHI.</b>" if len(text) > 4000 else text)


# ==================== UPLOAD HELPERS ====================

def _upload_to_uguu(filepath: str, filename: str, ctype: str) -> Optional[str]:
    """
    uguu.se upload → returns a browser-viewable URL.
    Images open in browser (VIEW), videos play inline in some browsers.
    """
    try:
        with open(filepath, "rb") as f:
            r = _SESSION.post(
                "https://uguu.se/upload",
                files={"files[]": (filename, f, ctype)},
                timeout=30,
            )
        if r.status_code == 200:
            d = r.json()
            if d.get("success") and d.get("files"):
                url = d["files"][0].get("url")
                if url:
                    return url
    except Exception as e:
        logger.warning(f"uguu upload failed: {e}")
    return None


def _upload_to_0x0(filepath: str, filename: str) -> Tuple[Optional[str], str]:
    """
    0x0.st upload → returns (direct-download URL, management token).
    The 'secret' field asks 0x0.st to return a longer, unguessable URL and
    an X-Token header that can later be used to delete the file (used by
    deactivate_link()). Browser will prompt Save-As / download dialog.
    """
    try:
        with open(filepath, "rb") as f:
            r = _SESSION.post(
                "https://0x0.st",
                files={"file": (filename, f)},
                data={"secret": ""},
                timeout=60,
            )
        if r.status_code == 200:
            txt = r.text.strip()
            if txt.startswith("http"):
                token = r.headers.get("X-Token", "")
                return txt, token
    except Exception as e:
        logger.warning(f"0x0.st upload failed: {e}")
    return None, ""


def _upload_to_tempsh(filepath: str, filename: str) -> Optional[str]:
    """temp.sh → direct download URL"""
    try:
        with open(filepath, "rb") as f:
            r = _SESSION.post("https://temp.sh/upload", files={"file": (filename, f)}, timeout=60)
        if r.status_code == 200:
            txt = r.text.strip()
            if txt.startswith("http"):
                return txt
    except Exception as e:
        logger.warning(f"temp.sh upload failed: {e}")
    return None


def _upload_to_fileio(filepath: str, filename: str) -> Optional[str]:
    """file.io → JSON response with link"""
    try:
        with open(filepath, "rb") as f:
            r = _SESSION.post("https://file.io/", files={"file": (filename, f)}, timeout=60)
        if r.status_code in (200, 201):
            try:
                d = r.json()
                if d.get("success"):
                    return d.get("link") or d.get("url")
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"file.io upload failed: {e}")
    return None


def upload_media_dual(
    filepath: str,
    filename: str,
    ctype: str,
    mode: str,
) -> Tuple[Optional[str], Optional[str], str, str]:
    """
    Upload the file TWICE (or to two services) to produce:
      view_url        — browser-friendly, no forced download
      dl_url          — forces download dialog in browser
      view_del_token  — 0x0.st management token, only set if view_url ended
                         up on 0x0.st (used later by deactivate_link())
      dl_del_token    — same, for dl_url

    Returns (view_url, dl_url, view_del_token, dl_del_token). Either URL may
    be None on failure. Strategy per mode:
      image → VIEW: uguu.se  |  DOWNLOAD: 0x0.st (or temp.sh fallback)
      video → VIEW: uguu.se  |  DOWNLOAD: 0x0.st (or temp.sh fallback)
      file  → VIEW: file.io  |  DOWNLOAD: 0x0.st (or temp.sh fallback)
              (file.io gives a share-page that previews; 0x0.st forces save)
    """
    view_url: Optional[str] = None
    dl_url:   Optional[str] = None
    view_del_token = ""
    dl_del_token = ""

    if mode in ("image", "video"):
        # VIEW path: uguu.se (inline browser preview)
        view_url = _upload_to_uguu(filepath, filename, ctype)
        if not view_url:
            # fallback: 0x0.st (may still show in some browsers for images)
            view_url, view_del_token = _upload_to_0x0(filepath, filename)

        # DOWNLOAD path: 0x0.st (Content-Disposition: attachment)
        dl_url, dl_del_token = _upload_to_0x0(filepath, filename)
        if not dl_url:
            dl_url = _upload_to_tempsh(filepath, filename)
            dl_del_token = ""
        if not dl_url:
            # last resort: reuse view url (at least user can right-click → save)
            dl_url = view_url
            dl_del_token = ""

    else:  # file mode
        # VIEW path: file.io returns a share page with preview
        view_url = _upload_to_fileio(filepath, filename)
        if not view_url:
            view_url = _upload_to_uguu(filepath, filename, ctype)

        # DOWNLOAD path: 0x0.st
        dl_url, dl_del_token = _upload_to_0x0(filepath, filename)
        if not dl_url:
            dl_url = _upload_to_tempsh(filepath, filename)
            dl_del_token = ""
        if not dl_url:
            dl_url = view_url
            dl_del_token = ""

    return view_url, dl_url, view_del_token, dl_del_token


def _check_mirror(mirror: str) -> str:
    base = mirror.split("/upload")[0]
    tag = "🖼" if "uguu" in mirror else "📁"
    try:
        r = _SESSION.head(base, timeout=5, allow_redirects=True)
        state = "<b>🟢 ONLINE</b>" if r.status_code < 500 else f"<b>🟡 {r.status_code}</b>"
    except Exception:
        state = "<b>🔴 DOWN</b>"
    return f"{tag} <code>{html.escape(base)}</code>\n{state}\n"


async def render_status(msg) -> None:
    all_mirrors = [
        "https://uguu.se/upload",
        "https://0x0.st",
        "https://temp.sh/upload",
        "https://file.io/",
    ]
    lines = ["<b>🩺 MIRROR STATUS</b>", "<b>━━━━━━━━━━━━━━━━━━</b>\n"]
    lines.extend(_POOL.map(_check_mirror, all_mirrors))
    await msg.edit_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=back_inline())


# ==================== HANDLERS ====================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user = update.effective_user
    await update.message.reply_text(
        home_text(user.first_name or "DOST"),
        parse_mode=ParseMode.HTML,
        reply_markup=main_reply_kb(user.id),
    )


def _clear_pending_file(context: ContextTypes.DEFAULT_TYPE):
    path = context.user_data.pop("pending_path", None)
    if path and os.path.exists(path):
        try:
            os.unlink(path)
        except Exception:
            pass


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = _norm(update.message.text or "")
    user = update.effective_user
    uid = user.id
    state = context.user_data.get("state")

    # ── CANCEL anytime ──
    if text in (BTN_CANCEL, "/cancel"):
        _clear_pending_file(context)
        context.user_data.clear()
        await update.message.reply_text(
            "<b>✅ CANCELLED.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_reply_kb(uid),
        )
        return

    # ── AFTER MEDIA: VIEW or DOWNLOAD → CORRECT LINK ──
    if state == WAIT_ACTION:
        view_url  = context.user_data.get("view_url")
        dl_url    = context.user_data.get("dl_url")
        mode      = context.user_data.get("mode", "file")
        fname     = context.user_data.get("pending_name", "file")
        data_key  = context.user_data.get("data_key", "file_links")
        link_id   = context.user_data.get("link_id", "")

        view_labels = {
            "image": "👁 GEN IMAGE VIEW",
            "file":  "👁 GEN FILE VIEW",
            "video": "👁 GEN VIDEO VIEW",
        }
        dl_labels = {
            "image": "⬇️ GEN IMAGE DOWNLOAD",
            "file":  "⬇️ GEN FILE DOWNLOAD",
            "video": "⬇️ GEN VIDEO DOWNLOAD",
        }

        is_view = text in (BTN_VIEW, view_labels.get(mode, ""), "👁 ONLY VIEW")
        is_dl   = text in (BTN_DOWNLOAD, dl_labels.get(mode, ""), "⬇️ DOWNLOAD")

        # Fuzzy match on keywords (handles bold-unicode edge cases)
        if not is_view and not is_dl:
            upper = text.upper()
            if "VIEW" in upper and "DOWN" not in upper:
                is_view = True
            elif "DOWNLOAD" in upper or "DOWN" in upper:
                is_dl = True

        if not view_url and not dl_url:
            context.user_data.clear()
            await update.message.reply_text(
                "<b>❌ SESSION EXPIRE. DOBARA FEATURE SELECT KARO.</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=main_reply_kb(uid),
            )
            return

        if is_view or is_dl:
            kind   = mode.upper()
            action = "VIEW" if is_view else "DOWNLOAD"
            url_kind = "view" if is_view else "dl"

            # The link handed to the user: if PUBLIC_BASE_URL is set this is
            # OUR link_server.py proxy — real deactivation, every mirror.
            # Otherwise it's the raw mirror URL (old behaviour).
            chosen_url = public_link(data_key, link_id, url_kind) if link_id else (
                view_url if is_view else dl_url
            )
            if not chosen_url:
                chosen_url = view_url or dl_url

            if is_view:
                note = (
                    f"<b>👁 ONLY VIEW LINK</b>\n"
                    f"<b>LINK OPEN KARO → SIRF DEKHO</b>\n"
                    f"<b>⚠️ DOWNLOAD OPTION NAHI HOGA</b>"
                )
                btn_label = "👁 OPEN TO VIEW"
            else:
                note = (
                    f"<b>⬇️ DOWNLOAD LINK</b>\n"
                    f"<b>LINK OPEN KARO → DOWNLOAD DIALOG AAYEGA</b>\n"
                    f"<b>⚠️ SIRF DOWNLOAD MILEGA</b>"
                )
                btn_label = "⬇️ OPEN TO DOWNLOAD"

            body = (
                f"<b>✅ {kind} {action} LINK READY</b>\n"
                f"<b>━━━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
                f"<b>📄 FILE:</b> <code>{html.escape(fname)}</code>\n"
                f"<b>🔗 LINK:</b>\n<code>{html.escape(chosen_url or 'N/A')}</code>\n\n"
                f"{note}"
            )

            _clear_pending_file(context)
            context.user_data.clear()

            await update.message.reply_text(
                body,
                parse_mode=ParseMode.HTML,
                reply_markup=open_link_kb(chosen_url or "", btn_label),
            )
            await update.message.reply_text(
                "<b>👇 MAIN MENU</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=main_reply_kb(uid),
            )
            return

        await update.message.reply_text(
            "<b>⚠️ SIRF VIEW YA DOWNLOAD BUTTON SELECT KARO.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=action_reply_kb(mode),
        )
        return

    # ── ADMIN INPUT ──
    if state in (WAIT_ADD_ADMIN, WAIT_REM_ADMIN):
        await process_admin_input(update, context, text)
        return

    # ── WAITING MEDIA but user pressed menu text ──
    if state == WAIT_MEDIA and text not in (
        BTN_IMG, BTN_FILE, BTN_VIDEO, BTN_STATUS, BTN_ABOUT, BTN_CHANNEL,
        BTN_OWNER, BTN_ADMIN, BTN_BACK,
    ):
        mode = context.user_data.get("mode", "image")
        await update.message.reply_text(
            f"<b>⚠️ PEHLE APNI {mode.upper()} BHEJO!</b>\n"
            f"<b>YA CANCEL DABAO.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_reply_kb(),
        )
        return

    if text == BTN_BACK:
        _clear_pending_file(context)
        context.user_data.clear()
        await update.message.reply_text(
            home_text(user.first_name or "DOST"),
            parse_mode=ParseMode.HTML,
            reply_markup=main_reply_kb(uid),
        )
        return

    # ── FEATURE SELECT ──
    if text == BTN_IMG:
        context.user_data.clear()
        context.user_data["state"] = WAIT_MEDIA
        context.user_data["mode"] = "image"
        await update.message.reply_text(
            prompt_media("image"), parse_mode=ParseMode.HTML, reply_markup=cancel_reply_kb()
        )
        return

    if text == BTN_FILE:
        context.user_data.clear()
        context.user_data["state"] = WAIT_MEDIA
        context.user_data["mode"] = "file"
        await update.message.reply_text(
            prompt_media("file"), parse_mode=ParseMode.HTML, reply_markup=cancel_reply_kb()
        )
        return

    if text == BTN_VIDEO:
        context.user_data.clear()
        context.user_data["state"] = WAIT_MEDIA
        context.user_data["mode"] = "video"
        await update.message.reply_text(
            prompt_media("video"), parse_mode=ParseMode.HTML, reply_markup=cancel_reply_kb()
        )
        return

    if text == BTN_STATUS:
        msg = await update.message.reply_text(
            "<b>🔍 CHECKING MIRRORS...</b>", parse_mode=ParseMode.HTML
        )
        await render_status(msg)
        return

    if text == BTN_ABOUT:
        await update.message.reply_text(
            about_text(), parse_mode=ParseMode.HTML, reply_markup=back_inline()
        )
        return

    if text == BTN_CHANNEL:
        await update.message.reply_text(
            f"<b>📢 JOIN OUR CHANNEL:</b>\n\n<b>https://t.me/{CHANNEL_USERNAME}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_reply_kb(uid),
        )
        return

    if text == BTN_OWNER:
        if not is_owner(uid):
            await update.message.reply_text(
                "<b>❌ ACCESS DENIED! SIRF OWNER.</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=main_reply_kb(uid),
            )
            return
        await update.message.reply_text(
            owner_text(), parse_mode=ParseMode.HTML, reply_markup=owner_reply_kb()
        )
        return

    if text == BTN_ADMIN:
        if is_owner(uid):
            await update.message.reply_text(
                owner_text(), parse_mode=ParseMode.HTML, reply_markup=owner_reply_kb()
            )
            return
        if not is_admin(uid):
            await update.message.reply_text(
                "<b>❌ ACCESS DENIED!</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=main_reply_kb(uid),
            )
            return
        await update.message.reply_text(
            admin_text(), parse_mode=ParseMode.HTML, reply_markup=admin_reply_kb()
        )
        return

    if text == BTN_IMG_LINKS:
        if not is_admin(uid):
            return
        kb = owner_reply_kb() if is_owner(uid) else admin_reply_kb()
        await update.message.reply_text(
            links_text(load_data().get("image_links", []), "🖼 IMAGE LINKS", "image_links"),
            parse_mode=ParseMode.HTML, reply_markup=kb,
        )
        return

    if text == BTN_FILE_LINKS:
        if not is_admin(uid):
            return
        kb = owner_reply_kb() if is_owner(uid) else admin_reply_kb()
        await update.message.reply_text(
            links_text(load_data().get("file_links", []), "📂 FILE LINKS", "file_links"),
            parse_mode=ParseMode.HTML, reply_markup=kb,
        )
        return

    if text == BTN_VIDEO_LINKS:
        if not is_admin(uid):
            return
        kb = owner_reply_kb() if is_owner(uid) else admin_reply_kb()
        await update.message.reply_text(
            links_text(load_data().get("video_links", []), "🎬 VIDEO LINKS", "video_links"),
            parse_mode=ParseMode.HTML, reply_markup=kb,
        )
        return

    if text == BTN_ACTIVE_LINKS:
        if not is_owner(uid):
            await update.message.reply_text(
                "<b>❌ SIRF OWNER.</b>",
                parse_mode=ParseMode.HTML, reply_markup=main_reply_kb(uid),
            )
            return
        body, kb = active_links_kb(0)
        await update.message.reply_text(body, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if text == BTN_ADD_ADMIN:
        if not is_owner(uid):
            await update.message.reply_text(
                "<b>❌ ADD ADMIN SIRF OWNER.</b>",
                parse_mode=ParseMode.HTML, reply_markup=main_reply_kb(uid),
            )
            return
        context.user_data["state"] = WAIT_ADD_ADMIN
        await update.message.reply_text(
            "<b>➕ ADD ADMIN</b>\n<b>USER ID BHEJO (EXAMPLE: 123456789)</b>",
            parse_mode=ParseMode.HTML, reply_markup=cancel_reply_kb(),
        )
        return

    if text == BTN_REM_ADMIN:
        if not is_owner(uid):
            await update.message.reply_text(
                "<b>❌ REMOVE ADMIN SIRF OWNER.</b>",
                parse_mode=ParseMode.HTML, reply_markup=main_reply_kb(uid),
            )
            return
        admins = load_data().get("admins", [])
        if not admins:
            await update.message.reply_text(
                "<b>❌ KOI ADMIN NAHI.</b>",
                parse_mode=ParseMode.HTML, reply_markup=owner_reply_kb(),
            )
            return
        context.user_data["state"] = WAIT_REM_ADMIN
        al = "\n".join(f"<b>• <code>{a}</code></b>" for a in admins)
        await update.message.reply_text(
            f"<b>➖ REMOVE ADMIN</b>\n{al}\n\n<b>USER ID BHEJO:</b>",
            parse_mode=ParseMode.HTML, reply_markup=cancel_reply_kb(),
        )
        return

    if text == BTN_ADM_LIST:
        if not is_admin(uid):
            return
        d = load_data()
        admins = d.get("admins", [])
        al = "\n".join(f"<b>• <code>{a}</code></b>" for a in admins) if admins else "<b>NONE</b>"
        kb = owner_reply_kb() if is_owner(uid) else admin_reply_kb()
        await update.message.reply_text(
            f"<b>👥 ADMIN LIST</b>\n<b>👑 OWNER: <code>{OWNER_ID}</code></b>\n\n"
            f"<b>EXTRA ADMINS:</b>\n{al}",
            parse_mode=ParseMode.HTML, reply_markup=kb,
        )
        return

    await update.message.reply_text(
        "<b>👇 PEHLE FEATURE SELECT KARO:</b>\n"
        "<b>📸 GEN IMAGE LINK</b>\n"
        "<b>📁 GEN FILE LINK</b>\n"
        "<b>🎬 GEN VIDEO LINK</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_reply_kb(uid),
    )


async def process_admin_input(update, context, text: str):
    user = update.effective_user
    if not is_owner(user.id):
        context.user_data.clear()
        await update.message.reply_text(
            "<b>❌ SIRF OWNER.</b>",
            parse_mode=ParseMode.HTML, reply_markup=main_reply_kb(user.id),
        )
        return
    state = context.user_data.get("state")
    try:
        target_id = int(text)
    except ValueError:
        await update.message.reply_text(
            "<b>❌ SIRF NUMBER USER ID BHEJO.</b>",
            parse_mode=ParseMode.HTML, reply_markup=cancel_reply_kb(),
        )
        return
    data = load_data()
    data.setdefault("admins", [])
    if state == WAIT_ADD_ADMIN:
        if target_id == OWNER_ID or target_id in [int(x) for x in data["admins"]]:
            await update.message.reply_text(
                "<b>⚠️ PEHLE SE OWNER/ADMIN HAI.</b>",
                parse_mode=ParseMode.HTML, reply_markup=owner_reply_kb(),
            )
        else:
            data["admins"].append(target_id)
            save_data(data)
            await update.message.reply_text(
                f"<b>✅ ADMIN ADD: <code>{target_id}</code></b>",
                parse_mode=ParseMode.HTML, reply_markup=owner_reply_kb(),
            )
            try:
                await context.bot.send_message(
                    target_id, "<b>🎉 AAP AB ADMIN HO!</b>", parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
    elif state == WAIT_REM_ADMIN:
        if target_id not in [int(x) for x in data["admins"]]:
            await update.message.reply_text(
                "<b>❌ YE ADMIN NAHI.</b>",
                parse_mode=ParseMode.HTML, reply_markup=owner_reply_kb(),
            )
        else:
            data["admins"] = [x for x in data["admins"] if int(x) != target_id]
            save_data(data)
            await update.message.reply_text(
                f"<b>✅ ADMIN REMOVE: <code>{target_id}</code></b>",
                parse_mode=ParseMode.HTML, reply_markup=owner_reply_kb(),
            )
    context.user_data.clear()


async def _process_media(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    received_type: str,
    tg_file,
    filename: str,
    fsize: int,
    mime: str,
):
    """received_type: image | file | video"""
    mode  = context.user_data.get("mode")
    state = context.user_data.get("state")
    uid   = update.effective_user.id

    if state != WAIT_MEDIA or mode not in ("image", "file", "video"):
        await update.message.reply_text(
            "<b>⚠️ PEHLE FEATURE SELECT KARO!</b>\n"
            "<b>📸 GEN IMAGE LINK / 📁 GEN FILE LINK / 🎬 GEN VIDEO LINK</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_reply_kb(uid),
        )
        return

    if received_type != mode:
        need = mode.upper()
        got  = received_type.upper()
        await update.message.reply_text(
            f"<b>❌ WRONG TYPE!</b>\n"
            f"<b>━━━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
            f"<b>AAPNE SELECT KIYA: GEN {need} LINK</b>\n"
            f"<b>AAPNE BHEJA: {got}</b>\n\n"
            f"<b>⚠️ SIRF {need} BHEJO IS FEATURE PAR.</b>\n"
            f"<b>YA DUSRA FEATURE SELECT KARO.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_reply_kb(),
        )
        return

    limits = {"image": MAX_IMAGE_SIZE_MB, "file": MAX_FILE_SIZE_MB, "video": MAX_VIDEO_SIZE_MB}
    limit = limits[mode]
    if fsize > limit * 1024 * 1024:
        await update.message.reply_text(
            f"<b>⚠️ FILE BAHUT BADA ({fsize/1048576:.1f} MB)</b>\n"
            f"<b>MAX: {limit} MB</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    status = await update.message.reply_text(
        f"<b>⏳ UPLOADING {mode.upper()}... (VIEW + DOWNLOAD DONO BAN RAHE HAIN)</b>",
        parse_mode=ParseMode.HTML,
    )

    suffix = os.path.splitext(filename)[1] or (
        ".jpg" if mode == "image" else (".mp4" if mode == "video" else ".bin")
    )
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
            tmp = f.name
        await tg_file.download_to_drive(tmp)

        # ── CORE FIX: Upload to two services for VIEW vs DOWNLOAD ──
        view_url, dl_url, view_tok, dl_tok = upload_media_dual(
            tmp, filename, mime or "application/octet-stream", mode
        )

        if not view_url and not dl_url:
            await status.edit_text(
                "<b>❌ UPLOAD FAILED. THODI DER BAAD TRY KARO.</b>",
                parse_mode=ParseMode.HTML,
            )
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)
            return

        # Determine data_key for logs
        data_key = "video_links" if mode == "video" else ("image_links" if mode == "image" else "file_links")

        # Save ONE record now (both urls together) so it has an id the owner
        # can deactivate immediately — even before the user picks VIEW/DOWNLOAD.
        link_id = save_link(
            data_key, uid, update.effective_user.username or str(uid), filename,
            view_url=view_url or "", dl_url=dl_url or "",
            view_del_token=view_tok, dl_del_token=dl_tok,
        )

        # Store session state
        context.user_data["state"]        = WAIT_ACTION
        context.user_data["view_url"]     = view_url
        context.user_data["dl_url"]       = dl_url
        context.user_data["link_id"]      = link_id
        context.user_data["pending_name"] = filename
        context.user_data["data_key"]     = data_key
        context.user_data["mode"]         = mode  # keep mode

        # Cleanup temp file
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except Exception:
                pass

        view_status  = "✅" if view_url else "❌"
        dl_status    = "✅" if dl_url   else "❌"

        await status.edit_text(
            f"<b>✅ {mode.upper()} UPLOAD COMPLETE</b>\n"
            f"<b>━━━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
            f"<b>📄 {html.escape(filename)}</b>\n\n"
            f"<b>{view_status} VIEW LINK READY</b>\n"
            f"<b>{dl_status} DOWNLOAD LINK READY</b>\n\n"
            f"<b>AB CHUNO — SIRF EK:</b>\n"
            f"<b>• 👁 GEN {mode.upper()} VIEW  → Sirf dekhega, download nahi</b>\n"
            f"<b>• ⬇️ GEN {mode.upper()} DOWNLOAD → Direct download dialog</b>\n\n"
            f"<b>👇 NEECHE BUTTON DABAO</b>",
            parse_mode=ParseMode.HTML,
        )
        await update.message.reply_text(
            "<b>👇 VIEW YA DOWNLOAD SELECT KARO</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=action_reply_kb(mode),
        )

    except Exception as e:
        logger.error(f"media err: {e}")
        try:
            await status.edit_text(
                f"<b>❌ ERROR:</b> <code>{html.escape(str(e))}</code>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except Exception:
                pass


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    tg = await photo.get_file()
    await _process_media(
        update, context, "image", tg,
        f"image_{int(time.time())}.jpg",
        photo.file_size or 0,
        "image/jpeg",
    )


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    video = update.message.video
    tg = await video.get_file()
    await _process_media(
        update, context, "video", tg,
        video.file_name or f"video_{int(time.time())}.mp4",
        video.file_size or 0,
        video.mime_type or "video/mp4",
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc  = update.message.document
    mime = doc.mime_type or "application/octet-stream"
    name = doc.file_name or f"file_{int(time.time())}"
    tg   = await doc.get_file()
    if mime.startswith("image/"):
        rtype = "image"
    elif mime.startswith("video/"):
        rtype = "video"
    else:
        rtype = "file"
    await _process_media(update, context, rtype, tg, name, doc.file_size or 0, mime)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data_str = q.data or ""
    uid = q.from_user.id

    if data_str.startswith("deact:"):
        if not is_owner(uid):
            await q.answer("SIRF OWNER!", show_alert=True)
            return
        try:
            _, key, link_id = data_str.split(":", 2)
        except ValueError:
            await q.answer("BAD REQUEST", show_alert=True)
            return
        remote_deleted, note = deactivate_link(key, link_id)
        await q.answer(("✅ DEACTIVATED — " + note)[:200], show_alert=True)
        body, kb = active_links_kb(0)
        try:
            await q.edit_message_text(body, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            pass
        return

    if data_str.startswith("actlinks:"):
        if not is_owner(uid):
            await q.answer("SIRF OWNER!", show_alert=True)
            return
        try:
            page = int(data_str.split(":", 1)[1])
        except Exception:
            page = 0
        try:
            await q.answer()
        except Exception:
            pass
        body, kb = active_links_kb(page)
        try:
            await q.edit_message_text(body, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            pass
        return

    try:
        await q.answer()
    except Exception:
        pass
    if (q.data or "") == "home":
        _clear_pending_file(context)
        context.user_data.clear()
        user = q.from_user
        try:
            await q.edit_message_text(
                home_text(user.first_name or "DOST"), parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
        try:
            await context.bot.send_message(
                q.message.chat_id,
                "<b>👇 MAIN MENU</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=main_reply_kb(user.id),
            )
        except Exception:
            pass


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Error:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "<b>❌ ERROR. DOBARA TRY KARO.</b>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass


async def post_init(app: Application):
    await app.bot.set_my_commands([BotCommand("start", "🏠 HOME MENU")])
    logger.info("✅ PREMIUM BOT READY!")


def main():
    if not TELEGRAM_TOKEN or ":" not in TELEGRAM_TOKEN:
        print("❌ BOT TOKEN MISSING — set the BOT_TOKEN environment variable.")
        return
    if not OWNER_ID:
        print("❌ OWNER_ID MISSING — set the OWNER_ID environment variable.")
        return

    # Python 3.10+ (and especially 3.12+/3.14) no longer auto-creates an
    # event loop for the main thread. python-telegram-bot's run_polling()
    # still expects one to exist, so we create it explicitly here — this
    # makes the bot work on ANY Python version without relying on runtime.txt.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    builder = Application.builder().token(TELEGRAM_TOKEN).concurrent_updates(True).post_init(post_init)

    # If you're running a local Bot API server (needed for files >20MB, up to
    # 550MB and beyond), point the app at it here.
    if BOT_API_BASE_URL:
        builder = builder.base_url(BOT_API_BASE_URL)
    if BOT_API_BASE_FILE_URL:
        builder = builder.base_file_url(BOT_API_BASE_FILE_URL)

    app = builder.build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(error_handler)
    logger.info("🚀 FLOW: FEATURE → MEDIA → VIEW/DOWNLOAD → CORRECT LINK")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
