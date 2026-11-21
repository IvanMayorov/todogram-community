"""Task deadlines and personal reminders in the private chat («за день», «за час», «в момент срока»).

A deadline is a date (all day) or a date and time, stored as unix time in the server's time zone (TZ in .env).
A reminder row says who wants which reminder; `sent_for` is the deadline it already went out for, so moving
the deadline arms it again by itself. «Через час» under a reminder is a one-off row of kind «snooze».
If the task has an assignee, every reminder someone set goes to the assignee as well (once per moment, however
many people set the same one).
"""
import asyncio
import html
import logging
import time
from datetime import datetime

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .db import db

log = logging.getLogger("deadlines")

KINDS = ("day", "hour", "due")
LATE = 3600          # a reminder missed by more than this (bot was down) is skipped, not sent hours late
MORNING = 10 * 3600  # all-day deadlines: reminders at 10:00
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
          "ноября", "декабря"]


def parse(date: str | None, tm: str | None = None) -> tuple[int, bool] | None:
    """'2026-11-30' [+ '18:00'] → (unix time, all day). Also takes '2026-11-30T18:00'. None/'' → None."""
    date = (date or "").strip()
    if not date:
        return None
    if "T" in date and not tm:
        date, tm = date.split("T", 1)
    d = datetime.strptime(date[:10], "%Y-%m-%d")
    tm = (tm or "").strip()[:5]
    if tm:
        h, m = (int(x) for x in tm.split(":"))
        return int(d.replace(hour=h, minute=m).timestamp()), False
    return int(d.timestamp()), True


def text(at: int | None, allday: bool) -> str:
    if not at:
        return ""
    t = time.localtime(at)
    s = f"{t.tm_mday} {MONTHS[t.tm_mon - 1]}"
    if t.tm_year != time.localtime().tm_year:
        s += f" {t.tm_year}"
    return s if allday else f"{s}, {t.tm_hour:02d}:{t.tm_min:02d}"


def iso(at: int | None, allday: bool) -> dict | None:
    if not at:
        return None
    t = time.localtime(at)
    return {"at": at, "allday": bool(allday), "text": text(at, allday), "date": time.strftime("%Y-%m-%d", t),
            "time": None if allday else time.strftime("%H:%M", t)}


def fire_at(kind: str, due: int, allday: bool) -> int:
    if allday:   # due = midnight of the day
        return {"day": due - 86400 + MORNING, "hour": due + MORNING, "due": due + MORNING}[kind]
    return {"day": due - 86400, "hour": due - 3600, "due": due}[kind]


HEAD = {"day": "Завтра срок", "hour": "Через час срок", "due": "Срок наступил", "snooze": "Напоминаю"}
HEAD_ALLDAY = {"day": "Завтра срок", "hour": "Сегодня срок", "due": "Сегодня срок"}


async def set_due(task_id: int, due: tuple[int, bool] | None):
    at, allday = due if due else (None, False)
    await db.x("UPDATE tasks SET due_at=?, due_allday=? WHERE id=?", (at, 1 if allday else 0, task_id))
    if at:   # reminders whose moment is already behind for the new deadline must not all go off at once
        for r in await db.all("SELECT * FROM reminders WHERE task_id=? AND kind<>'snooze'", (task_id,)):
            if fire_at(r["kind"], at, allday) <= time.time():
                await db.x("UPDATE reminders SET sent_for=? WHERE task_id=? AND user_id=? AND kind=?",
                           (at, task_id, r["user_id"], r["kind"]))


async def mine(task_id: int, user_id: int) -> list[str]:
    rows = await db.all("SELECT kind FROM reminders WHERE task_id=? AND user_id=? AND kind<>'snooze'", (task_id, user_id))
    return [k for k in KINDS if k in {r["kind"] for r in rows}]


async def set_mine(task_id: int, user_id: int, kinds: list[str]):
    t = await db.task(task_id)
    want = [k for k in KINDS if k in kinds]
    await db.x(f"DELETE FROM reminders WHERE task_id=? AND user_id=? AND kind<>'snooze' "
               f"AND kind NOT IN ({','.join('?' * len(want)) or 'NULL'})", (task_id, user_id, *want))
    for k in want:
        past = t["due_at"] and fire_at(k, t["due_at"], bool(t["due_allday"])) <= time.time()
        await db.x("INSERT INTO reminders(task_id,user_id,kind,sent_for) VALUES(?,?,?,?) "
                   "ON CONFLICT(task_id,user_id,kind) DO NOTHING",
                   (task_id, user_id, k, t["due_at"] if past else None))


