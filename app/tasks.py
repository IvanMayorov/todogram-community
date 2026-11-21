"""Turning marked messages into a checklist task."""
import asyncio
import html
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramMigrateToChat
from aiogram.types import ReplyParameters

from . import ai, deadlines, media
from .bg import spawn
from .config import settings
from .db import db, has_chat
from .render import checklist_kb, checklist_text, source_chat


def _is_personal(chat_id: int) -> bool:
    return chat_id > 0   # same as web.is_personal: a person's own «Без проекта»

log = logging.getLogger(__name__)

_chat_locks: dict[int, asyncio.Lock] = {}
BOT_USERNAME = ""  # set in main


def _lock(chat_id: int) -> asyncio.Lock:
    return _chat_locks.setdefault(chat_id, asyncio.Lock())


async def collect(chat_id: int, message_ids: list[int]) -> list[dict]:
    """Buffered, unused messages + whole albums, in send order."""
    out: dict[int, dict] = {}
    for mid in message_ids:
        m = await db.message(chat_id, mid)
        if not m or m["task_id"]:
            continue
        group = await db.album(chat_id, m["media_group_id"]) if m["media_group_id"] else [m]
        for g in group:
            if not g["task_id"]:
                out[g["message_id"]] = g
    return [out[k] for k in sorted(out)]


PARALLEL = 4   # downloads / transcriptions at once while building a task: faster, but gentle on Telegram and the AI


async def _fetch(bot: Bot, m: dict) -> dict:
    """The slow part for one message (download, speech, video frames). Done for all messages at once;
    the checklist content is then put together in message order."""
    kind = m["kind"]
    if kind in ("photo", "image_doc"):
        return {"data": await media.download(bot, m["file_id"])}
    if kind in ("voice", "audio"):
        tr = m.get("transcript")  # auto mode may have transcribed it already
        if tr is None:
            data = await media.download(bot, m["file_id"])
            tr = await ai.transcribe(data, m["file_name"] or ("voice.ogg" if kind == "voice" else "audio.mp3"))
            await db.set_transcript(m["chat_id"], m["message_id"], tr)
        return {"tr": tr}
    if kind in ("video", "video_note", "animation"):
        audio, frames = await media.video_audio_and_frames(await media.download(bot, m["file_id"]))
        return {"tr": await ai.transcribe(audio, "video.mp3") if audio else "", "frames": frames}
    return {}


