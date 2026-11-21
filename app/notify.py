"""Private notifications: people who allowed the bot to write to them get changes of the tasks they take part in.

Who: the task's author and everyone who has done something in it (ticked, added, took it into work), except whoever
made the change. What: the changes that matter — status, all items done, new items and screenshots. Ticks of single
items are left out: there would be too many. Changes coming one after another are gathered into one message.
"""
import asyncio
import html
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from . import tasks
from .bg import spawn
from .db import db
from .render import miniapp_url

log = logging.getLogger("notify")

# what a person can choose to get (the app's settings): kind → the history events it covers
# (the order is the app's: the sections of «Уведомления», questions first)
KINDS = {"questions": ("asked", "answered", "reasked"), "comments": ("commented",),
         "status": ("status", "closed", "reopened", "report"), "assignee": ("assigned",), "due": ("due",),
         "items": ("item_added", "items_added"), "shots": ("shot_added",), "changes": ("renamed", "descr", "moved")}

DELAY = 40          # seconds: changes within this time after the first one go out as one message
MAX_LINES = 8

_bot: Bot | None = None
_pending: dict[tuple[int, int], list[dict]] = {}   # (user_id, task_id) → events waiting to go out
_timers: dict[tuple[int, int], asyncio.Task] = {}

STATUS_LINE = {"progress": "Взята в работу", "review": "На проверке", "backlog": "Отложена в бэклог", "open": "Снова открыта"}


def setup(bot: Bot):
    global _bot
    _bot = bot
    db.on_event = on_event


def _result(ex: dict) -> str:
    """The result of a closed task: its description, then the link — always shown, as a short clickable address."""
    u, note = ex.get("result_url"), (ex.get("result_note") or "").strip()
    note = f"\n{html.escape(note if len(note) <= 300 else note[:297] + '…')}" if note else ""
    if not u:
        return note
    short = u.split("://", 1)[-1].removeprefix("www.")
    short = short if len(short) <= 60 else short[:57] + "…"
    return f'{note}\n<a href="{html.escape(u, quote=True)}">{html.escape(short)}</a>'


def line(e: dict) -> str | None:
    """One change, one short line: what · who [· hours], the result link under it."""
    kind, ex, who = e["kind"], e.get("extra") or {}, html.escape(e["actor"] or "")
    if kind in ("closed", "report"):
        from .render import hours_text
        head = " · ".join(x for x in ("Закрыта" if kind == "closed" else "Отчёт", who,
                                      hours_text(ex["hours"]) if ex.get("hours") else "") if x)
        return head + _result(ex)
    if kind == "status":
        s = STATUS_LINE.get(ex.get("to"))
    elif kind == "reopened":
        s = "Снова открыта"
    elif kind == "item_added":
        s = f"Новый пункт: «{html.escape((e.get('text') or '')[:200])}»"
    elif kind == "items_added":
        s = f"Новых пунктов: {ex.get('count', 0)}"
    elif kind == "commented":
        s = f"Комментарий: «{html.escape((e.get('text') or '')[:300])}»"
    elif kind == "asked":
        s = f"Вопрос: «{html.escape((e.get('text') or '')[:300])}»"
    elif kind == "answered":
        s = f"Вопрос решён: «{html.escape((e.get('text') or '')[:200])}»"
    elif kind == "reasked":
        s = f"Вопрос снова открыт: «{html.escape((e.get('text') or '')[:200])}»"
    elif kind == "renamed":
        s = f"Новое название: «{html.escape((e.get('text') or '')[:200])}»"
    elif kind == "descr":
        s = f"Описание: «{html.escape((e.get('text') or '')[:300])}»" if e.get("text") else "Описание убрано"
    elif kind == "moved":
        dest = html.escape((e.get("text") or ex.get("project") or "")[:200])
        src = html.escape((ex.get("from_project") or "")[:200])
        s = f"Из «{src}» в «{dest}»" if src else f"В «{dest}»"
    elif kind == "shot_added":
        s = "Новый скриншот"
    elif kind == "assigned":
        s = f"Исполнитель: {html.escape(ex['name'])}" if ex.get("name") else "Исполнитель снят"
    elif kind == "due":
        s = f"Срок: {html.escape(ex['text'])}" if ex.get("text") else "Срок убран"
    else:
        return None
    return f"{s} · {who}" if s and who else s


def on_event(e: dict):
    """Called by db.log_event. Only schedules: the event is written already, sending happens later."""
    if not _bot or not line(e):
        return
    try:
        spawn(_route(e))
    except RuntimeError:
        pass


async def _route(e: dict):
    try:
        people = await db.task_people(e["task_id"])
    except Exception:
        log.exception("notify: recipients")
        return
    author = None
    if e["kind"] == "status" and (e.get("extra") or {}).get("to") == "review":   # the author gets review.tell_author instead
        t = await db.task(e["task_id"])
        author = t and t.get("author_id")
    for uid in people:
        if uid == author:
            continue
        if uid == e["actor_id"] or (e["kind"] == "assigned" and uid == (e.get("extra") or {}).get("id")):
            continue   # the new assignee gets a message of their own
        key = (uid, e["task_id"])
        _pending.setdefault(key, []).append(e)
        if key not in _timers:
            _timers[key] = spawn(_later(key))


async def _later(key):
    try:
        await asyncio.sleep(DELAY)
        await flush(key)
    finally:
        _timers.pop(key, None)


async def flush(key):
    uid, task_id = key
    events = _pending.pop(key, [])
    # undone within a couple of minutes (tick → untick) — that change is gone from the history, so not sent either
    events = [e for e in events if await db.event_exists(e["id"])]
    t = await db.task(task_id)
    if not events or not t or not t["title"] or not await db.notify_enabled(uid):
        return
    want = await db.notify_kinds(uid)
    if want is not None:   # only the kinds they chose
        allowed = {k for w in want for k in KINDS.get(w, ())}
        events = [e for e in events if e["kind"] in allowed]
        if not events:
            return
    from .web import can_see_task   # someone who has left the chat must not keep getting its tasks
    if not await can_see_task(_bot, t, uid):
        return
    lines = list(dict.fromkeys(filter(None, map(line, events))))
    if len(lines) > MAX_LINES:
        lines = lines[:MAX_LINES - 1] + [f"…и ещё {len(lines) - MAX_LINES + 1}"]
    text = f"<b>{html.escape(t['title'])}</b>\n" + "\n".join(lines)   # compact: the task and what changed, nothing else
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="Открыть задачу", url=miniapp_url(tasks.BOT_USERNAME, f"task_{task_id}"))]])
    try:
        await _bot.send_message(uid, text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
    except TelegramForbiddenError:   # blocked the bot or never allowed it — stop trying
        await db.set_notify(uid, False)
    except Exception:
        log.exception("notify: send to %s", uid)


async def flush_all():
    """Send everything that is waiting at once (tests, shutdown)."""
    for key in list(_pending):
        t = _timers.pop(key, None)
        if t:
            t.cancel()
        await flush(key)
