"""«На проверке»: the one who does the task says it's ready, the one who set it (the task's author) checks it.

The author gets a private message right away, with «Принять» (close) and «Вернуть в работу». While the task stays
on review, the bot reminds them once a day, at most NUDGES times. A task on review is still «в работе»
(progress=1) — it counts as not closed everywhere — with review_at set: when it went on review.
"""
import asyncio
import html
import logging
import time

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .db import db
from .render import miniapp_url

log = logging.getLogger("review")

EVERY = 24 * 3600   # a reminder a day while it waits
NUDGES = 3          # and no more than this many


def keyboard(task_id: int, bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть задачу", url=miniapp_url(bot_username, f"task_{task_id}"))],
        [InlineKeyboardButton(text="Принять", callback_data=f"rv:{task_id}:ok"),
         InlineKeyboardButton(text="Вернуть в работу", callback_data=f"rv:{task_id}:back")]])


async def _blocked(uid: int) -> bool:
    """Turned private messages off in the app — then not these either."""
    r = await db.one("SELECT enabled FROM notify WHERE user_id=?", (uid,))
    return bool(r) and not r["enabled"]


async def tell_author(bot: Bot, task_id: int, actor_id: int, again: int = 0) -> bool:
    """The message to whoever set the task. False when there is nobody to tell (they did it themselves, no author,
    left the chat, turned messages off) or Telegram refused."""
    from . import tasks
    from .web import can_see_task
    t = await db.task(task_id)
    uid = t and t.get("author_id")
    if not t or not t["title"] or not uid or uid <= 0 or uid == actor_id or await _blocked(uid):
        return False
    if not await can_see_task(bot, t, uid):
        return False
    p = await db.project(t["chat_id"])
    who = t.get("assignee_name") or ""
    head = "Напоминание: задача ждёт проверки" if again else "Нужно проверить задачу"
    sub = " · ".join(x for x in ((p["display"] if p else ""), f"сделал(а) {who}" if who else "") if x)
    text = f"{head}\n<b>{html.escape(t['title'])}</b>" + (f"\n{html.escape(sub)}" if sub else "")
    try:
        await bot.send_message(uid, text, parse_mode="HTML", reply_markup=keyboard(task_id, tasks.BOT_USERNAME),
                               disable_web_page_preview=True)
        return True
    except TelegramForbiddenError:
        return False
    except Exception:
        log.exception("review: send to %s", uid)
        return False


async def due_now(now: float | None = None) -> list[dict]:
    """Tasks on review whose next reminder is due: a day after it went on review, then a day after each reminder."""
    now = now or time.time()
    return await db.all(
        "SELECT id, author_id, COALESCE(review_n,0) AS n FROM tasks WHERE review_at IS NOT NULL AND progress=1 "
        "AND COALESCE(closed,0)=0 AND COALESCE(archived,0)=0 AND COALESCE(backlog,0)=0 AND title<>'' "
        "AND COALESCE(review_n,0) < ? AND review_at + (COALESCE(review_n,0) + 1) * ? <= ?", (NUDGES, EVERY, now))


async def nudge_all(bot: Bot, now: float | None = None):
    for r in await due_now(now):
        await db.x("UPDATE tasks SET review_n=COALESCE(review_n,0)+1 WHERE id=?", (r["id"],))
        await tell_author(bot, r["id"], 0, again=r["n"] + 1)


async def loop(bot: Bot):
    while True:
        await asyncio.sleep(300)
        try:
            await nudge_all(bot)
        except Exception:
            log.exception("review loop")
