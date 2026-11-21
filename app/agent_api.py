"""Agent API for the MCP server (mcp_server/checklists.py): full access to every project by AGENT_TOKEN.
Changes go through the same code as the mini app's, so the checklist in the chat updates at once."""
import hmac
import json
import time
from pathlib import Path

from aiohttp import web

from . import search as fts
from .db import standalone
from .web import (
    _link_row,
    _on_review,
    _project_name,
    admit_assignee,
    AGENT_NAME,
    assign,
    assignee_from_body,
    can_see_task,
    change_due,
    COLORS,
    db,
    deadlines,
    due_from_body,
    fmt_date,
    is_personal,
    item_src,
    log_task_change,
    msg_link,
    NEW_STATUSES,
    PERSONAL,
    project_people,
    SEARCH_KINDS,
    SEARCH_PER_KIND,
    settings,
    spawn,
    source_chat,
    STATUS_FIELDS,
    task_full,
    tasks,
    team_people,
    tell_assignee,
)


def require_agent(request):
    tok = settings.agent_token
    auth = request.headers.get("Authorization", "")
    if not tok or not hmac.compare_digest(auth, f"Bearer {tok}"):
        raise web.HTTPUnauthorized(text="agent token required")


def _status(t: dict) -> str:
    if t["archived"]:
        return "archive"
    if t.get("backlog"):
        return "backlog"
    if t.get("closed"):
        return "closed"
    return "review" if _on_review(t) else "progress" if t.get("progress") else "open"


async def agent_projects(request):
    require_agent(request)
    out = []
    for p in await db.projects():
        if is_personal(p["chat_id"]):
            continue
        ts = await db.tasks_of_chat(p["chat_id"])
        out.append({"id": p["chat_id"], "name": p["display"],
                    "open_tasks": sum(1 for t in ts if _status(t) in ("open", "progress", "review")), "tasks_total": len(ts)})
    return web.json_response(out)


async def agent_tasks(request):
    require_agent(request)
    chat_id = int(request.match_info["chat_id"])
    want = request.query.get("status", "open")
    out = []
    for t in await db.tasks_of_chat(chat_id):
        st = _status(t)
        if want != "all" and st != want:
            continue
        out.append({"id": t["id"], "title": t["title"], "status": st, "author": t["author_name"],
                    "date": fmt_date(t["created_at"]), "done": t["done_cnt"], "total": t["total"],
                    "due": deadlines.text(t.get("due_at"), bool(t.get("due_allday"))),
                    "assignee": t.get("assignee_name")})
    return web.json_response(out)


async def _agent_task(task_id: int) -> dict:
    t = await db.task(task_id)
    if not t or not t["title"]:
        raise web.HTTPNotFound(text="task not found")
    full = await task_full(t)
    full["links"] = [await _link_row(o) for o in [await db.task(x) for x in await db.task_links(task_id)] if o and o["title"]]
    # the original wording of the source messages, so the agent sees what the client actually wrote
    refs = sorted({(source_chat(t, i), x) for i in await db.items(task_id) for x in item_src(i)})
    src = []
    for chat_id, mid in refs:
        m = await db.message(chat_id, mid)
        if m:
            src.append({"id": mid, "author": m["user_name"], "date": fmt_date(m["date"]), "kind": m["kind"],
                        "text": m["text"] or "", "transcript": m.get("transcript") or "",
                        "link": msg_link(chat_id, mid)})
    full["source_messages"] = src
    full["status"] = _status({**t, "total": len(full["items"]), "done_cnt": sum(1 for i in full["items"] if i["d"])})
    return full


async def agent_task(request):
    require_agent(request)
    return web.json_response(await _agent_task(int(request.match_info["task_id"])))


async def agent_item_done(request):
    require_agent(request)
    item = await db.item(int(request.match_info["item_id"]))
    if not item:
        raise web.HTTPNotFound(text="item not found")
    done = bool((await request.json()).get("done", True))
    await tasks.set_item_done(item["task_id"], item["id"], done, AGENT_NAME, -1)
    await tasks.refresh_checklist(request.app["bot"], item["task_id"])
    return web.json_response(await _agent_task(item["task_id"]))


