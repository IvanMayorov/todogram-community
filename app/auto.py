"""Auto mode (/auto on): the bot reads the chat itself and creates tasks.

1. After AUTO_PAUSE_SECONDS of silence, new messages go to the model as TEXT ONLY
   (photos as [фото], voices as transcripts) together with the chat's open tasks.
   The model says which messages form a new task or extend an open one.
2. Only for those groups the full content (screenshots, video frames) is sent,
   and the checklist is built exactly like for ✍ / ⚡.
3. The bot creates the task (or adds the items to the open one) right away, without asking.
   A mistake is one tap away: «Не задача» under a new checklist deletes it,
   «Отменить» / «Отдельной задачей» under an addition takes the items back.
"""
import asyncio
import json
import html
import logging
import time

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyParameters

from . import ai, deadlines, media, tasks
from .config import settings
from .db import db

log = logging.getLogger(__name__)
_backoff: dict[int, float] = {}   # chat_id -> do not retry before
_busy: set[int] = set()
CONTEXT_MESSAGES = 12  # earlier messages shown to the model so replies after a pause make sense

KIND_LABEL = {"photo": "фото", "image_doc": "картинка", "video": "видео", "video_note": "кружок",
              "animation": "GIF", "document": "файл"}


async def _line(bot: Bot, m: dict) -> str:
    text = (m["text"] or "").strip()
    kind = m["kind"]
    if kind in ("voice", "audio"):
        tr = m.get("transcript")
        if tr is None and (m["file_size"] or 0) <= media.TG_LIMIT:
            try:
                tr = await ai.transcribe(await media.download(bot, m["file_id"]), "voice.ogg" if kind == "voice" else "audio.mp3")
            except Exception:
                log.exception("transcribe failed")
                tr = ""
            await db.set_transcript(m["chat_id"], m["message_id"], tr)
        body = f"[голосовое] {tr or '(не расшифровано)'} {text}".strip()
    elif kind in KIND_LABEL:
        name = f" {m['file_name']}" if kind == "document" and m["file_name"] else ""
        body = f"[{KIND_LABEL[kind]}{name}] {text}".strip()
    else:
        body = text
    return f"[{m['message_id']}] {m['user_name']}: {body}"


async def _lines(bot: Bot, msgs: list[dict]) -> list[str]:
    """All lines at once: voice messages are transcribed side by side (a few at a time), order kept."""
    gate = asyncio.Semaphore(tasks.PARALLEL)

    async def one(m):
        async with gate:
            return await _line(bot, m)
    return list(await asyncio.gather(*(one(m) for m in msgs)))


async def scan_chat(bot: Bot, chat_id: int):
    proj = await db.project(chat_id)
    if not proj or not proj["auto"]:
        return
    msgs = await db.new_for_auto(chat_id, proj["auto_cursor"] or 0, settings.auto_max_batch)
    if not msgs:
        return
    silent = time.time() - max(m["date"] for m in msgs) >= settings.auto_pause_seconds
    if not silent and len(msgs) < settings.auto_max_batch:
        return  # conversation still going: wait for a pause

    open_tasks = await db.open_tasks(chat_id)
    lines = await _lines(bot, msgs)
    task_lines = []
    for t in open_tasks[:20]:
        its = (await db.items(t["id"]))[:8]
        task_lines.append(f"#{t['id']} {t['title']}: " + "; ".join(i["text"] for i in its))
    tasks_txt = "\n".join(task_lines) or "нет"
    ctx = await _lines(bot, await db.before(chat_id, proj["auto_cursor"] or 0, CONTEXT_MESSAGES))
    transcript = (("Контекст:\n" + "\n".join(ctx) + "\n\n") if ctx else "") + \
        "Новая переписка:\n" + "\n".join(lines) + "\n\nОткрытые задачи:\n" + tasks_txt

    groups = await ai.find_tasks(transcript, {m["message_id"] for m in msgs}, {t["id"] for t in open_tasks})
    await db.set_cursor(chat_id, max(m["message_id"] for m in msgs))
    log.info("auto %s: %s messages -> %s groups", chat_id, len(msgs), len(groups))

    for g in groups:
        try:
            await suggest(bot, chat_id, g)
        except Exception:
            log.exception("auto suggestion failed in %s", chat_id)