async def build_content(bot: Bot, msgs: list[dict]):
    """OpenAI content parts + screenshots + transcripts + notes + attached files (tables, PDFs …)."""
    parts: list[dict] = []
    shots: list[dict] = []        # {idx, kind, file_id, message_id}
    transcripts: list[dict] = []
    notes: list[str] = []
    videos: list[dict] = []
    files: list[dict] = []

    heavy = ("photo", "image_doc", "voice", "audio", "video", "video_note", "animation")
    too_big = {m["message_id"] for m in msgs if m["kind"] in heavy and (m["file_size"] or 0) > media.TG_LIMIT}
    gate = asyncio.Semaphore(PARALLEL)

    async def fetch(m):
        if m["kind"] not in heavy or m["message_id"] in too_big:
            return {}
        async with gate:
            try:
                return await _fetch(bot, m)
            except Exception as e:
                log.exception("media processing failed for %s/%s", m["chat_id"], m["message_id"])
                return {"error": e}

    got = await asyncio.gather(*(fetch(m) for m in msgs))

    for m, g in zip(msgs, got):
        head = f"Сообщение [{m['message_id']}] от {m['user_name']}:"
        text = (m["text"] or "").strip()
        kind = m["kind"]

        if m["message_id"] in too_big:
            notes.append(f"файл не обработан (больше 20 МБ): {m['file_name'] or kind}")
            parts.append({"type": "text", "text": f"{head} [файл больше 20 МБ, не обработан] {text}".strip()})
            continue
        if "error" in g:
            notes.append(f"файл не обработан: {m['file_name'] or kind}")
            parts.append({"type": "text", "text": f"{head} [{kind}: не удалось обработать] {text}".strip()})
            continue

        if kind in ("photo", "image_doc"):
            n = len(shots) + 1
            shots.append({"idx": n, "kind": "photo", "file_id": m["file_id"], "message_id": m["message_id"]})
            parts.append({"type": "text", "text": f"{head} [Скриншот {n}] {text}".strip()})
            parts.append(ai.image_part(g["data"], m["mime"] or "image/jpeg"))
        elif kind in ("voice", "audio"):
            tr = g["tr"]
            if tr:
                # file_id is kept so the mini app can play the recording after the chat buffer is purged
                transcripts.append({"author": m["user_name"], "text": tr, "message_id": m["message_id"],
                                    "file_id": m["file_id"], "mime": m["mime"], "duration": m.get("duration")})
            parts.append({"type": "text", "text": f"{head} [голосовое, расшифровка] {tr or '(не удалось расшифровать)'} {text}".strip()})
        elif kind in ("video", "video_note", "animation"):
            tr, frames = g["tr"], g["frames"]
            if tr:
                transcripts.append({"author": m["user_name"], "text": tr, "message_id": m["message_id"]})
            label = {"video": "видео", "video_note": "кружок", "animation": "GIF"}[kind]
            parts.append({"type": "text", "text": f"{head} [{label}; кадры ниже — это не скриншоты; звук: {tr or 'нет'}] {text}".strip()})
            for f in frames:
                parts.append(ai.image_part(f))
            if m["thumb_id"]:
                videos.append({"kind": "video", "file_id": m["thumb_id"], "message_id": m["message_id"]})
            if not frames and not tr:
                notes.append(f"{label} не обработано (нет ffmpeg или звука)")
        elif kind == "document":
            files.append(file_entry(m))
            parts.append({"type": "text", "text": f"{head} [файл: {m['file_name'] or 'документ'}] {text}".strip()})
        elif text:
            parts.append({"type": "text", "text": f"{head} {text}"})

    # video previews go after screenshots so item.shot numbers stay = screenshot numbers
    for v in videos:
        v["idx"] = len(shots) + 1
        shots.append(v)
    return parts, shots, transcripts, notes, files


def file_entry(m: dict) -> dict:
    """A file from the chat kept in the task: the mini app offers it for download (Telegram keeps the file)."""
    return {"name": m["file_name"] or "файл", "size": m["file_size"] or 0, "mime": m["mime"] or "",
            "file_id": m["file_id"], "message_id": m["message_id"], "author": m["user_name"]}


async def create_task(bot: Bot, chat_id: int, author_id: int, author_name: str, message_ids: list[int]):
    async with _lock(chat_id):
        msgs = await collect(chat_id, message_ids)
        if not msgs:
            return
        first = msgs[0]["message_id"]
        task_id = await db.create_task(chat_id, author_id, author_name, first)
        await db.mark_used(chat_id, [m["message_id"] for m in msgs], task_id)

    placeholder = await bot.send_message(
        chat_id, "Разбираю…", reply_parameters=ReplyParameters(message_id=first, allow_sending_without_reply=True)
    )

    try:
        parts, shots, transcripts, notes, files = await build_content(bot, msgs)
        photo_count = sum(1 for s in shots if s["kind"] == "photo")
        if not parts:
            raise ValueError("empty")
        result = await ai.make_checklist(parts, photo_count, {m["message_id"] for m in msgs})
    except ai.AIError:
        await db.delete_task(task_id)  # messages become free again so people can retry
        await _edit(placeholder, "Не получилось разобрать сообщения: ИИ-модель не ответила или ответила с ошибкой. "
                                 "Отметьте их ещё раз чуть позже.")
        return
    except Exception:
        log.exception("task %s failed", task_id)
        await db.delete_task(task_id)
        await _edit(placeholder, "Не получилось разобрать сообщения. Отметьте их ещё раз чуть позже.")
        return

    if not result["items"]:
        await db.delete_task(task_id)
        await _edit(placeholder, "Не нашёл задач в этих сообщениях")
        return

    await db.fill_task(task_id, result["title"], result["items"], transcripts, notes, files)
    await db.add_media(task_id, shots)
    if result.get("due"):
        await deadlines.set_due(task_id, result["due"])
    await db.log_event(task_id, "created", author_name, author_id, text=result["title"],
                       extra={"items": len(result["items"])})
    await db.set_checklist_msg(task_id, placeholder.message_id)
    await refresh_checklist(bot, task_id)