async def agent_items_add(request):
    """Add items to a task (e.g. a deadline the AI missed). The chat checklist updates."""
    require_agent(request)
    task_id = int(request.match_info["task_id"])
    t = await db.task(task_id)
    if not t or not t["title"]:
        raise web.HTTPNotFound(text="task not found")
    items = [s[:500] for s in (str(x).strip() for x in (await request.json()).get("items") or []) if s][:50]
    if not items:
        raise web.HTTPBadRequest(text="no items")
    before = await db.items(task_id)
    for text in items:
        await db.add_item(task_id, text)
        await db.log_event(task_id, "item_added", AGENT_NAME, -1, text=text)
    await tasks.log_closed_flip(task_id, before, AGENT_NAME, -1)
    await tasks.refresh_checklist(request.app["bot"], task_id)
    return web.json_response(await _agent_task(task_id))


async def agent_task_due(request):
    """{"date": "2026-11-30", "time": "18:00"} sets the deadline, {"date": null} removes it."""
    require_agent(request)
    task_id = int(request.match_info["task_id"])
    t = await db.task(task_id)
    if not t or not t["title"]:
        raise web.HTTPNotFound(text="task not found")
    body = await request.json()
    await change_due(task_id, due_from_body(body if body.get("date") else None), AGENT_NAME, -1)
    await tasks.refresh_checklist(request.app["bot"], task_id)
    return web.json_response(await _agent_task(task_id))


async def agent_task_reminders(request):
    """{"kinds": ["day"], "user_id": 123}: reminders about the deadline for that person (default: the task's author).
    The assignee gets every reminder set on the task anyway."""
    require_agent(request)
    task_id = int(request.match_info["task_id"])
    t = await db.task(task_id)
    if not t or not t["title"]:
        raise web.HTTPNotFound(text="task not found")
    body = await request.json()
    uid = int(body.get("user_id") or t["author_id"] or 0)
    if uid <= 0 or not await can_see_task(request.app["bot"], t, uid):
        raise web.HTTPBadRequest(text="no such member")
    await deadlines.set_mine(task_id, uid, [str(k) for k in body.get("kinds") or []])
    full = await _agent_task(task_id)
    full["reminders"] = {"user_id": uid, "kinds": await deadlines.mine(task_id, uid)}
    return web.json_response(full)


async def agent_people(request):
    """Who can be made the assignee of the project's tasks."""
    require_agent(request)
    chat_id = int(request.match_info["chat_id"])
    if not await db.project(chat_id):
        raise web.HTTPNotFound(text="project not found")
    bot = request.app["bot"]
    return web.json_response([{"id": p["id"], "name": p["name"]} for p in await project_people(bot, chat_id)]
                             + [{"id": p["id"], "name": p["name"], "outside": p["via"]}
                                for p in await team_people(bot, chat_id, None)])


async def agent_task_assignee(request):
    """{"user_id": 123} makes that person the assignee, {"user_id": null} removes the assignee."""
    require_agent(request)
    task_id = int(request.match_info["task_id"])
    t = await db.task(task_id)
    if not t or not t["title"]:
        raise web.HTTPNotFound(text="task not found")
    aid = assignee_from_body((await request.json()).get("user_id"))
    dm = await assign(request.app["bot"], task_id, aid, AGENT_NAME, -1)
    if aid is None and t.get("progress") and not t.get("backlog"):   # nobody does it → open again, as in the app
        await db.update_task(task_id, progress=0, review_at=None)
        await log_task_change(t, await db.task(task_id), AGENT_NAME, -1)
    await tasks.refresh_checklist(request.app["bot"], task_id)
    full = await _agent_task(task_id)
    if dm is False:
        full["warning"] = "the assignee has never opened the bot: no private messages or reminders reach them"
    return web.json_response(full)


def _hours_from_body(body: dict):
    """Parse hours like the mini app: number / «1,5» / empty → None. Raises HTTPBadRequest on bad input."""
    if "hours" not in body:
        return None, False
    raw = body.get("hours")
    try:
        h = float(str(raw).replace(",", ".")) if raw not in (None, "") else None
    except ValueError:
        raise web.HTTPBadRequest(text="hours: a number")
    if h is not None and not 0 <= h <= 10000:
        raise web.HTTPBadRequest(text="hours: 0…10000")
    return (round(h, 2) if h is not None else None), True


