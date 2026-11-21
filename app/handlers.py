"""Telegram updates: message buffer, reactions, checklist buttons, commands."""
import html
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import ChatMemberUpdatedFilter, Command, CommandObject, IS_MEMBER, IS_NOT_MEMBER
from aiogram.types import (
    CallbackQuery, ChatMemberUpdated, InlineKeyboardButton, InlineKeyboardMarkup, Message,
    MessageReactionUpdated, ReactionTypeEmoji,
)

from . import auto, deadlines, inbox, tasks
from .config import norm_emoji, settings
from .db import db
from .render import fmt_date, miniapp_url, msg_link

log = logging.getLogger(__name__)
router = Router()
GROUPS = F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP})

HELP = (
    "<b>Как создать задачу из сообщений</b>\n\n"
    "1. Поставьте реакцию {draft} на одно или несколько сообщений — они попадут в ваш черновик. "
    "Можно отмечать сообщения разных людей и не подряд.\n"
    "2. Задача создастся сама через {sec} с после последней {draft}. "
    "Чтобы сразу — поставьте {now} на любое сообщение.\n"
    "3. Снимите {draft}, чтобы убрать сообщение из черновика.\n"
    "4. Под задачей кнопка «Открыть»: в приложении можно отмечать пункты и править задачу.\n"
    "5. Или выделите сообщения в чате и перешлите их мне в личку — сделаю из них задачу и спрошу, в какой проект.\n"
    "6. Задачу можно создать и без сообщений: кнопка «Создать задачу» внизу списка в приложении.\n\n"
    "<b>Команды</b>\n"
    "/tasks — незакрытые задачи\n"
    "/done — последние 10 закрытых\n"
    "/project Название — назвать проект\n"
    "/auto on — бот сам находит задачи в переписке и создаёт их (/auto off — выключить)\n"
    "/app — открыть приложение\n"
    "/help — эта справка\n\n"
    "Боту нужны права администратора, иначе Telegram не присылает ему реакции."
)


def help_text() -> str:
    return HELP.format(draft=settings.emoji_draft + "️", now=settings.emoji_now, sec=settings.auto_create_seconds)


def sender_name(m: Message) -> str:
    if m.from_user and not m.sender_chat:
        return m.from_user.full_name
    if m.sender_chat:
        return m.sender_chat.title or "Аноним"
    return "Участник"


# ---------------- bot added / removed ----------------

@router.my_chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER))
async def on_added(ev: ChatMemberUpdated, bot: Bot):
    if ev.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    await db.upsert_project(ev.chat.id, ev.chat.title or "Чат")
    await bot.send_message(ev.chat.id, "Привет! Я превращаю сообщения чата в задачи.\n\n" + help_text(), parse_mode="HTML")


@router.my_chat_member(ChatMemberUpdatedFilter(IS_MEMBER >> IS_NOT_MEMBER))
async def on_removed(ev: ChatMemberUpdated):
    await db.remove_project(ev.chat.id)


# ---------------- commands ----------------

@router.message(Command("help", "start"))
async def cmd_help(m: Message, bot: Bot, command: CommandObject):
    if m.chat.type == ChatType.PRIVATE and (command.args or "").startswith("login_"):
        await ask_site_login(m, command.args[6:])
        return
    if m.chat.type == ChatType.PRIVATE and command.args == "waitlist":
        await join_waitlist(m, bot)
        return
    kb = None
    if m.chat.type == ChatType.PRIVATE:
        kb = await app_keyboard(bot, private=True)
    await m.answer(help_text(), parse_mode="HTML", reply_markup=kb)


# ---------------- the waiting list of the alpha (the landing's button) ----------------

def approval_button(uid, label="Открыть доступ"):
    return InlineKeyboardButton(text=label, callback_data=f"alpha:approve:{uid}")


async def join_waitlist(m: Message, bot: Bot):
    u = m.from_user
    if settings.closed_alpha and await db.alpha_allowed(u.id):
        await m.answer("Доступ к Todogram открыт.", reply_markup=await app_keyboard(bot, True))
        return
    new = await db.join_waitlist(u.id, u.username, u.full_name)
    if new and settings.admin_id and settings.admin_id != u.id:
        who = html.escape(u.full_name) + (f" · @{html.escape(u.username)}" if u.username else "")
        try:
            await bot.send_message(settings.admin_id, f"<b>Заявка в альфу</b>\n{who}", parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=[[approval_button(u.id)]]))
        except Exception:
            log.exception("waitlist notification failed; the application is available in /alpha")
    await m.answer(("Вы в листе ожидания." if new else "Вы уже в листе ожидания.")
                   + " Напишем сюда, когда откроем доступ к альфе.")


