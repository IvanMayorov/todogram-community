"""MCP server: lets an AI agent read checklist tasks and tick them off.

Runs over stdio; talks to the bot's agent API with AGENT_TOKEN.
    CHECKLIST_URL   bot address (default http://127.0.0.1:8787; on the server — your HTTPS domain)
    AGENT_TOKEN     same value as in the bot's .env (read from ../.env if not set)

Every change goes through the bot, so the checklist message in the Telegram chat updates too.
"""
import os
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP, Image

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
BASE = os.getenv("CHECKLIST_URL", "http://127.0.0.1:8787").rstrip("/")
TOKEN = os.getenv("AGENT_TOKEN", "")

mcp = FastMCP("checklists", instructions=(
    "Задачи из рабочих Telegram-чатов с заказчиками (сайты, 1С, каталог). "
    "Обычный порядок: list_projects → list_tasks → get_task → take_task (статус «В работе»), затем сделать работу и отметить пункты "
    "через mark_item_done (или complete_task, если сделано всё). Отмечай только то, что действительно сделано и проверено. "
    "Изменения сразу видны заказчику в чате."
))


async def _call(method: str, path: str, json: dict | None = None):
    async with httpx.AsyncClient(base_url=BASE, timeout=30, headers={"Authorization": f"Bearer {TOKEN}"}) as c:
        r = await c.request(method, path, json=json)
        if r.status_code == 401:
            raise RuntimeError("Неверный AGENT_TOKEN: проверьте .env бота и настройки MCP")
        if r.status_code in (400, 403, 404):
            raise RuntimeError(r.text or "не найдено")
        r.raise_for_status()
        return r.json()


def _task_text(t: dict) -> str:
    lines = [f"Задача #{t['id']}: {t['title']}",
             f"Проект: {t.get('project', '')} · автор: {t['author']} · {t['date']} · статус: {t.get('status', '')}"]
    if t.get("archived"):
        lines.append("В архиве")
    if t.get("due"):
        lines.append(f"Срок: {t['due']['text']}")
    if t.get("assignee"):
        lines.append(f"Исполнитель: {t['assignee']['name']} (user_id={t['assignee']['id']})")
    if t.get("link"):
        lines.append(f"Чеклист в чате: {t['link']}")
    if t.get("result_url") or t.get("hours") is not None or t.get("result_note"):
        parts = []
        if t.get("result_url"):
            parts.append(t["result_url"])
        if t.get("hours") is not None:
            parts.append(f"{t['hours']} ч")
        if t.get("result_note"):
            parts.append(t["result_note"])
        lines.append("Результат: " + " · ".join(parts))
    if t.get("descr"):
        lines.append("")
        lines.append("Описание:")
        lines.append(t["descr"])
    lines.append("")
    lines.append("Пункты (item_id — для mark_item_done / rename_item / delete_item):")
    for n, i in enumerate(t["items"], 1):
        mark = "[x]" if i["d"] else "[ ]"
        extra = []
        if i.get("s"):
            extra.append(f"скриншот {i['s']}")
        if i.get("m"):
            extra.append("сообщение: " + i["m"][0])
        lines.append(f"{n}. {mark} {i['t']}  (item_id={i['id']}{'; ' + '; '.join(extra) if extra else ''})")
    qs = [c for c in t.get("comments") or [] if c.get("question") and not c.get("resolved")]
    if qs:
        lines.append("")
        lines.append("Открытые вопросы (важнее комментариев — сначала ответь на них; comment_id — для resolve_question):")
        for c in qs:
            lines.append(f"- [{c['id']}] {c['author']}, {c['date']}: {c['text']}"
                         + (f" (пункт item_id={c['item_id']})" if c.get("item_id") else "")
                         + (f" (вложений: {len(c['files'])})" if c.get("files") else ""))
    cs = [c for c in t.get("comments") or [] if not c.get("question")]
    if cs:
        lines.append("")
        lines.append("Комментарии (comment_id — для edit_comment / delete_comment):")
        for c in cs[-20:]:
            lines.append(f"- [{c['id']}] {c['author']}, {c['date']}: {c['text']}"
                         + (f" (пункт item_id={c['item_id']})" if c.get("item_id") else "")
                         + (f" (вложений: {len(c['files'])})" if c.get("files") else ""))
    if t.get("links"):
        lines.append("")
        lines.append("Связанные задачи (та же тема в другом месте — посмотри их через get_task):")
        for x in t["links"]:
            lines.append(f"- #{x['id']} {x['title']} — {x['project']} [{x['status']}]")
    if t.get("source_messages"):
        lines.append("")
        lines.append("Исходные сообщения из чата:")
        for m in t["source_messages"]:
            body = m["text"] or ""
            if m.get("transcript"):
                body = (body + " " if body else "") + f"[голосовое] {m['transcript']}"
            lines.append(f"- [{m['id']}] {m['author']}, {m['date']} ({m['kind']}): {body or '—'}")
    if t.get("transcripts"):
        lines.append("")
        lines.append("Расшифровки голосовых:")
        for x in t["transcripts"]:
            lines.append(f"- {x['author']}: {x['text']}")
    if t.get("files"):
        lines.append("")
        lines.append("Файлы из чата:")
        for f in t["files"]:
            kb = f"{round(f['size'] / 1024)} КБ" if f.get("size") else ""
            lines.append(f"- {f['name']} {kb} ({f.get('author') or ''}){' — ' + f['link'] if f.get('link') else ''}")
    if t.get("notes"):
        lines.append("")
        lines.append("Заметки: " + "; ".join(t["notes"]))
    if t.get("history"):
        lines.append("")
        lines.append("История (новые сверху):")
        for e in t["history"][:30]:
            when = datetime.fromtimestamp(e["ts"]).strftime("%d.%m %H:%M")
            lines.append(f"- {when} {e['actor']}: {_event_text(e)}")
    return "\n".join(lines)