async def _patch_task_fields(task_id: int, old: dict, body: dict, bot) -> dict:
    """Apply title / descr / archived / report fields (same limits as api_task_patch). Returns the fresh task."""
    fields = {}
    if "title" in body and str(body["title"]).strip():
        fields["title"] = str(body["title"]).strip()[:120]
    if "archived" in body:
        fields["archived"] = 1 if body["archived"] else 0
    if "result_url" in body:
        u = str(body.get("result_url") or "").strip()[:500]
        fields["result_url"] = u or None
    if "descr" in body:
        d = str(body.get("descr") or "").strip()[:5000]
        if d != (old.get("descr") or ""):
            fields["descr"] = d or None
            await db.log_event(task_id, "descr", AGENT_NAME, -1, text=d[:300] or None)
    if "result_note" in body:
        fields["result_note"] = str(body.get("result_note") or "").strip()[:2000] or None
    h, has_h = _hours_from_body(body)
    if has_h:
        fields["hours"] = h
    if not fields:
        return old
    await db.update_task(task_id, **fields)
    new = await db.task(task_id)
    delta = (new.get("hours") or 0) - (old.get("hours") or 0)
    if abs(delta) >= 0.005:
        await db.log_time(task_id, -1, AGENT_NAME, delta)
    await log_task_change(old, new, AGENT_NAME, -1)
    await tasks.refresh_checklist(bot, task_id)
    return new


async def agent_task_complete(request):
    """Close every item. Optional body: result_url, hours, result_note — the closing report."""
    require_agent(request)
    task_id = int(request.match_info["task_id"])
    old = await db.task(task_id)
    if not old:
        raise web.HTTPNotFound(text="task not found")
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    await tasks.complete_all(task_id, AGENT_NAME, -1)
    # report after close → «report» event (same as filling the form after the last item)
    if any(k in body for k in ("result_url", "hours", "result_note")):
        await _patch_task_fields(task_id, await db.task(task_id), body, request.app["bot"])
    else:
        await tasks.refresh_checklist(request.app["bot"], task_id)
    return web.json_response(await _agent_task(task_id))


async def agent_task_patch(request):
    """Edit title, description, archive flag, or closing report fields — same validation as the mini app."""
    require_agent(request)
    task_id = int(request.match_info["task_id"])
    old = await db.task(task_id)
    if not old or not old["title"]:
        raise web.HTTPNotFound(text="task not found")
    body = await request.json()
    await _patch_task_fields(task_id, old, body, request.app["bot"])
    return web.json_response(await _agent_task(task_id))


async def agent_task_delete(request):
    require_agent(request)
    task_id = int(request.match_info["task_id"])
    task = await db.task(task_id)
    if not task or not task["title"]:
        raise web.HTTPNotFound(text="task not found")
    await tasks.remove_checklist(request.app["bot"], task)
    base = (Path(settings.db_path).parent / "media" / f"task_{task_id}").resolve()
    await db.delete_task(task_id)
    if base.exists():
        import shutil
        shutil.rmtree(base, ignore_errors=True)
    return web.json_response({"deleted": task_id})


async def agent_task_move(request):
    """{"project_id": N | null, "user_id"?: N}: move into that project, or into «Без проекта» when
    project_id is null (personal of user_id, or of the task's author if user_id omitted)."""
    require_agent(request)
    task_id = int(request.match_info["task_id"])
    t = await db.task(task_id)
    if not t or not t["title"]:
        raise web.HTTPNotFound(text="task not found")
    body = await request.json()
    raw = body.get("project_id")
    if raw is None:
        uid = body.get("user_id")
        try:
            pid = int(uid if uid is not None else t["author_id"] or 0)
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(text="user_id")
        if pid <= 0:
            raise web.HTTPBadRequest(text="project_id null needs user_id or a human task author")
    else:
        try:
            pid = int(raw)
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(text="project_id")
        if not is_personal(pid) and not await db.project(pid):
            raise web.HTTPNotFound(text="project not found")
    try:
        await tasks.move_to_project(request.app["bot"], task_id, pid, AGENT_NAME, -1)
    except ValueError as e:
        raise web.HTTPBadRequest(text=str(e))
    return web.json_response(await _agent_task(task_id))