@router.message(Command("alpha"), F.chat.type == ChatType.PRIVATE)
async def pending_applications(m: Message):
    if m.from_user.id != settings.admin_id:
        return
    rows = await db.all("SELECT user_id, name, username FROM waitlist WHERE approved IS NULL "
                        "ORDER BY created, user_id LIMIT 10")
    if not rows:
        await m.answer("Новых заявок нет.")
        return
    buttons = [[approval_button(r["user_id"], r["name"] or r["username"] or str(r["user_id"]))] for r in rows]
    await m.answer("Первые заявки в альфу. Нажмите на имя, чтобы открыть человеку доступ. "
                   "Следующие заявки — по /alpha.", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("alpha:"))
async def approve_alpha(cq: CallbackQuery, bot: Bot):
    if cq.from_user.id != settings.admin_id or not cq.message or cq.message.chat.id != settings.admin_id:
        await cq.answer("Только для администратора")
        return
    _, action, raw = cq.data.split(":")
    uid = int(raw)
    if action == "approve":
        cur = await db.x("UPDATE waitlist SET approved=unixepoch() WHERE user_id=? AND approved IS NULL", (uid,))
        if not cur.rowcount:
            await cq.answer("Заявка уже обработана")
            return
    elif action != "resend" or not await db.one("SELECT 1 FROM waitlist WHERE user_id=? AND approved IS NOT NULL", (uid,)):
        await cq.answer("Заявка не найдена")
        return
    await cq.answer("Доступ открыт")
    try:
        await bot.send_message(uid, "Вам открыт доступ к альфе Todogram.", reply_markup=await app_keyboard(bot, True))
    except Exception:
        log.exception("alpha invitation delivery failed")
        retry = InlineKeyboardButton(text="Повторить отправку", callback_data=f"alpha:resend:{uid}")
        await cq.message.answer("Доступ открыт. Telegram не доставил приглашение.",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[retry]]))
        return
    await cq.message.answer("Доступ открыт, приглашение отправлено.")


async def alpha_gate(handler, update, data):
    """Private entry points need personal approval; only an approved user can connect a new group."""
    chat, user = data.get("event_chat"), data.get("event_from_user")
    if chat is None:
        return
    if chat.type == ChatType.PRIVATE:
        if user and await db.alpha_allowed(user.id):
            return await handler(update, data)
        # Website login confirms identity. The API also checks admission before issuing a session.
        if ((update.message and (update.message.text or "").startswith("/start login_"))
                or (update.callback_query and (update.callback_query.data or "").startswith("lg:"))):
            return await handler(update, data)
        if update.message and user:
            await join_waitlist(update.message, data["bot"])
        elif update.callback_query:
            await update.callback_query.answer("Доступ откроется после одобрения заявки", show_alert=True)
        return
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    if update.my_chat_member and user and await db.alpha_allowed(user.id):
        return await handler(update, data)
    previous = update.message and update.message.migrate_from_chat_id
    if await db.project(chat.id) or (previous and await db.project(previous)):
        # The hosted group can still process its regular messages, reactions and team checklist.
        return await handler(update, data)


# ---------------- signing in to the site through the bot ----------------

async def ask_site_login(m: Message, token: str):
    from .web import login_known
    if not await login_known(token):
        await m.answer("Ссылка для входа устарела. Откройте сайт и нажмите «Войти через Telegram» ещё раз.")
        return
    site = (settings.public_url or "сайт задач").replace("https://", "").rstrip("/")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Войти", callback_data=f"lg:{token}:ok"),
        InlineKeyboardButton(text="Отмена", callback_data=f"lg:{token}:no")]])
    await m.answer(f"Вход на {html.escape(site)}\n\nНажмите «Войти», если это вы сейчас открыли сайт. "
                   "Если нет — нажмите «Отмена» и никому не пересылайте эту ссылку.", reply_markup=kb)


