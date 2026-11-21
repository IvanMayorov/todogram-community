"""Mini app: static page + JSON API, in the same process as the bot."""
import asyncio
import hashlib
import hmac
import html
import json
import logging
import re
import secrets
import time
from urllib.parse import quote
from pathlib import Path
from weakref import WeakValueDictionary

from aiogram import Bot
from aiogram.utils.web_app import safe_parse_webapp_init_data
from aiohttp import web

from . import media as tgmedia
from . import ai, deadlines, live, tasks
from . import search as fts
from .config import ROOT, settings
from .bg import spawn
from .db import HISTORY_FILTERS, NOTIF_SECTIONS, db, has_chat, standalone
from .render import bot_link_name, fmt_date, item_src, miniapp_url, msg_link, source_chat

log = logging.getLogger(__name__)
STATIC = ROOT / "webapp"
COLORS = ["#e11d48", "#f59e0b", "#0ea5e9", "#16a34a", "#7c3aed", "#db2777", "#0d9488", "#ea580c"]
# «is this person in the chat»: asked from Telegram at most every 10 minutes per person and chat. The answer is
# refreshed at once from what the bot sees: a message from them, someone joining or leaving (note_member).
MEMBER_TTL = 600
MEDIA_TTL = 3600
_member_cache: dict[tuple[int, int], tuple[float, bool]] = {}
_chat_photo: dict[int, tuple[float, str | None]] = {}   # chat_id -> (checked_at, small_file_id)
CHAT_PHOTO_TTL = 3600


async def chat_photo_id(bot: Bot, chat_id: int) -> str | None:
    """File id of the chat's avatar (cached for an hour), None if the chat has no photo."""
    if not has_chat(chat_id):
        return None
    hit = _chat_photo.get(chat_id)
    if hit and time.time() - hit[0] < CHAT_PHOTO_TTL:
        return hit[1]
    try:
        chat = await bot.get_chat(chat_id)
        fid = chat.photo.small_file_id if chat.photo else None
    except Exception:
        fid = hit[1] if hit else None
    _chat_photo[chat_id] = (time.time(), fid)
    return fid


def sign_chat_photo(chat_id: int) -> str:
    exp = _stable_exp()
    sig = hmac.new(_media_key(), f"chat:{chat_id}:{exp}".encode(), "sha256").hexdigest()[:32]
    return f"/api/chatphoto/{chat_id}?e={exp}&s={sig}"


_user_photo: dict[int, tuple[float, str | None]] = {}   # user_id -> (checked_at, smallest file_id)
_bot: Bot | None = None                                  # set in make_app; the serializers need it for avatars


async def user_photo_id(bot: Bot, user_id: int) -> str | None:
    """File id of the person's current avatar (cached for an hour). None when there is none
    or their privacy settings hide it from the bot."""
    hit = _user_photo.get(user_id)
    if hit and time.time() - hit[0] < CHAT_PHOTO_TTL:
        return hit[1]
    try:
        res = await bot.get_user_profile_photos(user_id, limit=1)
        fid = res.photos[0][0].file_id if res.photos else None   # [0] is the smallest size
    except Exception:
        fid = hit[1] if hit else None
    _user_photo[user_id] = (time.time(), fid)
    return fid


def sign_user_photo(user_id: int) -> str:
    exp = _stable_exp()
    sig = hmac.new(_media_key(), f"user:{user_id}:{exp}".encode(), "sha256").hexdigest()[:32]
    return f"/api/userphoto/{user_id}?e={exp}&s={sig}"


async def actor_photo(user_id) -> str | None:
    """Signed avatar link for a history row; None for the agent, the bot and people without a visible photo."""
    if not _bot or not user_id or user_id <= 0:
        return None
    return sign_user_photo(user_id) if await user_photo_id(_bot, user_id) else None


def _media_key() -> bytes:
    base = settings.media_secret or settings.bot_token
    return hashlib.sha256(("media:" + base).encode()).digest()