STATUS_RU = {"open": "открыта", "progress": "в работе", "review": "на проверке", "backlog": "в бэклоге", "closed": "закрыта"}


def _event_text(e: dict) -> str:
    k, txt, x = e["kind"], e.get("text") or "", e.get("extra") or {}
    return {
        "created": f"задача создана «{txt}»",
        "done": f"выполнено «{txt}»",
        "undone": f"снята отметка «{txt}»",
        "done_all": f"отмечены все оставшиеся пункты ({x.get('count', '')})",
        "closed": "всё выполнено — задача закрыта"
                  + (f", {x['hours']} ч" if x.get("hours") is not None else "")
                  + (f", результат {x['result_url']}" if x.get("result_url") else ""),
        "reopened": "задача снова открыта",
        "report": "отчёт"
                  + (f": {x['hours']} ч" if x.get("hours") is not None else "")
                  + (f" · {x['result_url']}" if x.get("result_url") else "")
                  + (f" · «{x['result_note']}»" if x.get("result_note") else ""),
        "time": f"потрачено: {x.get('hours')}" if x.get("hours") is not None else "время убрано",
        "status": f"статус: {STATUS_RU.get(x.get('to'), x.get('to'))}",
        "archived": "перенесена в архив", "unarchived": "возвращена из архива",
        "renamed": f"новое название «{txt}»",
        "descr": f"описание: «{txt}»" if txt else "описание убрано",
        "moved": (f"перенесена из «{x['from_project']}» в «{txt or x.get('project', '')}»"
                  if x.get("from_project") else f"перенесена в «{txt or x.get('project', '')}»"),
        "item_added": f"добавлен пункт «{txt}»", "item_edited": f"изменён пункт «{txt}»",
        "item_deleted": f"удалён пункт «{txt}»",
        "items_added": f"добавлены пункты из чата ({x.get('count', '')})",
        "items_removed": f"отменено добавление пунктов из чата ({x.get('count', '')})",
        "shot_added": "добавлен скриншот",
        "due": f"срок: {x['text']}" if x.get("text") else "срок убран",
        "assigned": f"исполнитель: {x.get('name') or txt}" if (x.get("name") or txt) else "исполнитель снят",
        "commented": f"комментарий: «{txt}»",
        "asked": f"вопрос: «{txt}»",
        "answered": f"вопрос решён: «{txt}»",
        "reasked": f"вопрос снова открыт: «{txt}»",
        "linked": f"связана с задачей #{x.get('id')} «{txt}»", "unlinked": f"убрана связь с задачей #{x.get('id')} «{txt}»",
    }.get(k, k)