async def agent_task_create(request):
    require_agent(request)
    chat_id = int(request.match_info["chat_id"])
    if not await db.project(chat_id):
        raise web.HTTPNotFound(text="project not found")
    body = await request.json()
    title = str(body.get("title", "")).strip()[:120]
    if not title:
        raise web.HTTPBadRequest(text="empty title")
    items = [s[:500] for s in (str(x).strip() for x in body.get("items") or []) if s][:50]
    status = body.get("status", "open")
    if status not in NEW_STATUSES:
        raise web.HTTPBadRequest(text="status: open | progress | backlog")
    bot = request.app["bot"]
    fields = dict(STATUS_FIELDS[status])
    aid = assignee_from_body(body.get("assignee_id"))
    aname, granted = await admit_assignee(bot, chat_id, aid, None) if aid else (None, False)
    if aid:
        fields.update(assignee_id=aid, assignee_name=aname)
    task_id = await tasks.create_manual(bot, chat_id, -1, AGENT_NAME, title, items, fields)
    if "descr" in body:
        d = str(body.get("descr") or "").strip()[:5000]
        if d:
            await db.update_task(task_id, descr=d)
            await db.log_event(task_id, "descr", AGENT_NAME, -1, text=d[:300])
    full = await _agent_task(task_id)
    if aid:
        await db.log_event(task_id, "assigned", AGENT_NAME, -1, text=aname, extra={"id": aid, "name": aname})
        if not await tell_assignee(bot, task_id, aid, AGENT_NAME, granted):
            full["warning"] = "the assignee has never opened the bot: no private messages or reminders reach them"
    return web.json_response(full)


async def agent_task_status(request):
    require_agent(request)
    task_id = int(request.match_info["task_id"])
    old = await db.task(task_id)
    if not old:
        raise web.HTTPNotFound(text="task not found")
    status = (await request.json()).get("status")
    if status not in STATUS_FIELDS:
        raise web.HTTPBadRequest(text="status: open | progress | review | backlog | closed")
    fields = dict(STATUS_FIELDS[status])
    if status == "review" and not _on_review(old):
        fields.update(review_at=int(time.time()), review_n=0)
    await db.update_task(task_id, **fields)
    await log_task_change(old, await db.task(task_id), AGENT_NAME, -1)
    await tasks.refresh_checklist(request.app["bot"], task_id)
    if status == "review" and not _on_review(old):
        from . import review
        spawn(review.tell_author(request.app["bot"], task_id, -1))
    return web.json_response(await _agent_task(task_id))


async def agent_comment_add(request):
    """{"text": "...", "question": bool, "item_id": optional}: a comment or a question on the task."""
    require_agent(request)
    task_id = int(request.match_info["task_id"])
    t = await db.task(task_id)
    if not t or not t["title"]:
        raise web.HTTPNotFound(text="task not found")
    body = await request.json()
    text = str(body.get("text", "")).strip()[:2000]
    if not text:
        raise web.HTTPBadRequest(text="empty")
    item = None
    if body.get("item_id") is not None:
        try:
            item = next((i for i in await db.items(task_id) if i["id"] == int(body["item_id"])), None)
        except (TypeError, ValueError):
            item = None
        if not item:
            raise web.HTTPBadRequest(text="no such item in this task")
    q = bool(body.get("question"))
    await db.add_comment(task_id, -1, AGENT_NAME, text, item["id"] if item else None, question=q)
    extra = {"item": item["text"][:120]} if item else None
    await db.log_event(task_id, "asked" if q else "commented", AGENT_NAME, -1,
                       item_id=item["id"] if item else None, text=text[:300], extra=extra)
    return web.json_response(await _agent_task(task_id))