def _stable_exp() -> int:
    """Expiry rounded to the hour: the same link for an hour, so phones can cache pictures
    instead of downloading them (and blinking) on every open. Valid 1–2 hours."""
    return (int(time.time()) // MEDIA_TTL + 2) * MEDIA_TTL


def sign_media(task_id: int, idx: int) -> str:
    exp = _stable_exp()
    sig = hmac.new(_media_key(), f"{task_id}:{idx}:{exp}".encode(), "sha256").hexdigest()[:32]
    return f"/api/media/{task_id}/{idx}?e={exp}&s={sig}"


def sign_audio(task_id: int, n: int) -> str:
    exp = _stable_exp()
    sig = hmac.new(_media_key(), f"audio:{task_id}:{n}:{exp}".encode(), "sha256").hexdigest()[:32]
    return f"/api/audio/{task_id}/{n}?e={exp}&s={sig}"


async def task_transcripts(t: dict) -> list[dict]:
    """Stored transcripts with the Telegram file of each voice message. Older tasks kept only the
    message id: the file is looked up in the chat buffer and saved while the message is still there."""
    trs = json.loads(t["transcripts"] or "[]")
    changed = False
    for x in trs:
        if "file_id" not in x and x.get("message_id"):
            m = await db.message(source_chat(t, x), x["message_id"])
            if m and m["kind"] in ("voice", "audio") and m["file_id"]:
                x["file_id"], x["mime"], x["duration"] = m["file_id"], m["mime"], m.get("duration")
                changed = True
    if changed:
        await db.x("UPDATE tasks SET transcripts=? WHERE id=?", (json.dumps(trs, ensure_ascii=False), t["id"]))
    return trs


def voice_key(entry: dict) -> str:
    return hashlib.sha256((entry.get("text") or "").encode()).hexdigest()


_voice_locks = WeakValueDictionary()
_voice_slots = asyncio.Semaphore(4)


async def api_voice_summary(request):
    task_id = int(request.match_info["task_id"])
    key = request.match_info["key"]
    # Share a generation between people who open the same recording together.
    lock = _voice_locks.setdefault((task_id, key), asyncio.Lock())
    async with lock:
        t = await task_for_user(request, task_id)
        tr = next((x for x in json.loads(t["transcripts"] or "[]") if voice_key(x) == key), None)
        if not tr or not (tr.get("text") or "").strip():
            raise web.HTTPNotFound(text="transcript not found")
        summary = json.loads(t["voice_summaries"]).get(key)
        if summary is None:
            try:
                async with _voice_slots:
                    summary = await ai.summarize_voice(tr["text"])
            except ai.AIError:
                raise web.HTTPFailedDependency(text="voice summary unavailable")
            # Update only this entry so simultaneous summaries of different recordings both survive.
            await db.x("UPDATE tasks SET voice_summaries=json_set(voice_summaries, ?, json(?)) WHERE id=?",
                       ("$." + key, json.dumps(summary, ensure_ascii=False), task_id))
        return web.json_response(summary)


def sign_file(task_id: int, n: int) -> str:
    exp = _stable_exp()
    sig = hmac.new(_media_key(), f"file:{task_id}:{n}:{exp}".encode(), "sha256").hexdigest()[:32]
    return f"/api/file/{task_id}/{n}?e={exp}&s={sig}"


async def task_files(t: dict) -> list[dict]:
    """Files attached to the task's messages. Tasks made before files were kept get them from the chat
    buffer once (messages of the task that are still there); after that the list lives in the task."""
    if t.get("files") is not None:
        return json.loads(t["files"] or "[]")
    rows = await db.all("SELECT * FROM messages WHERE chat_id IN (?,?) AND task_id=? AND kind='document' "
                        "ORDER BY date, message_id", (t["chat_id"], source_chat(t), t["id"]))
    files = [{**tasks.file_entry(m), "source_chat_id": m["chat_id"]} for m in rows]
    await db.set_files(t["id"], files)
    return files


CACHE_DAYS = 30   # a cached Telegram file nobody opened for this long is removed (it is downloaded again if needed)


async def cached_file(bot: Bot, file_id: str, prefix: str = "", ext: str = "") -> Path:
    """A Telegram file in the local cache, downloaded on first use. Every use refreshes its date, so
    media.prune_cache removes only what nobody has opened for CACHE_DAYS."""
    cache = Path(settings.cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    f = cache / (prefix + hashlib.sha256(file_id.encode()).hexdigest()[:40] + ext)
    if f.exists():
        try:
            f.touch()
        except OSError:
            pass
        return f
    try:
        data = await tgmedia.download(bot, file_id)
    except Exception:
        raise web.HTTPNotFound()
    tmp = f.with_name(f.name + ".part")   # a half-written file must never be served
    tmp.write_bytes(data)
    tmp.replace(f)
    return f


# ---- screenshots and videos on comments: media/task_<id>/c/<random>.<ext>, served by a signed link ----
CFILE_RE = re.compile(r"^[0-9a-f]{24}\.(jpg|png|webp|gif|mp4|mov|webm)$")
CFILE_TYPES = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
               ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}
CHUNK_MAX = 8 * 1024 * 1024        # one request of an upload (nginx lets ~16 MB through, a video comes in parts)
CFILE_MAX = 100 * 1024 * 1024      # one file: a screenshot or a video


def cfile_dir(task_id: int) -> Path:
    return Path(settings.db_path).parent / "media" / f"task_{task_id}" / "c"


def cfile_kind(head: bytes) -> tuple[str, str] | None:
    """(extension, kind) by the file's first bytes — never by its name."""
    ext = next((e for magic, e in IMAGE_MAGIC.items() if head.startswith(magic)), None)
    if ext and (ext != ".webp" or head[8:12] == b"WEBP"):
        return ext, "photo"
    if head[4:8] == b"ftyp":
        return (".mov" if head[8:10] == b"qt" else ".mp4"), "video"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return ".webm", "video"
    return None


def sign_cfile(task_id: int, name: str) -> str:
    exp = _stable_exp()
    sig = hmac.new(_media_key(), f"cfile:{task_id}:{name}:{exp}".encode(), "sha256").hexdigest()[:32]
    return f"/api/cfile/{task_id}/{name}?e={exp}&s={sig}"


def comment_files(task_id: int, c: dict) -> list[dict]:
    return [{"url": sign_cfile(task_id, x["f"]), "kind": x["kind"]}
            for x in json.loads(c.get("files") or "[]") if CFILE_RE.match(x.get("f", ""))]


def check_media_sig(task_id: int, idx: int, exp: str, sig: str) -> bool:
    try:
        if int(exp) < time.time():
            return False
    except ValueError:
        return False
    good = hmac.new(_media_key(), f"{task_id}:{idx}:{exp}".encode(), "sha256").hexdigest()[:32]
    return hmac.compare_digest(good, sig)


# ---------------- auth ----------------

def user_from_request(request: web.Request) -> dict:
    if request.get("site_user"):   # signed in on the site through the bot (see site_session)
        return request["site_user"]
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("tma "):
        raise web.HTTPUnauthorized(text="no initData")
    try:
        data = safe_parse_webapp_init_data(settings.bot_token, auth[4:])
    except ValueError:
        raise web.HTTPUnauthorized(text="bad initData")
    if time.time() - data.auth_date.timestamp() > 24 * 3600:
        raise web.HTTPUnauthorized(text="initData expired")
    if not data.user:
        raise web.HTTPUnauthorized(text="no user")
    name = " ".join(x for x in (data.user.first_name, data.user.last_name) if x)
    return {"id": data.user.id, "name": name, "start_param": data.start_param}


PERSONAL = "Без проекта"   # tasks without a project live in the person's own one: its id is theirs (no chat behind it)


def is_personal(chat_id: int) -> bool:
    return chat_id > 0   # group chats have negative ids


async def is_member(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Can see and work with the project: is in its chat, or was let in by the invite link.
    «Без проекта» — only its owner. A project without a chat — its maker and whoever they let in."""
    if is_personal(chat_id):
        return chat_id == user_id
    if standalone(chat_id):
        return await db.has_access(chat_id, user_id)
    return await db.has_access(chat_id, user_id) or await in_chat(bot, chat_id, user_id)


def note_member(chat_id: int, user_id: int, ok: bool):
    """The bot saw it itself (a message, a join, a leave): no need to ask Telegram."""
    if user_id and user_id > 0:
        _member_cache[(chat_id, user_id)] = (time.time(), ok)


async def in_chat(bot: Bot, chat_id: int, user_id: int) -> bool:
    if not has_chat(chat_id):
        return False
    key = (chat_id, user_id)
    hit = _member_cache.get(key)
    if hit and time.time() - hit[0] < MEMBER_TTL:
        return hit[1]
    try:
        cm = await bot.get_chat_member(chat_id, user_id)
        ok = cm.status in ("creator", "administrator", "member") or (cm.status == "restricted" and getattr(cm, "is_member", False))
    except Exception:
        ok = False
    _member_cache[key] = (time.time(), ok)
    return ok


_bot_admin: dict[int, tuple[float, bool]] = {}
BOT_ADMIN_TTL = 300


async def bot_is_admin(bot: Bot, chat_id: int) -> bool:
    """Without admin rights (and with privacy mode on) Telegram sends the bot neither the chat's messages nor reactions."""
    if not has_chat(chat_id):
        return True   # no chat to read: nothing to warn about
    hit = _bot_admin.get(chat_id)
    if hit and time.time() - hit[0] < BOT_ADMIN_TTL:
        return hit[1]
    try:
        me = await bot.me()
        cm = await bot.get_chat_member(chat_id, me.id)
        ok = cm.status in ("creator", "administrator")
    except Exception:
        return hit[1] if hit else True     # can't tell — don't raise an alarm
    _bot_admin[chat_id] = (time.time(), ok)
    return ok


async def can_see_task(bot: Bot, t: dict, user_id: int) -> bool:
    """In the project — or its assignee: someone picked from another project sees this one task only,
    and only while it is theirs."""
    return bool(user_id) and (t.get("assignee_id") == user_id or await is_member(bot, t["chat_id"], user_id))


async def task_for_user(request: web.Request, task_id: int) -> dict:
    user = user_from_request(request)
    task = await db.task(task_id)
    if not task or not task["title"] or not await can_see_task(request.app["bot"], task, user["id"]):
        raise web.HTTPNotFound(text="task not found")
    return task


async def guest_of(request, t: dict) -> bool:
    """Sees the task only as its assignee, not the whole project."""
    return not await is_member(request.app["bot"], t["chat_id"], user_from_request(request)["id"])


async def no_guest(request, t: dict):
    """Who does it, archive, delete: for the project's people only."""
    if await guest_of(request, t):
        raise web.HTTPForbidden(text="only the project's people can do this")


# ---------------- serializers ----------------

async def task_summary(t: dict) -> dict:
    m = await db.media(t["id"])
    first = next((x for x in m if x["kind"] == "photo"), None)
    return {
        "id": t["id"], "pid": t["chat_id"], "title": t["title"], "author": t["author_name"],
        "date": fmt_date(t["created_at"]), "archived": bool(t["archived"]), "backlog": bool(t.get("backlog")),
        "progress": bool(t.get("progress")) and not t.get("backlog"), "closed": bool(t.get("closed")),
        "review": _on_review(t),
        "done": t.get("done_cnt", 0), "total": t.get("total", 0),
        "thumb": sign_media(t["id"], first["idx"]) if first else None,
        "shots": sum(1 for x in m if x["kind"] in ("photo", "video")),   # so the loading screen matches the task
        "due": deadlines.iso(t.get("due_at"), bool(t.get("due_allday"))),
        "rank": t.get("rank") or t["id"],
        "assignee": await assignee_json(t),
        "questions": await db.open_questions(t["id"]),   # open questions: shown on the row, they matter more than comments
    }


async def assignee_json(t: dict) -> dict | None:
    if not t.get("assignee_id"):
        return None
    return {"id": t["assignee_id"], "name": t.get("assignee_name") or "", "photo": await actor_photo(t["assignee_id"])}


AGENT_NAME = "Агент"


async def task_history(t: dict) -> list[dict]:
    ev = await db.events(t["id"])
    out = [{"ts": e["ts"], "actor": e["actor"], "agent": e["actor_id"] == -1, "photo": await actor_photo(e["actor_id"]),
            "kind": e["kind"], "text": e["text"], "extra": json.loads(e["extra"]) if e["extra"] else {}} for e in ev]
    if not any(e["kind"] == "created" for e in out):   # tasks from before the history existed
        out.append({"ts": t["created_at"], "actor": t["author_name"], "agent": False, "photo": await actor_photo(t["author_id"]),
                    "kind": "created", "text": t["title"], "extra": {}})
    return out


async def task_full(t: dict) -> dict:
    summaries = json.loads(t["voice_summaries"])
    items = await db.items(t["id"])
    m = await db.media(t["id"])
    proj = await db.project(t["chat_id"])
    link = msg_link(source_chat(t), t["checklist_msg_id"])
    return {
        "id": t["id"], "pid": t["chat_id"], "project": proj["display"] if proj else "",
        "title": t["title"], "author": t["author_name"], "date": fmt_date(t["created_at"]),
        "archived": bool(t["archived"]), "backlog": bool(t.get("backlog")),
        "progress": bool(t.get("progress")) and not t.get("backlog"), "closed": bool(t.get("closed")), "link": link,
        "review": _on_review(t),
        "result_url": t.get("result_url"), "hours": t.get("hours"), "result_note": t.get("result_note"),
        "descr": t.get("descr") or "",
        "due": deadlines.iso(t.get("due_at"), bool(t.get("due_allday"))),
        "assignee": await assignee_json(t),
        "task_reminders": await deadlines.task_kinds(t["id"]),   # what anyone set: the assignee gets these
        "items": [{"id": i["id"], "t": i["text"], "d": bool(i["done"]), "s": i["shot"],
                   "m": [u for u in (msg_link(source_chat(t, i), x) for x in item_src(i)) if u]} for i in items],
        "media": [{"idx": x["idx"], "kind": x["kind"], "url": sign_media(t["id"], x["idx"]),
                   "link": msg_link(source_chat(t, x), x["message_id"])} for x in m],
        "transcripts": [{"author": x.get("author"), "text": x.get("text"), "message_id": x.get("message_id"),
                         "key": (key := voice_key(x)), "summary": summaries.get(key), "duration": x.get("duration"),
                         **({"audio": sign_audio(t["id"], n)} if x.get("file_id") else {})}
                        for n, x in enumerate(await task_transcripts(t))],
        "files": [{"name": x["name"], "size": x["size"], "author": x.get("author"),
                   "link": msg_link(source_chat(t, x), x.get("message_id")),
                   **({"url": sign_file(t["id"], n)} if x.get("file_id") and x["size"] <= tgmedia.TG_LIMIT else {})}
                  for n, x in enumerate(await task_files(t))],
        "notes": json.loads(t["notes"] or "[]"),
        "history": await task_history(t),
        "comments": [{"id": c["id"], "author": c["author"], "user_id": c["user_id"], "photo": await actor_photo(c["user_id"]),
                      "item_id": c.get("item_id"), "question": bool(c.get("question")),
                      "resolved": bool(c.get("resolved_at")), "resolved_by": c.get("resolved_by"),
                      "files": comment_files(t["id"], c), "edited": bool(c.get("edited_at")),
                      "text": c["text"], "ts": c["ts"], "date": fmt_date(c["ts"])} for c in await db.comments(t["id"])],
    }


STATUS_FIELDS = {"open": {"backlog": 0, "progress": 0, "closed": 0, "review_at": None},
                 "progress": {"backlog": 0, "progress": 1, "closed": 0, "review_at": None},
                 "review": {"backlog": 0, "progress": 1, "closed": 0},   # + review_at: now (see api_task_patch)
                 "backlog": {"backlog": 1, "progress": 0, "closed": 0, "review_at": None},
                 "closed": {"backlog": 0, "progress": 0, "closed": 1, "review_at": None}}   # «Закрыта» — whatever the items say


def _on_review(t: dict) -> bool:
    """«На проверке»: still in work (progress=1), the one who did it waits for the author to check."""
    return bool(t.get("review_at")) and bool(t.get("progress")) and not t.get("backlog") and not t.get("closed")
NEW_STATUSES = ("open", "progress", "backlog")   # a task is not born closed


# ---------------- handlers ----------------

async def api_me(request):
    u = user_from_request(request)
    bot = (await request.app["bot"].me()).username
    # «Скопировать ссылку»: the app swaps {param} for task_<id>; the link opens that task for chat members
    return web.json_response({"id": u["id"], "name": u["name"], "start_param": u["start_param"],
                              "link": miniapp_url(bot, "PARAM").replace("PARAM", "{param}"),
                              "notify": await db.notify_enabled(u["id"]),
                              "notify_kinds": await db.notify_kinds(u["id"]) or list(notify_kinds())})


def notify_kinds() -> dict:
    from .notify import KINDS
    return KINDS


async def api_notify_kinds(request):
    """{"kinds": [...]}: which changes the bot sends to the private chat (see notify.KINDS)."""
    u = user_from_request(request)
    kinds = (await request.json()).get("kinds")
    if not isinstance(kinds, list) or any(k not in notify_kinds() for k in kinds):
        raise web.HTTPBadRequest(text="kinds: " + ", ".join(notify_kinds()))
    kinds = [k for k in notify_kinds() if k in kinds]
    await db.set_notify_kinds(u["id"], kinds)
    return web.json_response({"notify_kinds": kinds})


NOTIFY_HELLO = ("Уведомления включены.\n\nБуду писать сюда, когда в задачах, где вы участвуете, что-то меняется: "
                "смена статуса, новые пункты, скриншоты, исполнитель, срок. Что присылать — в настройках мини-приложения.")


async def api_notify(request):
    """On: the app has just asked Telegram for write access; a hello message proves the bot can really write."""
    u = user_from_request(request)
    on = bool((await request.json()).get("on"))
    if on:
        try:
            await request.app["bot"].send_message(u["id"], NOTIFY_HELLO)
        except Exception as e:
            log.info("notify: can't write to %s: %s", u["id"], e)
            return web.json_response({"error": "no_access"}, status=409)
    await db.set_notify(u["id"], on)
    return web.json_response({"notify": on})


async def api_task_share(request):
    """A ready-made message for Telegram's «share» window (WebApp.shareMessage): a card of the task with «Открыть»."""
    from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle,
                               InlineQueryResultCachedPhoto, InputTextMessageContent)
    task_id = int(request.match_info["task_id"])
    t = await task_for_user(request, task_id)
    u = user_from_request(request)
    bot = request.app["bot"]
    items = await db.items(task_id)
    done = sum(1 for i in items if i["done"])
    p = await db.project(t["chat_id"])
    project = (p and (p["name"] or p["chat_title"])) or ""
    status = ("в архиве" if t["archived"] else "закрыта" if t.get("closed")
              else "в бэклоге" if t.get("backlog") else "на проверке" if _on_review(t) else "в работе" if t.get("progress") else "открыта")
    e = lambda x: x.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    sub = " · ".join(x for x in (project, status.capitalize(), f"{done}/{len(items)} пунктов" if items else "") if x)
    text = f"<b>{e(t['title'])}</b>\n{e(sub)}"
    if items:
        text += "\n\n" + "\n".join(("☑︎ " if i["done"] else "☐ ") + e(i["text"][:120]) for i in items[:8])
        if len(items) > 8:
            text += f"\n…и ещё {len(items) - 8}"
    me = (await bot.me()).username
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть задачу",
                                                                     url=miniapp_url(me, f"task_{task_id}"))]])
    photo = next((m for m in await db.media(task_id) if m["kind"] == "photo" and m["file_id"]), None)
    rid = f"task{task_id}"
    if photo and len(text) <= 1024:
        result = InlineQueryResultCachedPhoto(id=rid, photo_file_id=photo["file_id"], caption=text, parse_mode="HTML",
                                              reply_markup=kb)
    else:
        result = InlineQueryResultArticle(id=rid, title=t["title"], description=sub, reply_markup=kb,
                                          input_message_content=InputTextMessageContent(
                                              message_text=text[:4000], parse_mode="HTML"))
    try:
        prepared = await bot.save_prepared_inline_message(user_id=u["id"], result=result, allow_user_chats=True,
                                                          allow_group_chats=True, allow_channel_chats=True)
    except Exception as ex:
        log.warning("share: %s", ex)
        return web.json_response({"error": "share_failed"}, status=502)
    return web.json_response({"id": prepared.id})


def _sort_by_project_order(items: list[dict], order_ids: list[int]) -> list[dict]:
    """Apply the user's personal project order. Projects not in the order yet (new ones) stay on top,
    keeping their relative order; the rest follow the saved places."""
    if not order_ids:
        return items
    pos = {cid: i for i, cid in enumerate(order_ids)}
    ranked = list(enumerate(items))
    ranked.sort(key=lambda pair: (0, pair[0]) if pair[1]["id"] not in pos else (1, pos[pair[1]["id"]]))
    return [p for _, p in ranked]