def _snip(parts) -> str:
    """parts from search: [[text, hit], …] → plain text with hits marked."""
    if not parts:
        return ""
    return "".join(f"«{t}»" if hit else t for t, hit in parts)


@mcp.tool()
async def list_projects() -> str:
    """Проекты (рабочие чаты) с количеством открытых задач."""
    ps = await _call("GET", "/agent/projects")
    if not ps:
        return "Проектов нет: бот не добавлен ни в один чат."
    return "\n".join(f"{p['name']} — project_id={p['id']}, открытых задач: {p['open_tasks']} из {p['tasks_total']}" for p in ps)


@mcp.tool()
async def list_tasks(project_id: int, status: str = "open") -> str:
    """Задачи проекта. status: open (новые, по умолчанию), progress (в работе), backlog (на потом), closed, archive или all."""
    ts = await _call("GET", f"/agent/projects/{project_id}/tasks?status={status}")
    if not ts:
        return f"Задач со статусом «{status}» нет."
    return "\n".join(f"#{t['id']} {t['title']} — {t['done']}/{t['total']}, {t['author']}, {t['date']} [{t['status']}]"
                     + (f" · срок {t['due']}" if t.get("due") else "")
                     + (f" · исполнитель {t['assignee']}" if t.get("assignee") else "") for t in ts)


@mcp.tool()
async def get_task(task_id: int, include_screenshots: bool = True) -> list:
    """Задача целиком: пункты с item_id, описание, комментарии и вопросы с comment_id, история, исходные сообщения, скриншоты."""
    t = await _call("GET", f"/agent/tasks/{task_id}")
    out: list = [_task_text(t)]
    if include_screenshots:
        async with httpx.AsyncClient(base_url=BASE, timeout=30) as c:
            for m in t.get("media", []):
                if m["kind"] != "photo":
                    continue
                r = await c.get(m["url"])
                if r.status_code == 200:
                    out.append(f"Скриншот {m['idx']}:")
                    out.append(Image(data=r.content, format="jpeg"))
    return out


@mcp.tool()
async def create_task(project_id: int, title: str, items: list[str] | None = None, status: str = "open",
                      assignee_id: int | None = None, descr: str | None = None) -> str:
    """Создать задачу в проекте: название и пункты чеклиста. status: open (новая, по умолчанию), progress или backlog.
    assignee_id — исполнитель (user_id из list_people), ему придёт сообщение в личку.
    descr — описание задачи (свободный текст со ссылками).
    Чеклист сразу публикуется в чате и его видит заказчик."""
    body = {"title": title, "items": items or [], "status": status,
            **({"assignee_id": assignee_id} if assignee_id else {}),
            **({"descr": descr} if descr is not None else {})}
    t = await _call("POST", f"/agent/projects/{project_id}/tasks", body)
    msg = f"Создана задача #{t['id']} «{t['title']}»: пунктов {len(t['items'])}, статус {t['status']}."
    if t.get("descr"):
        msg += " Описание задано."
    if t.get("assignee"):
        msg += f" Исполнитель: {t['assignee']['name']}."
    return msg + (" Внимание: исполнитель ни разу не открывал бота — сообщения и напоминания ему не дойдут." if t.get("warning") else "")