BOT_ACTOR = "Бот"   # who is shown in the task history for automatic changes


async def suggest(bot: Bot, chat_id: int, group: dict):
    """Build the checklist for a group of messages and apply it at once (new task or addition)."""
    msgs = await tasks.collect(chat_id, group["message_ids"])  # adds whole albums, drops used ones
    if not msgs:
        return
    parts, shots, transcripts, notes, files = await tasks.build_content(bot, msgs)
    photo_count = sum(1 for s in shots if s["kind"] == "photo")
    result = await ai.make_checklist(parts, photo_count, {m["message_id"] for m in msgs})
    if not result["items"]:
        return

    kind, task_id = group["type"], group["task_id"]
    data = {"title": result["title"], "items": result["items"], "shots": shots, "transcripts": transcripts,
            "notes": notes, "files": files, "due": result.get("due"), "message_ids": [m["message_id"] for m in msgs],
            "author_id": msgs[0]["user_id"], "author_name": msgs[0]["user_name"]}
    reply = ReplyParameters(message_id=msgs[0]["message_id"], allow_sending_without_reply=True)
    target = await db.task(task_id) if kind == "add" and task_id else None
    if target and not target["archived"]:
        sid = await db.add_suggestion(chat_id, "add", task_id, data)
        await _add_items(bot, sid, BOT_ACTOR, 0)
        e = html.escape
        lines = [f"☐ {e(it['text'])}" for it in result["items"]]
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Отменить", callback_data=f"a:{sid}:undo"),
            InlineKeyboardButton(text="Отдельной задачей", callback_data=f"a:{sid}:new")]])
        msg = await bot.send_message(chat_id, f"Добавил в задачу «{e(target['title'])}»:\n\n" + "\n".join(lines),
                                     parse_mode="HTML", reply_markup=kb, reply_parameters=reply)
        await db.set_suggestion(sid, msg_id=msg.message_id)
        return
    sid = await db.add_suggestion(chat_id, "new", None, data)
    msg = await bot.send_message(chat_id, "Создаю задачу…", reply_parameters=reply)
    await db.set_suggestion(sid, msg_id=msg.message_id)
    await _create(bot, sid, BOT_ACTOR, 0)


async def _create(bot: Bot, sid: int, actor: str, actor_id: int) -> int:
    """The bot message of the suggestion becomes the checklist of a new task."""
    s = await db.suggestion(sid)
    d, chat_id = s["data"], s["chat_id"]
    task_id = await db.create_task(chat_id, d["author_id"], d["author_name"], d["message_ids"][0])
    await db.mark_used(chat_id, d["message_ids"], task_id)
    await db.fill_task(task_id, d["title"], d["items"], d["transcripts"], d["notes"], d.get("files"))
    await db.add_media(task_id, d["shots"])
    await db.update_task(task_id, auto=1)
    if d.get("due"):
        await deadlines.set_due(task_id, tuple(d["due"]))
    await db.log_event(task_id, "created", actor, actor_id, text=d["title"],
                       extra={"items": len(d["items"]), "auto": 1, "author": d["author_name"]})
    await db.set_suggestion(sid, status="created")
    await db.set_checklist_msg(task_id, s["msg_id"])
    await tasks.refresh_checklist(bot, task_id)
    return task_id