async def create_forwarded(bot: Bot, chat_id: int, author_id: int, author_name: str,
                           src_chat: int, message_ids: list[int], status_chat: int, status_msg_id: int,
                           assignee_id: int | None = None) -> int | None:
    """Messages forwarded to the bot in private → a task in the project `chat_id`. Progress and errors are
    shown in the private chat (status message); the checklist itself is posted to the project chat.
    `assignee_id` is set the same way as in the mini app (history, DM, warning if they never opened the bot)."""
    async def status(text: str, kb=None):
        try:
            await bot.edit_message_text(chat_id=status_chat, message_id=status_msg_id, text=text,
                                        parse_mode="HTML", reply_markup=kb)
        except TelegramBadRequest:
            pass

    async with _lock(src_chat):
        msgs = await collect(src_chat, message_ids)
        if not msgs:
            await status("Эти сообщения уже разобраны")
            return None
        task_id = await db.create_task(chat_id, author_id, author_name, None)
        await db.mark_used(src_chat, [m["message_id"] for m in msgs], task_id)
    try:
        parts, shots, transcripts, notes, files = await build_content(bot, msgs)
        if not parts:
            raise ValueError("empty")
        result = await ai.make_checklist(parts, sum(1 for s in shots if s["kind"] == "photo"),
                                         {m["message_id"] for m in msgs})
    except Exception as e:
        if not isinstance(e, ai.AIError):
            log.exception("forwarded task %s failed", task_id)
        await db.delete_task(task_id)   # the messages are free again: the project can be picked once more
        await status("Не получилось разобрать сообщения. Перешлите их ещё раз чуть позже.")
        return None
    if not result["items"]:
        await db.delete_task(task_id)
        await status("Не нашёл задач в этих сообщениях")
        return None

    # ids of the private chat mean nothing in the project chat: no links to «the message in the chat»
    items = [{**it, "src": []} for it in result["items"]]
    for x in (*shots, *transcripts, *files):
        x["message_id"] = None
    await db.fill_task(task_id, result["title"], items, transcripts, notes, files)
    await db.add_media(task_id, shots)
    if result.get("due"):
        await deadlines.set_due(task_id, result["due"])
    await db.log_event(task_id, "created", author_name, author_id, text=result["title"],
                       extra={"items": len(items), "forwarded": 1})

    # assignee before the checklist so the chat message already shows who does it
    from . import web
    dm, aname = None, None
    if assignee_id:
        if assignee_id == author_id:
            aname, granted = author_name, False
        else:
            aname, granted = await web.admit_assignee(bot, chat_id, assignee_id, author_id)
        await db.update_task(task_id, assignee_id=assignee_id, assignee_name=aname)
        await db.log_event(task_id, "assigned", author_name, author_id, text=aname,
                           extra={"id": assignee_id, "name": aname})
        if assignee_id != author_id:
            dm = await web.tell_assignee(bot, task_id, assignee_id, author_name, granted)

    task, rows, proj = await db.task(task_id), await db.items(task_id), await db.project(chat_id)
    pname = proj["display"] if proj else ""
    if has_chat(chat_id):
        try:
            msg = await bot.send_message(chat_id, checklist_text(task, rows, pname),
                                         reply_markup=checklist_kb(task, BOT_USERNAME), parse_mode="HTML")
            await db.set_checklist_msg(task_id, msg.message_id)
        except Exception:
            log.exception("cannot post checklist of forwarded task %s", task_id)   # the task stays in the app anyway
    e = html.escape
    warn = (f"\n\n{e(aname)} ещё не запускал(а) бота — личные сообщения и напоминания не дойдут."
            if dm is False and aname else "")
    await status(f"Задача «{e(result['title'])}» — в проекте «{e(pname)}», пунктов: {len(items)}{warn}",
                 checklist_kb(task, BOT_USERNAME))
    return task_id


# ---------------- history ----------------