@mcp.tool()
async def edit_task(task_id: int, title: str | None = None, descr: str | None = None,
                    archived: bool | None = None, result_url: str | None = None,
                    hours: float | None = None, result_note: str | None = None) -> str:
    """Изменить задачу: название, описание (пустая строка — убрать), архив, поля отчёта при закрытии
    (result_url, hours, result_note; пустая строка у ссылки/заметки — убрать). Непереданные поля не трогаются."""
    body = {}
    if title is not None:
        body["title"] = title
    if descr is not None:
        body["descr"] = descr
    if archived is not None:
        body["archived"] = archived
    if result_url is not None:
        body["result_url"] = result_url
    if hours is not None:
        body["hours"] = hours
    if result_note is not None:
        body["result_note"] = result_note
    if not body:
        return "Нечего менять: передайте хотя бы одно поле."
    t = await _call("PATCH", f"/agent/tasks/{task_id}", body)
    bits = []
    if title is not None:
        bits.append(f"название «{t['title']}»")
    if descr is not None:
        bits.append("описание обновлено" if t.get("descr") else "описание убрано")
    if archived is not None:
        bits.append("в архиве" if t.get("archived") else "из архива")
    if any(x is not None for x in (result_url, hours, result_note)):
        bits.append("отчёт обновлён")
    return f"Готово. Задача #{t['id']}: " + ", ".join(bits) + "."


@mcp.tool()
async def delete_task(task_id: int) -> str:
    """Удалить задачу целиком (как в приложении). Чеклист в чате убирается. Без отмены — действуйте осторожно."""
    j = await _call("DELETE", f"/agent/tasks/{task_id}")
    return f"Задача #{j['deleted']} удалена."


@mcp.tool()
async def move_task(task_id: int, project_id: int | None, user_id: int | None = None) -> str:
    """Перенести задачу в другой проект (project_id) или в «Без проекта» (project_id=None).
    При project_id=None задача станет личной: user_id — чьей (по умолчанию автор задачи).
    Старый чеклист в чате убирается или помечается «перенесена»; в новом чате публикуется новый."""
    body = {"project_id": project_id, **({"user_id": user_id} if user_id is not None else {})}
    t = await _call("POST", f"/agent/tasks/{task_id}/move", body)
    return f"Задача #{t['id']} «{t['title']}» теперь в проекте «{t.get('project', '')}»."


@mcp.tool()
async def add_items(task_id: int, items: list[str]) -> str:
    """Дописать пункты в конец задачи (например, срок или условие, которое пропустил ИИ). Чеклист в чате обновится."""
    t = await _call("POST", f"/agent/tasks/{task_id}/items", {"items": items})
    return f"Готово. В задаче #{t['id']} «{t['title']}» теперь пунктов: {len(t['items'])}."


@mcp.tool()
async def rename_item(item_id: int, text: str) -> str:
    """Переименовать пункт чеклиста. Чеклист в чате обновится."""
    t = await _call("PATCH", f"/agent/items/{item_id}", {"text": text})
    it = next((i for i in t["items"] if i["id"] == item_id), None)
    return f"Готово. Пункт item_id={item_id} в задаче #{t['id']}: «{it['t'] if it else text}»."


@mcp.tool()
async def delete_item(item_id: int) -> str:
    """Удалить пункт чеклиста. Чеклист в чате обновится."""
    t = await _call("DELETE", f"/agent/items/{item_id}")
    return f"Готово. В задаче #{t['id']} «{t['title']}» осталось пунктов: {len(t['items'])}."


@mcp.tool()
async def reorder_items(task_id: int, item_ids: list[int]) -> str:
    """Порядок пунктов чеклиста: item_ids сверху вниз (все id этой задачи). Чеклист в чате обновится."""
    t = await _call("PUT", f"/agent/tasks/{task_id}/order", {"item_ids": item_ids})
    order = ", ".join(str(i["id"]) for i in t["items"])
    return f"Готово. Задача #{t['id']}: порядок пунктов {order}."


@mcp.tool()
async def set_due(task_id: int, date: str | None, time: str | None = None) -> str:
    """Срок задачи: date «2026-11-30», time «18:00» (необязательно). date=None — убрать срок.
    Срок виден в чеклисте в чате; кто поставил себе напоминание, получит его в личку."""
    t = await _call("POST", f"/agent/tasks/{task_id}/due", {"date": date, "time": time})
    return f"Готово. Задача #{t['id']}: " + (f"срок {t['due']['text']}." if t.get("due") else "срок убран.")