@router.callback_query(F.data.startswith("lg:"))
async def on_site_login(cq: CallbackQuery):
    from .web import login_confirmed
    try:
        _, token, act = cq.data.split(":")
    except ValueError:
        await cq.answer()
        return
    if act != "ok":
        if cq.message:
            await cq.message.edit_text("Вход отменён.")
        await cq.answer()
        return
    ok = await login_confirmed(token, cq.from_user.id, cq.from_user.full_name)
    if cq.message:
        await cq.message.edit_text("Готово — вернитесь на сайт, вход выполнен." if ok
                                   else "Ссылка для входа устарела. Нажмите «Войти через Telegram» на сайте ещё раз.")
    await cq.answer("Вход выполнен" if ok else "Ссылка устарела")


@router.message(Command("project"), GROUPS)
async def cmd_project(m: Message, command: CommandObject):
    await db.upsert_project(m.chat.id, m.chat.title or "Чат")
    name = (command.args or "").strip()
    if not name:
        p = await db.project(m.chat.id)
        await m.answer(f"Проект называется «{html.escape(p['display'])}». Переименовать: /project Новое название", parse_mode="HTML")
        return
    await db.set_project_name(m.chat.id, name[:80])
    await m.answer(f"Готово, проект называется «{html.escape(name[:80])}»", parse_mode="HTML")


def _task_line(t: dict) -> str:
    title = html.escape(t["title"])
    link = msg_link(t.get("legacy_chat_id") or t["chat_id"], t["checklist_msg_id"])
    title = f'<a href="{link}">{title}</a>' if link else title
    mark = (" · в работе" if t.get("progress") else "") + (f" · исполнитель: {html.escape(t['assignee_name'])}" if t.get("assignee_name") else "")
    return f"• {title} — {t['done_cnt']}/{t['total']} · {fmt_date(t['created_at'])}{mark}"


@router.message(Command("tasks"), GROUPS)
async def cmd_tasks(m: Message):
    all_ts = [t for t in await db.tasks_of_chat(m.chat.id) if not t["archived"] and not t["closed"]]
    ts = [t for t in all_ts if not t.get("backlog")]
    later = len(all_ts) - len(ts)
    tail = f"\n\nВ бэклоге ещё {later} — они в приложении" if later else ""
    if not ts:
        await m.answer("Незакрытых задач нет" + tail)
        return
    await m.answer("<b>Незакрытые задачи</b>\n\n" + "\n".join(_task_line(t) for t in ts[:30]) + tail,
                   parse_mode="HTML", disable_web_page_preview=True)


@router.message(Command("done"), GROUPS)
async def cmd_done(m: Message):
    ts = [t for t in await db.tasks_of_chat(m.chat.id) if t["closed"] and not t["archived"]][:10]
    if not ts:
        await m.answer("Закрытых задач пока нет")
        return
    await m.answer("<b>Последние закрытые задачи</b>\n\n" + "\n".join(_task_line(t) for t in ts),
                   parse_mode="HTML", disable_web_page_preview=True)


async def app_keyboard(bot: Bot, private: bool) -> InlineKeyboardMarkup:
    # Everywhere the same t.me link to the main mini app (a web_app button would be another kind of launch,
    # and Telegram would open a second copy of the app next to one already open)
    btn = InlineKeyboardButton(text="Открыть приложение", url=miniapp_url((await bot.me()).username, "projects"))
    return InlineKeyboardMarkup(inline_keyboard=[[btn]])


@router.message(Command("app"))
async def cmd_app(m: Message, bot: Bot):
    await m.answer("Проекты и задачи — в приложении:",
                   reply_markup=await app_keyboard(bot, private=m.chat.type == ChatType.PRIVATE))