async def set_item_done(task_id: int, item_id: int, done: bool, actor: str, actor_id: int):
    """Tick or untick one item and write it to the task history (plus «closed»/«reopened» when that flips)."""
    before = await db.items(task_id)
    it = next((i for i in before if i["id"] == item_id), None)
    if not it or bool(it["done"]) == done:
        return
    await db.update_item(item_id, done=1 if done else 0)
    await db.log_toggle(task_id, "done" if done else "undone", "undone" if done else "done", actor, actor_id,
                        item_id, it["text"])
    await log_closed_flip(task_id, before, actor, actor_id)


async def log_closed_flip(task_id: int, before: list[dict], actor: str, actor_id: int):
    """Ticking, adding or removing items can close or reopen a task. `before` = its items before the change.
    The last open item ticked closes it; an open item appearing in a task that had none reopens it.
    A task closed by its status with items still open stays closed while they are ticked or unticked."""
    t = await db.task(task_id)
    if not t:
        return
    after = await db.items(task_id)
    open_before, open_after = any(not i["done"] for i in before), any(not i["done"] for i in after)
    if t.get("closed") and open_after and not open_before:
        await db.update_task(task_id, closed=0)
        await db.log_toggle(task_id, "reopened", "closed", actor, actor_id)
    elif not t.get("closed") and after and not open_after and (open_before or not before):
        await db.update_task(task_id, closed=1)
        await db.log_toggle(task_id, "closed", "reopened", actor, actor_id)


async def complete_all(task_id: int, actor: str, actor_id: int):
    """«Закрыть всё»: every item ticked and the task closed (one without items is simply closed)."""
    t = await db.task(task_id)
    left = [i for i in await db.items(task_id) if not i["done"]]
    if left:
        await db.x("UPDATE items SET done=1 WHERE task_id=?", (task_id,))
        await db.log_event(task_id, "done_all", actor, actor_id, extra={"count": len(left)})
    if t and not t.get("closed"):
        await db.update_task(task_id, closed=1, backlog=0, progress=0, review_at=None)
        await db.log_event(task_id, "closed", actor, actor_id)


async def _edit(msg, text: str):
    try:
        await msg.edit_text(text)
    except TelegramBadRequest:
        pass


async def refresh_checklist(bot: Bot, task_id: int):
    task = await db.task(task_id)
    if not task or not task["checklist_msg_id"]:
        return
    items = await db.items(task_id)
    proj = await db.project(task["chat_id"])
    try:
        await bot.edit_message_text(
            chat_id=source_chat(task),
            message_id=task["checklist_msg_id"],
            text=checklist_text(task, items, proj["display"] if proj else ""),
            reply_markup=checklist_kb(task, BOT_USERNAME),
            parse_mode="HTML",
        )
    except (TelegramBadRequest, TelegramMigrateToChat) as e:
        if "not modified" not in str(e):
            log.warning("cannot update checklist %s: %s", task_id, e)


async def create_manual(bot: Bot, chat_id: int, author_id: int, author_name: str, title: str,
                        items: list[str], status: dict) -> int:
    """Task typed in the mini app: no source messages, no AI. The checklist is posted to the chat."""
    task_id = await db.create_task(chat_id, author_id, author_name, None)
    await db.fill_task(task_id, title, [{"text": t} for t in items], [], [])
    if status:
        await db.update_task(task_id, **status)
    await db.log_event(task_id, "created", author_name, author_id, text=title, extra={"items": len(items)})
    await _post_checklist(bot, task_id)
    return task_id


async def _post_checklist(bot: Bot, task_id: int):
    """Post the checklist message into the task's chat, if that project has a Telegram chat."""
    task = await db.task(task_id)
    if not task or not has_chat(task["chat_id"]):
        return
    if task.get("checklist_msg_id"):
        return
    proj = await db.project(task["chat_id"])
    rows = await db.items(task_id)
    try:
        msg = await bot.send_message(task["chat_id"], checklist_text(task, rows, proj["display"] if proj else ""),
                                     reply_markup=checklist_kb(task, BOT_USERNAME), parse_mode="HTML")
        await db.set_checklist_msg(task_id, msg.message_id)
    except Exception:
        log.exception("cannot post checklist of task %s", task_id)