@mcp.tool()
async def set_reminders(task_id: int, kinds: list[str], user_id: int | None = None) -> str:
    """Напоминания о сроке в личку: kinds из «day» (за день; для срока без времени — накануне в 10:00),
    «hour» (за час), «due» (в момент срока; без времени — в день срока в 10:00). [] — убрать.
    user_id — кому (по умолчанию автору задачи). Исполнитель задачи получает все напоминания, поставленные на задачу."""
    body = {"kinds": kinds, **({"user_id": user_id} if user_id else {})}
    t = await _call("POST", f"/agent/tasks/{task_id}/reminders", body)
    r = t["reminders"]
    return f"Готово. Задача #{t['id']}: напоминания {', '.join(r['kinds']) or 'нет'} для пользователя {r['user_id']}."


@mcp.tool()
async def list_people(project_id: int) -> str:
    """Кого можно назначить исполнителем задач проекта: user_id и имя. Люди с пометкой «из другого проекта»
    в этом проекте не состоят — при назначении увидят только эту задачу (и только пока они исполнители)."""
    ps = await _call("GET", f"/agent/projects/{project_id}/people")
    return "\n".join(f"{p['name']} (user_id={p['id']})"
                     + (f" — из другого проекта «{p['outside']}», увидит только эту задачу" if p.get("outside") else "")
                     for p in ps) or "Пока никого: бот ещё не видел участников проекта."


@mcp.tool()
async def assign_task(task_id: int, user_id: int | None) -> str:
    """Назначить исполнителя задачи (user_id из list_people; None — снять). Исполнителю придёт сообщение в личку,
    и напоминания о сроке, поставленные на задачу, будут приходить ему. Исполнитель виден в чеклисте в чате."""
    t = await _call("POST", f"/agent/tasks/{task_id}/assignee", {"user_id": user_id})
    a = t.get("assignee")
    msg = f"Задача #{t['id']} «{t['title']}»: " + (f"исполнитель {a['name']}." if a else "исполнитель снят.")
    return msg + (" Внимание: этот человек ни разу не открывал бота — сообщения и напоминания ему не дойдут."
                  if t.get("warning") else "")


@mcp.tool()
async def mark_item_done(item_id: int, done: bool = True) -> str:
    """Отметить пункт выполненным (done=false — снять отметку). Чеклист в чате обновится."""
    t = await _call("POST", f"/agent/items/{item_id}/done", {"done": done})
    d = sum(1 for i in t["items"] if i["d"])
    return f"Готово. Задача #{t['id']} «{t['title']}»: выполнено {d} из {len(t['items'])}."


@mcp.tool()
async def complete_task(task_id: int, result_url: str | None = None, hours: float | None = None,
                        result_note: str | None = None) -> str:
    """Отметить все пункты задачи выполненными — задача закроется. Чеклист в чате обновится.
    По желанию сразу укажите отчёт: result_url (ссылка на результат), hours (часы), result_note (текст)."""
    body = {k: v for k, v in (("result_url", result_url), ("hours", hours), ("result_note", result_note)) if v is not None}
    t = await _call("POST", f"/agent/tasks/{task_id}/complete", body or None)
    msg = f"Задача #{t['id']} «{t['title']}» закрыта: все {len(t['items'])} пунктов выполнены."
    if t.get("result_url") or t.get("hours") is not None or t.get("result_note"):
        msg += " Отчёт сохранён."
    return msg


@mcp.tool()
async def take_task(task_id: int) -> str:
    """Взять задачу в работу: статус «В работе» виден в приложении и в чеклисте в чате."""
    t = await _call("POST", f"/agent/tasks/{task_id}/status", {"status": "progress"})
    return f"Задача #{t['id']} «{t['title']}» теперь в работе."


@mcp.tool()
async def set_task_status(task_id: int, status: str) -> str:
    """Сменить статус задачи: open (новая), progress (в работе), review (на проверке — автору задачи придёт просьба проверить), backlog (на потом) или closed (закрыта — даже если пункты не все отмечены)."""
    t = await _call("POST", f"/agent/tasks/{task_id}/status", {"status": status})
    return f"Задача #{t['id']} «{t['title']}»: статус {t['status']}."