async def api_projects(request):
    user = user_from_request(request)
    bot = request.app["bot"]
    # «Без проекта» is not a project of the list: its tasks are in «Мои задачи»
    ps = [p for p in await db.projects() if not is_personal(p["chat_id"])]
    # membership may ask Telegram (once per MEMBER_TTL): all projects at once, not one after another
    seen = await asyncio.gather(*(is_member(bot, p["chat_id"], user["id"]) for p in ps))
    ps = [p for p, ok in zip(ps, seen) if ok]
    stats = await db.project_stats(int(time.time()) - 86400)
    photos = await asyncio.gather(*(project_photo(bot, p) for p in ps))
    admins = await asyncio.gather(*(bot_is_admin(bot, p["chat_id"]) for p in ps))
    zero = {"open": 0, "items_done": 0, "items_total": 0, "today": 0}
    out = [{"id": p["chat_id"], "name": p["display"], **zero, **stats.get(p["chat_id"], {}), "photo": photo,
            "pic": bool(p.get("pic")), "color": COLORS[abs(p["chat_id"]) % len(COLORS)],
            "bot_admin": admin, "standalone": standalone(p["chat_id"]),
            "owner": standalone(p["chat_id"]) and p.get("owner_id") == user["id"]}
           for p, photo, admin in zip(ps, photos, admins)]
    out = _sort_by_project_order(out, await db.project_order_ids(user["id"]))
    return web.json_response(out)


async def api_projects_order(request):
    """{"project_ids": [...]}: this user's order of projects in the list (top first)."""
    user = user_from_request(request)
    try:
        ids = [int(i) for i in (await request.json()).get("project_ids") or []][:500]
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text="project_ids: numbers")
    bot = request.app["bot"]
    if ids:
        # only projects this person can see; ignore the rest
        ok = await asyncio.gather(*(is_member(bot, cid, user["id"]) for cid in ids))
        ids = [cid for cid, yes in zip(ids, ok) if yes and not is_personal(cid)]
    await db.set_project_order(user["id"], ids)
    return await api_projects(request)


PROJECT_NAME_MAX = 80


def _project_name(body: dict) -> str:
    name = " ".join(str(body.get("name") or "").split())[:PROJECT_NAME_MAX]
    if not name:
        raise web.HTTPBadRequest(text="empty name")
    return name


async def api_project_create(request):
    """{"name": "..."}: a project without a chat. Tasks come from the app and from messages forwarded to the bot;
    people come in by its invite link («Доступ»)."""
    user = user_from_request(request)
    name = _project_name(await request.json())
    chat_id = await db.create_standalone(user["id"], user["name"], name)
    return web.json_response({"id": chat_id, "name": name, "open": 0, "photo": None, "pic": False,
                              "color": COLORS[abs(chat_id) % len(COLORS)], "items_done": 0, "items_total": 0,
                              "today": 0, "bot_admin": True, "standalone": True, "owner": True})


async def api_project_rename(request):
    """{"name": "..."}: only a project without a chat — a chat's project is named by /project in the chat."""
    chat_id = await _project_for_user(request)
    if not standalone(chat_id):
        raise web.HTTPBadRequest(text="named in its chat: /project")
    name = _project_name(await request.json())
    await db.set_project_name(chat_id, name)
    return web.json_response({"id": chat_id, "name": name})


async def api_project_delete(request):
    """A project without a chat, with all its tasks: only its maker. A chat's project goes when the bot leaves the chat."""
    chat_id = await _project_for_user(request)
    p = await db.project(chat_id)
    if not standalone(chat_id) or p["owner_id"] != user_from_request(request)["id"]:
        raise web.HTTPForbidden(text="only the maker of a project without a chat deletes it")
    ids = [r["id"] for r in await db.all("SELECT id FROM tasks WHERE chat_id=?", (chat_id,))]
    root = (Path(settings.db_path).parent / "media").resolve()
    for task_id in ids:
        await db.delete_task(task_id)
        base = root / f"task_{task_id}"
        if base.exists():
            import shutil
            shutil.rmtree(base, ignore_errors=True)
    await db.x("DELETE FROM project_access WHERE chat_id=?", (chat_id,))
    if p.get("pic"):
        _pic_path(p["pic"]).unlink(missing_ok=True)
    await db.remove_project(chat_id)
    return web.json_response({"deleted": chat_id, "tasks": len(ids)})


TIME_PERIODS = {"week": 7, "month": 30, "quarter": 91, "all": None}


async def api_time(request):
    """«Время»: every time log row of the projects this person can see (and their own «Без проекта»),
    ?period=week|month|quarter|all, or a range of the person's own days: ?from=<unix>&to=<unix> (to — not included;
    the app sends its local midnights). The app filters by project and person and sums itself."""
    user = user_from_request(request)
    bot = request.app["bot"]
    q = request.query
    until = None
    if "from" in q or "to" in q:
        try:
            since, until = int(q.get("from") or 0), int(q["to"]) if q.get("to") else None
        except ValueError:
            raise web.HTTPBadRequest(text="from, to: unix time")
        if until is not None and until <= since:
            raise web.HTTPBadRequest(text="to must be after from")
    else:
        period = q.get("period", "month")
        if period not in TIME_PERIODS:
            raise web.HTTPBadRequest(text="period: " + "|".join(TIME_PERIODS))
        days = TIME_PERIODS[period]
        since = int(time.time()) - days * 86400 if days else 0
    names = {}
    for p in await db.projects():
        if await is_member(bot, p["chat_id"], user["id"]):
            names[p["chat_id"]] = PERSONAL if is_personal(p["chat_id"]) else p["display"]
    rows = await db.time_rows(list(names), since, until)
    return web.json_response({"me": user["id"], "rows": [
        {"id": r["id"], "task_id": r["task_id"], "task": r["title"], "pid": r["chat_id"], "project": names[r["chat_id"]],
         "color": "var(--hint)" if is_personal(r["chat_id"]) else COLORS[abs(r["chat_id"]) % len(COLORS)],
         "user": {"id": r["user_id"], "name": r["user_name"] or ""}, "hours": r["hours"], "ts": r["ts"],
         "closed": bool(r["closed"]), "archived": bool(r["archived"])} for r in rows]})


async def api_people_tasks(request):
    """«Задачи людей»: tasks of every project this person can see (archive left out), with who does each,
    and the list of assignees with their active counts. The app filters by person and status itself."""
    user = user_from_request(request)
    bot = request.app["bot"]
    out, people = [], {}
    for p in await db.projects():
        inside = await is_member(bot, p["chat_id"], user["id"])
        color = COLORS[abs(p["chat_id"]) % len(COLORS)]
        for t in await db.tasks_of_chat(p["chat_id"]):
            if t["archived"] or not (inside or t.get("assignee_id") == user["id"]):   # outside: only what's mine
                continue
            row = await task_summary(t)
            row.update(project=p["display"], color=color)
            out.append(row)
            active = not t.get("backlog") and not t["closed"]
            if t.get("assignee_id"):
                who = people.setdefault(t["assignee_id"], {**row["assignee"], "active": 0, "total": 0})
                who["total"] += 1
                who["active"] += 1 if active else 0
    if user["id"] not in people:   # «Мои» is always there, even with nothing yet
        people[user["id"]] = {"id": user["id"], "name": user["name"], "photo": await actor_photo(user["id"]),
                              "active": 0, "total": 0}
    ranked = sorted(people.values(), key=lambda x: (x["id"] != user["id"], -x["active"], -x["total"], x["name"]))
    return web.json_response({"me": user["id"], "people": ranked, "tasks": out})


async def api_project_tasks(request):
    user = user_from_request(request)
    chat_id = int(request.match_info["chat_id"])
    if not await is_member(request.app["bot"], chat_id, user["id"]):
        raise web.HTTPNotFound(text="project not found")
    return web.json_response([await task_summary(t) for t in await db.tasks_of_chat(chat_id)])


# ---------- assignee ----------

async def project_people(bot: Bot, chat_id: int) -> list[dict]:
    """Who can be the assignee: people the bot has seen in the project and who are still in it."""
    me = (await bot.me()).id
    out = []
    for p in await db.people(chat_id):
        if p["id"] != me and await is_member(bot, chat_id, p["id"]):
            out.append({"id": p["id"], "name": p["name"], "photo": await actor_photo(p["id"])})
    return out


async def _viewer_projects(bot: Bot, viewer: int | None) -> list[dict]:
    """Projects the person can see; viewer None (the agent) — all of them."""
    return [p for p in await db.projects() if viewer is None or await is_member(bot, p["chat_id"], viewer)]


async def team_people(bot: Bot, chat_id: int, viewer: int | None) -> list[dict]:
    """People from the viewer's other projects who are not in this one. Picking one as the assignee lets them
    into this project (as with the invite link). Only people the viewer already works with — no strangers."""
    inside = {p["id"] for p in await project_people(bot, chat_id)}
    me = (await bot.me()).id
    out: dict[int, dict] = {}
    for proj in await _viewer_projects(bot, viewer):
        if proj["chat_id"] == chat_id:
            continue
        for p in await project_people(bot, proj["chat_id"]):
            if p["id"] in inside or p["id"] in out or p["id"] == me:
                continue
            out[p["id"]] = {**p, "outside": True, "via": proj["display"]}
    return sorted(out.values(), key=lambda p: p["name"].lower())


async def api_project_people(request):
    chat_id = await _project_for_user(request)
    bot = request.app["bot"]
    user = user_from_request(request)
    return web.json_response(await project_people(bot, chat_id) + await team_people(bot, chat_id, user["id"]))


async def person_name(bot: Bot, chat_id: int, user_id: int) -> str | None:
    """The name of someone in the project, or None if they are not in it."""
    if not await is_member(bot, chat_id, user_id):
        return None
    hit = next((p["name"] for p in await db.people(chat_id) if p["id"] == user_id), None)
    if hit:
        return hit
    try:
        u = (await bot.get_chat_member(chat_id, user_id)).user
        return " ".join(x for x in (u.first_name, u.last_name) if x) or str(user_id)
    except Exception:
        return str(user_id)


async def admit_assignee(bot: Bot, chat_id: int, user_id: int, viewer: int | None) -> tuple[str, bool]:
    """The assignee's name, and whether they are from outside the project. Someone outside may be picked if they
    are in another project of the viewer's: they then see this one task (can_see_task), not the project."""
    name = await person_name(bot, chat_id, user_id)
    if name is not None:
        return name, False
    hit = next((p for p in await team_people(bot, chat_id, viewer) if p["id"] == user_id), None)
    if not hit:
        raise web.HTTPBadRequest(text="no such member")
    return hit["name"], True


async def assign(bot: Bot, task_id: int, user_id: int | None, actor: str, actor_id: int) -> bool | None:
    """Make someone the assignee (None — nobody). They are told in private; the reminders set on the task go to
    them from now on. Returns whether the private message got through (None if nobody was told)."""
    t = await db.task(task_id)
    if (t["assignee_id"] or None) == (user_id or None):
        return None
    name, granted = None, False
    if user_id:
        name, granted = await admit_assignee(bot, t["chat_id"], user_id, actor_id if actor_id > 0 else None)
    await db.update_task(task_id, assignee_id=user_id or None, assignee_name=name)
    await db.log_event(task_id, "assigned", actor, actor_id, text=name,
                       extra={"id": user_id, "name": name, "from": t.get("assignee_name")})
    if not user_id or user_id == actor_id:
        return None
    return await tell_assignee(bot, task_id, user_id, actor, granted)


async def tell_assignee(bot: Bot, task_id: int, user_id: int, actor: str, granted: bool = False) -> bool:
    """«Вы исполнитель задачи» in private. False if the bot can't write to them (never opened it)."""
    t = await db.task(task_id)
    p = await db.project(t["chat_id"])
    when = deadlines.text(t.get("due_at"), bool(t.get("due_allday")))
    rem = await deadlines.task_kinds(task_id) if t.get("due_at") else []
    e = html.escape
    msg = (f"Вы исполнитель задачи\n<b>{e(t['title'])}</b>\n"
           + " · ".join(x for x in ((p["display"] if p else ""), f"до {when}" if when else "") if x)
           + (f"\nНазначил(а): {e(actor)}" if actor else "")
           + ("\n\nВам открыт доступ к этой задаче (только к ней, не ко всему проекту) — пока вы её исполнитель." if granted else "")
           + ("\n\nНапомню о сроке сюда, в личку." if rem else ""))
    kb = deadlines.InlineKeyboardMarkup(inline_keyboard=[[deadlines.InlineKeyboardButton(
        text="Открыть задачу", url=miniapp_url((await bot.me()).username, f"task_{task_id}"))]])
    try:
        await bot.send_message(user_id, msg.strip(), parse_mode="HTML", reply_markup=kb)
        return True
    except Exception as ex:   # never opened the bot: reminders won't reach them either
        log.info("assign: can't write to %s: %s", user_id, ex)
        return False