async def agent_comment_edit(request):
    """{"text": "..."}: only the agent's own comments (user_id=-1), like the author-only rule in the app."""
    require_agent(request)
    c = await db.comment(int(request.match_info["comment_id"]))
    if not c:
        raise web.HTTPNotFound(text="comment not found")
    if c["user_id"] != -1:
        raise web.HTTPForbidden(text="only the author can change a comment")
    if not await db.task(c["task_id"]):
        raise web.HTTPNotFound(text="task not found")
    text = str((await request.json()).get("text", "")).strip()[:2000]
    if not text and not json.loads(c.get("files") or "[]"):
        raise web.HTTPBadRequest(text="empty")
    if text != c["text"]:
        await db.edit_comment(c["id"], text)
    return web.json_response(await _agent_task(c["task_id"]))


async def agent_comment_delete(request):
    require_agent(request)
    c = await db.comment(int(request.match_info["comment_id"]))
    if not c:
        raise web.HTTPNotFound(text="comment not found")
    if c["user_id"] != -1:
        raise web.HTTPForbidden(text="only the author can delete a comment")
    if not await db.task(c["task_id"]):
        raise web.HTTPNotFound(text="task not found")
    await db.delete_comment(c["id"])
    return web.json_response(await _agent_task(c["task_id"]))


async def agent_comment_resolve(request):
    """{"resolved": true | false}: mark a question answered, or open it again."""
    require_agent(request)
    c = await db.comment(int(request.match_info["comment_id"]))
    if not c or not c.get("question"):
        raise web.HTTPNotFound(text="question not found")
    t = await db.task(c["task_id"])
    if not t or not t["title"]:
        raise web.HTTPNotFound(text="task not found")
    on = bool((await request.json()).get("resolved", True))
    if on != bool(c.get("resolved_at")):
        await db.resolve_comment(c["id"], AGENT_NAME if on else None)
        await db.log_event(t["id"], "answered" if on else "reasked", AGENT_NAME, -1,
                           item_id=c.get("item_id"), text=c["text"][:300])
    return web.json_response(await _agent_task(t["id"]))


async def agent_item_patch(request):
    """{"text": "..."} and/or {"done": bool}: rename or tick/untick a checklist item."""
    require_agent(request)
    item = await db.item(int(request.match_info["item_id"]))
    if not item:
        raise web.HTTPNotFound(text="item not found")
    body = await request.json()
    fields = {}
    if "text" in body and str(body["text"]).strip():
        fields["text"] = str(body["text"]).strip()[:500]
    done = body.get("done") if "done" in body else None
    if fields:
        await db.update_item(item["id"], **fields)
    if "text" in fields and fields["text"] != item["text"]:
        await db.log_event(item["task_id"], "item_edited", AGENT_NAME, -1, item["id"], fields["text"],
                           extra={"from": item["text"]})
    if done is not None:
        await tasks.set_item_done(item["task_id"], item["id"], bool(done), AGENT_NAME, -1)
    await tasks.refresh_checklist(request.app["bot"], item["task_id"])
    return web.json_response(await _agent_task(item["task_id"]))


async def agent_item_delete(request):
    require_agent(request)
    item = await db.item(int(request.match_info["item_id"]))
    if not item:
        raise web.HTTPNotFound(text="item not found")
    before = await db.items(item["task_id"])
    await db.delete_item(item["id"])
    await db.log_event(item["task_id"], "item_deleted", AGENT_NAME, -1, text=item["text"])
    await tasks.log_closed_flip(item["task_id"], before, AGENT_NAME, -1)
    await tasks.refresh_checklist(request.app["bot"], item["task_id"])
    return web.json_response(await _agent_task(item["task_id"]))


async def agent_order(request):
    """{"item_ids": [...]}: reorder checklist items, top first."""
    require_agent(request)
    task_id = int(request.match_info["task_id"])
    t = await db.task(task_id)
    if not t or not t["title"]:
        raise web.HTTPNotFound(text="task not found")
    try:
        ids = [int(i) for i in (await request.json()).get("item_ids", [])]
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text="item_ids: numbers")
    await db.reorder(task_id, ids)
    await tasks.refresh_checklist(request.app["bot"], task_id)
    return web.json_response(await _agent_task(task_id))