async def _retire_checklist(bot: Bot, task: dict, note: str):
    """Remove the checklist from its current chat, or edit it to `note` if Telegram refuses delete.
    Same pattern as remove_checklist (delete → fallback edit)."""
    if not task.get("checklist_msg_id"):
        return
    chat_id = source_chat(task)
    try:
        await bot.delete_message(chat_id, task["checklist_msg_id"])
    except (TelegramBadRequest, TelegramMigrateToChat):
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=task["checklist_msg_id"],
                                        text=note, reply_markup=None)
        except (TelegramBadRequest, TelegramMigrateToChat):
            pass


def _project_label(chat_id: int, proj: dict | None) -> str:
    if _is_personal(chat_id):
        return "Без проекта"
    return (proj or {}).get("display") or ""


async def move_to_project(bot: Bot, task_id: int, new_chat_id: int, actor: str, actor_id: int):
    """Move a task to another project or into «Без проекта» (personal, chat_id = that person's id).
    Retires the old chat's checklist, posts a new one in the target chat when it has one.
    Comments, items, files and history stay on the task."""
    t = await db.task(task_id)
    if not t or not t["title"]:
        raise ValueError("task not found")
    old = t["chat_id"]
    if new_chat_id == old:
        raise ValueError("already in this project")
    if _is_personal(new_chat_id):
        if not await db.project(new_chat_id):
            await db.upsert_project(new_chat_id, "Без проекта")
        new_proj = await db.project(new_chat_id)
    else:
        new_proj = await db.project(new_chat_id)
        if not new_proj:
            raise ValueError("project not found")
    old_proj = await db.project(old)
    dest = _project_label(new_chat_id, new_proj)
    src = _project_label(old, old_proj)
    await _retire_checklist(bot, t, f"Задача «{t['title']}» перенесена в «{dest}»")
    # New home owns the checklist; drop legacy so refresh/links follow the new chat
    # (item/media source_chat_id still points at original messages).
    await db.x("UPDATE tasks SET chat_id=?, checklist_msg_id=NULL, legacy_chat_id=NULL WHERE id=?",
               (new_chat_id, task_id))
    await db.log_event(task_id, "moved", actor, actor_id, text=dest,
                       extra={"id": new_chat_id, "from": old, "to": new_chat_id,
                              "project": dest, "from_project": src})
    await _post_checklist(bot, task_id)


async def remove_checklist(bot: Bot, task: dict):
    """Task deleted in the app: delete the bot's checklist message, or mark it if Telegram refuses."""
    await _retire_checklist(bot, task, f"Задача «{task['title']}» удалена")


async def drafts_loop(bot: Bot):
    """Create tasks AUTO_CREATE_SECONDS after a user's last ✍."""
    import time

    tick = 0
    while True:
        await asyncio.sleep(3)
        try:
            for chat_id, user_id in await db.drafts_due(time.time() - settings.auto_create_seconds):
                await flush_draft(bot, chat_id, user_id)
            tick += 1
            if tick % 1200 == 0:  # ~hourly
                await db.purge_old()
                from .web import CACHE_DAYS
                n, size = await asyncio.to_thread(media.prune_cache, settings.cache_dir, CACHE_DAYS)
                if n:
                    log.info("cache: removed %s old files, %.1f MB", n, size / 2 ** 20)
        except Exception:
            log.exception("drafts loop error")


_author_names: dict[tuple[int, int], str] = {}


def remember_name(chat_id: int, user_id: int, name: str):
    _author_names[(chat_id, user_id)] = name


async def flush_draft(bot: Bot, chat_id: int, user_id: int, extra: list[int] | None = None):
    ids = await db.draft_take(chat_id, user_id)
    ids += extra or []
    if ids:
        name = _author_names.get((chat_id, user_id))
        if not name:  # e.g. after a restart: take the name from the user's last buffered message
            row = await db.one("SELECT user_name FROM messages WHERE chat_id=? AND user_id=? ORDER BY date DESC LIMIT 1",
                               (chat_id, user_id))
            name = row["user_name"] if row else "Участник"
        spawn(_safe_create(bot, chat_id, user_id, name, ids))


async def _safe_create(bot, chat_id, user_id, name, ids):
    try:
        await create_task(bot, chat_id, user_id, name, ids)
    except Exception:
        log.exception("create_task crashed")