def assignee_from_body(v) -> int | None:
    if v in (None, "", 0):
        return None
    try:
        v = int(v.get("id") if isinstance(v, dict) else v)
    except (TypeError, ValueError, AttributeError):
        raise web.HTTPBadRequest(text="assignee: user id or null")
    return v if v > 0 else None


# ---------- access to a project without its chat ----------

async def _access_json(request, chat_id: int) -> dict:
    bot = (await request.app["bot"].me()).username
    owner = (await db.project(chat_id))["owner_id"]   # the maker of a project without a chat is not «let in»
    return {"link": miniapp_url(bot, "join_" + await db.invite_token(chat_id)),
            "people": [{"id": r["user_id"], "name": r["name"], "photo": await actor_photo(r["user_id"]),
                        "date": fmt_date(r["added_at"])} for r in await db.access_list(chat_id) if r["user_id"] != owner]}


async def _project_for_user(request) -> int:
    user = user_from_request(request)
    chat_id = int(request.match_info["chat_id"])
    if not await db.project(chat_id) or not await is_member(request.app["bot"], chat_id, user["id"]):
        raise web.HTTPNotFound(text="project not found")
    return chat_id


async def api_access(request):
    """The invite link and who came in by it."""
    return web.json_response(await _access_json(request, await _project_for_user(request)))


async def api_access_reset(request):
    """A new invite link: the old one stops working (people already in stay)."""
    chat_id = await _project_for_user(request)
    await db.invite_token(chat_id, reset=True)
    return web.json_response(await _access_json(request, chat_id))


async def api_access_remove(request):
    chat_id = await _project_for_user(request)
    if int(request.match_info["user_id"]) == (await db.project(chat_id))["owner_id"]:
        raise web.HTTPBadRequest(text="the project's maker keeps access")
    await db.revoke_access(chat_id, int(request.match_info["user_id"]))
    return web.json_response(await _access_json(request, chat_id))


async def api_join(request):
    """{"token": "..."} from startapp=join_<token>: let this person into the project."""
    user = user_from_request(request)
    token = str((await request.json()).get("token") or "")
    p = await db.project_by_invite(token) if token else None
    if not p:
        raise web.HTTPNotFound(text="invite not found")
    bot = request.app["bot"]
    if not await in_chat(bot, p["chat_id"], user["id"]) and await db.grant_access(p["chat_id"], user["id"], user["name"]):
        # everyone in the chat sees who got in: the link may have gone further than meant; no chat — its maker does
        to = p["chat_id"] if has_chat(p["chat_id"]) else p["owner_id"]
        where = "" if has_chat(p["chat_id"]) else f" «{html.escape(p['display'])}»"
        try:
            if to:
                await bot.send_message(to, f"{html.escape(user['name'])} теперь видит задачи проекта{where} — открыл(а) ссылку-приглашение. "
                                       "Убрать доступ: мини-приложение → проект → «Доступ».", parse_mode="HTML")
        except Exception as e:
            log.info("join notice to %s: %s", p["chat_id"], e)
    return web.json_response({"id": p["chat_id"], "name": p["display"]})


HISTORY_PAGE = 30
HISTORY_MAX = 200


async def _history_response(request, chat_ids: list[int]):
    """?filter=<HISTORY_FILTERS key>&before=<event id>&limit=N"""
    q = request.query
    flt = q.get("filter", "all")
    if flt not in HISTORY_FILTERS:
        raise web.HTTPBadRequest(text="unknown filter")
    try:
        before = int(q["before"]) if "before" in q else None
        limit = max(1, min(int(q.get("limit", HISTORY_PAGE)), HISTORY_MAX))
    except ValueError:
        raise web.HTTPBadRequest(text="before and limit must be numbers")
    rows = await db.history_feed(chat_ids, flt, before, limit + 1)   # one extra: is there a next page?
    page = rows[:limit]
    return web.json_response({
        "events": [{"id": e["id"], "task_id": e["task_id"], "task": e["task_title"], "pid": e["pid"], "project": e["project"],
                    "ts": e["ts"], "actor": e["actor"], "agent": e["actor_id"] == -1, "photo": await actor_photo(e["actor_id"]),
                    "kind": e["kind"], "text": e["text"],
                    "extra": json.loads(e["extra"]) if e["extra"] else {}} for e in page],
        "next": page[-1]["id"] if len(rows) > limit else None,
    })


async def api_project_history(request):
    user = user_from_request(request)
    chat_id = int(request.match_info["chat_id"])
    if not await is_member(request.app["bot"], chat_id, user["id"]):
        raise web.HTTPNotFound(text="project not found")
    return await _history_response(request, [chat_id])


async def api_history(request):
    """Everything in one feed, only from the projects this user is a member of."""
    user = user_from_request(request)
    bot = request.app["bot"]
    return await _history_response(request, [p["chat_id"] for p in await db.projects() if await is_member(bot, p["chat_id"], user["id"])])


async def _notif_task_ids(bot, user_id: int) -> list[int]:
    """My tasks I can still see: in a project I am in, or assigned to me."""
    rows = await db.notif_tasks(user_id)
    seen: dict[int, bool] = {}
    out = []
    for t in rows:
        if t["assignee_id"] == user_id:
            out.append(t["id"])
            continue
        if t["chat_id"] not in seen:
            seen[t["chat_id"]] = await is_member(bot, t["chat_id"], user_id)
        if seen[t["chat_id"]]:
            out.append(t["id"])
    return out


async def api_notifications(request):
    """«Уведомления»: ?section=all|q|c|s|a|e&before=<event id>&limit=N — events in my tasks by other people,
    plus the unread numbers and up to which id each section was read (rows above it are new)."""
    user = user_from_request(request)
    q = request.query
    sec = q.get("section", "all")
    if sec != "all" and sec not in NOTIF_SECTIONS:
        raise web.HTTPBadRequest(text="unknown section")
    try:
        before = int(q["before"]) if "before" in q else None
        limit = max(1, min(int(q.get("limit", HISTORY_PAGE)), HISTORY_MAX))
    except ValueError:
        raise web.HTTPBadRequest(text="before and limit must be numbers")
    ids = await _notif_task_ids(request.app["bot"], user["id"])
    rows = await db.notif_feed(user["id"], ids, sec, before, limit + 1)
    page = rows[:limit]
    return web.json_response({
        "events": [{"id": e["id"], "task_id": e["task_id"], "task": e["task_title"], "pid": e["pid"], "project": e["project"],
                    "ts": e["ts"], "actor": e["actor"], "agent": e["actor_id"] == -1, "photo": await actor_photo(e["actor_id"]),
                    "kind": e["kind"], "text": e["text"], "unread": bool(e["unread"]),
                    "extra": json.loads(e["extra"]) if e["extra"] else {}} for e in page],
        "next": page[-1]["id"] if len(rows) > limit else None,
        "unread": await db.notif_unread(user["id"], ids),
    })


async def api_notification_read(request):
    """{"read": true | false}: one notification read (tapped) or made unread again (swiped) → the unread numbers."""
    user = user_from_request(request)
    event_id = int(request.match_info["event_id"])
    ids = await _notif_task_ids(request.app["bot"], user["id"])
    e = await db.one("SELECT task_id FROM events WHERE id=?", (event_id,))
    if not e or e["task_id"] not in ids:
        raise web.HTTPNotFound(text="notification not found")
    await db.notif_set(user["id"], event_id, bool((await request.json()).get("read", True)))
    return web.json_response({"unread": await db.notif_unread(user["id"], ids)})


async def api_notifications_unread(request):
    user = user_from_request(request)
    return web.json_response({"unread": await db.notif_unread(user["id"], await _notif_task_ids(request.app["bot"], user["id"]))})


async def api_notifications_seen(request):
    """«Прочитать все»: {"section": "all" | one of NOTIF_SECTIONS, "upto": <newest event id shown>} → the unread numbers."""
    user = user_from_request(request)
    body = await request.json()
    sec = body.get("section", "all")
    if sec != "all" and sec not in NOTIF_SECTIONS:
        raise web.HTTPBadRequest(text="unknown section")
    try:
        upto = int(body["upto"])
    except (KeyError, TypeError, ValueError):
        raise web.HTTPBadRequest(text="upto must be a number")
    newest = (await db.one("SELECT COALESCE(MAX(id), 0) AS m FROM events"))["m"]   # never past now: later ones come as new
    await db.notif_mark(user["id"], list(NOTIF_SECTIONS) if sec == "all" else [sec], min(upto, newest))
    return web.json_response({"unread": await db.notif_unread(user["id"], await _notif_task_ids(request.app["bot"], user["id"]))})


async def api_task_create(request):
    """«Создать задачу» in the mini app: title + optional description, items, assignee."""
    user = user_from_request(request)
    chat_id = int(request.match_info["chat_id"])
    if chat_id == user["id"] and not await db.project(chat_id):   # the first task without a project
        await db.upsert_project(chat_id, PERSONAL)
    if not await is_member(request.app["bot"], chat_id, user["id"]) or not await db.project(chat_id):
        raise web.HTTPNotFound(text="project not found")
    rid = request.headers.get("X-Request-Id", "")[:64] or None
    if rid:   # the app sent it again after the connection dropped: the task is already there
        twin = await db.one("SELECT id FROM tasks WHERE author_id=? AND req_id=?", (user["id"], rid))
        if twin:
            return web.json_response(await task_full(await db.task(twin["id"])))
    body = await request.json()
    title = str(body.get("title", "")).strip()[:120]
    if not title:
        raise web.HTTPBadRequest(text="empty title")
    items = [s[:500] for s in (str(x).strip() for x in body.get("items") or []) if s][:50]
    descr = str(body.get("descr") or "").strip()[:5000] or None   # free text with links, same as PATCH
    status = dict(STATUS_FIELDS[body["status"]]) if body.get("status") in NEW_STATUSES else {}
    bot = request.app["bot"]
    aid = assignee_from_body(body.get("assignee"))
    if aid is None and (status.get("progress") or is_personal(chat_id)):
        aid = user["id"]   # created straight into work, or without a project: it's theirs
    if aid == user["id"]:
        aname, granted = user["name"], False
    else:
        aname, granted = await admit_assignee(bot, chat_id, aid, user["id"]) if aid else (None, False)
    if aid:   # set before the checklist goes to the chat, so it shows the assignee from the start
        status.update(assignee_id=aid, assignee_name=aname)
    task_id = await tasks.create_manual(bot, chat_id, user["id"], user["name"], title, items, status)
    extra = {}
    if rid:
        extra["req_id"] = rid
    if descr:
        extra["descr"] = descr
    if extra:
        await db.update_task(task_id, **extra)
    dm = None
    if aid:
        await db.log_event(task_id, "assigned", user["name"], user["id"], text=aname, extra={"id": aid, "name": aname})
        if aid != user["id"]:
            dm = await tell_assignee(bot, task_id, aid, user["name"], granted)
    data = await task_full(await db.task(task_id))
    if dm is False:
        data["assignee_dm"] = False
    return web.json_response(data)


async def api_task(request):
    t = await task_for_user(request, int(request.match_info["task_id"]))
    return web.json_response(await _for_user(request, t))


async def api_task_source(request):
    """The messages a task was made from, as they were written: author, date, text (or the voice transcript),
    the task's screenshots that came from each. The app shows them in a popup instead of sending you to the chat."""
    t = await task_for_user(request, int(request.match_info["task_id"]))
    ids = {t["chat_id"]: [], source_chat(t): []}
    for i in await db.items(t["id"]):
        ids.setdefault(source_chat(t, i), []).extend(item_src(i))
    if t.get("reply_to_id"):
        ids[source_chat(t)].append(t["reply_to_id"])
    shots = {}
    for x in await db.media(t["id"]):
        if x.get("message_id"):
            shots.setdefault((source_chat(t, x), x["message_id"]), []).append(sign_media(t["id"], x["idx"]))
    bot_id = (await request.app["bot"].me()).id   # the bot's own checklist message is not the original
    rows = [m for chat_id, mids in ids.items() for m in await db.task_messages(chat_id, t["id"], mids)]
    msgs = sorted((m for m in rows if m["user_id"] != bot_id), key=lambda m: (m["date"], m["message_id"]))
    return web.json_response({"messages": [
        {"id": m["message_id"], "author": m["user_name"], "date": fmt_date(m["date"]), "kind": m["kind"],
         "text": m["text"] or "", "transcript": m.get("transcript") or "", "file": m.get("file_name") or "",
         "shots": shots.get((m["chat_id"], m["message_id"]), [])} for m in msgs]})


