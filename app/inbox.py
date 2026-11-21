"""Messages forwarded to the bot in private. Select messages in a work chat → «Переслать» → the bot:
it gathers one burst of forwards into a task and asks which project it belongs to (Telegram does not
tell bots where a forwarded group message came from). After the project — who the assignee is
(the same people the mini app offers), or «Без исполнителя». A personal task («Без проекта»)
is simply yours: no assignee step."""
import asyncio
import html
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from . import tasks, web
from .bg import spawn
from .db import db

log = logging.getLogger(__name__)

QUIET = 3.0     # a forward of many messages arrives as a quick burst; it is one task once the bot hears nothing
_batches: dict[int, list[int]] = {}           # user id → [first, last] message id of the current burst
_timers: dict[int, asyncio.Task] = {}


def origin(m) -> tuple[str, int, int]:
    """(name, user id, unix date) of who originally wrote a forwarded message."""
    o = m.forward_origin
    date = int(o.date.timestamp()) if o and o.date else int(m.date.timestamp())
    if o is None:
        return (m.from_user.full_name if m.from_user else "Участник"), (m.from_user.id if m.from_user else 0), date
    if o.type == "user":
        return o.sender_user.full_name, o.sender_user.id, date
    if o.type == "hidden_user":
        return o.sender_user_name or "Участник", 0, date
    if o.type == "chat":
        return o.sender_chat.title or "Аноним", 0, date
    return getattr(o.chat, "title", None) or "Канал", 0, date


def add(bot: Bot, user_id: int, message_id: int):
    """One more message of the burst: the task waits until the forwards stop."""
    b = _batches.setdefault(user_id, [message_id, message_id])
    b[0], b[1] = min(b[0], message_id), max(b[1], message_id)
    if t := _timers.get(user_id):
        t.cancel()
    _timers[user_id] = spawn(_later(bot, user_id))


async def _later(bot: Bot, user_id: int):
    try:
        await asyncio.sleep(QUIET)
    except asyncio.CancelledError:
        return
    _timers.pop(user_id, None)
    first, last = _batches.pop(user_id)
    try:
        await flush(bot, user_id, first, last)
    except Exception:
        log.exception("forwarded batch of %s failed", user_id)


async def my_projects(bot: Bot, user_id: int) -> list[dict]:
    return [p for p in await db.projects() if await web.is_member(bot, p["chat_id"], user_id)]


async def _ids(user_id: int, first: int, last: int) -> list[int]:
    rows = await db.all("SELECT message_id FROM messages WHERE chat_id=? AND message_id BETWEEN ? AND ? "
                        "AND task_id IS NULL ORDER BY message_id", (user_id, first, last))
    return [r["message_id"] for r in rows]