@mcp.tool()
async def list_comments(task_id: int) -> str:
    """Комментарии и вопросы задачи: comment_id, автор, текст, вопрос ли, решён ли, пункт если есть."""
    t = await _call("GET", f"/agent/tasks/{task_id}")
    cs = t.get("comments") or []
    if not cs:
        return f"У задачи #{t['id']} комментариев нет."
    lines = []
    for c in cs:
        kind = "вопрос" + (" (решён)" if c.get("resolved") else " (открыт)") if c.get("question") else "комментарий"
        lines.append(f"[{c['id']}] {kind} · {c['author']}, {c['date']}: {c['text']}"
                     + (f" · item_id={c['item_id']}" if c.get("item_id") else ""))
    return "\n".join(lines)


@mcp.tool()
async def add_comment(task_id: int, text: str, question: bool = False, item_id: int | None = None) -> str:
    """Добавить комментарий к задаче. question=true — вопрос (важнее обычных, пока не решён).
    item_id — комментарий к конкретному пункту чеклиста."""
    body = {"text": text, "question": question, **({"item_id": item_id} if item_id is not None else {})}
    t = await _call("POST", f"/agent/tasks/{task_id}/comments", body)
    c = (t.get("comments") or [])[-1]
    kind = "Вопрос" if c.get("question") else "Комментарий"
    return f"{kind} [{c['id']}] добавлен к задаче #{t['id']}."


@mcp.tool()
async def edit_comment(comment_id: int, text: str) -> str:
    """Изменить свой комментарий или вопрос агента (только те, что написал агент)."""
    t = await _call("PATCH", f"/agent/comments/{comment_id}", {"text": text})
    return f"Комментарий [{comment_id}] в задаче #{t['id']} обновлён."


@mcp.tool()
async def delete_comment(comment_id: int) -> str:
    """Удалить свой комментарий или вопрос агента (только те, что написал агент)."""
    t = await _call("DELETE", f"/agent/comments/{comment_id}")
    return f"Комментарий [{comment_id}] удалён из задачи #{t['id']}."


@mcp.tool()
async def resolve_question(comment_id: int, resolved: bool = True) -> str:
    """Отметить вопрос решённым (resolved=true) или снова открыть (resolved=false). Может любой, кто видит задачу."""
    t = await _call("POST", f"/agent/comments/{comment_id}/resolve", {"resolved": resolved})
    return f"Вопрос [{comment_id}] в задаче #{t['id']}: " + ("решён." if resolved else "снова открыт.")


@mcp.tool()
async def search_tasks(query: str) -> str:
    """Поиск по всем проектам: задачи, пункты, комментарии, результаты, названия проектов (как в приложении)."""
    from urllib.parse import quote
    j = await _call("GET", f"/agent/search?q={quote(query)}")
    total = j.get("total") or {}
    if not any(total.values()):
        return f"По запросу «{query}» ничего не найдено."
    lines = [f"Найдено по «{query}»:"]
    labels = {"task": "Задачи", "item": "Пункты", "comment": "Комментарии", "result": "Результаты", "project": "Проекты"}
    for kind, title in labels.items():
        rows = (j.get("groups") or {}).get(kind) or []
        if not rows:
            continue
        lines.append(f"\n{title} ({total.get(kind, len(rows))}):")
        for r in rows[:15]:
            snip = _snip(r.get("parts"))
            if kind == "project":
                lines.append(f"- {snip or r.get('project')} — project_id={r['pid']}")
            else:
                extra = f" [{r.get('status', '')}]" if r.get("status") else ""
                lines.append(f"- #{r['task_id']} {r.get('task', '')}{extra} · {r.get('project', '')}: {snip}")
    return "\n".join(lines)


@mcp.tool()
async def create_project(name: str) -> str:
    """Создать проект без чата (как «Новый проект» в приложении). Люди заходят по ссылке «Доступ»."""
    p = await _call("POST", "/agent/projects", {"name": name})
    return f"Создан проект «{p['name']}» — project_id={p['id']} (без чата)."


@mcp.tool()
async def rename_project(project_id: int, name: str) -> str:
    """Переименовать проект без чата. Проект из Telegram-чата называется командой /project в самом чате."""
    p = await _call("PATCH", f"/agent/projects/{project_id}", {"name": name})
    return f"Проект project_id={p['id']} теперь «{p['name']}»."


if __name__ == "__main__":
    mcp.run()