def _status_of(t: dict) -> str:
    return ("closed" if t.get("closed") else "backlog" if t.get("backlog") else
            "review" if _on_review(t) else "progress" if t.get("progress") else "open")


async def _link_row(t: dict) -> dict:
    """A task in «Связанные задачи» and in the list to link from: name, project, status."""
    proj = await db.project(t["chat_id"])
    return {"id": t["id"], "title": t["title"], "pid": t["chat_id"],
            "project": PERSONAL if is_personal(t["chat_id"]) else (proj or {}).get("display") or "",
            "color": COLORS[abs(t["chat_id"]) % len(COLORS)], "status": _status_of(t), "archived": bool(t["archived"])}


async def _visible(bot, uid: int, task_id: int) -> dict | None:
    t = await db.task(task_id)
    return t if t and t["title"] and await can_see_task(bot, t, uid) else None


async def _for_user(request, t: dict) -> dict:
    """The task plus what is personal: this person's reminders, the linked tasks they can see."""
    full = await task_full(t)
    uid = user_from_request(request)["id"]
    bot = request.app["bot"]
    full["reminders"] = await deadlines.mine(t["id"], uid)
    links, hidden = [], 0
    for other in await db.task_links(t["id"]):
        o = await _visible(bot, uid, other)
        if o:
            links.append(await _link_row(o))
        else:
            hidden += 1   # a task in a chat this person is not in: only that it exists, not what it is
    full["links"], full["links_hidden"] = links, hidden
    seen = {x["id"] for x in links}
    for e in full["history"]:
        if e["kind"] in ("linked", "unlinked") and e["extra"].get("id") not in seen and not await _visible(bot, uid, e["extra"].get("id") or 0):
            e["text"], e["extra"] = None, {"hidden": True}
    if await guest_of(request, t):
        full["guest"] = True
    return full


async def _after_change(request, task_id: int):
    await tasks.refresh_checklist(request.app["bot"], task_id)
    return web.json_response(await _for_user(request, await db.task(task_id)))


def due_from_body(v) -> tuple[int, bool] | None:
    """{"date": "2026-11-30", "time": "18:00" | null} or null."""
    if not v:
        return None
    try:
        return deadlines.parse(str(v.get("date") or ""), v.get("time"))
    except (ValueError, TypeError, AttributeError):
        raise web.HTTPBadRequest(text="bad due")


async def change_due(task_id: int, due, actor: str, actor_id: int):
    old = await db.task(task_id)
    if due == ((old["due_at"], bool(old["due_allday"])) if old["due_at"] else None):
        return
    await deadlines.set_due(task_id, due)
    await db.log_event(task_id, "due", actor, actor_id,
                       extra={"text": deadlines.text(*due) if due else None, "at": due[0] if due else None})


async def api_reminders(request):
    """This person's reminders about the deadline: {"kinds": ["day", "hour", "due"]}."""
    task_id = int(request.match_info["task_id"])
    await task_for_user(request, task_id)
    kinds = (await request.json()).get("kinds") or []
    await deadlines.set_mine(task_id, user_from_request(request)["id"], [str(k) for k in kinds])
    return web.json_response(await _for_user(request, await db.task(task_id)))


async def api_task_patch(request):
    task_id = int(request.match_info["task_id"])
    old = await task_for_user(request, task_id)
    user = user_from_request(request)
    body = await request.json()
    if "assignee" in body or "archived" in body:
        await no_guest(request, old)
    fields = {}
    if "title" in body and str(body["title"]).strip():
        fields["title"] = str(body["title"]).strip()[:120]
    if "archived" in body:
        fields["archived"] = 1 if body["archived"] else 0
    if "backlog" in body:
        fields["backlog"] = 1 if body["backlog"] else 0
        if body["backlog"]:
            fields["progress"] = 0
            fields["review_at"] = None
    if body.get("status") in STATUS_FIELDS:   # open | progress | review | backlog | closed — one status at a time
        fields.update(STATUS_FIELDS[body["status"]])
        if body["status"] == "review" and not _on_review(old):
            fields["review_at"], fields["review_n"] = int(time.time()), 0
    if "result_url" in body:   # what closing asks for: a link to the result and the hours spent, both optional
        u = str(body.get("result_url") or "").strip()[:500]
        fields["result_url"] = u or None
    if "descr" in body:   # the description: free text with links
        d = str(body.get("descr") or "").strip()[:5000]
        if d != (old.get("descr") or ""):
            fields["descr"] = d or None
            await db.log_event(task_id, "descr", user["name"], user["id"], text=d[:300] or None)
    if "result_note" in body:   # the result in words
        fields["result_note"] = str(body.get("result_note") or "").strip()[:2000] or None
    if "hours" in body:
        try:
            h = float(str(body.get("hours")).replace(",", ".")) if body.get("hours") not in (None, "") else None
        except ValueError:
            raise web.HTTPBadRequest(text="hours: a number")
        if h is not None and not 0 <= h <= 10000:
            raise web.HTTPBadRequest(text="hours: 0…10000")
        fields["hours"] = round(h, 2) if h is not None else None
    if "due" in body:
        await change_due(task_id, due_from_body(body["due"]), user["name"], user["id"])
    dm = None
    if "assignee" in body and assignee_from_body(body["assignee"]) is None \
            and fields.get("progress", old.get("progress")) and not fields.get("backlog", old.get("backlog")):
        fields["progress"] = 0   # nobody does it any more → not «в работе», open again
        fields["review_at"] = None
    if "assignee" in body:
        dm = await assign(request.app["bot"], task_id, assignee_from_body(body["assignee"]), user["name"], user["id"])
    elif fields.get("progress") and not old.get("progress") and not old.get("assignee_id"):
        await assign(request.app["bot"], task_id, user["id"], user["name"], user["id"])   # took it into work → it's theirs
    await db.update_task(task_id, **fields)
    new = await db.task(task_id)
    delta = (new.get("hours") or 0) - (old.get("hours") or 0)
    if abs(delta) >= 0.005:   # who put how much time in, and when: the «Время» dashboard sums these
        await db.log_time(task_id, user["id"], user["name"], delta)
    await log_task_change(old, new, user["name"], user["id"])
    if body.get("complete"):   # «Закрыть» from a row swipe: every item ticked
        await tasks.complete_all(task_id, user["name"], user["id"])
    await tasks.refresh_checklist(request.app["bot"], task_id)
    fresh = await db.task(task_id)
    if _on_review(fresh) and not _on_review(old):   # ready → whoever set the task is asked to check it
        from . import review
        spawn(review.tell_author(request.app["bot"], task_id, user["id"]))
    data = await _for_user(request, fresh)
    if dm is False:   # the assignee never opened the bot: say so in the app
        data["assignee_dm"] = False
    return web.json_response(data)


def _st(t: dict) -> str:
    return "backlog" if t.get("backlog") else "review" if _on_review(t) else "progress" if t.get("progress") else "open"


async def log_task_change(old: dict, new: dict, actor: str, actor_id: int):
    tid = new["id"]
    if new["title"] != old["title"]:
        await db.log_event(tid, "renamed", actor, actor_id, text=new["title"], extra={"from": old["title"]})
    if bool(new["archived"]) != bool(old["archived"]):
        await db.log_event(tid, "archived" if new["archived"] else "unarchived", actor, actor_id)
    report = {k: new[k] for k in ("result_url", "hours", "result_note") if new.get("closed") and new.get(k) is not None}
    if bool(new.get("closed")) != bool(old.get("closed")):
        await db.log_event(tid, "closed" if new.get("closed") else "reopened", actor, actor_id, extra=report or None)
    elif new.get("closed") and report and any(new.get(k) != old.get(k) for k in ("result_url", "hours", "result_note")):
        await db.log_event(tid, "report", actor, actor_id, extra=report)   # given after it closed itself (last item)
    elif not new.get("closed") and new.get("hours") != old.get("hours"):   # time put in before it is closed
        await db.log_event(tid, "time", actor, actor_id, extra={"hours": new.get("hours"), "from": old.get("hours")})
    if _st(new) != _st(old) and not new.get("closed"):   # closing is its own event, not «status: open»
        await db.log_event(tid, "status", actor, actor_id, extra={"to": _st(new), "from": _st(old)})


async def api_comment_add(request):
    """{"text": "..."}: a comment on the task — who and when come from the login."""
    t = await task_for_user(request, int(request.match_info["task_id"]))
    user = user_from_request(request)
    body = await request.json()
    text = str(body.get("text", "")).strip()[:2000]
    files = []   # uploaded first (/comment_files), here only their names: each must be this task's, at most 10
    for name in (body.get("files") or [])[:10]:
        name = str(name)
        if not CFILE_RE.match(name) or not (cfile_dir(t["id"]) / name).is_file():
            raise web.HTTPBadRequest(text="no such file")
        files.append({"f": name, "kind": "video" if name.rsplit(".", 1)[1] in ("mp4", "mov", "webm") else "photo"})
    if not text and not files:
        raise web.HTTPBadRequest(text="empty")
    item = None
    if body.get("item_id") is not None:   # on one item of this task's checklist
        try:
            item = next((i for i in await db.items(t["id"]) if i["id"] == int(body["item_id"])), None)
        except (TypeError, ValueError):
            item = None
        if not item:
            raise web.HTTPBadRequest(text="no such item in this task")
    q = bool(body.get("question"))   # a question: above the comments until someone marks it answered
    await db.add_comment(t["id"], user["id"], user["name"], text, item["id"] if item else None, question=q, files=files)
    shots, videos = sum(x["kind"] == "photo" for x in files), sum(x["kind"] == "video" for x in files)
    what = ", ".join(w for w in (f"скриншотов: {shots}" if shots > 1 else "скриншот" if shots else "",
                                 f"видео: {videos}" if videos > 1 else "видео" if videos else "") if w)
    extra = {**({"item": item["text"][:120]} if item else {}), **({"files": what} if files else {})}
    await db.log_event(t["id"], "asked" if q else "commented", user["name"], user["id"], item_id=item["id"] if item else None,
                       text=(text or what.capitalize())[:300], extra=extra or None)
    return web.json_response(await _for_user(request, await db.task(t["id"])))


async def api_comments_feed(request):
    """«Комментарии» in the menu: the newest comments of every task this person can see, newest first."""
    user = user_from_request(request)
    bot = request.app["bot"]
    projs = [p for p in await db.projects() if await is_member(bot, p["chat_id"], user["id"])]
    names = {p["chat_id"]: p["display"] for p in projs}
    mine = [t["id"] for t in await db.all("SELECT id FROM tasks WHERE assignee_id=?", (user["id"],))]   # assigned from outside
    rows = await db.comments_feed(list(names), mine)
    return web.json_response([{"id": c["id"], "task_id": c["task_id"], "pid": c["chat_id"], "task": c["task_title"],
                               "project": names.get(c["chat_id"], ""), "item_id": c.get("item_id"), "item": c.get("item_text"),
                               "author": c["author"], "user_id": c["user_id"], "photo": await actor_photo(c["user_id"]),
                               "text": c["text"], "ts": c["ts"], "date": fmt_date(c["ts"]),
                               "color": COLORS[abs(c["chat_id"]) % len(COLORS)]} for c in rows])


SEARCH_KINDS = ("task", "item", "comment", "result", "project")
SEARCH_PER_KIND = 40