async def agent_search(request):
    """Search every project (agent has full access): ?q=… → same shape as /api/search."""
    require_agent(request)
    q = (request.query.get("q") or "").strip()[:100]
    ts = fts.terms(q)
    groups = {k: [] for k in SEARCH_KINDS}
    total = {k: 0 for k in SEARCH_KINDS}
    if not ts:
        return web.json_response({"terms": [], "groups": groups, "total": total})
    hits = await db.search(ts)
    projs = {p["chat_id"]: p for p in await db.projects() if not is_personal(p["chat_id"])}
    task_ids = {h["task"] for h in hits if h["task"] is not None}
    tasks_by_id = {}
    if task_ids:
        marks = ",".join("?" * len(task_ids))
        for t in await db.all(f"SELECT * FROM tasks WHERE id IN ({marks}) AND title<>''", tuple(task_ids)):
            tasks_by_id[t["id"]] = t
    names = {cid: p["display"] for cid, p in projs.items()}
    for cid in {t["chat_id"] for t in tasks_by_id.values()} - set(names):
        names[cid] = PERSONAL if is_personal(cid) else (await db.project(cid) or {}).get("display") or ""

    async def body(table, ref):
        return await db.one(f"SELECT * FROM {table} WHERE id=?", (ref,))

    for h in hits:
        k = h["kind"]
        if k == "project":
            p = projs.get(h["ref"])
            if not p:
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
               "status": _status(t), "archived": bool(t["archived"])}
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


async def agent_project_create(request):
    """{"name": "..."}: a project without a chat (same as the mini app's «Новый проект»).
    Owner is the agent (id -1); people join by the invite link from the app — the agent is not in «Доступ»."""
    require_agent(request)
    name = _project_name(await request.json())
    chat_id = await db.create_standalone(-1, AGENT_NAME, name)
    await db.revoke_access(chat_id, -1)   # -1 is not a person; the invite link is how people get in
    return web.json_response({"id": chat_id, "name": name, "standalone": True})


async def agent_project_rename(request):
    """{"name": "..."}: only a project without a chat — a chat's project is named by /project in the chat."""
    require_agent(request)
    chat_id = int(request.match_info["chat_id"])
    p = await db.project(chat_id)
    if not p:
        raise web.HTTPNotFound(text="project not found")
    if not standalone(chat_id):
        raise web.HTTPBadRequest(text="named in its chat: /project")
    name = _project_name(await request.json())
    await db.set_project_name(chat_id, name)
    return web.json_response({"id": chat_id, "name": name})


def add_routes(r: web.UrlDispatcher):
    r.add_get("/agent/projects", agent_projects)
    r.add_get("/agent/projects/{chat_id}/tasks", agent_tasks)
    r.add_post("/agent/projects/{chat_id}/tasks", agent_task_create)
    r.add_post("/agent/projects", agent_project_create)
    r.add_patch("/agent/projects/{chat_id}", agent_project_rename)
    r.add_get("/agent/tasks/{task_id}", agent_task)
    r.add_patch("/agent/tasks/{task_id}", agent_task_patch)
    r.add_delete("/agent/tasks/{task_id}", agent_task_delete)
    r.add_post("/agent/tasks/{task_id}/move", agent_task_move)
    r.add_post("/agent/items/{item_id}/done", agent_item_done)
    r.add_patch("/agent/items/{item_id}", agent_item_patch)
    r.add_delete("/agent/items/{item_id}", agent_item_delete)
    r.add_post("/agent/tasks/{task_id}/complete", agent_task_complete)
    r.add_post("/agent/tasks/{task_id}/items", agent_items_add)
    r.add_put("/agent/tasks/{task_id}/order", agent_order)
    r.add_post("/agent/tasks/{task_id}/due", agent_task_due)
    r.add_post("/agent/tasks/{task_id}/reminders", agent_task_reminders)
    r.add_post("/agent/tasks/{task_id}/status", agent_task_status)
    r.add_post("/agent/tasks/{task_id}/assignee", agent_task_assignee)
    r.add_post("/agent/tasks/{task_id}/comments", agent_comment_add)
    r.add_patch("/agent/comments/{comment_id}", agent_comment_edit)
    r.add_delete("/agent/comments/{comment_id}", agent_comment_delete)
    r.add_post("/agent/comments/{comment_id}/resolve", agent_comment_resolve)
    r.add_get("/agent/projects/{chat_id}/people", agent_people)
    r.add_get("/agent/search", agent_search)