async def flush(bot: Bot, user_id: int, first: int, last: int):
    ids = await _ids(user_id, first, last)
    if not ids:
        return
    projects = await my_projects(bot, user_id)
    n = len(ids)
    if not projects:
        await bot.send_message(user_id, "Не нашёл ваших проектов. Добавьте меня в рабочий чат (администратором) "
                                        "или создайте проект в мини-приложении — и пересылайте сюда сообщения.")
        return
    if len(projects) == 1:
        await ask_assignee(bot, user_id, projects[0]["chat_id"], first, last, n=n)
        return
    rows = [[InlineKeyboardButton(text=p["display"][:60], callback_data=f"fw:{p['chat_id']}:{first}:{last}")]
            for p in projects[:20]]
    rows.append([InlineKeyboardButton(text="Отмена", callback_data=f"fw:0:{first}:{last}")])
    await bot.send_message(user_id, f"Сообщений: {n}. В какой проект поставить задачу?",
                           reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


def _assignee_kb(chat_id: int, first: int, last: int, people: list[dict], user_id: int) -> InlineKeyboardMarkup:
    """Same people the mini app's assignee picker shows, plus «Без исполнителя»."""
    me = [p for p in people if p["id"] == user_id]
    rest = [p for p in people if p["id"] != user_id]
    rows = []
    for p in (me + rest)[:20]:
        label = p["name"] or str(p["id"])
        if p["id"] == user_id:
            label = f"{label} (вы)"
        elif p.get("outside") and p.get("via"):
            label = f"{label} · {p['via']}"
        rows.append([InlineKeyboardButton(text=label[:60],
                                          callback_data=f"fa:{chat_id}:{first}:{last}:{p['id']}")])
    rows.append([InlineKeyboardButton(text="Без исполнителя",
                                      callback_data=f"fa:{chat_id}:{first}:{last}:0")])
    rows.append([InlineKeyboardButton(text="Отмена", callback_data=f"fw:0:{first}:{last}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def ask_assignee(bot: Bot, user_id: int, chat_id: int, first: int, last: int,
                       *, msg_id: int | None = None, n: int | None = None) -> str:
    """After the project is known: pick an assignee, or create at once for «Без проекта»."""
    ids = await _ids(user_id, first, last)
    if not ids:
        if msg_id:
            await _edit(bot, user_id, msg_id, "Эти сообщения уже разобраны")
        return "Уже разобрано"
    if web.is_personal(chat_id):
        return await _start_create(bot, user_id, chat_id, ids, msg_id, assignee_id=user_id)
    people = await web.project_people(bot, chat_id) + await web.team_people(bot, chat_id, user_id)
    proj = await db.project(chat_id)
    pname = html.escape(proj["display"] if proj else "")
    text = f"Кого назначить исполнителем в «{pname}»?"
    if n is not None:
        text = f"Сообщений: {n}. {text}"
    kb = _assignee_kb(chat_id, first, last, people, user_id)
    if msg_id:
        await _edit(bot, user_id, msg_id, text, kb)
    else:
        await bot.send_message(user_id, text, reply_markup=kb, parse_mode="HTML")
    return "Выберите исполнителя"


async def create(bot: Bot, user_id: int, chat_id: int, ids: list[int], status_msg_id: int,
                 assignee_id: int | None = None) -> int | None:
    row = await db.one("SELECT user_name FROM messages WHERE chat_id=? ORDER BY date DESC LIMIT 1", (user_id,))
    name = _names.get(user_id) or (row["user_name"] if row else "Участник")
    return await tasks.create_forwarded(bot, chat_id, user_id, name, user_id, ids, user_id, status_msg_id,
                                        assignee_id=assignee_id)


_names: dict[int, str] = {}   # the forwarding person's own name (messages carry the original authors)


def remember(user_id: int, name: str):
    _names[user_id] = name


async def _start_create(bot: Bot, user_id: int, chat_id: int, ids: list[int],
                        msg_id: int | None, assignee_id: int | None) -> str:
    proj = await db.project(chat_id)
    label = f"Разбираю… ({html.escape(proj['display'] if proj else '')})"
    if msg_id:
        await _edit(bot, user_id, msg_id, label)
        spawn(_safe(create(bot, user_id, chat_id, ids, msg_id, assignee_id=assignee_id)))
    else:
        msg = await bot.send_message(user_id, label)
        spawn(_safe(create(bot, user_id, chat_id, ids, msg.message_id, assignee_id=assignee_id)))
    return "Создаю задачу"


async def on_choice(bot: Bot, user_id: int, msg_id: int, data: str) -> str:
    """Button under «В какой проект?»: fw:<chat id>:<first>:<last>, chat id 0 = cancel."""
    try:
        _, chat_id, first, last = data.split(":")
        chat_id, first, last = int(chat_id), int(first), int(last)
    except ValueError:
        return ""
    if chat_id == 0:
        await _edit(bot, user_id, msg_id, "Отменено")
        return "Отменено"
    if not await web.is_member(bot, chat_id, user_id):
        return "Вы не участник этого проекта"
    return await ask_assignee(bot, user_id, chat_id, first, last, msg_id=msg_id)


async def on_assignee(bot: Bot, user_id: int, msg_id: int, data: str) -> str:
    """Button under «Кого назначить?»: fa:<chat id>:<first>:<last>:<user id>, user id 0 = nobody."""
    try:
        _, chat_id, first, last, assignee_id = data.split(":")
        chat_id, first, last, assignee_id = int(chat_id), int(first), int(last), int(assignee_id)
    except ValueError:
        return ""
    if not await web.is_member(bot, chat_id, user_id):
        return "Вы не участник этого проекта"
    ids = await _ids(user_id, first, last)
    if not ids:
        await _edit(bot, user_id, msg_id, "Эти сообщения уже разобраны")
        return "Уже разобрано"
    aid = None if assignee_id == 0 else assignee_id
    if aid and aid != user_id:
        people = await web.project_people(bot, chat_id) + await web.team_people(bot, chat_id, user_id)
        if aid not in {p["id"] for p in people}:
            return "Нет такого участника"
    return await _start_create(bot, user_id, chat_id, ids, msg_id, assignee_id=aid)


async def _safe(coro):
    try:
        await coro
    except Exception:
        log.exception("forwarded task crashed")


async def _edit(bot: Bot, chat_id: int, msg_id: int, text: str, kb=None):
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text, parse_mode="HTML",
                                    reply_markup=kb)
    except TelegramBadRequest:
        pass