async def api_search(request):
    """Search everything this person can see: ?q=… → {"terms", "groups": {kind: [row]}, "total": {kind: n}}.
    Each row has the piece of text with the found words marked (parts: [[text, hit], …])."""
    user = user_from_request(request)
    bot = request.app["bot"]
    q = (request.query.get("q") or "").strip()[:100]
    ts = fts.terms(q)
    groups = {k: [] for k in SEARCH_KINDS}
    total = {k: 0 for k in SEARCH_KINDS}
    if not ts:
        return web.json_response({"terms": [], "groups": groups, "total": total})
    hits = await db.search(ts)
    projs = {p["chat_id"]: p for p in await _viewer_projects(bot, user["id"])}
    task_ids = {h["task"] for h in hits if h["task"] is not None}
    tasks_by_id = {}
    if task_ids:
        marks = ",".join("?" * len(task_ids))
        for t in await db.all(f"SELECT * FROM tasks WHERE id IN ({marks}) AND title<>''", tuple(task_ids)):
            if t["chat_id"] in projs or t.get("assignee_id") == user["id"]:
                tasks_by_id[t["id"]] = t
    names = {cid: p["display"] for cid, p in projs.items()}
    for cid in {t["chat_id"] for t in tasks_by_id.values()} - set(names):   # a task of another project, assigned to me
        names[cid] = (await db.project(cid) or {}).get("display") or ""

    def body(table, ref):
        return db.one(f"SELECT * FROM {table} WHERE id=?", (ref,))

    for h in hits:
        k = h["kind"]
        if k == "project":
            p = projs.get(h["ref"])
            if not p or is_personal(p["chat_id"]):
                continue
            total[k] += 1
            if len(groups[k]) < SEARCH_PER_KIND:
                groups[k].append({"kind": k, "pid": p["chat_id"], "project": p["display"],
                                  "color": COLORS[abs(p["chat_id"]) % len(COLORS)], "parts": fts.snippet(p["display"], ts)})
            continue
        t = tasks_by_id.get(h["task"])
        if not t:
            continue
        total[k] += 1
        if len(groups[k]) >= SEARCH_PER_KIND:
            continue
        row = {"kind": k, "task_id": t["id"], "pid": t["chat_id"], "task": t["title"],
               "project": PERSONAL if is_personal(t["chat_id"]) else names.get(t["chat_id"], ""),
               "color": COLORS[abs(t["chat_id"]) % len(COLORS)],
               "status": "closed" if t.get("closed") else "backlog" if t.get("backlog") else
                         "review" if _on_review(t) else "progress" if t.get("progress") else "open",
               "archived": bool(t["archived"]), "mine": t.get("assignee_id") == user["id"]}
        if k == "task":
            row.update(parts=fts.snippet(t["title"], ts), date=fmt_date(t["created_at"]))
        elif k == "result":
            row.update(parts=fts.snippet(t.get("result_note"), ts))
        elif k == "item":
            it = await body("items", h["ref"])
            if not it:
                continue
            row.update(ref=it["id"], done=bool(it["done"]), parts=fts.snippet(it["text"], ts))
        else:
            c = await body("comments", h["ref"])
            if not c:
                continue
            row.update(ref=c["id"], item_id=c.get("item_id"), author=c["author"], date=fmt_date(c["ts"]),
                       parts=fts.snippet(c["text"], ts))
        groups[k].append(row)
    return web.json_response({"terms": ts, "groups": groups, "total": total})


RELATED_MAX = 8


async def api_task_related(request):
    """Tasks to link this one with: ?q= searches by words; without it — the ones that look alike (title words),
    from every project this person sees, not this task and not the ones linked already."""
    user = user_from_request(request)
    bot = request.app["bot"]
    t = await task_for_user(request, int(request.match_info["task_id"]))
    q = (request.query.get("q") or "").strip()[:100]
    ts = fts.terms(q) if q else [x for x in fts.terms(t["title"]) if len(x) >= 4]
    skip = {t["id"], *await db.task_links(t["id"])}
    rows = []
    for h in await db.search(ts, any_term=not q) if ts else []:
        tid = h["task"]
        if tid is None or tid in skip or h["kind"] == "project":
            continue
        skip.add(tid)
        o = await _visible(bot, user["id"], tid)
        if o:
            rows.append(await _link_row(o))
            if len(rows) >= RELATED_MAX:
                break
    return web.json_response({"rows": rows, "similar": not q})


async def _log_link(kind: str, a: dict, b: dict, actor: dict):
    for x, y in ((a, b), (b, a)):
        proj = await db.project(y["chat_id"])
        await db.log_event(x["id"], kind, actor["name"], actor["id"], text=y["title"],
                           extra={"id": y["id"], "project": PERSONAL if is_personal(y["chat_id"]) else (proj or {}).get("display") or ""})


async def api_link_add(request):
    """Link two tasks: whoever sees both. Each stays where it is; both show the other in «Связанные задачи»."""
    user = user_from_request(request)
    t = await task_for_user(request, int(request.match_info["task_id"]))
    body = await request.json()
    try:
        other_id = int(body.get("id"))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text="id")
    if other_id == t["id"]:
        raise web.HTTPBadRequest(text="a task is not linked to itself")
    o = await task_for_user(request, other_id)
    if await db.link_tasks(t["id"], o["id"], user["id"]):
        await _log_link("linked", t, o, user)
    return web.json_response(await _for_user(request, await db.task(t["id"])))


async def api_link_delete(request):
    """Unlink: anyone who sees this task, even if the other one is in a chat they are not in."""
    user = user_from_request(request)
    t = await task_for_user(request, int(request.match_info["task_id"]))
    o = await db.task(int(request.match_info["other_id"]))
    if o and await db.unlink_tasks(t["id"], o["id"]):
        await _log_link("unlinked", t, o, user)
    return web.json_response(await _for_user(request, await db.task(t["id"])))


async def api_comment_file(request):
    """A screenshot or a video for a comment, before the comment itself: raw bytes, in parts of up to 8 MB
    (?up=<the upload's id>&part=0,1,…&last=1). The last part checks the file and gives its name for /comments."""
    t = await task_for_user(request, int(request.match_info["task_id"]))
    up, q = request.query.get("up", ""), request.query
    if not re.fullmatch(r"[0-9a-f]{24}", up):
        raise web.HTTPBadRequest(text="bad upload id")
    try:
        part = int(q.get("part", "0"))
    except ValueError:
        raise web.HTTPBadRequest(text="bad part")
    data = await request.read()
    if len(data) > CHUNK_MAX:
        raise web.HTTPRequestEntityTooLarge(max_size=CHUNK_MAX, actual_size=len(data))
    d = cfile_dir(t["id"])
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"up_{up}.part"
    if part == 0:
        tmp.write_bytes(data)
    else:
        if not tmp.exists() or tmp.stat().st_size != part * CHUNK_MAX:   # parts in order, all but the last full
            raise web.HTTPConflict(text="parts out of order")
        if tmp.stat().st_size + len(data) > CFILE_MAX:
            tmp.unlink(missing_ok=True)
            raise web.HTTPRequestEntityTooLarge(max_size=CFILE_MAX, actual_size=tmp.stat().st_size + len(data) if tmp.exists() else 0)
        with tmp.open("ab") as f:
            f.write(data)
    if q.get("last") != "1":
        return web.json_response({"ok": True})
    with tmp.open("rb") as f:
        kind = cfile_kind(f.read(16))
    if not kind:
        tmp.unlink(missing_ok=True)
        raise web.HTTPUnsupportedMediaType(text="only pictures (JPEG, PNG, WebP, GIF) and videos (MP4, MOV, WebM)")
    name = secrets.token_hex(12) + kind[0]
    tmp.rename(d / name)
    return web.json_response({"name": name, "kind": kind[1], "url": sign_cfile(t["id"], name)})


async def api_cfile(request):
    """A comment's screenshot or video by its signed link; videos can be played from any point (Range)."""
    task_id, name = int(request.match_info["task_id"]), request.match_info["name"]
    exp, sig = request.query.get("e", ""), request.query.get("s", "")
    try:
        ok = int(exp) >= time.time()
    except ValueError:
        ok = False
    good = hmac.new(_media_key(), f"cfile:{task_id}:{name}:{exp}".encode(), "sha256").hexdigest()[:32]
    if not ok or not hmac.compare_digest(good, sig) or not CFILE_RE.match(name):
        raise web.HTTPForbidden()
    f = cfile_dir(task_id) / name
    if not f.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(f, headers={"Cache-Control": "private, max-age=3600",
                                        "Content-Type": CFILE_TYPES.get(f.suffix, "application/octet-stream")})


async def api_comment_resolve(request):
    """{"resolved": true | false}: a question answered, or open again — anyone who sees the task."""
    c = await db.comment(int(request.match_info["comment_id"]))
    if not c or not c.get("question"):
        raise web.HTTPNotFound(text="question not found")
    t = await task_for_user(request, c["task_id"])
    user = user_from_request(request)
    on = bool((await request.json()).get("resolved", True))
    if on != bool(c.get("resolved_at")):
        await db.resolve_comment(c["id"], user["name"] if on else None)
        await db.log_event(t["id"], "answered" if on else "reasked", user["name"], user["id"],
                           item_id=c.get("item_id"), text=c["text"][:300])
    return web.json_response(await _for_user(request, await db.task(t["id"])))


async def api_comment_edit(request):
    """{"text": "..."}: only the one who wrote it changes a comment or a question (its files stay)."""
    c = await db.comment(int(request.match_info["comment_id"]))
    user = user_from_request(request)
    if not c:
        raise web.HTTPNotFound(text="comment not found")
    t = await task_for_user(request, c["task_id"])
    if c["user_id"] != user["id"]:
        raise web.HTTPForbidden(text="only the author can change a comment")
    text = str((await request.json()).get("text", "")).strip()[:2000]
    if not text and not json.loads(c.get("files") or "[]"):
        raise web.HTTPBadRequest(text="empty")
    if text != c["text"]:
        await db.edit_comment(c["id"], text)
    return web.json_response(await _for_user(request, await db.task(t["id"])))


async def api_comment_delete(request):
    """Only the one who wrote it removes a comment."""
    c = await db.comment(int(request.match_info["comment_id"]))
    user = user_from_request(request)
    if not c:
        raise web.HTTPNotFound(text="comment not found")
    t = await task_for_user(request, c["task_id"])
    if c["user_id"] != user["id"]:
        raise web.HTTPForbidden(text="only the author can delete a comment")
    await db.delete_comment(c["id"])
    return web.json_response(await _for_user(request, await db.task(t["id"])))


async def api_task_delete(request):
    task_id = int(request.match_info["task_id"])
    task = await task_for_user(request, task_id)
    await no_guest(request, task)
    await tasks.remove_checklist(request.app["bot"], task)
    base = (Path(settings.db_path).parent / "media" / f"task_{task_id}").resolve()
    await db.delete_task(task_id)
    if base.exists():  # screenshots imported from an export
        import shutil
        shutil.rmtree(base, ignore_errors=True)
    return web.json_response({"deleted": task_id})


async def api_task_move(request):
    """{"project_id": N | null}: move the task into that project, or into «Без проекта» when null
    (or when project_id is the person's own id). Must be in the source project (not a guest) and
    in the target; «Без проекта» is only their own. Checklist is retired in the old chat and posted
    in the new one when it has a Telegram chat."""
    t = await task_for_user(request, int(request.match_info["task_id"]))
    user = user_from_request(request)
    await no_guest(request, t)   # source: project's people only (same as archive/delete)
    body = await request.json()
    raw = body.get("project_id")
    if raw is None:
        pid = user["id"]   # «Без проекта»
    else:
        try:
            pid = int(raw)
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(text="project_id")
    if is_personal(pid):
        if pid != user["id"]:
            raise web.HTTPForbidden(text="only your own «Без проекта»")
    else:
        if not await db.project(pid):
            raise web.HTTPNotFound(text="project not found")
        if not await is_member(request.app["bot"], pid, user["id"]):
            raise web.HTTPForbidden(text="not a member")
    try:
        await tasks.move_to_project(request.app["bot"], t["id"], pid, user["name"], user["id"])
    except ValueError as e:
        raise web.HTTPBadRequest(text=str(e))
    return web.json_response(await _for_user(request, await db.task(t["id"])))


UPLOAD_MAX = 15 * 1024 * 1024
IMAGE_MAGIC = {b"\xff\xd8\xff": ".jpg", b"\x89PNG": ".png", b"RIFF": ".webp", b"GIF8": ".gif"}


async def api_media_add(request):
    """Screenshot uploaded from the phone's photo library (mini app)."""
    task_id = int(request.match_info["task_id"])
    await task_for_user(request, task_id)
    reader = await request.multipart()
    field = await reader.next()
    if field is None or field.name != "file":
        raise web.HTTPBadRequest(text="no file")
    data = bytearray()
    while chunk := await field.read_chunk(256 * 1024):
        data += chunk
        if len(data) > UPLOAD_MAX:
            raise web.HTTPRequestEntityTooLarge(max_size=UPLOAD_MAX, actual_size=len(data))
    ext = next((e for magic, e in IMAGE_MAGIC.items() if bytes(data[:4]).startswith(magic)), None)
    if not ext:
        raise web.HTTPUnsupportedMediaType(text="only JPEG, PNG, WebP or GIF")
    media = await db.media(task_id)
    idx = max((m["idx"] for m in media), default=0) + 1
    rel = f"task_{task_id}/u{idx}_{int(time.time())}{ext}"
    dst = Path(settings.db_path).parent / "media" / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(bytes(data))
    await db.add_media(task_id, [{"idx": idx, "kind": "photo", "file_id": "local:" + rel, "message_id": None}])
    u = user_from_request(request)
    await db.log_event(task_id, "shot_added", u["name"], u["id"])
    full = await _for_user(request, await db.task(task_id))
    photos = [m for m in full["media"] if m["kind"] == "photo"]
    full["new_shot"] = next(n for n, m in enumerate(photos, 1) if m["idx"] == idx)   # number to put on an item
    return web.json_response(full)