@router.message(Command("auto"), GROUPS)
async def cmd_auto(m: Message, command: CommandObject, bot: Bot):
    await db.upsert_project(m.chat.id, m.chat.title or "Чат")
    arg = (command.args or "").strip().lower()
    p = await db.project(m.chat.id)
    if arg not in ("on", "off", "вкл", "выкл"):
        state = "включён" if p["auto"] else "выключен"
        await m.answer(f"Автоматический поиск задач {state}.\n/auto on — включить, /auto off — выключить.")
        return
    # sending the whole chat to the AI provider is a privacy decision: admins only
    if m.from_user:
        cm = await bot.get_chat_member(m.chat.id, m.from_user.id)
        if cm.status not in ("creator", "administrator"):
            await m.answer("Включать и выключать автоматический режим могут только администраторы чата.")
            return
    on = arg in ("on", "вкл")
    await db.set_auto(m.chat.id, on)
    if on:
        mins = max(1, settings.auto_pause_seconds // 60)
        await m.answer(
            "Автоматический режим включён.\n\n"
            f"Когда в чате {mins} мин. нет новых сообщений, я читаю новую переписку и сразу создаю задачи "
            "или дописываю пункты в открытые. Если ошибся, задачу можно удалить в приложении, "
            "а под дополнением есть кнопка «Отменить».\n\n"
            "Учтите: для этого вся новая переписка чата отправляется ИИ-модели. "
            f"Ручной режим ({settings.emoji_draft}️ и {settings.emoji_now}) продолжает работать.\n"
            "Выключить: /auto off"
        )
    else:
        await m.answer("Автоматический режим выключен. Задачи — только по реакциям.")


@router.callback_query(F.data.startswith("s:"))
async def on_suggestion(cq: CallbackQuery, bot: Bot):
    try:
        _, sid, act = cq.data.split(":")
        sid = int(sid)
    except ValueError:
        await cq.answer()
        return
    s = await db.suggestion(sid)
    if not s or cq.message is None or s["chat_id"] != cq.message.chat.id:
        await cq.answer("Не найдено")
        return
    if act in ("y", "new"):
        text = await auto.accept(bot, sid, cq.from_user.id, cq.from_user.full_name, as_new=act == "new")
    else:
        text = await auto.reject(bot, sid)
    await cq.answer(text)


@router.callback_query(F.data.startswith("a:"))
async def on_auto_added(cq: CallbackQuery, bot: Bot):
    try:
        _, sid, act = cq.data.split(":")
        sid = int(sid)
    except ValueError:
        await cq.answer()
        return
    s = await db.suggestion(sid)
    if not s or cq.message is None or s["chat_id"] != cq.message.chat.id:
        await cq.answer("Не найдено")
        return
    await cq.answer(await auto.undo_add(bot, sid, cq.from_user.id, cq.from_user.full_name, as_new=act == "new"))


@router.callback_query(F.data.startswith("x:"))
async def on_not_a_task(cq: CallbackQuery, bot: Bot):
    try:
        task_id = int(cq.data.split(":")[1])
    except (ValueError, IndexError):
        await cq.answer()
        return
    t = await db.task(task_id)
    if not t or cq.message is None or t["chat_id"] != cq.message.chat.id:
        await cq.answer("Не найдено")
        return
    await cq.answer(await auto.undo_create(bot, task_id, cq.from_user.id, cq.from_user.full_name))


# ---------------- checklist buttons (only on messages posted before the single «Открыть» button) ----------------

@router.callback_query(F.data.startswith("t:"))
async def on_toggle(cq: CallbackQuery, bot: Bot):
    try:
        _, task_id, idx = cq.data.split(":")
        task_id, idx = int(task_id), int(idx)
    except ValueError:
        await cq.answer()
        return
    task = await db.task(task_id)
    if not task or cq.message is None:
        await cq.answer("Задача не найдена")
        return
    from .web import in_chat
    origin = task.get("legacy_chat_id") or task["chat_id"]
    if cq.message.chat.id != origin or (origin != task["chat_id"] and (
            cq.message.message_id != task["checklist_msg_id"] or not await in_chat(bot, task["chat_id"], cq.from_user.id))):
        await cq.answer("Задача не найдена")
        return
    items = await db.items(task_id)
    if 0 <= idx < len(items):
        it = items[idx]
        await tasks.set_item_done(task_id, it["id"], not it["done"], cq.from_user.full_name, cq.from_user.id)
        await tasks.refresh_checklist(bot, task_id)
    await cq.answer()


# ---------------- reactions ----------------

def _emojis(reactions) -> set[str]:
    return {norm_emoji(r.emoji) for r in reactions or [] if isinstance(r, ReactionTypeEmoji)}


@router.message_reaction()
async def on_reaction(ev: MessageReactionUpdated, bot: Bot):
    if ev.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    user = ev.user
    if user is None:  # anonymous admin: reactions come from the chat, no personal draft
        return
    old, new = _emojis(ev.old_reaction), _emojis(ev.new_reaction)
    added, removed = new - old, old - new
    draft, now = norm_emoji(settings.emoji_draft), norm_emoji(settings.emoji_now)
    tasks.remember_name(ev.chat.id, user.id, user.full_name)

    msg = await db.message(ev.chat.id, ev.message_id)
    if msg is None or msg["task_id"]:
        return  # not buffered (sent before the bot joined) or already used in a task

    if draft in removed:
        await db.draft_remove(ev.chat.id, user.id, ev.message_id)
    if draft in added:
        await db.draft_add(ev.chat.id, user.id, ev.message_id)
    if now in added:
        await tasks.flush_draft(bot, ev.chat.id, user.id, extra=[ev.message_id])


# ---------------- buffer: every group message ----------------

@router.message(GROUPS, F.migrate_to_chat_id | F.migrate_from_chat_id)
async def on_chat_upgrade(m: Message):
    if m.migrate_to_chat_id:
        old_id, new_id = m.chat.id, m.migrate_to_chat_id
    else:
        old_id, new_id = m.migrate_from_chat_id, m.chat.id
    moved = await db.upgrade_chat(old_id, new_id, m.chat.title or "Чат")
    log.info("Telegram group %s upgraded to %s, moved %s tasks", old_id, new_id, moved)


@router.message(GROUPS)
async def buffer_message(m: Message):
    from .web import note_member
    if m.from_user and not m.sender_chat and not m.from_user.is_bot:
        note_member(m.chat.id, m.from_user.id, True)
    for u in m.new_chat_members or []:
        note_member(m.chat.id, u.id, True)
    if m.left_chat_member:
        note_member(m.chat.id, m.left_chat_member.id, False)
    await save(m)


@router.chat_member()
async def on_member_change(ev: ChatMemberUpdated):
    """Someone joined or left (sent to admins bots only): project access follows at once, not after the cache."""
    from .web import note_member
    st = ev.new_chat_member
    note_member(ev.chat.id, st.user.id,
                st.status in ("creator", "administrator", "member") or (st.status == "restricted" and getattr(st, "is_member", False)))


@router.edited_message(GROUPS)
async def buffer_edit(m: Message):
    await db.x("UPDATE messages SET text=? WHERE chat_id=? AND message_id=? AND task_id IS NULL",
               (m.text or m.caption or "", m.chat.id, m.message_id))


async def save(m: Message):
    if m.chat.title:
        await db.upsert_project(m.chat.id, m.chat.title)
    rec = record(m)
    if rec:
        await db.save_message(rec)


def record(m: Message) -> dict | None:
    """A buffer row for a message, None for what is no use in a task (stickers, service messages …)."""
    rec = dict(chat_id=m.chat.id, message_id=m.message_id, user_id=(m.from_user.id if m.from_user else 0),
               user_name=sender_name(m), date=int(m.date.timestamp()), kind="text", text=m.text or m.caption or "",
               file_id=None, file_size=None, file_name=None, thumb_id=None, mime=None, media_group_id=m.media_group_id)
    if m.photo:
        p = m.photo[-1]
        rec.update(kind="photo", file_id=p.file_id, file_size=p.file_size, mime="image/jpeg")
    elif m.document:
        d = m.document
        is_img = (d.mime_type or "").startswith("image/") and d.mime_type not in ("image/svg+xml",)
        rec.update(kind="image_doc" if is_img else "document", file_id=d.file_id, file_size=d.file_size,
                   file_name=d.file_name, mime=d.mime_type)
    elif m.voice:
        rec.update(kind="voice", file_id=m.voice.file_id, file_size=m.voice.file_size, mime=m.voice.mime_type,
                   duration=m.voice.duration)
    elif m.audio:
        rec.update(kind="audio", file_id=m.audio.file_id, file_size=m.audio.file_size, file_name=m.audio.file_name,
                   mime=m.audio.mime_type, duration=m.audio.duration)
    elif m.video:
        v = m.video
        rec.update(kind="video", file_id=v.file_id, file_size=v.file_size, file_name=v.file_name,
                   thumb_id=v.thumbnail.file_id if v.thumbnail else None)
    elif m.video_note:
        v = m.video_note
        rec.update(kind="video_note", file_id=v.file_id, file_size=v.file_size,
                   thumb_id=v.thumbnail.file_id if v.thumbnail else None)
    elif m.animation:
        a = m.animation
        rec.update(kind="animation", file_id=a.file_id, file_size=a.file_size,
                   thumb_id=a.thumbnail.file_id if a.thumbnail else None)
    elif m.sticker or m.poll or m.location or m.contact:
        return None  # nothing useful for a task
    elif not rec["text"]:
        return None  # service messages
    return rec


# ---------------- private chat: forwarded messages become a task ----------------

@router.message(F.chat.type == ChatType.PRIVATE)
async def private_message(m: Message, bot: Bot):
    if (m.text or "").startswith("/") or m.web_app_data or not m.from_user:
        return
    rec = record(m)
    if not rec:
        return
    name, uid, date = inbox.origin(m)
    rec.update(user_name=name, user_id=uid, date=date)
    await db.save_message(rec)
    inbox.remember(m.from_user.id, m.from_user.full_name)
    inbox.add(bot, m.from_user.id, m.message_id)


@router.callback_query(F.data.startswith("rv:"))
async def on_review_button(cq: CallbackQuery, bot: Bot):
    """Under «Нужно проверить задачу» in the private chat: «Принять» closes it, «Вернуть в работу» sends it back."""
    from .web import STATUS_FIELDS, _on_review, can_see_task, log_task_change
    try:
        _, task_id, act = cq.data.split(":")
        task_id = int(task_id)
    except ValueError:
        await cq.answer()
        return
    t = await db.task(task_id)
    if not t or not t["title"] or not await can_see_task(bot, t, cq.from_user.id):
        await cq.answer("Задача не найдена")
        return
    if not _on_review(t):
        await cq.answer("Задача уже не на проверке")
        if cq.message:
            try:
                await cq.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
        return
    to = "closed" if act == "ok" else "progress"
    await db.update_task(task_id, **STATUS_FIELDS[to])
    await log_task_change(t, await db.task(task_id), cq.from_user.full_name, cq.from_user.id)
    await tasks.refresh_checklist(bot, task_id)
    if cq.message:
        try:
            await cq.message.edit_text(f"Задача «{html.escape(t['title'])}» "
                                       + ("принята и закрыта" if to == "closed" else "возвращена в работу"), parse_mode="HTML")
        except Exception:
            pass
    await cq.answer("Принято" if to == "closed" else "Вернул в работу")


@router.callback_query(F.data.startswith("rm:"))
async def on_reminder_button(cq: CallbackQuery, bot: Bot):
    """Under a reminder in the private chat: «Через час» and «Сделано»."""
    from .web import can_see_task
    try:
        _, task_id, act = cq.data.split(":")
        task_id = int(task_id)
    except ValueError:
        await cq.answer()
        return
    t = await db.task(task_id)
    if not t or not t["title"] or not await can_see_task(bot, t, cq.from_user.id):
        await cq.answer("Задача не найдена")
        return
    if act == "snooze":
        await deadlines.snooze(task_id, cq.from_user.id)
        await cq.answer("Напомню через час")
        return
    if act == "done":
        await tasks.complete_all(task_id, cq.from_user.full_name, cq.from_user.id)
        await tasks.refresh_checklist(bot, task_id)
        if cq.message:
            try:
                await cq.message.edit_text(f"Задача «{html.escape(t['title'])}» выполнена", parse_mode="HTML")
            except Exception:
                pass
        await cq.answer("Отметил все пункты")
        return
    await cq.answer()


@router.callback_query(F.data.startswith("fw:"))
async def on_forward_project(cq: CallbackQuery, bot: Bot):
    if cq.message is None or cq.message.chat.id != cq.from_user.id:
        await cq.answer()
        return
    await cq.answer(await inbox.on_choice(bot, cq.from_user.id, cq.message.message_id, cq.data))


@router.callback_query(F.data.startswith("fa:"))
async def on_forward_assignee(cq: CallbackQuery, bot: Bot):
    if cq.message is None or cq.message.chat.id != cq.from_user.id:
        await cq.answer()
        return
    await cq.answer(await inbox.on_assignee(bot, cq.from_user.id, cq.message.message_id, cq.data))