async def _add_items(bot: Bot, sid: int, actor: str, actor_id: int):
    s = await db.suggestion(sid)
    d, chat_id, t = s["data"], s["chat_id"], await db.task(s["task_id"])
    media_before = await db.media(t["id"])
    have, photos_before = len(media_before), sum(1 for m in media_before if m["kind"] == "photo")
    # renumber the new screenshots after the task's existing ones
    shots = [{**m, "idx": have + m["idx"], "source_chat_id": chat_id} for m in d["shots"]]
    items = [{**it, "shot": (photos_before + it["shot"]) if it.get("shot") else None,
              "source_chat_id": chat_id} for it in d["items"]]
    was = await db.items(t["id"])
    before = {i["id"] for i in was}
    await db.append_items(t["id"], items)
    await db.add_media(t["id"], shots)
    transcripts = [{**entry, "source_chat_id": chat_id} for entry in d["transcripts"]]
    files = [{**entry, "source_chat_id": chat_id} for entry in d.get("files") or []]
    await db.add_transcripts(t["id"], transcripts, d["notes"], files)
    if d.get("due") and not t.get("due_at"):
        await deadlines.set_due(t["id"], tuple(d["due"]))
    await db.mark_used(chat_id, d["message_ids"], t["id"])
    await db.log_event(t["id"], "items_added", actor, actor_id, extra={"count": len(items), "auto": 1})
    await tasks.log_closed_flip(t["id"], was, actor, actor_id)
    d["added"] = {"items": [i["id"] for i in await db.items(t["id"]) if i["id"] not in before],
                  "media": [m["idx"] for m in shots]}
    await db.set_suggestion(sid, status="added", data=d)
    await tasks.refresh_checklist(bot, t["id"])


async def undo_add(bot: Bot, sid: int, user_id: int, user_name: str, as_new: bool = False) -> str:
    """«Отменить» takes the added items back; «Отдельной задачей» then makes a task of them."""
    s = await db.suggestion(sid)
    if not s or s["status"] != "added":
        return "Уже обработано"
    d, chat_id, t = s["data"], s["chat_id"], await db.task(s["task_id"])
    added = d.get("added", {})
    if t:
        was = await db.items(t["id"])
        for iid in added.get("items", []):
            await db.delete_item(iid)
        for idx in added.get("media", []):
            await db.x("DELETE FROM media WHERE task_id=? AND idx=?", (t["id"], idx))
        gone = {f["message_id"] for f in d.get("files") or []}
        if gone and t.get("files"):
            await db.set_files(t["id"], [f for f in json.loads(t["files"])
                                        if tasks.source_chat(t, f) != chat_id or f.get("message_id") not in gone])
        await db.log_event(t["id"], "items_removed", user_name, user_id, extra={"count": len(added.get("items", []))})
        await tasks.log_closed_flip(t["id"], was, user_name, user_id)
        await tasks.refresh_checklist(bot, t["id"])
    await db.mark_used(chat_id, d["message_ids"], None)
    if as_new:
        await _create(bot, sid, user_name, user_id)
        return "Сделал отдельной задачей"
    await db.set_suggestion(sid, status="undone")
    try:
        await bot.delete_message(chat_id, s["msg_id"])
    except TelegramBadRequest:
        await _edit(bot, chat_id, s["msg_id"], "Отменено")
    return "Отменил"


async def undo_create(bot: Bot, task_id: int, user_id: int, user_name: str) -> str:
    """«Не задача» under a checklist the bot created by itself."""
    t = await db.task(task_id)
    if not t or not t.get("auto"):
        return "Уже обработано"
    if any(i["done"] for i in await db.items(task_id)):
        return "В задаче уже есть отметки — удалите её в приложении"
    await tasks.remove_checklist(bot, t)
    await db.delete_task(task_id)
    log.info("auto task %s removed by %s", task_id, user_name)
    return "Убрал"


async def _edit(bot, chat_id, msg_id, text):
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text, reply_markup=None)
    except TelegramBadRequest:
        pass


async def auto_loop(bot: Bot):
    while True:
        await asyncio.sleep(30)
        for p in await db.auto_projects():
            cid = p["chat_id"]
            if cid in _busy or _backoff.get(cid, 0) > time.time():
                continue
            _busy.add(cid)
            try:
                await scan_chat(bot, cid)
            except ai.AIError:
                log.warning("auto %s: AI failed, retry in 5 min", cid)
                _backoff[cid] = time.time() + 300
            except Exception:
                log.exception("auto loop error in %s", cid)
                _backoff[cid] = time.time() + 300
            finally:
                _busy.discard(cid)