async def api_media_delete(request):
    task_id, idx = int(request.match_info["task_id"]), int(request.match_info["idx"])
    await task_for_user(request, task_id)
    m = next((x for x in await db.media(task_id) if x["idx"] == idx), None)
    if not m:
        raise web.HTTPNotFound(text="no such screenshot")
    await db.delete_media(task_id, idx)
    if (m["file_id"] or "").startswith("local:"):      # uploaded from the app: the file is ours to remove
        (Path(settings.db_path).parent / "media" / m["file_id"][6:]).unlink(missing_ok=True)
    u = user_from_request(request)
    await db.log_event(task_id, "shot_deleted", u["name"], u["id"])
    return await _after_change(request, task_id)


async def api_item_add(request):
    task_id = int(request.match_info["task_id"])
    await task_for_user(request, task_id)
    text = str((await request.json()).get("text", "")).strip()
    if not text:
        raise web.HTTPBadRequest(text="empty")
    before = await db.items(task_id)
    await db.add_item(task_id, text[:500])
    u = user_from_request(request)
    await db.log_event(task_id, "item_added", u["name"], u["id"], text=text[:500])
    await tasks.log_closed_flip(task_id, before, u["name"], u["id"])
    return await _after_change(request, task_id)


async def _item_for_user(request) -> dict:
    item = await db.item(int(request.match_info["item_id"]))
    if not item:
        raise web.HTTPNotFound(text="item not found")
    await task_for_user(request, item["task_id"])
    return item


async def api_item_patch(request):
    item = await _item_for_user(request)
    body = await request.json()
    fields = {}
    if "text" in body and str(body["text"]).strip():
        fields["text"] = str(body["text"]).strip()[:500]
    if "done" in body:
        fields["done"] = 1 if body["done"] else 0
    if "shot" in body:
        fields["shot"] = int(body["shot"]) if body["shot"] else None
    u = user_from_request(request)
    done = fields.pop("done", None)
    if fields:
        await db.update_item(item["id"], **fields)
    if "text" in fields and fields["text"] != item["text"]:
        await db.log_event(item["task_id"], "item_edited", u["name"], u["id"], item["id"], fields["text"],
                           extra={"from": item["text"]})
    if done is not None:
        await tasks.set_item_done(item["task_id"], item["id"], bool(done), u["name"], u["id"])
    return await _after_change(request, item["task_id"])


async def api_item_delete(request):
    item = await _item_for_user(request)
    before = await db.items(item["task_id"])
    await db.delete_item(item["id"])
    u = user_from_request(request)
    await db.log_event(item["task_id"], "item_deleted", u["name"], u["id"], text=item["text"])
    await tasks.log_closed_flip(item["task_id"], before, u["name"], u["id"])
    return await _after_change(request, item["task_id"])


async def api_task_order(request):
    """{"task_ids": [...]}: the tasks of one tab, top first."""
    chat_id = await _project_for_user(request)
    try:
        ids = [int(i) for i in (await request.json()).get("task_ids") or []][:1000]
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text="task_ids: numbers")
    if ids:
        await db.reorder_tasks(chat_id, ids)
    ts = await db.tasks_of_chat(chat_id)
    return web.json_response([await task_summary(t) for t in ts])


async def api_order(request):
    task_id = int(request.match_info["task_id"])
    await task_for_user(request, task_id)
    ids = [int(i) for i in (await request.json()).get("item_ids", [])]
    await db.reorder(task_id, ids)
    return await _after_change(request, task_id)


async def api_media(request):
    task_id, idx = int(request.match_info["task_id"]), int(request.match_info["idx"])
    if not check_media_sig(task_id, idx, request.query.get("e", ""), request.query.get("s", "")):
        raise web.HTTPForbidden()
    row = await db.one("SELECT * FROM media WHERE task_id=? AND idx=?", (task_id, idx))
    if not row:
        raise web.HTTPNotFound()
    if row["file_id"].startswith("local:"):
        # imported from a chat export: the picture is stored under data/media, not in Telegram
        base = (Path(settings.db_path).parent / "media").resolve()
        f = (base / row["file_id"][6:]).resolve()
        if base not in f.parents or not f.exists():
            raise web.HTTPNotFound()
        return web.FileResponse(f, headers={"Cache-Control": "private, max-age=3600"})
    f = await cached_file(request.app["bot"], row["file_id"], "", ".jpg")
    return web.FileResponse(f, headers={"Cache-Control": "private, max-age=3600"})


AUDIO_TYPES = {"audio/ogg": ".ogg", "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/x-m4a": ".m4a",
               "audio/aac": ".aac", "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/flac": ".flac"}


async def api_audio(request):
    """The original voice message of a transcript, as Telegram stores it (no re-encoding)."""
    task_id, n = int(request.match_info["task_id"]), int(request.match_info["n"])
    exp, sig = request.query.get("e", ""), request.query.get("s", "")
    try:
        ok = int(exp) >= time.time()
    except ValueError:
        ok = False
    good = hmac.new(_media_key(), f"audio:{task_id}:{n}:{exp}".encode(), "sha256").hexdigest()[:32]
    if not ok or not hmac.compare_digest(good, sig):
        raise web.HTTPForbidden()
    t = await db.task(task_id)
    trs = json.loads(t["transcripts"] or "[]") if t else []
    if not 0 <= n < len(trs) or not trs[n].get("file_id"):
        raise web.HTTPNotFound()
    mime = (trs[n].get("mime") or "audio/ogg").split(";")[0].strip().lower()
    if mime not in AUDIO_TYPES:
        mime = "audio/ogg"
    f = await cached_file(request.app["bot"], trs[n]["file_id"], "audio_", AUDIO_TYPES[mime])
    return web.FileResponse(f, headers={"Cache-Control": "private, max-age=3600", "Content-Type": mime})


async def api_file(request):
    """A file from the chat (table, PDF, archive …) through the bot: Telegram gives bots files up to 20 MB."""
    task_id, n = int(request.match_info["task_id"]), int(request.match_info["n"])
    exp, sig = request.query.get("e", ""), request.query.get("s", "")
    try:
        ok = int(exp) >= time.time()
    except ValueError:
        ok = False
    good = hmac.new(_media_key(), f"file:{task_id}:{n}:{exp}".encode(), "sha256").hexdigest()[:32]
    if not ok or not hmac.compare_digest(good, sig):
        raise web.HTTPForbidden()
    t = await db.task(task_id)
    files = json.loads(t["files"] or "[]") if t and t.get("files") else []
    if not 0 <= n < len(files) or not files[n].get("file_id"):
        raise web.HTTPNotFound()
    x = files[n]
    f = await cached_file(request.app["bot"], x["file_id"], "file_", "")
    name = x["name"] or "file"
    ascii_name = "".join(c if c.isascii() and c not in '"\\' and c.isprintable() else "_" for c in name)
    return web.FileResponse(f, headers={
        "Cache-Control": "private, max-age=3600",
        "Content-Type": x.get("mime") or "application/octet-stream",
        "Content-Disposition": f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(name)}'})


async def project_photo(bot: Bot, p: dict) -> str | None:
    """The project's picture: its own (set in the app) over the chat's photo; ?v= changes with it, so a new one shows."""
    if p.get("pic"):
        return sign_chat_photo(p["chat_id"]) + "&v=" + Path(p["pic"]).stem.rsplit("_", 1)[-1]
    return sign_chat_photo(p["chat_id"]) if await chat_photo_id(bot, p["chat_id"]) else None


PIC_MAX = 2 * 1024 * 1024


def _pic_path(rel: str) -> Path:
    return Path(settings.db_path).parent / "media" / rel


async def api_project_pic(request):
    """POST multipart «file»: the project's own picture (the app sends a square JPEG). Any member may change it."""
    chat_id = await _project_for_user(request)
    reader = await request.multipart()
    field = await reader.next()
    if field is None or field.name != "file":
        raise web.HTTPBadRequest(text="no file")
    data = bytearray()
    while chunk := await field.read_chunk(256 * 1024):
        data += chunk
        if len(data) > PIC_MAX:
            raise web.HTTPRequestEntityTooLarge(max_size=PIC_MAX, actual_size=len(data))
    ext = next((e for magic, e in IMAGE_MAGIC.items() if bytes(data[:4]).startswith(magic)), None)
    if not ext:
        raise web.HTTPUnsupportedMediaType(text="only JPEG, PNG, WebP or GIF")
    old = (await db.project(chat_id)).get("pic")
    rel = f"projects/p{abs(chat_id)}_{time.time_ns()}{ext}"
    dst = _pic_path(rel)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(bytes(data))
    await db.x("UPDATE projects SET pic=? WHERE chat_id=?", (rel, chat_id))
    if old:
        _pic_path(old).unlink(missing_ok=True)
    return web.json_response({"photo": await project_photo(request.app["bot"], await db.project(chat_id)), "pic": True})


FETCH_MAX = 10 * 1024 * 1024


def _public_host(host: str) -> bool:
    """Only addresses on the open internet: no localhost, LAN or cloud metadata behind a link."""
    import ipaddress
    import socket
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global or ip.is_multicast:
            return False
    return bool(infos)


async def api_image_fetch(request):
    """POST {url}: download a picture by its link for the app (a browser can't read other sites' images).
    Returns the image itself; the app crops it and uploads it like a chosen file."""
    import asyncio
    from urllib.parse import urljoin, urlsplit
    import aiohttp
    await _project_for_user(request)
    try:
        url = str((await request.json()).get("url") or "").strip()
    except Exception:
        raise web.HTTPBadRequest(text="no url")
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "Mozilla/5.0 Todogram"}) as s:
        for _ in range(4):   # follow a few redirects, checking every hop
            u = urlsplit(url)
            if u.scheme not in ("http", "https") or not u.hostname:
                raise web.HTTPBadRequest(text="bad url")
            if not await asyncio.get_running_loop().run_in_executor(None, _public_host, u.hostname):
                raise web.HTTPBadRequest(text="bad host")
            try:
                async with s.get(url, allow_redirects=False) as r:
                    if r.status in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                        url = urljoin(url, r.headers["Location"])
                        continue
                    if r.status != 200:
                        raise web.HTTPBadGateway(text=f"upstream {r.status}")
                    data = bytearray()
                    async for chunk in r.content.iter_chunked(256 * 1024):
                        data += chunk
                        if len(data) > FETCH_MAX:
                            raise web.HTTPRequestEntityTooLarge(max_size=FETCH_MAX, actual_size=len(data))
            except (aiohttp.ClientError, asyncio.TimeoutError):
                raise web.HTTPBadGateway(text="fetch failed")
            ext = next((e for magic, e in IMAGE_MAGIC.items() if bytes(data[:4]).startswith(magic)), None)
            if not ext:
                raise web.HTTPUnsupportedMediaType(text="not an image")
            ctype = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}[ext]
            return web.Response(body=bytes(data), content_type=ctype, headers={"Cache-Control": "no-store"})
    raise web.HTTPBadRequest(text="too many redirects")


async def api_project_pic_delete(request):
    """Back to the chat's photo (or the letter, without a chat)."""
    chat_id = await _project_for_user(request)
    old = (await db.project(chat_id)).get("pic")
    await db.x("UPDATE projects SET pic=NULL WHERE chat_id=?", (chat_id,))
    if old:
        _pic_path(old).unlink(missing_ok=True)
    return web.json_response({"photo": await project_photo(request.app["bot"], await db.project(chat_id)), "pic": False})


async def api_chat_photo(request):
    chat_id = int(request.match_info["chat_id"])
    exp, sig = request.query.get("e", ""), request.query.get("s", "")
    try:
        ok = int(exp) >= time.time()
    except ValueError:
        ok = False
    good = hmac.new(_media_key(), f"chat:{chat_id}:{exp}".encode(), "sha256").hexdigest()[:32]
    if not ok or not hmac.compare_digest(good, sig):
        raise web.HTTPForbidden()
    p = await db.project(chat_id)
    if p and p.get("pic") and _pic_path(p["pic"]).exists():   # the project's own picture
        return web.FileResponse(_pic_path(p["pic"]), headers={"Cache-Control": "private, max-age=86400"})
    fid = await chat_photo_id(request.app["bot"], chat_id)
    if not fid:
        raise web.HTTPNotFound()
    f = await cached_file(request.app["bot"], fid, "chat_", ".jpg")  # new avatar → new file id → new file
    return web.FileResponse(f, headers={"Cache-Control": "private, max-age=3600"})


