"""Checklist message text and buttons."""
import html
import json
import re
import time
from urllib.parse import urlsplit

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .config import settings

MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def fmt_date(ts: int) -> str:
    t = time.localtime(ts)
    return f"{t.tm_mday} {MONTHS[t.tm_mon - 1]}, {t.tm_hour:02d}:{t.tm_min:02d}"


def msg_link(chat_id: int, message_id: int | None) -> str | None:
    """t.me link to a message. Works in supergroups only (ids start with -100)."""
    s = str(chat_id)
    return f"https://t.me/c/{s[4:]}/{message_id}" if message_id and s.startswith("-100") else None


def source_chat(task: dict, source: dict | None = None) -> int:
    return (source or {}).get("source_chat_id") or task.get("legacy_chat_id") or task["chat_id"]


def item_src(item: dict) -> list[int]:
    v = item.get("src")
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            v = []
    return [int(x) for x in v or []]


URL_RE = re.compile(r"https?://[^\s<>]+")
TRAIL = ").,;:!?»\"'"


def short_url(url: str) -> str:
    """https://site.ru/a/b/very-long-slug/ -> site.ru/…/very-long-slug"""
    u = urlsplit(url)
    host = u.netloc.removeprefix("www.")
    parts = [p for p in u.path.split("/") if p]
    if not parts:
        return host
    tail = parts[-1] if len(parts[-1]) <= 24 else parts[-1][:22] + "…"
    return f"{host}/…/{tail}" if len(parts) > 1 else f"{host}/{tail}"


def linkify(text: str) -> str:
    """Escape for Telegram HTML and turn URLs into short <a> links."""
    out, pos = [], 0
    for m in URL_RE.finditer(text):
        url = m.group(0).rstrip(TRAIL)
        out.append(html.escape(text[pos:m.start()]))
        out.append(f'<a href="{html.escape(url, quote=True)}">{html.escape(short_url(url))}</a>')
        pos = m.start() + len(url)
    out.append(html.escape(text[pos:]))
    return "".join(out)


def bot_link_name(bot_username: str) -> str:
    """The bot's name for t.me links: BOT_LINK_USERNAME (e.g. a collectible @todogram_app) or its own username."""
    return settings.bot_link_username or bot_username


def miniapp_url(bot_username: str, param: str) -> str:
    return settings.miniapp_link.format(bot=bot_link_name(bot_username), param=param)


def hours_text(h: float) -> str:
    """2 → «2 ч», 1.5 → «1,5 ч»."""
    s = f"{h:g}".replace(".", ",")
    return f"{s} ч"


def checklist_text(task: dict, items: list[dict], project_name: str) -> str:
    """The checklist message in the chat: title, items. Author, date and project are in the app;
    a status line appears only when there is something to say (in work, backlog, closed, archive)."""
    e = html.escape
    lines = [f"<b>{e(task['title'])}</b>"]
    st = []
    if task.get("backlog") and not task.get("archived"):
        st.append("в бэклоге")
    elif task.get("progress") and task.get("review_at") and not task.get("closed") and not task.get("archived"):
        st.append("на проверке")
    elif task.get("progress") and not task.get("archived"):
        st.append("в работе")
    if task.get("closed") and not task.get("archived"):
        st.append("закрыта" + (f", {hours_text(task['hours'])}" if task.get("hours") else ""))
    elif task.get("hours") and not task.get("archived"):
        st.append(f"потрачено {hours_text(task['hours'])}")
    if task.get("archived"):
        st.append("в архиве")
    if task.get("due_at"):
        from .deadlines import text as due_text
        st.insert(0, f"срок до {due_text(task['due_at'], bool(task.get('due_allday')))}")
    if task.get("assignee_name") and not task.get("archived"):
        st.append(f"исполнитель: {e(task['assignee_name'])}")
    if st:
        lines.append(" · ".join(st))
    if task.get("closed") and (task.get("result_url") or task.get("result_note")):
        res = " · ".join(x for x in (e(task.get("result_note") or ""), linkify(task["result_url"]) if task.get("result_url") else "") if x)
        lines.append(f"Результат: {res}")
    lines.append("")
    for i, it in enumerate(items, 1):
        mark = "☑︎" if it["done"] else "☐"   # text check boxes, no colour emoji
        shot = f" (скрин {it['shot']})" if it.get("shot") else ""
        src = item_src(it)
        link = msg_link(source_chat(task, it), src[0]) if src else None
        num = f'<a href="{link}">{i}.</a>' if link else f"{i}."   # tap the number → jump to the source message
        lines.append(f"{mark} {num} {linkify(it['text'])}{shot}")
    done = sum(1 for it in items if it["done"])
    if not items:
        lines.append("Пунктов пока нет — добавьте их в приложении")
    elif done == len(items):
        lines += ["", "Все пункты выполнены"]
    return "\n".join(lines)


def checklist_kb(task: dict, bot_username: str) -> InlineKeyboardMarkup:
    # web_app buttons are not allowed in groups, so a t.me link opens the mini app on this task
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Открыть", url=miniapp_url(bot_username, f"task_{task['id']}"))]])