async def snooze(task_id: int, user_id: int, seconds: int = 3600):
    await db.x("INSERT INTO reminders(task_id,user_id,kind,at) VALUES(?,?,'snooze',?) "
               "ON CONFLICT(task_id,user_id,kind) DO UPDATE SET at=excluded.at", (task_id, user_id, int(time.time()) + seconds))


async def due_now(now: float | None = None) -> list[dict]:
    """Reminders whose moment has come; ones missed by more than LATE are marked as sent without sending."""
    now = now or time.time()
    rows = await db.all(
        "SELECT r.*, t.due_at, t.due_allday, t.archived, t.closed, t.title, t.assignee_id, "
        " (SELECT COUNT(*) FROM items i WHERE i.task_id=t.id) AS total, "
        " (SELECT COUNT(*) FROM items i WHERE i.task_id=t.id AND i.done=1) AS done_cnt "
        "FROM reminders r JOIN tasks t ON t.id=r.task_id "
        "WHERE t.title<>'' AND ((r.kind='snooze' AND r.at<=?) OR (r.kind<>'snooze' AND t.due_at IS NOT NULL "
        " AND r.sent_for IS NOT t.due_at))", (now,))
    out, seen = [], set()

    def push(r: dict, uid: int, assignee: bool = False):
        if (r["task_id"], uid, r["kind"]) not in seen:
            seen.add((r["task_id"], uid, r["kind"]))
            out.append({**r, "user_id": uid, "as_assignee": assignee or uid == r["assignee_id"]})

    for r in rows:
        closed = r["archived"] or r["closed"]
        if r["kind"] == "snooze":
            await db.x("DELETE FROM reminders WHERE task_id=? AND user_id=? AND kind='snooze'", (r["task_id"], r["user_id"]))
            if not closed:
                push(r, r["user_id"])
            continue
        at = fire_at(r["kind"], r["due_at"], bool(r["due_allday"]))
        if at > now or closed:
            continue
        await db.x("UPDATE reminders SET sent_for=? WHERE task_id=? AND user_id=? AND kind=?",
                   (r["due_at"], r["task_id"], r["user_id"], r["kind"]))
        if now - at <= LATE:
            push(r, r["user_id"])
            if r["assignee_id"] and r["assignee_id"] > 0:
                push(r, r["assignee_id"], True)
    return out


async def task_kinds(task_id: int) -> list[str]:
    """Reminders anyone set on the task: these are the ones its assignee gets."""
    rows = await db.all("SELECT DISTINCT kind FROM reminders WHERE task_id=? AND kind<>'snooze'", (task_id,))
    return [k for k in KINDS if k in {r["kind"] for r in rows}]


def keyboard(task_id: int, bot_username: str) -> InlineKeyboardMarkup:
    from .render import miniapp_url
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть", url=miniapp_url(bot_username, f"task_{task_id}"))],
        [InlineKeyboardButton(text="Через час", callback_data=f"rm:{task_id}:snooze"),
         InlineKeyboardButton(text="Сделано", callback_data=f"rm:{task_id}:done")]])


async def send(bot: Bot, r: dict, bot_username: str):
    t = await db.task(r["task_id"])
    if r.get("as_assignee") and r["user_id"] != t["assignee_id"]:
        return   # reassigned meanwhile
    p = await db.project(t["chat_id"])
    heads = HEAD_ALLDAY if t["due_allday"] and r["kind"] != "snooze" else HEAD
    e = html.escape
    when = text(t["due_at"], bool(t["due_allday"]))
    msg = (f"{heads[r['kind']]}: <b>{e(t['title'])}</b>\n" + " · ".join(
        x for x in ((p["display"] if p else ""), f"до {when}" if when else "",
                    "вы исполнитель" if r.get("as_assignee") else "") if x))
    await bot.send_message(r["user_id"], msg, parse_mode="HTML", reply_markup=keyboard(t["id"], bot_username))


async def loop(bot: Bot):
    from . import tasks
    while True:
        await asyncio.sleep(30)
        try:
            for r in await due_now():
                try:
                    await send(bot, r, tasks.BOT_USERNAME)
                except Exception as e:   # the person never opened the bot or blocked it
                    log.info("reminder %s/%s to %s not sent: %s", r["task_id"], r["kind"], r["user_id"], e)
        except Exception:
            log.exception("reminders loop")