async def api_user_photo(request):
    user_id = int(request.match_info["user_id"])
    exp, sig = request.query.get("e", ""), request.query.get("s", "")
    try:
        ok = int(exp) >= time.time()
    except ValueError:
        ok = False
    good = hmac.new(_media_key(), f"user:{user_id}:{exp}".encode(), "sha256").hexdigest()[:32]
    if not ok or not hmac.compare_digest(good, sig):
        raise web.HTTPForbidden()
    fid = await user_photo_id(request.app["bot"], user_id)
    if not fid:
        raise web.HTTPNotFound()
    f = await cached_file(request.app["bot"], fid, "user_", ".jpg")  # new avatar → new file id → new file
    return web.FileResponse(f, headers={"Cache-Control": "private, max-age=3600"})


PAGE_FILES = ("index.html", "app.css", "app.js")   # the mini app: markup, styles, script — one version for all three
_built: tuple[tuple, dict[str, bytes], str] | None = None


def _build() -> tuple[dict[str, bytes], str]:
    """The app's files from memory (re-read when one changes) and their version — a hash of all three. The version
    is written into the page (__BUILD__): the app compares it on «потянуть, чтобы обновить», and the page asks for
    app.css / app.js with ?v=<version>, so a phone never mixes a new page with an old script from its cache."""
    global _built
    stamp = tuple((STATIC / n).stat().st_mtime for n in PAGE_FILES)
    if not _built or _built[0] != stamp:
        raw = {n: (STATIC / n).read_bytes() for n in PAGE_FILES}
        v = hashlib.md5(b"".join(raw[n] for n in PAGE_FILES)).hexdigest()[:12]
        raw["index.html"] = raw["index.html"].replace(b"__BUILD__", v.encode())
        _built = (stamp, raw, v)
    return _built[1], _built[2]


def _page() -> tuple[bytes, str]:
    """The page and its version."""
    files, v = _build()
    return files["index.html"], v


async def app_asset(request):
    """app.css / app.js from memory, so the middleware gzips them; with the current ?v= — cached for a year."""
    name = request.match_info["name"]
    files, v = _build()
    resp = web.Response(body=files[name], charset="utf-8",
                        content_type="text/css" if name.endswith(".css") else "application/javascript")
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable" if request.query.get("v") == v else "no-cache"
    return resp


LANDING_HOSTS = {"todogram.pro", "www.todogram.pro"}   # the service's own domain: «/» is the landing, «/?demo» the app inside it


async def index(request):
    """The page from memory (re-read when the file changes): small, so the middleware can gzip it and answer 304."""
    if request.host.split(":")[0].lower() in LANDING_HOSTS and "demo" not in request.query:
        return await landing(request)
    return web.Response(body=_page()[0], content_type="text/html", charset="utf-8")


async def landing(request):
    """The service's landing page: the live demo (/?demo) in a phone frame and a few lines about it."""
    page = (STATIC / "landing.html").read_text("utf-8")
    link = f"https://t.me/{bot_link_name(tasks.BOT_USERNAME)}" if tasks.BOT_USERNAME else "https://t.me/"
    return web.Response(text=page.replace("__BOT_LINK__", link), content_type="text/html", charset="utf-8")


async def api_live(request):
    """A stream of «something changed» pushes (see live.py): the app refreshes its screen on each one."""
    return await live.stream(request, user_from_request(request))


async def api_version(request):
    """The version of the page now on the server (no login: it says nothing about anyone)."""
    return web.json_response({"v": _page()[1]})


COMPRESS = ("application/json", "text/html", "text/css", "application/javascript", "text/javascript")


# ---------------- the site outside Telegram: sign in through the bot ----------------
LOGIN_TTL = 300               # seconds to tap «Войти» in the bot
SESSION_TTL = 30 * 86400      # a session lives 30 days after the last visit
SESSION_TOUCH = 3600          # «seen» is written at most once an hour


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@web.middleware
async def site_session(request, handler):
    """«Authorization: Session <token>» — someone signed in on the site: the same person as in the mini app."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Session "):
        h = _h(auth[8:].strip())
        s = await db.session(h)
        if not s or time.time() - s["seen"] > SESSION_TTL:
            raise web.HTTPUnauthorized(text="session expired")
        if time.time() - s["seen"] > SESSION_TOUCH:
            await db.session_seen(h)
        request["site_user"] = {"id": s["user_id"], "name": s["name"], "start_param": ""}
    if settings.closed_alpha and request.path.startswith("/api/") and handler not in (
        api_version, api_login_start, api_login_poll, api_logout,
        api_media, api_file, api_audio, api_cfile, api_chat_photo, api_user_photo,
    ):
        user = user_from_request(request)
        if not await db.alpha_allowed(user["id"]):
            bot_name = bot_link_name((await request.app["bot"].me()).username)
            return web.json_response({"error": "alpha_required", "link": f"https://t.me/{bot_name}?start=waitlist"},
                                     status=403)
        request["site_user"] = user
    return await handler(request)


async def api_login_start(request):
    """A one-time code; the site opens t.me/<bot>?start=login_<code>, the bot asks «Войти?»."""
    import secrets
    token = secrets.token_urlsafe(18)
    await db.login_new(_h(token))
    me = (await request.app["bot"].me()).username
    return web.json_response({"token": token, "link": f"https://t.me/{bot_link_name(me)}?start=login_{token}", "ttl": LOGIN_TTL})


async def api_login_poll(request):
    """The site asks every couple of seconds: confirmed in the bot → a session (once), else pending."""
    import secrets
    token = request.query.get("token", "")
    lg = await db.login(_h(token)) if token else None
    if not lg or time.time() - lg["created"] > LOGIN_TTL:
        return web.json_response({"status": "expired"}, status=410)
    if not lg["user_id"]:
        return web.json_response({"status": "pending"})
    await db.login_drop(_h(token))
    if not await db.alpha_allowed(lg["user_id"]):
        bot_name = bot_link_name((await request.app["bot"].me()).username)
        return web.json_response({"status": "alpha", "link": f"https://t.me/{bot_name}?start=waitlist"})
    session = secrets.token_urlsafe(32)
    await db.session_new(_h(session), lg["user_id"], lg["name"])
    return web.json_response({"status": "ok", "session": session, "user": {"id": lg["user_id"], "name": lg["name"]}})


async def login_confirmed(token: str, user_id: int, name: str) -> bool:
    """Called by the bot when «Войти» is tapped; False if the code is unknown or too old."""
    lg = await db.login(_h(token))
    if not lg or lg["user_id"] or time.time() - lg["created"] > LOGIN_TTL:
        return False
    await db.login_confirm(_h(token), user_id, name)
    return True


async def login_known(token: str) -> bool:
    lg = await db.login(_h(token))
    return bool(lg) and not lg["user_id"] and time.time() - lg["created"] <= LOGIN_TTL


async def api_logout(request):
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Session "):
        await db.session_drop(_h(auth[8:].strip()))
    return web.json_response({"ok": True})


@web.middleware
async def speed(request, handler):
    """ETag + «304 Not Modified» for the page and API answers (the app polls every 5 s: most answers are
    the same as last time), gzip for text, a long cache for the fonts."""
    resp = await handler(request)
    if request.path.startswith("/static/fonts/"):
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        if request.path.endswith(".woff2"):
            resp.headers["Content-Type"] = "font/woff2"   # aiohttp's own table does not know it
        return resp
    body = getattr(resp, "body", None)
    if request.method != "GET" or resp.status != 200 or not isinstance(body, (bytes, bytearray)) \
            or resp.content_type not in COMPRESS:
        return resp
    tag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
    resp.headers["ETag"] = tag
    resp.headers.setdefault("Cache-Control", "private, no-cache")
    if tag in (request.headers.get("If-None-Match") or ""):
        return web.Response(status=304, headers={"ETag": tag, "Cache-Control": resp.headers["Cache-Control"]})
    if len(body) > 1024 and "gzip" in request.headers.get("Accept-Encoding", ""):
        resp.enable_compression(web.ContentCoding.gzip)
    return resp


def make_app(bot: Bot) -> web.Application:
    global _bot
    _bot = bot
    app = web.Application(client_max_size=UPLOAD_MAX + 1024 * 1024, middlewares=[speed, site_session])
    app["bot"] = bot
    app.on_shutdown.append(live.close_all)   # end the live streams first: a restart doesn't wait for them
    r = app.router
    r.add_get("/api/live", api_live)
    r.add_get("/", index)
    r.add_get("/landing", landing)
    r.add_get("/api/version", api_version)
    r.add_post("/api/login/start", api_login_start)
    r.add_get("/api/login/poll", api_login_poll)
    r.add_post("/api/logout", api_logout)
    r.add_get("/api/me", api_me)
    r.add_post("/api/me/notify", api_notify)
    r.add_post("/api/me/notify_kinds", api_notify_kinds)
    r.add_post("/api/tasks/{task_id}/share", api_task_share)
    r.add_get("/api/projects", api_projects)
    r.add_put("/api/projects/order", api_projects_order)
    r.add_get("/api/time", api_time)
    r.add_post("/api/projects", api_project_create)
    r.add_patch("/api/projects/{chat_id}", api_project_rename)
    r.add_delete("/api/projects/{chat_id}", api_project_delete)
    r.add_post("/api/projects/{chat_id}/photo", api_project_pic)
    r.add_delete("/api/projects/{chat_id}/photo", api_project_pic_delete)
    r.add_post("/api/projects/{chat_id}/photo_fetch", api_image_fetch)
    r.add_get("/api/history", api_history)
    r.add_get("/api/notifications", api_notifications)
    r.add_get("/api/notifications/unread", api_notifications_unread)
    r.add_post("/api/notifications/seen", api_notifications_seen)
    r.add_post("/api/notifications/{event_id:\\d+}/read", api_notification_read)
    r.add_get("/api/projects/{chat_id}/tasks", api_project_tasks)
    r.add_post("/api/projects/{chat_id}/tasks", api_task_create)
    r.add_get("/api/projects/{chat_id}/history", api_project_history)
    r.add_get("/api/tasks/{task_id}", api_task)
    r.add_get("/api/tasks/{task_id}/source", api_task_source)
    r.add_post("/api/tasks/{task_id}/voice/{key:[0-9a-f]{64}}/summary", api_voice_summary)
    r.add_get("/api/tasks/{task_id}/related", api_task_related)
    r.add_post("/api/tasks/{task_id}/links", api_link_add)
    r.add_delete("/api/tasks/{task_id}/links/{other_id}", api_link_delete)
    r.add_post("/api/tasks/{task_id}/comments", api_comment_add)
    r.add_delete("/api/comments/{comment_id}", api_comment_delete)
    r.add_patch("/api/comments/{comment_id}", api_comment_edit)
    r.add_post("/api/comments/{comment_id}/resolve", api_comment_resolve)
    r.add_post("/api/tasks/{task_id}/comment_files", api_comment_file)
    r.add_get("/api/cfile/{task_id}/{name}", api_cfile)
    r.add_get("/api/comments", api_comments_feed)
    r.add_get("/api/search", api_search)
    r.add_patch("/api/tasks/{task_id}", api_task_patch)
    r.add_delete("/api/tasks/{task_id}", api_task_delete)
    r.add_post("/api/tasks/{task_id}/move", api_task_move)
    r.add_post("/api/tasks/{task_id}/items", api_item_add)
    r.add_post("/api/tasks/{task_id}/media", api_media_add)
    r.add_delete("/api/tasks/{task_id}/media/{idx}", api_media_delete)
    r.add_put("/api/tasks/{task_id}/order", api_order)
    r.add_patch("/api/items/{item_id}", api_item_patch)
    r.add_delete("/api/items/{item_id}", api_item_delete)
    r.add_get("/api/media/{task_id}/{idx}", api_media)
    r.add_get("/api/audio/{task_id}/{n}", api_audio)
    r.add_get("/api/file/{task_id}/{n}", api_file)
    r.add_get("/api/chatphoto/{chat_id}", api_chat_photo)
    r.add_get("/api/userphoto/{user_id}", api_user_photo)
    r.add_put("/api/tasks/{task_id}/reminders", api_reminders)
    r.add_get("/api/projects/{chat_id}/access", api_access)
    r.add_post("/api/projects/{chat_id}/access/reset", api_access_reset)
    r.add_delete("/api/projects/{chat_id}/access/{user_id}", api_access_remove)
    r.add_post("/api/join", api_join)
    r.add_put("/api/projects/{chat_id}/order", api_task_order)
    r.add_get("/api/projects/{chat_id}/people", api_project_people)
    r.add_get("/api/people/tasks", api_people_tasks)
    r.add_get("/static/{name:app\\.(?:css|js)}", app_asset)
    from . import agent_api   # here, not at the top: it imports from this module
    agent_api.add_routes(r)
    r.add_static("/static/", STATIC, show_index=False)
    return app
