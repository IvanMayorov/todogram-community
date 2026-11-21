"""Offline check of the whole flow with a fake Telegram bot and a fake AI.
Run: .venv/bin/python -m tests.test_flow
"""
import asyncio
import hashlib
import hmac
import json
import os
import tempfile
import time
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

TMP = tempfile.mkdtemp()
os.environ.update(BOT_TOKEN="123456:TEST-token", DB_PATH=f"{TMP}/t.sqlite3", CACHE_DIR=f"{TMP}/cache",
                  AI_API_KEY="x", AUTO_CREATE_SECONDS="1", AGENT_TOKEN="agent-test")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from app import ai, tasks  # noqa: E402
from app.db import db  # noqa: E402
from app.handlers import _emojis  # noqa: E402
from app.render import checklist_text  # noqa: E402
from app.web import actor_photo, make_app  # noqa: E402

CHAT = -1001234567890


class Msg:
    def __init__(self, mid): self.message_id = mid
    async def edit_text(self, text): FakeBot.edits.append(text)


class Member:
    status = "member"


class FakeBot:
    sent, edits, deleted = [], [], []
    async def delete_message(self, chat_id, message_id): self.deleted.append(message_id)
    async def get_chat(self, chat_id):
        class P: small_file_id = "chatpic"
        class C: photo = P() if chat_id == CHAT else None
        return C()
    async def send_message(self, chat_id, text, **kw):
        if chat_id > 0:      # a private chat: notifications
            self.dms.append((chat_id, text)); return Msg(1)
        self.sent.append(text); return Msg(9000 + len(self.sent))
    async def edit_message_text(self, **kw): self.edits.append(kw["text"])
    async def download(self, file_id, destination): destination.write(b"\xff\xd8fakejpeg")
    async def get_user_profile_photos(self, user_id, limit=1):
        class S: file_id = f"userpic{user_id}"
        class R: photos = [[S()]] if user_id == 1 else []    # user 2 hides the photo
        return R()
    async def get_chat_member(self, chat_id, user_id):
        m = Member(); m.status = "member" if user_id == 1 else "left"; return m
    dms, prepared = [], []
    async def me(self):
        class U: username = "testbot"; id = 999
        return U()
    async def save_prepared_inline_message(self, user_id, result, **kw):
        self.prepared.append(result)
        class P: id = "prep1"
        return P()


async def fake_ai(content, shots, ids=None):
    texts = " ".join(p.get("text", "") for p in content if p["type"] == "text")
    assert "Скриншот 1" in texts and "кнопку" in texts, texts
    return {"title": "Исправления на главной", "items": [
        {"text": "Поменять цвет кнопки «Купить» на зелёный", "shot": 1, "src": [1, 2]},
        {"text": "Убрать отступ под шапкой", "shot": None}]}


async def check_chat_upgrade(bot):
    from aiogram import Bot, Dispatcher
    from aiogram.types import Update
    from app.handlers import on_toggle, router
    from app.render import checklist_kb
    from app.web import task_full

    old, new = -710234567, -1007102345678
    await db.upsert_project(old, "Группа")
    await db.upsert_project(new, "Группа")
    await db.x("UPDATE projects SET info=?, info_revision=1 WHERE chat_id=?", ("Материалы проекта", old))
    await db.x("INSERT INTO project_info_images(id,chat_id,mime,data) VALUES(?,?,?,?)",
               ("c" * 32, old, "image/png", b"old photo"))
    old_invite = await db.invite_token(old)
    new_invite = await db.invite_token(new)
    await db.grant_access(old, 13, "Саша")
    tid = await db.create_task(old, 1, "Иван", 9)
    await db.save_message(rec(9, chat_id=old, text="Исходное сообщение"))
    await db.mark_used(old, [9], tid)
    await db.fill_task(tid, "Задача до перехода", [{"text": "Старый пункт", "src": [9]}], [], [])
    await db.set_checklist_msg(tid, 10)
    await db.log_event(tid, "created", "Иван", 1)
    await db.add_comment(tid, 1, "Иван", "Комментарий до перехода")
    existing = await db.create_task(new, 1, "Иван", None)
    await db.fill_task(existing, "Уже в супергруппе", [], [], [])
    old_url = checklist_kb(await db.task(tid), "testbot").inline_keyboard[0][0].url
    history_before = await db.all("SELECT * FROM events WHERE task_id=?", (tid,))

    dp = Dispatcher()
    dp.include_router(router)
    async with Bot(os.environ["BOT_TOKEN"]) as telegram:
        for upd, chat, typ, field, peer in (
            (1, old, "group", "migrate_to_chat_id", new),
            (2, new, "supergroup", "migrate_from_chat_id", old),
        ):
            update = Update.model_validate({"update_id": upd, "message": {
                "message_id": 20, "date": int(time.time()), "chat": {"id": chat, "type": typ, "title": "Группа"},
                field: peer,
            }})
            await dp.feed_update(telegram, update)
    await db.upsert_project(old, "Позднее событие старой группы")
    assert await db.project(old) is None
    assert (await db.project(new))["info"] == "Материалы проекта"
    assert (await db.one("SELECT chat_id FROM project_info_images WHERE id=?", ("c" * 32,)))["chat_id"] == new
    assert {row["id"] for row in await db.tasks_of_chat(new)} == {tid, existing}
    assert await db.has_access(new, 13)
    assert (await db.project_by_invite(old_invite))["chat_id"] == new
    assert (await db.project_by_invite(new_invite))["chat_id"] == new
    assert history_before == await db.all("SELECT * FROM events WHERE task_id=?", (tid,))
    assert (await db.comments(tid))[0]["text"] == "Комментарий до перехода"
    t = await db.task(tid)
    assert t["legacy_chat_id"] == old
    assert checklist_kb(t, "testbot").inline_keyboard[0][0].url == old_url

    await db.save_message(rec(9, chat_id=new, text="Сообщение с таким же номером в супергруппе"))
    await db.append_items(tid, [{"text": "Новый пункт", "src": [9], "source_chat_id": new}])
    view = await task_full(t)
    assert view["link"] is None and view["pid"] == new
    assert view["items"][0]["m"] == [] and view["items"][1]["m"] == ["https://t.me/c/7102345678/9"]

    client = TestClient(TestServer(make_app(bot)))
    await client.start_server()
    try:
        response = await client.get(f"/api/tasks/{tid}/source", headers={"Authorization": "tma " + init_data(1)})
        assert response.status == 200
        assert {m["text"] for m in (await response.json())["messages"]} == {
            "Исходное сообщение", "Сообщение с таким же номером в супергруппе",
        }
    finally:
        await client.close()

    async def answer(*args):
        pass

    user = SimpleNamespace(id=2, full_name="Участник")
    message = SimpleNamespace(chat=SimpleNamespace(id=old), message_id=10)
    callback = SimpleNamespace(data=f"t:{tid}:0", message=message, from_user=user, answer=answer)
    await on_toggle(callback, bot)
    assert not (await db.items(tid))[0]["done"]
    user.id = 1
    await on_toggle(callback, bot)
    assert (await db.items(tid))[0]["done"]


def init_data(user_id: int) -> str:
    fields = {"auth_date": str(int(time.time())), "query_id": "q", "user": json.dumps({"id": user_id, "first_name": "Иван"})}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", os.environ["BOT_TOKEN"].encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(fields)


def rec(mid, **kw):
    base = dict(chat_id=CHAT, message_id=mid, user_id=7, user_name="Анна", date=int(time.time()), kind="text",
                text="", file_id=None, file_size=None, file_name=None, thumb_id=None, mime=None, media_group_id=None)
    base.update(kw); return base


async def main():
    await db.connect()
    bot = FakeBot()
    tasks.BOT_USERNAME = "tg_checklists_proto_bot"
    ai.make_checklist = fake_ai

    await db.upsert_project(CHAT, "Сайт клиники")
    await db.save_message(rec(1, text="Привет! Поменяйте кнопку на зелёную"))
    await db.save_message(rec(2, kind="photo", file_id="p1", media_group_id="A", text="вот так"))
    await db.save_message(rec(3, kind="photo", file_id="p2", media_group_id="A"))
    await db.save_message(rec(4, text="и ещё отступ"))

    # ✍ on 1 and on one photo of the album, then remove ✍ from 4
    await db.draft_add(CHAT, 1, 1)
    await db.draft_add(CHAT, 1, 2)
    await db.draft_add(CHAT, 1, 4)
    await db.draft_remove(CHAT, 1, 4)
    assert await db.drafts_due(time.time() + 10) == [(CHAT, 1)]
    ids = await db.draft_take(CHAT, 1)
    msgs = await tasks.collect(CHAT, ids)
    assert [m["message_id"] for m in msgs] == [1, 2, 3], msgs  # whole album, no message 4

    await tasks.create_task(bot, CHAT, 1, "Иван", ids)
    assert bot.sent == ["Разбираю…"]
    t = (await db.tasks_of_chat(CHAT))[0]
    assert t["title"] == "Исправления на главной" and t["total"] == 2
    assert (await db.message(CHAT, 2))["task_id"] == t["id"]
    assert len(await db.media(t["id"])) == 2
    assert '☐ <a href="https://t.me/c/1234567890/1">1.</a> Поменять цвет' in bot.edits[-1], bot.edits[-1]
    assert "☐ 2. Убрать отступ" in bot.edits[-1] and "Готово" not in bot.edits[-1] and "📋" not in bot.edits[-1]
    assert bot.edits[-1].startswith("<b>Исправления на главной</b>\n\n"), bot.edits[-1]

    # the only button under a checklist opens the task in the app
    from app.render import checklist_kb as _kb
    kb = _kb({"id": 1, "auto": 1}, "bot").inline_keyboard
    assert len(kb) == 1 and len(kb[0]) == 1 and kb[0][0].text == "Открыть" and "task_1" in kb[0][0].url, kb

    # used messages are not reused
    assert await tasks.collect(CHAT, [1, 2]) == []

    # toggle from the chat button
    await db.toggle_item_by_pos(t["id"], 0)
    items = await db.items(t["id"])
    assert items[0]["done"] == 1
    assert "☑︎ <a href" in checklist_text(await db.task(t["id"]), items, "Сайт клиники")

    # reactions: emoji normalisation (✍️ with FE0F == ✍)
    from aiogram.types import ReactionTypeEmoji
    assert _emojis([ReactionTypeEmoji(emoji="✍")]) == {"✍"}

    # ---------- mini app API ----------
    client = TestClient(TestServer(make_app(bot)))
    await client.start_server()
    H = {"Authorization": "tma " + init_data(1)}
    r = await client.get("/api/projects", headers=H)
    assert r.status == 200, await r.text()
    pr = await r.json()
    assert pr[0]["name"] == "Сайт клиники" and pr[0]["open"] == 1, pr
    assert pr[0]["photo"].startswith(f"/api/chatphoto/{CHAT}?"), pr
    assert pr[0]["bot_admin"] is False, pr     # the fake bot is not an admin: the app warns it sees no messages
    pic = await client.get(pr[0]["photo"])
    assert pic.status == 200 and (await pic.read()).startswith(b"\xff\xd8")
    assert (await client.get(pr[0]["photo"].replace("s=", "s=0"))).status == 403
    # the project's own picture: over the chat's photo, any member sets it, removing it brings the chat's photo back
    from aiohttp import FormData as FD
    fd = FD(); fd.add_field("file", b"\x89PNG-own-picture", filename="p.jpg", content_type="image/jpeg")
    assert (await client.post(f"/api/projects/{CHAT}/photo", data=fd, headers={"Authorization": "tma " + init_data(2)})).status == 404
    fd = FD(); fd.add_field("file", b"%PDF", filename="p.pdf")
    assert (await client.post(f"/api/projects/{CHAT}/photo", data=fd, headers=H)).status == 415
    fd = FD(); fd.add_field("file", b"\x89PNG-own-picture", filename="p.jpg", content_type="image/jpeg")
    pj = await (await client.post(f"/api/projects/{CHAT}/photo", data=fd, headers=H)).json()
    assert pj["pic"] and "&v=" in pj["photo"], pj
    own = await client.get(pj["photo"])
    assert own.status == 200 and await own.read() == b"\x89PNG-own-picture"
    row = next(p for p in await (await client.get("/api/projects", headers=H)).json() if p["id"] == CHAT)
    assert row["photo"] == pj["photo"] and row["pic"] is True
    fd = FD(); fd.add_field("file", b"\xff\xd8\xff-second", filename="p.jpg", content_type="image/jpeg")
    pj2 = await (await client.post(f"/api/projects/{CHAT}/photo", data=fd, headers=H)).json()
    assert pj2["photo"] != pj["photo"], "a new picture — a new address"
    first = (await db.project(CHAT))["pic"]
    back = await (await client.delete(f"/api/projects/{CHAT}/photo", headers=H)).json()
    assert back == {"photo": pr[0]["photo"], "pic": False}, back
    from app.web import _pic_path
    assert not _pic_path(first).exists(), "the file goes with it"
    # Project info is shared by members, with optimistic editing and signed photo URLs.
    info_url = f"/api/projects/{CHAT}/info"
    outsider = {"Authorization": "tma " + init_data(2)}
    start = await (await client.get(info_url, headers=H)).json()
    assert start == {"text": "", "revision": 0, "photos": []}
    assert (await client.get(info_url, headers=outsider)).status == 404
    assert (await client.put(info_url, headers=outsider, json={"text": "чужой проект", "revision": 0})).status == 404
    update = {"text": "Макеты\nhttps://example.com/design", "revision": 0}
    saved = await (await client.put(info_url, headers=H, json=update)).json()
    assert saved["text"] == update["text"] and saved["revision"] == 1
    assert (await client.put(info_url, headers=H, json=update)).status == 200  # retry after a lost response
    assert (await client.put(info_url, headers=H, json={**update, "text": "Старая версия"})).status == 409

    photo_id = "a" * 32
    photo_path = f"{info_url}/photos/{photo_id}"
    image_bytes = b"\x89PNG\r\n\x1a\n-test"
    assert (await client.put(photo_path, data=image_bytes, headers=outsider)).status == 404
    assert (await client.put(photo_path, data=b"<html>", headers=H)).status == 415
    uploaded = await (await client.put(photo_path, data=image_bytes, headers=H)).json()
    assert [p["id"] for p in uploaded["photos"]] == [photo_id]
    photo_url = uploaded["photos"][0]["url"]
    assert (await client.get(photo_path)).status == 403
    photo = await client.get(photo_url)
    assert photo.content_type == "image/png" and await photo.read() == image_bytes
    assert (await (await client.put(photo_path, data=image_bytes, headers=H)).json()) == uploaded
    assert (await client.delete(photo_path, headers=outsider)).status == 404
    assert (await (await client.delete(photo_path, headers=H)).json())["photos"] == []
    assert (await client.get(photo_url)).status == 404
    restored = await (await client.patch(photo_path, headers=H)).json()
    assert restored["photos"][0]["id"] == photo_id and restored["text"] == saved["text"]
    await client.delete(photo_path, headers=H)

    # a picture by its link: the server downloads it, but never from localhost / the LAN / a non-http scheme
    for bad in ("http://127.0.0.1/x.png", "http://10.0.0.1/x.png", "http://169.254.169.254/latest", "file:///etc/passwd", "ftp://example.com/a.png", ""):
        r = await client.post(f"/api/projects/{CHAT}/photo_fetch", json={"url": bad}, headers=H)
        assert r.status == 400, (bad, r.status)
    assert (await client.post(f"/api/projects/{CHAT}/photo_fetch", json={"url": "https://example.com/a.png"}, headers={"Authorization": "tma " + init_data(2)})).status == 404

    r = await client.get("/api/projects", headers={"Authorization": "tma " + init_data(2)})
    assert (await r.json()) == [], "non-member must not see the project"
    r = await client.get("/api/projects", headers={"Authorization": "tma bad"})
    assert r.status == 401
    # live pushes: a write the app can show → «data: changed» on the stream; the chat buffer stays quiet; no login → 401
    assert (await client.get("/api/live", headers={"Authorization": "tma bad"})).status == 401
    from app import live
    ls = await client.get("/api/live", headers=H)
    assert ls.status == 200 and ls.headers["Content-Type"].startswith("text/event-stream")
    assert (await ls.content.readline()).startswith(b"retry:")
    await ls.content.readline()
    live.changed("INSERT INTO messages(chat_id) VALUES(1)")
    await asyncio.sleep(live.DEBOUNCE + 0.2)
    assert ls.content.at_eof() is False and not ls.content._buffer, "the chat buffer is not a change"
    await db.x("UPDATE projects SET name=name WHERE chat_id=?", (CHAT,))
    assert await asyncio.wait_for(ls.content.readline(), 3) == b"data: changed\n"
    await live.close_all()
    await asyncio.wait_for(ls.read(), 3)
    ls.close()

    # «Создать задачу» in the app: no AI, the checklist goes to the chat
    n_sent = len(FakeBot.sent)
    r = await client.post(f"/api/projects/{CHAT}/tasks", headers=H,
                          json={"title": "  Баннер на главной ", "items": ["Макет", " ", "Вёрстка"],
                                "descr": "  Макет: https://figma.com/x  ", "status": "progress"})
    assert r.status == 200, await r.text()
    nt = await r.json()
    assert nt["title"] == "Баннер на главной" and [i["t"] for i in nt["items"]] == ["Макет", "Вёрстка"], nt
    assert nt["descr"] == "Макет: https://figma.com/x", nt
    assert nt["progress"] and [e["kind"] for e in nt["history"][:2]] == ["assigned", "created"], nt   # into work → yours
    assert nt["assignee"]["id"] == 1, nt
    assert len(FakeBot.sent) == n_sent + 1 and "Баннер на главной" in str(FakeBot.sent[-1]), FakeBot.sent[-1]
    assert (await db.task(nt["id"]))["checklist_msg_id"], "checklist message id saved"
    r = await client.post(f"/api/projects/{CHAT}/tasks", headers=H, json={"title": " "})
    assert r.status == 400
    r = await client.post(f"/api/projects/{CHAT}/tasks", headers={"Authorization": "tma " + init_data(2)}, json={"title": "x"})
    assert r.status == 404, "non-member cannot create tasks"
    r = await client.post(f"/api/projects/{CHAT}/tasks", headers=H, json={"title": "Пустая"})
    empty = await r.json()
    assert empty["items"] == [] and "Пунктов пока нет" in str(FakeBot.sent[-1])
    # nobody on it any more → not «в работе»: open again
    uj = await (await client.patch(f"/api/tasks/{nt['id']}", json={"assignee": None}, headers=H)).json()
    assert not uj["progress"] and uj["assignee"] is None and uj["history"][0]["kind"] in ("status", "assigned"), uj["history"][:2]
    assert any(e["kind"] == "status" and e["extra"]["to"] == "open" for e in uj["history"][:2]), uj["history"][:2]
    # once: an old task in work with nobody on it gets the one who put it into work
    await db.x("UPDATE tasks SET progress=1, assignee_id=NULL, assignee_name=NULL WHERE id=?", (nt["id"],))
    await db.log_event(nt["id"], "status", "Ира", 7, extra={"to": "progress", "from": "open"})
    await db.x("PRAGMA user_version=0")
    await db._heal_progress()
    healed = await db.task(nt["id"])
    assert healed["assignee_id"] == 7 and healed["assignee_name"] == "Ира", healed
    await db.x("UPDATE tasks SET assignee_id=NULL WHERE id=?", (nt["id"],))
    await db._heal_progress()   # only once
    assert (await db.task(nt["id"]))["assignee_id"] is None
    for x in (nt, empty):   # the rest of the test counts the original tasks only
        await client.delete(f"/api/tasks/{x['id']}", headers=H)

    r = await client.get(f"/api/tasks/{t['id']}", headers=H)
    full = await r.json()
    assert full["link"] == f"https://t.me/c/1234567890/{t['checklist_msg_id']}"
    assert len(full["media"]) == 2 and full["items"][0]["s"] == 1
    assert full["items"][0]["m"] == ["https://t.me/c/1234567890/1", "https://t.me/c/1234567890/2"] and full["items"][1]["m"] == []

    r = await client.get(f"/api/tasks/{t['id']}", headers={"Authorization": "tma " + init_data(2)})
    assert r.status == 404, "non-member must not open the task"

    # comments: who wrote and when; in the history; only the author deletes
    cj = await (await client.post(f"/api/tasks/{t['id']}/comments", json={"text": "Посмотрите макет"}, headers=H)).json()
    cm = cj["comments"][-1]
    assert cm["text"] == "Посмотрите макет" and cm["author"] and cm["user_id"] == 1 and cm["date"], cm
    assert cj["history"][0]["kind"] == "commented"
    assert (await client.post(f"/api/tasks/{t['id']}/comments", json={"text": " "}, headers=H)).status == 400
    assert (await client.post(f"/api/tasks/{t['id']}/comments", json={"text": "x"}, headers={"Authorization": "tma " + init_data(2)})).status == 404
    await db.grant_access(CHAT, 2, "Олег")
    assert (await client.delete(f"/api/comments/{cm['id']}", headers={"Authorization": "tma " + init_data(2)})).status == 403
    await db.x("DELETE FROM project_access WHERE chat_id=? AND user_id=2", (CHAT,))
    cj = await (await client.delete(f"/api/comments/{cm['id']}", headers=H)).json()
    assert cj["comments"] == []
    # a question: above the comments until someone marks it answered; the row counts the open ones
    qj = await (await client.post(f"/api/tasks/{t['id']}/comments", json={"text": "Какой срок?", "question": True}, headers=H)).json()
    qc = qj["comments"][-1]
    assert qc["question"] and not qc["resolved"] and qj["history"][0]["kind"] == "asked", qc
    assert (await db.open_questions(t["id"])) == 1
    qj = await (await client.post(f"/api/comments/{qc['id']}/resolve", json={"resolved": True}, headers=H)).json()
    assert qj["comments"][-1]["resolved"] and qj["comments"][-1]["resolved_by"] and qj["history"][0]["kind"] == "answered"
    assert (await db.open_questions(t["id"])) == 0
    qj = await (await client.post(f"/api/comments/{qc['id']}/resolve", json={"resolved": False}, headers=H)).json()
    assert not qj["comments"][-1]["resolved"] and qj["history"][0]["kind"] == "reasked"
    # only the author changes the text; «изменено»
    assert (await client.patch(f"/api/comments/{qc['id']}", json={"text": "Чужая правка"}, headers={"Authorization": "tma " + init_data(2)})).status == 404
    assert (await client.patch(f"/api/comments/{qc['id']}", json={"text": " "}, headers=H)).status == 400
    ej = await (await client.patch(f"/api/comments/{qc['id']}", json={"text": "Какой срок сдачи?"}, headers=H)).json()
    assert ej["comments"][-1]["text"] == "Какой срок сдачи?" and ej["comments"][-1]["edited"] and ej["comments"][-1]["question"]
    await client.delete(f"/api/comments/{qc['id']}", headers=H)
    # screenshots and videos on a comment: uploaded first (a video in 8 MB parts), then named in the comment
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    up = await (await client.post(f"/api/tasks/{t['id']}/comment_files?up={'a' * 24}&part=0&last=1", data=png, headers=H)).json()
    assert up["kind"] == "photo" and up["name"].endswith(".png") and "/api/cfile/" in up["url"], up
    big = b"\x00\x00\x00\x18ftypmp42" + b"0" * (8 * 1024 * 1024 - 12)
    assert (await (await client.post(f"/api/tasks/{t['id']}/comment_files?up={'b' * 24}&part=0", data=big, headers=H)).json())["ok"]
    assert (await client.post(f"/api/tasks/{t['id']}/comment_files?up={'b' * 24}&part=2&last=1", data=b"x", headers=H)).status == 409
    vid = await (await client.post(f"/api/tasks/{t['id']}/comment_files?up={'b' * 24}&part=1&last=1", data=b"tail", headers=H)).json()
    assert vid["kind"] == "video" and vid["name"].endswith(".mp4"), vid
    assert (await client.post(f"/api/tasks/{t['id']}/comment_files?up={'c' * 24}&part=0&last=1", data=b"hello", headers=H)).status == 415
    fj = await (await client.post(f"/api/tasks/{t['id']}/comments", json={"text": "", "files": [up["name"], vid["name"]]}, headers=H)).json()
    fc = fj["comments"][-1]
    assert fc["text"] == "" and [f["kind"] for f in fc["files"]] == ["photo", "video"], fc
    assert fj["history"][0]["text"] == "Скриншот, видео", fj["history"][0]
    r = await client.get(fc["files"][1]["url"], headers={"Range": "bytes=0-9"})
    assert r.status == 206 and r.headers["Content-Type"] == "video/mp4" and len(await r.read()) == 10
    assert (await client.get(fc["files"][0]["url"].split("&s=")[0] + "&s=x")).status == 403
    assert (await client.post(f"/api/tasks/{t['id']}/comments", json={"text": "", "files": ["0" * 24 + ".png"]}, headers=H)).status == 400
    await client.delete(f"/api/comments/{fc['id']}", headers=H)
    await db.x("DELETE FROM events WHERE kind='commented'")
    # the description: free text, in the history; empty removes it
    dj = await (await client.patch(f"/api/tasks/{t['id']}", json={"descr": "  Макет: https://figma.com/x  "}, headers=H)).json()
    assert dj["descr"] == "Макет: https://figma.com/x" and dj["history"][0]["kind"] == "descr", dj["descr"]
    dj = await (await client.patch(f"/api/tasks/{t['id']}", json={"descr": ""}, headers=H)).json()
    assert dj["descr"] == "" and dj["history"][0]["kind"] == "descr"
    await db.x("DELETE FROM events WHERE kind IN ('asked','answered','reasked','descr')")
    # on one item; the feed of all comments (newest first, only what the person can see)
    iid = cj["items"][0]["id"]
    cj = await (await client.post(f"/api/tasks/{t['id']}/comments", json={"text": "к пункту", "item_id": iid}, headers=H)).json()
    assert cj["comments"][-1]["item_id"] == iid
    assert (await client.post(f"/api/tasks/{t['id']}/comments", json={"text": "x", "item_id": 999999}, headers=H)).status == 400
    feed = await (await client.get("/api/comments", headers=H)).json()
    assert feed[0]["text"] == "к пункту" and feed[0]["task_id"] == t["id"] and feed[0]["item"] and feed[0]["project"], feed[0]
    assert await (await client.get("/api/comments", headers={"Authorization": "tma " + init_data(2)})).json() == []
    # search: sections, the found words marked, word forms («пункта» finds «к пункту»), only what the person sees
    S = lambda q, h=H: client.get("/api/search", params={"q": q}, headers=h)   # noqa: E731
    sr = await (await S("ПУНКТА")).json()
    hit = sr["groups"]["comment"][0]
    assert hit["task_id"] == t["id"] and hit["item_id"] == iid and hit["author"] and hit["project"], hit
    assert ["пункту", 1] in hit["parts"], hit["parts"]
    word = cj["items"][0]["t"].split()[0]
    sr = await (await S(word)).json()
    assert any(r["ref"] == iid for r in sr["groups"]["item"]) and sr["total"]["item"] >= 1, sr
    assert any(r["task_id"] == t["id"] for r in sr["groups"]["item"])
    assert sum((await (await S(word, {"Authorization": "tma " + init_data(2)})).json())["total"].values()) == 0
    assert (await (await S("  ")).json())["terms"] == []
    await db.update_item(iid, text="Совсем другое")   # the index follows edits and deletes
    assert not any(r["ref"] == iid for r in (await (await S(word)).json())["groups"]["item"])
    assert any(r["ref"] == iid for r in (await (await S("совсем")).json())["groups"]["item"])
    await db.update_item(iid, text=cj["items"][0]["t"])
    await client.delete(f"/api/comments/{cj['comments'][-1]['id']}", headers=H)
    assert (await (await S("пункту")).json())["total"]["comment"] == 0
    await db.x("DELETE FROM events WHERE kind='commented'")

    r = await client.get(f"/api/tasks/{t['id']}/source", headers=H)
    src = (await r.json())["messages"]
    assert [m["id"] for m in src] == [1, 2, 3] and src[0]["author"] and all("text" in m for m in src), src
    assert any(m["shots"] for m in src), "a screenshot shows under the message it came from"
    r = await client.get(f"/api/tasks/{t['id']}/source", headers={"Authorization": "tma " + init_data(2)})
    assert r.status == 404, "non-member must not read the original messages"
    await db.x("UPDATE messages SET date=0 WHERE chat_id=?", (CHAT,)); await db.purge_old()
    assert len((await (await client.get(f"/api/tasks/{t['id']}/source", headers=H)).json())["messages"]) == 3, "a task's messages outlive the buffer"

    img = await client.get(full["media"][0]["url"])
    assert img.status == 200 and (await img.read()).startswith(b"\xff\xd8")
    bad = await client.get(full["media"][0]["url"].replace("s=", "s=0"))
    assert bad.status == 403

    n_edits = len(bot.edits)
    r = await client.patch(f"/api/tasks/{t['id']}", json={"title": "Главная: правки"}, headers=H)
    assert (await r.json())["title"] == "Главная: правки"
    r = await client.post(f"/api/tasks/{t['id']}/items", json={"text": "Заменить телефон"}, headers=H)
    full = await r.json()
    assert len(full["items"]) == 3
    ids = [i["id"] for i in full["items"]][::-1]
    r = await client.put(f"/api/tasks/{t['id']}/order", json={"item_ids": ids}, headers=H)
    assert [i["id"] for i in (await r.json())["items"]] == ids
    r = await client.patch(f"/api/items/{ids[0]}", json={"done": True, "text": "Заменить телефон в футере"}, headers=H)
    assert (await r.json())["items"][0]["d"] is True
    r = await client.delete(f"/api/items/{ids[-1]}", headers=H)
    assert len((await r.json())["items"]) == 2
    # history: who did what; a quick tick-untick-tick is one change, closing the task is logged
    other = ids[1]
    for d in (True, False, True):
        r = await client.patch(f"/api/items/{other}", json={"done": d}, headers=H)
    h = (await r.json())["history"]
    kinds = [e["kind"] for e in h]
    assert kinds.count("done") == 2 and kinds.count("undone") == 0 and kinds.count("reopened") == 0, kinds
    assert kinds[:2] == ["closed", "done"], kinds
    assert {"renamed", "item_added", "item_edited", "item_deleted", "created"} <= set(kinds), kinds
    # avatars of the people in the history: a signed link, served like the chat photo
    ph = h[0]["photo"]
    assert ph and ph.startswith("/api/userphoto/1?"), h[0]
    pic = await client.get(ph)
    assert pic.status == 200 and (await pic.read()).startswith(b"\xff\xd8")
    assert (await client.get(ph.replace("s=", "s=0"))).status == 403
    assert await actor_photo(-1) is None and await actor_photo(2) is None and await actor_photo(0) is None
    # voice messages can be played: the file is found by the message id (older tasks), then kept in the task
    await db.save_message(rec(7701, kind="voice", file_id="voice7701", mime="audio/ogg", duration=83))
    await db.x("UPDATE tasks SET transcripts=? WHERE id=?", (json.dumps(
        [{"author": "Анна", "text": "с записью", "message_id": 7701},
         {"author": "Анна", "text": "звук из видео", "message_id": 7702}]), t["id"]))
    trs = (await (await client.get(f"/api/tasks/{t['id']}", headers=H)).json())["transcripts"]
    assert trs[0]["audio"].startswith(f"/api/audio/{t['id']}/0?") and "audio" not in trs[1], trs
    assert "file_id" not in trs[0] and trs[0]["duration"] == 83 and trs[1]["duration"] is None, trs
    au = await client.get(trs[0]["audio"])
    assert au.status == 200 and au.headers["Content-Type"] == "audio/ogg" and await au.read()
    assert (await client.get(trs[0]["audio"].replace("s=", "s=0"))).status == 403
    assert (await client.get(trs[0]["audio"].replace("/0?", "/1?"))).status == 403
    await db.x("DELETE FROM messages WHERE message_id=7701")     # the chat buffer is purged later
    trs = (await (await client.get(f"/api/tasks/{t['id']}", headers=H)).json())["transcripts"]
    assert trs[0].get("audio"), trs
    # Archived transcripts work after the chat buffer is purged. Two readers pay for one summary.
    from unittest.mock import AsyncMock, patch
    summary = {"actions": ["Предложить варианты"], "purpose": None, "details": []}
    url = f"/api/tasks/{t['id']}/voice/{trs[0]['key']}/summary"
    with patch.object(ai, "summarize_voice", new_callable=AsyncMock) as summarize:
        summarize.return_value = summary
        assert (await client.post(url, headers={"Authorization": "tma " + init_data(2)})).status == 404
        assert not summarize.called
        responses = await asyncio.gather(client.post(url, headers=H), client.post(url, headers=H))
        assert [await r.json() for r in responses] == [summary, summary]
        summarize.assert_awaited_once_with("с записью")
    trs = (await (await client.get(f"/api/tasks/{t['id']}", headers=H)).json())["transcripts"]
    assert trs[0]["summary"] == summary and trs[1]["summary"] is None
    # files from the chat (tables, PDFs) go into the task and can be downloaded through the bot
    _, _, _, _, fl = await tasks.build_content(bot, [rec(7800, kind="document", file_id="doc7800", file_size=1000,
                                                         file_name="Прайс.xlsx", mime="application/vnd.ms-excel")])
    assert fl == [{"name": "Прайс.xlsx", "size": 1000, "mime": "application/vnd.ms-excel", "file_id": "doc7800",
                   "message_id": 7800, "author": "Анна"}], fl
    # older tasks: files are picked up once from the task's messages still in the chat buffer
    await db.save_message(rec(7801, kind="document", file_id="doc7801", file_size=2048, file_name="Смета \"итог\".xlsx",
                              mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
    await db.save_message(rec(7802, kind="document", file_id="doc7802", file_size=30 * 1024 * 1024, file_name="big.zip"))
    await db.mark_used(CHAT, [7801, 7802], t["id"])
    await db.x("UPDATE tasks SET files=NULL WHERE id=?", (t["id"],))
    fs = (await (await client.get(f"/api/tasks/{t['id']}", headers=H)).json())["files"]
    assert [f["name"] for f in fs] == ['Смета "итог".xlsx', "big.zip"], fs
    assert fs[0]["url"].startswith(f"/api/file/{t['id']}/0?") and "url" not in fs[1] and fs[1]["link"], fs
    assert "file_id" not in fs[0], fs
    fr = await client.get(fs[0]["url"])
    assert fr.status == 200 and await fr.read() and "filename*=UTF-8''" in fr.headers["Content-Disposition"], fr.headers
    assert fr.headers["Content-Type"].startswith("application/vnd.openxmlformats"), fr.headers
    assert (await client.get(fs[0]["url"].replace("s=", "s=0"))).status == 403
    assert (await client.get(fs[0]["url"].replace("/0?", "/1?"))).status == 403
    await db.x("DELETE FROM messages WHERE message_id IN (7801, 7802)")
    assert len((await (await client.get(f"/api/tasks/{t['id']}", headers=H)).json())["files"]) == 2
    # private notifications: on in the app → a hello message; then changes by others come as one message
    from app import notify
    notify.setup(bot)
    me = await (await client.get("/api/me", headers=H)).json()
    assert me["notify"] is False
    r = await client.post("/api/me/notify", json={"on": True}, headers=H)
    assert r.status == 200 and bot.dms[-1][0] == 1 and "Уведомления включены" in bot.dms[-1][1]
    assert (await (await client.get("/api/me", headers=H)).json())["notify"] is True
    await db.set_notify(7, True)          # the author has left the chat: gets nothing
    n0 = len(bot.dms)
    await db.log_event(t["id"], "item_added", "Анна", 7, text="Новый пункт от Анны")
    await db.log_event(t["id"], "status", "Анна", 7, extra={"to": "progress", "from": "open"})
    await db.log_event(t["id"], "done", "Анна", 7, text="тик")           # single ticks are not sent
    await client.post(f"/api/tasks/{t['id']}/items", json={"text": "свой пункт"}, headers=H)   # own change: not sent to self
    await asyncio.sleep(0); await asyncio.sleep(0.05)
    await notify.flush_all()
    new = bot.dms[n0:]
    assert len(new) == 1 and new[0][0] == 1, new
    assert "Новый пункт от Анны" in new[0][1] and "Взята в работу" in new[0][1] and "тик" not in new[0][1], new
    assert "свой пункт" not in new[0][1]
    # what to get is chosen per kind: only new items now — a status change does not come, a new item does
    assert me["notify_kinds"] == ["questions", "comments", "status", "assignee", "due", "items", "shots", "changes"], me
    r = await client.post("/api/me/notify_kinds", json={"kinds": ["items"]}, headers=H)
    assert (await r.json())["notify_kinds"] == ["items"]
    assert (await client.post("/api/me/notify_kinds", json={"kinds": ["spam"]}, headers=H)).status == 400
    n1 = len(bot.dms)
    await db.log_event(t["id"], "status", "Анна", 7, extra={"to": "backlog", "from": "progress"})
    await asyncio.sleep(0.05); await notify.flush_all()
    assert len(bot.dms) == n1, "a kind they turned off"
    await db.log_event(t["id"], "item_added", "Анна", 7, text="ещё пункт")
    await asyncio.sleep(0.05); await notify.flush_all()
    assert len(bot.dms) == n1 + 1 and "ещё пункт" in bot.dms[-1][1]
    assert (await (await client.get("/api/me", headers=H)).json())["notify_kinds"] == ["items"]
    n0 = len(bot.dms) - 1
    # closed: one compact line, the result link always under it; no project line
    r0 = await client.post("/api/me/notify_kinds", json={"kinds": ["status"]}, headers=H)
    n2 = len(bot.dms)
    await db.log_event(t["id"], "closed", "Анна", 7, extra={"result_url": "https://figma.com/file/abc", "hours": 2.5})
    await asyncio.sleep(0.05); await notify.flush_all()
    msg = bot.dms[-1][1]
    assert len(bot.dms) == n2 + 1 and "Закрыта · Анна · 2,5 ч" in msg and 'href="https://figma.com/file/abc"' in msg, msg
    assert msg.count("\n") == 2 and "Сайт клиники" not in msg, msg
    n0 = len(bot.dms) - 1
    # questions and comments are separate: only comments chosen — a question stays in the app
    await client.post("/api/me/notify_kinds", json={"kinds": ["comments"]}, headers=H)
    n3 = len(bot.dms)
    await db.log_event(t["id"], "asked", "Анна", 7, text="Когда сдаём?")
    await asyncio.sleep(0.05); await notify.flush_all()
    assert len(bot.dms) == n3, "questions are off"
    await client.post("/api/me/notify_kinds", json={"kinds": ["questions"]}, headers=H)
    await db.log_event(t["id"], "asked", "Анна", 7, text="Когда сдаём?")
    await asyncio.sleep(0.05); await notify.flush_all()
    assert len(bot.dms) == n3 + 1 and "Вопрос: «Когда сдаём?» · Анна" in bot.dms[-1][1], bot.dms[-1]
    n0 = len(bot.dms) - 1
    await client.post("/api/me/notify", json={"on": False}, headers=H)
    await db.log_event(t["id"], "closed", "Анна", 7)
    await asyncio.sleep(0.05); await notify.flush_all()
    assert len(bot.dms) == n0 + 1
    db.on_event = None
    # share: a prepared message with the task card and an «Открыть» button
    r = await client.post(f"/api/tasks/{t['id']}/share", headers=H)
    assert r.status == 200 and (await r.json())["id"] == "prep1"
    card = bot.prepared[-1]
    body = getattr(card, "caption", None) or card.input_message_content.message_text
    assert (await db.task(t["id"]))["title"] in body and "startapp=task_" in card.reply_markup.inline_keyboard[0][0].url, body
    assert (await client.post(f"/api/tasks/{t['id']}/share", headers={"Authorization": "tma " + init_data(2)})).status == 404
    assert all(e["actor"] for e in h)
    r = await client.patch(f"/api/items/{other}", json={"done": False}, headers=H)   # back to open for the rest
    # backlog: not counted as open, marked in the chat checklist, visible to the agent
    r = await client.patch(f"/api/tasks/{t['id']}", json={"backlog": True}, headers=H)
    assert (await r.json())["backlog"] is True and "в бэклоге" in bot.edits[-1]
    pr = await (await client.get("/api/projects", headers=H)).json()
    assert pr[0]["open"] == 0, pr
    ts = await (await client.get(f"/api/projects/{CHAT}/tasks", headers=H)).json()
    assert next(x for x in ts if x["id"] == t["id"])["backlog"] is True
    r = await client.patch(f"/api/tasks/{t['id']}", json={"backlog": False}, headers=H)
    assert (await r.json())["backlog"] is False and "бэклог" not in bot.edits[-1]
    # «В работе»: one status at a time, shown in the chat, visible to the agent, still counted as open
    r = await client.patch(f"/api/tasks/{t['id']}", json={"status": "progress"}, headers=H)
    j = await r.json()
    assert j["progress"] is True and j["backlog"] is False and "в работе" in bot.edits[-1]
    pr = await (await client.get("/api/projects", headers=H)).json()
    assert pr[0]["open"] == 1, pr
    r = await client.patch(f"/api/tasks/{t['id']}", json={"status": "backlog"}, headers=H)
    j = await r.json()
    assert j["backlog"] is True and j["progress"] is False and "в работе" not in bot.edits[-1]
    r = await client.patch(f"/api/tasks/{t['id']}", json={"status": "open"}, headers=H)
    j = await r.json()
    assert not j["backlog"] and not j["progress"]

    # «Закрыть» from a row swipe ticks every item; a separate task so the counts below stay as they were
    r = await client.post(f"/api/projects/{CHAT}/tasks", headers=H, json={"title": "Закрыть свайпом", "items": ["a", "b"]})
    ct = await r.json()
    r = await client.patch(f"/api/tasks/{ct['id']}", json={"complete": True}, headers=H)
    cj = await r.json()
    assert all(i["d"] for i in cj["items"]) and any(h["kind"] == "closed" for h in cj["history"]), cj["history"]
    await client.delete(f"/api/tasks/{ct['id']}", headers=H)
    assert cj["closed"] is True

    # «Закрыта» is a status of its own: a task without items closes, an item added reopens it
    patch = lambda tid, body: client.patch(f"/api/tasks/{tid}", json=body, headers=H)
    ct = await (await client.post(f"/api/projects/{CHAT}/tasks", headers=H, json={"title": "Без пунктов"})).json()
    cj = await (await patch(ct["id"], {"status": "closed"})).json()
    assert cj["closed"] is True and cj["items"] == [] and cj["history"][0]["kind"] == "closed", cj["history"][:2]
    assert "закрыта" in bot.edits[-1], "the chat checklist says it is closed"
    lst = await (await client.get(f"/api/projects/{CHAT}/tasks", headers=H)).json()
    assert next(x for x in lst if x["id"] == ct["id"])["closed"] is True
    cj = await (await client.post(f"/api/tasks/{ct['id']}/items", json={"text": "ещё одно"}, headers=H)).json()
    assert cj["closed"] is False, "a new open item reopens it"
    await client.delete(f"/api/tasks/{ct['id']}", headers=H)
    # «Создать» sent again after a dropped connection (X-Request-Id): one task, the same answer
    RH = {**H, "X-Request-Id": "req-1"}
    r1 = await (await client.post(f"/api/projects/{CHAT}/tasks", headers=RH, json={"title": "Один раз"})).json()
    r2 = await (await client.post(f"/api/projects/{CHAT}/tasks", headers=RH, json={"title": "Один раз"})).json()
    assert r1["id"] == r2["id"] and len(await db.all("SELECT 1 FROM tasks WHERE req_id='req-1'")) == 1
    r3 = await (await client.post(f"/api/projects/{CHAT}/tasks", headers={**H, "X-Request-Id": "req-2"}, json={"title": "Один раз"})).json()
    assert r3["id"] != r1["id"], "another id — another task"
    for x in (r1, r3):
        await client.delete(f"/api/tasks/{x['id']}", headers=H)
    # time spent, at any stage: the total on the task, every change in the log (who, how much), «Время» sums it
    tt = await (await client.post(f"/api/projects/{CHAT}/tasks", headers=H, json={"title": "Часы", "items": ["a"]})).json()
    cj = await (await patch(tt["id"], {"hours": 1.5})).json()
    assert cj["hours"] == 1.5 and not cj["closed"] and cj["history"][0]["kind"] == "time", cj["history"][:1]
    assert cj["history"][0]["extra"] == {"hours": 1.5, "from": None} and "потрачено 1,5 ч" in bot.edits[-1], bot.edits[-1]
    await client.patch(f"/api/tasks/{tt['id']}", json={"hours": "2,25"}, headers={"Authorization": "tma " + init_data(3)})   # not in it
    await db.grant_access(CHAT, 3, "Мария")
    cj = await (await client.patch(f"/api/tasks/{tt['id']}", json={"hours": "2,25"}, headers={"Authorization": "tma " + init_data(3)})).json()
    assert cj["hours"] == 2.25
    cj = await (await patch(tt["id"], {"status": "closed", "hours": 3})).json()   # closing with the hours: its own event
    assert cj["history"][0]["kind"] == "closed" and cj["history"][0]["extra"]["hours"] == 3
    log = await db.all("SELECT user_id, hours FROM time_log WHERE task_id=? ORDER BY id", (tt["id"],))
    assert [(r["user_id"], r["hours"]) for r in log] == [(1, 1.5), (3, 0.75), (1, 0.75)], log
    tj = await (await client.get("/api/time", headers=H)).json()
    mine = [r for r in tj["rows"] if r["task_id"] == tt["id"]]
    assert tj["me"] == 1 and len(mine) == 3 and sum(r["hours"] for r in mine) == 3 and mine[0]["project"] == "Сайт клиники", mine
    assert {r["user"]["id"] for r in mine} == {1, 3} and all(r["closed"] for r in mine)
    assert (await client.get("/api/time?period=year", headers=H)).status == 400
    assert not [r for r in (await (await client.get("/api/time", headers={"Authorization": "tma " + init_data(2)})).json())["rows"]
                if r["task_id"] == tt["id"]], "someone outside the project does not see its time"
    await db.x("UPDATE time_log SET ts=ts-40*86400 WHERE task_id=? AND user_id=3", (tt["id"],))
    assert sum(r["hours"] for r in (await (await client.get("/api/time?period=month", headers=H)).json())["rows"] if r["task_id"] == tt["id"]) == 2.25
    assert sum(r["hours"] for r in (await (await client.get("/api/time?period=all", headers=H)).json())["rows"] if r["task_id"] == tt["id"]) == 3
    now = int(time.time())   # a range of days: from included, to not
    async def rng(f, t):
        j = await (await client.get(f"/api/time?from={f}&to={t}", headers=H)).json()
        return sum(r["hours"] for r in j["rows"] if r["task_id"] == tt["id"])
    assert await rng(now - 45 * 86400, now - 35 * 86400) == 0.75, "only Мария's row from 40 days ago"
    assert await rng(now - 3600, now + 60) == 2.25
    assert (await client.get(f"/api/time?from={now}&to={now - 1}", headers=H)).status == 400
    assert (await client.get("/api/time?from=вчера", headers=H)).status == 400
    await db.revoke_access(CHAT, 3)
    await client.delete(f"/api/tasks/{tt['id']}", headers=H)
    assert not await db.all("SELECT 1 FROM time_log WHERE task_id=?", (tt["id"],)), "deleting the task drops its time"
    # with items: closed by status the items stay as they were; the last tick closes, an untick reopens
    ct = await (await client.post(f"/api/projects/{CHAT}/tasks", headers=H, json={"title": "Два пункта", "items": ["a", "b"]})).json()
    ia, ib = [i["id"] for i in ct["items"]]
    cj = await (await patch(ct["id"], {"status": "closed", "result_url": "https://example.com/r", "hours": "1,5"})).json()
    assert cj["closed"] and not any(i["d"] for i in cj["items"])
    assert cj["result_url"] == "https://example.com/r" and cj["hours"] == 1.5, cj
    assert cj["history"][0]["kind"] == "closed" and cj["history"][0]["extra"] == {"result_url": "https://example.com/r", "hours": 1.5}
    assert "закрыта, 1,5 ч" in bot.edits[-1] and "Результат:" in bot.edits[-1], bot.edits[-1]
    assert (await patch(ct["id"], {"hours": "много"})).status == 400
    cj = await (await client.patch(f"/api/items/{ia}", json={"done": True}, headers=H)).json()
    assert cj["closed"] is True, "ticking inside a closed task keeps it closed"
    cj = await (await patch(ct["id"], {"status": "open"})).json()
    assert cj["closed"] is False and cj["history"][0]["kind"] == "reopened", cj["history"][:2]
    cj = await (await client.patch(f"/api/items/{ib}", json={"done": True}, headers=H)).json()
    assert cj["closed"] is True, "the last tick closes it"
    cj = await (await patch(ct["id"], {"result_url": "https://example.com/x", "hours": 3})).json()   # the report after that
    assert cj["closed"] and cj["hours"] == 3 and cj["history"][0]["kind"] == "report", cj["history"][:2]
    cj = await (await patch(ct["id"], {"result_note": "  Сделал главную  "})).json()   # the result in words: on the task, not a comment
    assert cj["result_note"] == "Сделал главную" and not cj["comments"], cj
    assert cj["history"][0]["kind"] == "report" and cj["history"][0]["extra"]["result_note"] == "Сделал главную", cj["history"][:1]
    sr = await (await client.get("/api/search", params={"q": "главная"}, headers=H)).json()
    assert any(r["task_id"] == ct["id"] and r["status"] == "closed" for r in sr["groups"]["result"]), sr["groups"]["result"]
    assert "Результат: Сделал главную · " in bot.edits[-1], bot.edits[-1]
    cj = await (await client.patch(f"/api/items/{ib}", json={"done": False}, headers=H)).json()
    assert cj["closed"] is False, "an untick reopens it"
    r = await client.post(f"/api/projects/{CHAT}/tasks", headers=H, json={"title": "x", "status": "closed"})
    assert (await r.json())["closed"] is False, "a task is not born closed"
    await client.delete(f"/api/tasks/{(await r.json())['id']}", headers=H)
    await client.delete(f"/api/tasks/{ct['id']}", headers=H)

    r = await client.patch(f"/api/tasks/{t['id']}", json={"archived": True}, headers=H)
    assert (await r.json())["archived"] is True
    assert len(bot.edits) > n_edits and "в архиве" in bot.edits[-1], "chat checklist must follow app edits"

    # project history feed: status-level changes by default, a filter per chip, paging, members only
    async def feed(q=""):
        r = await client.get(f"/api/projects/{CHAT}/history{q}", headers=H)
        assert r.status == 200, await r.text()
        return await r.json()
    j = await feed()
    kinds = [e["kind"] for e in j["events"]]
    assert kinds[0] == "archived" and j["events"][0]["task"] == (await db.task(t["id"]))["title"], kinds
    assert "status" in kinds and "created" in kinds and not {"done", "undone", "renamed", "item_added"} & set(kinds), kinds
    assert [e["kind"] for e in (await feed("?filter=archive"))["events"]] == ["archived"]
    assert (await feed("?filter=backlog"))["events"] and all(e["extra"]["to"] == "backlog" for e in (await feed("?filter=backlog"))["events"])
    assert all(e["extra"]["to"] == "progress" for e in (await feed("?filter=progress"))["events"])
    assert {e["kind"] for e in (await feed("?filter=items"))["events"]} <= {"done", "undone", "done_all"}
    p1 = await feed("?limit=1")
    p2 = await feed(f"?limit=1&before={p1['next']}")
    assert len(p1["events"]) == 1 and p2["events"][0]["id"] < p1["events"][0]["id"], (p1, p2)
    assert (await client.get(f"/api/projects/{CHAT}/history?filter=nope", headers=H)).status == 400
    assert (await client.get(f"/api/projects/{CHAT}/history?before=x", headers=H)).status == 400
    assert (await client.get(f"/api/projects/{CHAT}/history", headers={"Authorization": "tma " + init_data(2)})).status == 404
    # the feed of all projects: same events plus the project's name, only the user's own projects
    r = await client.get("/api/history?limit=1", headers=H)
    e = (await r.json())["events"][0]
    assert r.status == 200 and e["pid"] == CHAT and e["project"] == "Сайт клиники" and e["kind"] == "archived", e
    assert (await (await client.get("/api/history", headers={"Authorization": "tma " + init_data(2)})).json()) == {"events": [], "next": None}
    # a task can close by deleting its last open item, and reopen by adding one: both count
    r = await client.post(f"/api/projects/{CHAT}/tasks", json={"title": "Закрытие удалением", "items": ["а", "б"]}, headers=H)
    nt = await r.json()
    await client.patch(f"/api/items/{nt['items'][0]['id']}", json={"done": True}, headers=H)
    await client.delete(f"/api/items/{nt['items'][1]['id']}", headers=H)
    assert [e["task_id"] for e in (await feed("?filter=closed"))["events"]][0] == nt["id"]
    await db.x("UPDATE events SET ts=ts-1000 WHERE task_id=?", (nt["id"],))    # older than the «same change» window
    await client.post(f"/api/tasks/{nt['id']}/items", json={"text": "в"}, headers=H)
    assert (await feed("?filter=open"))["events"][0]["task_id"] == nt["id"]
    assert (await feed("?filter=open"))["events"][0]["kind"] == "reopened"
    assert (await client.delete(f"/api/tasks/{nt['id']}", headers=H)).status == 200
    assert nt["id"] not in [e["task_id"] for e in (await feed())["events"]]

    # upload a screenshot from the phone library
    import aiohttp
    fd = aiohttp.FormData(); fd.add_field("file", b"\xff\xd8\xff\xe0fake-jpeg", filename="a.jpg", content_type="image/jpeg")
    r = await client.post(f"/api/tasks/{t['id']}/media", data=fd, headers=H)
    up = await r.json()
    assert r.status == 200 and up["new_shot"] == 3 and len([m for m in up["media"] if m["kind"] == "photo"]) == 3, up
    pic = await client.get(up["media"][-1]["url"])
    assert pic.status == 200 and (await pic.read()).startswith(b"\xff\xd8\xff")
    fd = aiohttp.FormData(); fd.add_field("file", b"%PDF-1.4", filename="a.pdf", content_type="application/pdf")
    assert (await client.post(f"/api/tasks/{t['id']}/media", data=fd, headers=H)).status == 415
    fd = aiohttp.FormData(); fd.add_field("file", b"\xff\xd8\xff", filename="a.jpg")
    assert (await client.post(f"/api/tasks/{t['id']}/media", data=fd, headers={"Authorization": "tma " + init_data(2)})).status == 404
    # delete screenshot 1: the rest move down, items follow their screenshot, the one on №1 loses it
    before = {i["id"]: i["s"] for i in up["items"]}
    local = Path(os.environ["DB_PATH"]).parent / "media" / (await db.media(t["id"]))[-1]["file_id"][6:]
    n_media = len(up["media"])
    assert (await client.delete(f"/api/tasks/{t['id']}/media/1", headers={"Authorization": "tma " + init_data(2)})).status == 404
    r = await client.delete(f"/api/tasks/{t['id']}/media/1", headers=H)
    after = await r.json()
    assert r.status == 200 and [m["idx"] for m in after["media"]] == list(range(1, n_media)), after["media"]
    for i in after["items"]:
        s0 = before[i["id"]]
        assert i["s"] == (None if s0 in (None, 1) else s0 - 1), (i, s0)
    assert after["history"][0]["kind"] == "shot_deleted"
    r = await client.delete(f"/api/tasks/{t['id']}/media/{n_media - 1}", headers=H)   # the uploaded one: file removed
    assert r.status == 200 and not local.exists()
    assert (await client.delete(f"/api/tasks/{t['id']}/media/99", headers=H)).status == 404

    # delete a task from the app: gone from API, chat checklist removed, messages freed
    await db.save_message(rec(50, text="удалить меня"))
    async def del_ai(c, s, ids=None): return {"title": "На удаление", "items": [{"text": "x", "shot": None}]}
    ai.make_checklist, keep_ai = del_ai, ai.make_checklist
    await tasks.create_task(bot, CHAT, 1, "Иван", [50])
    ai.make_checklist = keep_ai
    td = next(t for t in await db.tasks_of_chat(CHAT) if t["title"] == "На удаление")
    assert (await client.delete(f"/api/tasks/{td['id']}", headers={"Authorization": "tma " + init_data(2)})).status == 404
    r = await client.delete(f"/api/tasks/{td['id']}", headers=H)
    assert r.status == 200 and await db.task(td["id"]) is None
    assert td["checklist_msg_id"] in FakeBot.deleted and (await db.message(CHAT, 50))["task_id"] is None

    # agent creates a task: posted to the chat, written to history, token and input checked
    A = {"Authorization": "Bearer agent-test"}
    url = f"/agent/projects/{CHAT}/tasks"
    assert (await client.post(url, json={"title": "x"})).status == 401
    assert (await client.post(url, json={"title": "  "}, headers=A)).status == 400
    assert (await client.post(url, json={"title": "x", "status": "closed"}, headers=A)).status == 400
    assert (await client.post("/agent/projects/1/tasks", json={"title": "x"}, headers=A)).status == 404
    n_sent = len(bot.sent)
    r = await client.post(url, json={"title": "От агента", "items": ["раз", " ", "два"], "status": "backlog"}, headers=A)
    assert r.status == 200, await r.text()
    j = await r.json()
    assert j["title"] == "От агента" and [i["t"] for i in j["items"]] == ["раз", "два"] and j["status"] == "backlog", j
    assert j["author"] == "Агент" and j["history"][0]["kind"] == "created", j
    assert len(bot.sent) == n_sent + 1 and "От агента" in bot.sent[-1]
    assert (await db.task(j["id"]))["checklist_msg_id"]
    assert (await client.delete(f"/api/tasks/{j['id']}", headers=H)).status == 200

    # a task without items counts as open in the project list
    before_open = next(p for p in await (await client.get("/api/projects", headers=H)).json() if p["id"] == CHAT)["open"]
    r = await client.post(f"/api/projects/{CHAT}/tasks", json={"title": "Без пунктов", "items": []}, headers=H)
    assert r.status == 200, await r.text()
    assert next(p for p in await (await client.get("/api/projects", headers=H)).json() if p["id"] == CHAT)["open"] == before_open + 1
    assert (await client.delete(f"/api/tasks/{(await r.json())['id']}", headers=H)).status == 200

    # deadline and reminders: set in the app, shown in the chat checklist, reminders go out in private
    from app import deadlines
    t = await (await client.post(f"/api/projects/{CHAT}/tasks", json={"title": "Со сроком", "items": ["раз"]}, headers=H)).json()
    soon = time.localtime(time.time() + 3 * 86400)
    day = time.strftime("%Y-%m-%d", soon)
    r = await client.patch(f"/api/tasks/{t['id']}", json={"due": {"date": day, "time": "18:00"}}, headers=H)
    j = await r.json()
    assert r.status == 200 and j["due"]["date"] == day and j["due"]["time"] == "18:00" and j["reminders"] == [], j
    assert j["history"][0]["kind"] == "due" and "18:00" in j["history"][0]["extra"]["text"]
    assert "срок до " in bot.edits[-1] and "18:00" in bot.edits[-1], bot.edits[-1]
    assert (await client.patch(f"/api/tasks/{t['id']}", json={"due": {"date": "завтра"}}, headers=H)).status == 400
    r = await client.put(f"/api/tasks/{t['id']}/reminders", json={"kinds": ["day", "hour", "nonsense"]}, headers=H)
    assert (await r.json())["reminders"] == ["day", "hour"]
    due_at = (await db.task(t["id"]))["due_at"]
    assert await deadlines.due_now(due_at - 3 * 86400) == []                        # nothing yet
    got = await deadlines.due_now(due_at - 86400 + 60)                              # a day before
    assert [(x["kind"], x["user_id"]) for x in got] == [("day", 1)], got
    n_dm = len(bot.dms)
    await deadlines.send(bot, got[0], "testbot")
    assert "Завтра срок" in bot.dms[-1][1] and len(bot.dms) == n_dm + 1, bot.dms[-1]
    assert await deadlines.due_now(due_at - 86400 + 120) == []                      # sent once
    late = await deadlines.due_now(due_at + 2 * 3600)                               # «за час» missed by >1 h: skipped
    assert late == [] and await deadlines.due_now(due_at + 3 * 3600) == []
    # the deadline moves → the reminders are armed again; ones already in the past for it stay quiet
    r = await client.patch(f"/api/tasks/{t['id']}", json={"due": {"date": time.strftime("%Y-%m-%d", time.localtime(time.time() + 5 * 86400))}}, headers=H)
    j = await r.json()
    assert j["due"]["allday"] is True and j["due"]["time"] is None, j
    t2 = await db.task(t["id"])
    got = await deadlines.due_now(deadlines.fire_at("day", t2["due_at"], True) + 1)
    assert [x["kind"] for x in got] == ["day"], got
    # snooze: one-off, an hour later
    await deadlines.snooze(t["id"], 1, seconds=-1)
    assert [x["kind"] for x in await deadlines.due_now()] == ["snooze"]
    assert await deadlines.due_now() == []
    # removing the deadline; the task in the list carries it
    r = await client.patch(f"/api/tasks/{t['id']}", json={"due": None}, headers=H)
    assert (await r.json())["due"] is None and (await r.json())["history"][0]["kind"] == "due"
    assert "срок до" not in bot.edits[-1]
    await client.patch(f"/api/tasks/{t['id']}", json={"due": {"date": day}}, headers=H)
    lst = await (await client.get(f"/api/projects/{CHAT}/tasks", headers=H)).json()
    row = next(x for x in (lst if isinstance(lst, list) else lst["tasks"]) if x["id"] == t["id"])
    assert row["due"]["date"] == day, row
    # agent sets the deadline
    r = await client.post(f"/agent/tasks/{t['id']}/due", json={"date": day, "time": "09:30"}, headers=A)
    assert r.status == 200 and (await r.json())["due"]["time"] == "09:30", await r.text()
    r = await client.post(f"/agent/tasks/{t['id']}/reminders", json={"kinds": ["day"]}, headers=A)
    assert (await r.json())["reminders"] == {"user_id": 1, "kinds": ["day"]}, await r.text()
    assert (await client.post(f"/agent/tasks/{t['id']}/reminders", json={"kinds": ["day"], "user_id": 2}, headers=A)).status == 400
    r = await client.post(f"/agent/tasks/{t['id']}/due", json={"date": None}, headers=A)
    assert (await r.json())["due"] is None

    # assignee: picked from the project's people, told in private; reminders set on the task reach them too
    await db.grant_access(CHAT, 2, "Олег")
    ppl = await (await client.get(f"/api/projects/{CHAT}/people", headers=H)).json()
    assert {p["id"] for p in ppl} >= {1, 2} and 999 not in {p["id"] for p in ppl}, ppl
    assert (await client.patch(f"/api/tasks/{t['id']}", json={"assignee": 5}, headers=H)).status == 400   # not in the project
    n_dm = len(bot.dms)
    r = await client.patch(f"/api/tasks/{t['id']}", json={"assignee": 2}, headers=H)
    j = await r.json()
    assert j["assignee"]["id"] == 2 and j["assignee"]["name"] == "Олег" and j["history"][0]["kind"] == "assigned", j
    assert bot.dms[n_dm][0] == 2 and "исполнитель" in bot.dms[n_dm][1], bot.dms[n_dm:]
    assert "исполнитель: Олег" in bot.edits[-1], bot.edits[-1]
    lst = await (await client.get(f"/api/projects/{CHAT}/tasks", headers=H)).json()
    assert next(x for x in lst if x["id"] == t["id"])["assignee"]["name"] == "Олег"
    await client.patch(f"/api/tasks/{t['id']}", json={"due": {"date": day, "time": "18:00"}}, headers=H)
    r = await client.put(f"/api/tasks/{t['id']}/reminders", json={"kinds": ["hour"]}, headers=H)
    assert (await r.json())["task_reminders"] == ["hour"]
    due_at = (await db.task(t["id"]))["due_at"]
    got = await deadlines.due_now(due_at - 3600 + 10)
    assert sorted((x["kind"], x["user_id"]) for x in got) == [("hour", 1), ("hour", 2)], got
    await deadlines.send(bot, next(x for x in got if x["user_id"] == 2), "testbot")
    assert bot.dms[-1][0] == 2 and "вы исполнитель" in bot.dms[-1][1], bot.dms[-1]
    assert await deadlines.due_now(due_at - 3600 + 20) == []                     # once each
    # agent: people and assignee
    ppl = await (await client.get(f"/agent/projects/{CHAT}/people", headers=A)).json()
    assert any(p["id"] == 2 for p in ppl), ppl
    r = await client.post(f"/agent/tasks/{t['id']}/assignee", json={"user_id": None}, headers=A)
    assert (await r.json())["assignee"] is None and "исполнитель:" not in bot.edits[-1]
    # taking a task into work with nobody on it makes you its assignee
    r = await client.patch(f"/api/tasks/{t['id']}", json={"status": "progress"}, headers=H)
    assert (await r.json())["assignee"]["id"] == 1
    # assignee chosen right when the task is created: in the first checklist, told in private
    n_dm, n_sent = len(bot.dms), len(bot.sent)
    r = await client.post(f"/api/projects/{CHAT}/tasks", json={"title": "Сразу с исполнителем", "assignee": 2}, headers=H)
    j = await r.json()
    assert r.status == 200 and j["assignee"]["id"] == 2 and j["history"][0]["kind"] == "assigned", j
    assert "Олег" in bot.sent[n_sent] and bot.dms[n_dm][0] == 2, (bot.sent[n_sent:], bot.dms[n_dm:])
    assert (await client.delete(f"/api/tasks/{j['id']}", headers=H)).status == 200
    assert (await client.post(f"/api/projects/{CHAT}/tasks", json={"title": "x", "assignee": 5}, headers=H)).status == 400
    j = await (await client.post(f"/api/projects/{CHAT}/tasks", json={"title": "В работу", "status": "progress"}, headers=H)).json()
    assert j["assignee"]["id"] == 1, j
    assert (await client.delete(f"/api/tasks/{j['id']}", headers=H)).status == 200
    r = await client.post(f"/agent/projects/{CHAT}/tasks", json={"title": "Агент назначил", "assignee_id": 2}, headers=A)
    j = await r.json()
    assert j["assignee"]["id"] == 2, j
    assert (await client.delete(f"/api/tasks/{j['id']}", headers=H)).status == 200
    # «На проверке»: still in work, the author is asked to check it (not when they put it there themselves),
    # reminded once a day up to review.NUDGES times; «Принять» in the private chat closes it
    from app import review as rv
    was_on = await db.notify_enabled(1)
    await db.set_notify(1, False)
    j = await (await client.post(f"/api/projects/{CHAT}/tasks", json={"title": "Проверь меня"}, headers=H)).json()
    n_dm = len(bot.dms)
    r = await (await client.patch(f"/api/tasks/{j['id']}", json={"status": "review"}, headers=H)).json()
    assert r["review"] and r["progress"] and r["history"][0]["extra"]["to"] == "review", r
    await asyncio.sleep(0.05)
    assert len(bot.dms) == n_dm, "put on review by its author: nobody to ask"
    assert (await (await client.patch(f"/api/tasks/{j['id']}", json={"status": "progress"}, headers=H)).json())["review"] is False
    r = await (await client.post(f"/agent/tasks/{j['id']}/status", json={"status": "review"}, headers=A)).json()
    assert r["status"] == "review", r
    await asyncio.sleep(0.05)
    assert len(bot.dms) == n_dm, "messages turned off in the app: none of these either"
    await db.set_notify(1, True)
    await client.post(f"/agent/tasks/{j['id']}/status", json={"status": "progress"}, headers=A)
    await client.post(f"/agent/tasks/{j['id']}/status", json={"status": "review"}, headers=A)
    await asyncio.sleep(0.05)
    assert bot.dms[-1][0] == 1 and "Нужно проверить" in bot.dms[-1][1] and "Проверь меня" in bot.dms[-1][1], bot.dms[n_dm:]
    assert "на проверке" in bot.edits[-1], bot.edits[-1]
    n_dm = len(bot.dms)
    await rv.nudge_all(bot)
    assert len(bot.dms) == n_dm, "not a day yet"
    t0 = time.time()
    for k in range(1, rv.NUDGES + 2):
        await rv.nudge_all(bot, t0 + k * rv.EVERY + 60)
    assert len(bot.dms) == n_dm + rv.NUDGES and "Напоминание" in bot.dms[-1][1], bot.dms[n_dm:]
    proj_rows = await (await client.get(f"/api/projects/{CHAT}/tasks", headers=H)).json()
    row = next(x for x in (proj_rows if isinstance(proj_rows, list) else proj_rows["tasks"]) if x["id"] == j["id"])
    assert row["review"] is True, row
    from app.handlers import on_review_button
    class CQ:
        data = f"rv:{j['id']}:ok"; message = None; answers = []
        class from_user: id = 1; full_name = "Иван"
        async def answer(self, text=None, **kw): self.answers.append(text)
    cq = CQ(); await on_review_button(cq, bot)
    rt = await db.task(j["id"])
    assert rt["closed"] and not rt["review_at"] and cq.answers == ["Принято"], (rt, cq.answers)
    await on_review_button(cq, bot)
    assert cq.answers[-1] == "Задача уже не на проверке"
    assert (await client.delete(f"/api/tasks/{j['id']}", headers=H)).status == 200
    await db.set_notify(1, was_on)

    await db.revoke_access(CHAT, 2)

    # messages forwarded to the bot in private: a burst → project (if several) → assignee → a task
    from app import inbox
    inbox.remember(1, "Иван")
    await db.grant_access(CHAT, 2, "Олег")                 # so the assignee picker has someone
    await db.save_message(rec(8101, chat_id=1, text="Поменяйте кнопку на зелёную"))
    await db.save_message(rec(8102, chat_id=1, kind="photo", file_id="fwp"))
    await db.save_message(rec(8103, chat_id=1, kind="document", file_id="fwd", file_size=10, file_name="Прайс.xlsx"))
    n_sent, n_dm = len(bot.sent), len(bot.dms)
    await inbox.flush(bot, 1, 8101, 8103)                  # one project → ask for an assignee
    assert "исполнител" in bot.dms[-1][1].lower(), bot.dms[-1]
    assert len(bot.sent) == n_sent and (await db.message(1, 8101))["task_id"] is None
    assert await inbox.on_assignee(bot, 1, 1, f"fa:{CHAT}:8101:8103:2") == "Создаю задачу"
    await asyncio.sleep(0.05)
    ft = (await db.tasks_of_chat(CHAT))[0]
    assert ft["author_name"] == "Иван" and ft["assignee_id"] == 2 and ft["assignee_name"] == "Олег", ft
    assert len(bot.sent) == n_sent + 1 and ft["checklist_msg_id"] and "исполнитель: Олег" in bot.sent[-1], bot.sent[-1]
    assert bot.dms[-1][0] == 2 and "исполнитель" in bot.dms[-1][1], bot.dms[n_dm:]
    assert all(not json.loads(i["src"]) for i in await db.items(ft["id"]))      # private ids: no chat links
    assert [m["message_id"] for m in await db.media(ft["id"])] == [None]
    assert [f["name"] for f in json.loads(ft["files"])] == ["Прайс.xlsx"]
    assert "Задача «" in bot.edits[-1], bot.edits[-1]
    hist = await db.all("SELECT kind FROM events WHERE task_id=? ORDER BY id", (ft["id"],))
    assert [h["kind"] for h in hist[:2]] == ["created", "assigned"], hist
    await inbox.flush(bot, 1, 8101, 8103)                  # already used: nothing happens
    assert len(bot.sent) == n_sent + 1
    # two projects: ask project, then assignee; stranger's button does nothing; «Без исполнителя» creates the task
    await db.upsert_project(-1009999, "Второй")
    await db.save_message(rec(8104, chat_id=1, kind="photo", file_id="fwp2", text="и кнопку"))
    n_dm = len(bot.dms)
    await inbox.flush(bot, 1, 8104, 8104)
    assert "В какой проект" in bot.dms[-1][1] and len(bot.dms) == n_dm + 1, bot.dms[-1]
    assert await inbox.on_choice(bot, 3, 1, f"fw:{CHAT}:8104:8104") == "Вы не участник этого проекта"
    assert await inbox.on_choice(bot, 1, 1, f"fw:{CHAT}:8104:8104") == "Выберите исполнителя"
    assert "исполнител" in bot.edits[-1].lower(), bot.edits[-1]
    assert await inbox.on_assignee(bot, 3, 1, f"fa:{CHAT}:8104:8104:0") == "Вы не участник этого проекта"
    assert await inbox.on_assignee(bot, 1, 1, f"fa:{CHAT}:8104:8104:5") == "Нет такого участника"
    assert await inbox.on_assignee(bot, 1, 1, f"fa:{CHAT}:8104:8104:0") == "Создаю задачу"
    await asyncio.sleep(0.05)
    assert (await db.message(1, 8104))["task_id"]
    ft2 = await db.task((await db.message(1, 8104))["task_id"])
    assert ft2["assignee_id"] is None, ft2
    assert await inbox.on_assignee(bot, 1, 1, f"fa:{CHAT}:8104:8104:0") == "Уже разобрано"
    await db.remove_project(-1009999)
    # «Без проекта»: no assignee step — the task is yours
    await db.upsert_project(1, "Без проекта")
    await db.save_message(rec(8105, chat_id=1, text="Поменяйте кнопку на зелёную"))
    await db.save_message(rec(8106, chat_id=1, kind="photo", file_id="fwp3", text="вот"))
    n_sent = len(bot.sent)
    await inbox.flush(bot, 1, 8105, 8106)                  # two projects incl. personal → ask which
    assert "В какой проект" in bot.dms[-1][1], bot.dms[-1]
    assert await inbox.on_choice(bot, 1, 1, f"fw:1:8105:8106") == "Создаю задачу"
    await asyncio.sleep(0.05)
    pt = await db.task((await db.message(1, 8105))["task_id"])
    assert pt["chat_id"] == 1 and pt["assignee_id"] == 1 and pt["assignee_name"] == "Иван", pt
    assert len(bot.sent) == n_sent, "no checklist in a chat for «Без проекта»"
    await db.update_task(ft["id"], assignee_id=None, assignee_name=None)   # don't leave Олег on a task for later «outsiders» checks
    await db.remove_project(1)
    await db.revoke_access(CHAT, 2)

    # agent adds items to a task
    r = await client.post(f"/agent/tasks/{t['id']}/items", json={"items": ["Включить скидки 13 ноября", " "]}, headers=A)
    assert r.status == 200 and (await r.json())["items"][-1]["t"] == "Включить скидки 13 ноября", await r.text()
    assert (await client.post(f"/agent/tasks/{t['id']}/items", json={"items": []}, headers=A)).status == 400
    assert (await client.post(f"/agent/tasks/{t['id']}/items", json={"items": ["x"]})).status == 401

    # agent parity: description, title, report, archive, comments, items edit/reorder/delete, search, projects
    r = await client.post(f"/agent/projects/{CHAT}/tasks",
                          json={"title": "Паритет", "items": ["а", "б", "в"], "descr": "  Макет https://x  "}, headers=A)
    assert r.status == 200, await r.text()
    ap = await r.json()
    assert ap["descr"] == "Макет https://x" and ap["author"] == "Агент" and any(e["kind"] == "descr" for e in ap["history"]), ap
    iid_a, iid_b, iid_c = [i["id"] for i in ap["items"]]
    r = await client.patch(f"/agent/tasks/{ap['id']}", json={"title": "Паритет 2", "descr": ""}, headers=A)
    j = await r.json()
    assert j["title"] == "Паритет 2" and j["descr"] == "", j
    kinds = [e["kind"] for e in j["history"][:3]]
    assert "renamed" in kinds and "descr" in kinds, kinds
    r = await client.patch(f"/agent/items/{iid_a}", json={"text": "альфа"}, headers=A)
    assert (await r.json())["items"][0]["t"] == "альфа" and (await r.json())["history"][0]["kind"] == "item_edited"
    r = await client.put(f"/agent/tasks/{ap['id']}/order", json={"item_ids": [iid_c, iid_a, iid_b]}, headers=A)
    assert [i["id"] for i in (await r.json())["items"]] == [iid_c, iid_a, iid_b]
    r = await client.delete(f"/agent/items/{iid_b}", headers=A)
    assert [i["id"] for i in (await r.json())["items"]] == [iid_c, iid_a] and (await r.json())["history"][0]["kind"] == "item_deleted"
    r = await client.post(f"/agent/tasks/{ap['id']}/comments", json={"text": "Как макет?", "question": True}, headers=A)
    j = await r.json()
    qc = j["comments"][-1]
    assert qc["question"] and qc["author"] == "Агент" and j["history"][0]["kind"] == "asked", j
    r = await client.post(f"/agent/comments/{qc['id']}/resolve", json={"resolved": True}, headers=A)
    assert (await r.json())["comments"][-1]["resolved"] and (await r.json())["history"][0]["kind"] == "answered"
    r = await client.patch(f"/agent/comments/{qc['id']}", json={"text": "Какой срок?"}, headers=A)
    assert (await r.json())["comments"][-1]["text"] == "Какой срок?" and (await r.json())["comments"][-1]["edited"]
    # a human's comment the agent must not edit
    hum = await (await client.post(f"/api/tasks/{ap['id']}/comments", json={"text": "человечий"}, headers=H)).json()
    hid = hum["comments"][-1]["id"]
    assert (await client.patch(f"/agent/comments/{hid}", json={"text": "x"}, headers=A)).status == 403
    assert (await client.delete(f"/agent/comments/{hid}", headers=A)).status == 403
    await client.delete(f"/api/comments/{hid}", headers=H)
    await client.delete(f"/agent/comments/{qc['id']}", headers=A)
    r = await client.post(f"/agent/tasks/{ap['id']}/complete",
                          json={"result_url": "https://example.com/r", "hours": 1.5, "result_note": "готово"}, headers=A)
    j = await r.json()
    assert j["closed"] and j["result_url"] == "https://example.com/r" and j["hours"] == 1.5 and j["result_note"] == "готово", j
    assert any(e["kind"] == "report" for e in j["history"]), j["history"][:3]
    r = await client.patch(f"/agent/tasks/{ap['id']}", json={"archived": True}, headers=A)
    assert (await r.json())["archived"] and (await r.json())["history"][0]["kind"] == "archived"
    sr = await (await client.get("/agent/search", params={"q": "Паритет"}, headers=A)).json()
    assert sr["total"]["task"] >= 1 and any(x["task_id"] == ap["id"] for x in sr["groups"]["task"]), sr
    agp = await (await client.post("/agent/projects", json={"name": "  Агентский  "}, headers=A)).json()
    assert agp["name"] == "Агентский" and agp["standalone"] and agp["id"] <= -2 * 10**15, agp
    ren = await (await client.patch(f"/agent/projects/{agp['id']}", json={"name": "Агент 2"}, headers=A)).json()
    assert ren["name"] == "Агент 2"
    assert (await client.patch(f"/agent/projects/{CHAT}", json={"name": "x"}, headers=A)).status == 400
    assert (await client.delete(f"/agent/tasks/{ap['id']}", headers=A)).status == 200
    assert await db.task(ap["id"]) is None
    # cleanup agent-owned standalone (no human maker to call DELETE /api/projects)
    await db.x("DELETE FROM project_access WHERE chat_id=?", (agp["id"],))
    await db.remove_project(agp["id"])

    # the site outside Telegram: a one-time code → «Войти» in the bot → a session that works like initData
    from app import web as appweb
    lg = await (await client.post("/api/login/start")).json()
    assert lg["link"].endswith("?start=login_" + lg["token"]), lg
    assert (await (await client.get("/api/login/poll", params={"token": lg["token"]})).json())["status"] == "pending"
    assert await appweb.login_confirmed(lg["token"], 1, "Иван")
    assert not await appweb.login_confirmed(lg["token"], 2, "Чужой"), "a code is confirmed once"
    ok = await (await client.get("/api/login/poll", params={"token": lg["token"]})).json()
    assert ok["status"] == "ok" and ok["user"]["id"] == 1 and ok["session"], ok
    assert (await client.get("/api/login/poll", params={"token": lg["token"]})).status == 410, "the code is used up"
    S = {"Authorization": "Session " + ok["session"]}
    assert any(p["id"] == CHAT for p in await (await client.get("/api/projects", headers=S)).json())
    assert (await (await client.get("/api/me", headers=S)).json())["id"] == 1
    assert (await client.get("/api/projects", headers={"Authorization": "Session nope"})).status == 401
    await client.post("/api/logout", headers=S)
    assert (await client.get("/api/projects", headers=S)).status == 401, "signed out"
    assert (await client.get("/api/login/poll", params={"token": "nope"})).status == 410

    # the alpha's waiting list (the landing's button → t.me/<bot>?start=waitlist): once per person, a place in the queue
    # links can carry a second (collectible) username of the bot
    from app.render import miniapp_url
    from app.config import settings as cfg
    object.__setattr__(cfg, "bot_link_username", "todogram_app")
    assert miniapp_url("tg_checklists_proto_bot", "task_1") == "https://t.me/todogram_app?startapp=task_1"
    assert "https://t.me/todogram_app?start=waitlist" in await (await client.get("/landing")).text()
    object.__setattr__(cfg, "bot_link_username", "")
    assert await db.join_waitlist(501, "anna", "Анна") is True
    assert await db.join_waitlist(502, None, "Борис") is True
    assert await db.join_waitlist(501, "anna", "Анна") is False, "one application per user"
    assert "?start=waitlist" in await (await client.get("/landing")).text()
    # a new person on the list: they get their place, the admin (ADMIN_ID) a short note; again — no note
    from types import SimpleNamespace
    from app import handlers
    from app.config import settings
    object.__setattr__(settings, "admin_id", 77); said = []
    async def answer(text, **kw): said.append(text)
    msg = lambda uid: SimpleNamespace(from_user=SimpleNamespace(id=uid, username="vera", full_name="Вера"), answer=answer)
    fb = FakeBot(); n_dm = len(fb.dms)
    await handlers.join_waitlist(msg(503), fb)
    assert "Вы в листе ожидания" in said[-1] and fb.dms[n_dm:] == [(77, "<b>Заявка в альфу</b>\nВера · @vera")], fb.dms[n_dm:]
    await handlers.join_waitlist(msg(503), fb)
    assert "уже в листе" in said[-1] and len(fb.dms) == n_dm + 1, "joining twice — no second note"
    object.__setattr__(settings, "closed_alpha", True)
    await db.enable_alpha()
    assert await db.alpha_allowed(1) and await db.alpha_allowed(77) and not await db.alpha_allowed(503)
    unapproved = {"Authorization": "tma " + init_data(503)}
    r = await client.post("/api/projects", json={"name": "test"}, headers=unapproved)
    assert r.status == 403 and (await r.json())["error"] == "alpha_required"
    assert (await client.get("/api/projects", headers=H)).status == 200
    cq = SimpleNamespace(data="alpha:approve:503", from_user=SimpleNamespace(id=77), answer=answer,
                         message=SimpleNamespace(chat=SimpleNamespace(id=77), answer=answer))
    await handlers.approve_alpha(cq, fb)
    await handlers.approve_alpha(cq, fb)
    assert len([dm for dm in fb.dms[n_dm:] if dm[0] == 503]) == 1
    assert (await client.get("/api/projects", headers=unapproved)).status == 200
    object.__setattr__(settings, "closed_alpha", False)
    object.__setattr__(settings, "admin_id", 0)
    r = await client.get("/", headers={"Host": "todogram.pro"})
    assert "Todogram" in await r.text(), "the service's domain opens the landing"
    assert "<title>Задачи</title>" in await (await client.get("/?demo", headers={"Host": "todogram.pro"})).text()
    r = await client.get("/landing")
    page = await r.text()
    assert r.status == 200 and "Todogram" in page and 'src="/?demo&amp;projects"' in page and "__BOT_LINK__" not in page
    r = await client.get("/")
    page = await r.text()
    assert r.status == 200 and "<title>Задачи</title>" in page and 'rel="icon"' in page
    # the page carries its version; /api/version says which one is on the server now (pull to refresh compares)
    v = (await (await client.get("/api/version")).json())["v"]
    assert len(v) == 12 and f'const BUILD = "{v}"' in page and "__BUILD__" not in page
    # styles and script are their own files, asked for with the page's version: cached for a year, gzipped
    assert f'static/app.css?v={v}' in page and f'static/app.js?v={v}' in page
    js = await client.get(f"/static/app.js?v={v}", headers={"Accept-Encoding": "gzip"})
    assert js.status == 200 and "javascript" in js.headers["Content-Type"] and "immutable" in js.headers["Cache-Control"]
    assert js.headers.get("Content-Encoding") == "gzip" and "function renderTask(" in await js.text()
    css = await client.get("/static/app.css?v=old")
    assert css.status == 200 and "text/css" in css.headers["Content-Type"] and "immutable" not in css.headers["Cache-Control"]
    # order of tasks: the given ones swap their places, the others keep theirs, a new task lands on top
    before = [t["id"] for t in await (await client.get(f"/api/projects/{CHAT}/tasks", headers=H)).json()]
    assert len(before) >= 2, before
    a, b = before[0], before[-1]
    after = [t["id"] for t in await (await client.put(f"/api/projects/{CHAT}/order", json={"task_ids": [b, a]}, headers=H)).json()]
    assert after == [b, *before[1:-1], a], (before, after)
    assert (await client.put(f"/api/projects/{CHAT}/order", json={"task_ids": [a, b]},
                             headers={"Authorization": "tma " + init_data(2)})).status == 404
    nt = await (await client.post(f"/api/projects/{CHAT}/tasks", json={"title": "Свежая"}, headers=H)).json()
    assert [t["id"] for t in await (await client.get(f"/api/projects/{CHAT}/tasks", headers=H)).json()][:2] == [nt["id"], b]

    # access without the chat: the invite link lets user 3 into the project, removing them shuts it again
    G = {"Authorization": "tma " + init_data(3)}
    assert (await client.get(f"/api/projects/{CHAT}/tasks", headers=G)).status == 404
    assert (await client.get(f"/api/projects/{CHAT}/access", headers=G)).status == 404
    acc = await (await client.get(f"/api/projects/{CHAT}/access", headers=H)).json()
    assert "startapp=join_" in acc["link"] and acc["people"] == [], acc
    tok = acc["link"].split("join_")[1]
    assert (await client.post("/api/join", json={"token": "nope"}, headers=G)).status == 404
    n_sent = len(bot.sent)
    j = await (await client.post("/api/join", json={"token": tok}, headers=G)).json()
    assert j["id"] == CHAT and len(bot.sent) == n_sent + 1 and "ссылку-приглашение" in bot.sent[-1]
    await client.post("/api/join", json={"token": tok}, headers=G)
    assert len(bot.sent) == n_sent + 1, "a second open of the link is not announced again"
    assert (await client.post("/api/join", json={"token": tok}, headers=H)).status == 200
    assert [p["id"] for p in (await (await client.get(f"/api/projects/{CHAT}/access", headers=H)).json())["people"]] == [3], \
        "a chat member opening the link is not added"
    assert any(p["id"] == CHAT for p in await (await client.get("/api/projects", headers=G)).json())
    gt = await (await client.get(f"/api/projects/{CHAT}/tasks", headers=G)).json()
    assert gt and (await client.get(f"/api/tasks/{gt[0]['id']}", headers=G)).status == 200
    new = await (await client.post(f"/api/projects/{CHAT}/access/reset", headers=G)).json()
    assert new["link"] != acc["link"]
    assert (await client.post("/api/join", json={"token": tok}, headers={"Authorization": "tma " + init_data(4)})).status == 404
    left = await (await client.delete(f"/api/projects/{CHAT}/access/3", headers=H)).json()
    assert left["people"] == []
    assert (await client.get(f"/api/projects/{CHAT}/tasks", headers=G)).status == 404

    # «Задачи людей»: every visible project, who does what; «мои» always first
    await db.grant_access(CHAT, 2, "Олег")
    t1 = await (await client.post(f"/api/projects/{CHAT}/tasks", json={"title": "Олегу", "assignee": 2}, headers=H)).json()
    t2 = await (await client.post(f"/api/projects/{CHAT}/tasks", json={"title": "Мне", "assignee": 1}, headers=H)).json()
    j = await (await client.get("/api/people/tasks", headers=H)).json()
    assert j["me"] == 1 and j["people"][0]["id"] == 1, j["people"]
    oleg = next(p for p in j["people"] if p["id"] == 2)
    assert oleg["name"] == "Олег" and oleg["active"] >= 1, oleg
    row = next(x for x in j["tasks"] if x["id"] == t1["id"])
    assert row["assignee"]["id"] == 2 and row["project"] == "Сайт клиники" and row["color"], row
    assert not any(x["archived"] for x in j["tasks"])

    # «Без проекта»: the person's own space (id = theirs) — only they see it, it is not in the projects list,
    # the task is theirs, nothing is posted to any chat; move works any direction (personal ↔ project ↔ project)
    sent = len(FakeBot.sent)
    r = await client.post("/api/projects/1/tasks", json={"title": "Личное", "items": ["x"]}, headers=H)
    pt = await r.json()
    assert r.status == 200 and pt["pid"] == 1 and pt["assignee"]["id"] == 1 and pt["project"] == "Без проекта", pt
    assert len(FakeBot.sent) == sent, "no checklist in a chat"
    assert not any(p["id"] == 1 for p in await (await client.get("/api/projects", headers=H)).json())
    j = await (await client.get("/api/people/tasks", headers=H)).json()
    assert any(x["id"] == pt["id"] and x["project"] == "Без проекта" for x in j["tasks"])
    O = {"Authorization": "tma " + init_data(2)}
    assert (await client.get(f"/api/tasks/{pt['id']}", headers=O)).status == 404, "someone else's personal task"
    assert (await client.post("/api/projects/1/tasks", json={"title": "чужое"}, headers=O)).status == 404
    assert (await client.post(f"/api/tasks/{pt['id']}/move", json={"project_id": "x"}, headers=H)).status == 400
    assert (await client.post(f"/api/tasks/{pt['id']}/move", json={"project_id": -100999888}, headers=H)).status == 404
    assert (await client.post(f"/api/tasks/{pt['id']}/move", json={"project_id": CHAT}, headers=O)).status == 404
    assert (await client.post(f"/api/tasks/{pt['id']}/move", json={"project_id": 2}, headers=H)).status == 403, "not their personal"
    n_sent = len(FakeBot.sent)
    mv = await (await client.post(f"/api/tasks/{pt['id']}/move", json={"project_id": CHAT}, headers=H)).json()
    assert mv["pid"] == CHAT and mv["project"] == "Сайт клиники" and mv["history"][0]["kind"] == "moved", mv
    ex = mv["history"][0]["extra"]
    assert ex["from"] == 1 and ex["to"] == CHAT and ex["from_project"] == "Без проекта" and ex["project"] == "Сайт клиники", ex
    assert (await db.task(pt["id"]))["checklist_msg_id"] and len(FakeBot.sent) == n_sent + 1 and "Личное" in FakeBot.sent[-1]
    assert (await client.post(f"/api/tasks/{pt['id']}/move", json={"project_id": CHAT}, headers=H)).status == 400, "already there"
    # project → personal (null): checklist retired, nothing posted
    old_msg = (await db.task(pt["id"]))["checklist_msg_id"]
    n_del = len(FakeBot.deleted)
    back = await (await client.post(f"/api/tasks/{pt['id']}/move", json={"project_id": None}, headers=H)).json()
    assert back["pid"] == 1 and back["project"] == "Без проекта" and back["history"][0]["kind"] == "moved", back
    assert back["history"][0]["extra"]["from"] == CHAT and back["history"][0]["extra"]["to"] == 1
    assert (await db.task(pt["id"]))["checklist_msg_id"] is None
    assert old_msg in FakeBot.deleted or any("перенесена" in (e or "") for e in FakeBot.edits[-5:]), (FakeBot.deleted[-3:], FakeBot.edits[-5:])
    # comments / items survive the moves
    await client.post(f"/api/tasks/{pt['id']}/comments", json={"text": "останется"}, headers=H)
    await client.post(f"/api/tasks/{pt['id']}/move", json={"project_id": CHAT}, headers=H)
    kept = await (await client.get(f"/api/tasks/{pt['id']}", headers=H)).json()
    assert kept["pid"] == CHAT and any(c["text"] == "останется" for c in kept["comments"]) and [i["t"] for i in kept["items"]] == ["x"]
    # agent: personal → project, project → personal (null → author)
    pt2 = await (await client.post("/api/projects/1/tasks", json={"title": "Ещё личное", "items": ["y"]}, headers=H)).json()
    n_sent = len(FakeBot.sent)
    ag = await (await client.post(f"/agent/tasks/{pt2['id']}/move", json={"project_id": CHAT}, headers=A)).json()
    assert ag["pid"] == CHAT and ag["history"][0]["kind"] == "moved" and ag["history"][0]["agent"] is True, ag
    assert len(FakeBot.sent) == n_sent + 1
    ag2 = await (await client.post(f"/agent/tasks/{pt2['id']}/move", json={"project_id": None}, headers=A)).json()
    assert ag2["pid"] == 1 and ag2["project"] == "Без проекта" and ag2["history"][0]["extra"]["from"] == CHAT, ag2
    # chat → chat: old checklist deleted, new one posted
    await db.upsert_project(-100555, "Другой чат")
    await db.grant_access(-100555, 1, "Иван")
    old_msg = (await db.task(pt["id"]))["checklist_msg_id"]
    n_sent = len(FakeBot.sent)
    cross = await (await client.post(f"/api/tasks/{pt['id']}/move", json={"project_id": -100555}, headers=H)).json()
    assert cross["pid"] == -100555 and cross["history"][0]["extra"]["from"] == CHAT, cross
    assert cross["history"][0]["extra"]["from_project"] == "Сайт клиники" and "Другой" in cross["history"][0]["text"]
    assert (await db.task(pt["id"]))["checklist_msg_id"] and len(FakeBot.sent) == n_sent + 1
    assert old_msg in FakeBot.deleted or any("перенесена" in (e or "") for e in FakeBot.edits[-5:])
    assert (await client.post(f"/api/tasks/{pt['id']}/move", json={"project_id": -100555}, headers=O)).status in (403, 404)
    # back to CHAT before dropping the temp project, then clean up
    await client.post(f"/api/tasks/{pt['id']}/move", json={"project_id": CHAT}, headers=H)
    await db.x("DELETE FROM project_access WHERE chat_id=?", (-100555,))
    await db.remove_project(-100555)
    for x in (pt, pt2, t1, t2):
        assert (await client.delete(f"/api/tasks/{x['id']}", headers=H)).status == 200

    # a project without a chat: made in the app, seen by its maker and whoever comes by its link, nothing posted
    SA, SB = {"Authorization": "tma " + init_data(5)}, {"Authorization": "tma " + init_data(6)}
    assert (await client.post("/api/projects", json={"name": "  "}, headers=SA)).status == 400
    sp = await (await client.post("/api/projects", json={"name": "  Личные   дела "}, headers=SA)).json()
    assert sp["name"] == "Личные дела" and sp["standalone"] and sp["id"] <= -2 * 10**15, sp
    sp2 = await (await client.post("/api/projects", json={"name": "Второй"}, headers=SA)).json()
    assert sp2["id"] == sp["id"] - 1
    mine = {p["id"]: p for p in await (await client.get("/api/projects", headers=SA)).json()}
    assert mine[sp["id"]]["standalone"] and mine[sp["id"]]["bot_admin"] is True and mine[sp["id"]]["photo"] is None
    assert not any(p["id"] == sp["id"] for p in await (await client.get("/api/projects", headers=H)).json())
    # personal order of projects: each user has their own; new ones without a place land on top
    before = [p["id"] for p in await (await client.get("/api/projects", headers=SA)).json()]
    assert before[:2] == [sp["id"], sp2["id"]] or set(before[:2]) == {sp["id"], sp2["id"]}, before
    a, b = sp["id"], sp2["id"]
    ordered = await (await client.put("/api/projects/order", json={"project_ids": [b, a]}, headers=SA)).json()
    assert [p["id"] for p in ordered if p["id"] in (a, b)] == [b, a], ordered
    assert [p["id"] for p in await (await client.get("/api/projects", headers=SA)).json() if p["id"] in (a, b)] == [b, a]
    # another user does not see that order; bad ids and strangers' projects are ignored
    assert (await client.put("/api/projects/order", json={"project_ids": [b, a]}, headers=SB)).status == 200
    assert (await client.put("/api/projects/order", json={"project_ids": ["x"]}, headers=SA)).status == 400
    sp3 = await (await client.post("/api/projects", json={"name": "Свежий"}, headers=SA)).json()
    top = [p["id"] for p in await (await client.get("/api/projects", headers=SA)).json()]
    assert top[0] == sp3["id"] and top[1:3] == [b, a], top
    await client.delete(f"/api/projects/{sp3['id']}", headers=SA)
    assert (await client.get(f"/api/projects/{sp['id']}/tasks", headers=SB)).status == 404
    sent = len(FakeBot.sent)
    st = await (await client.post(f"/api/projects/{sp['id']}/tasks", json={"title": "Купить лампу", "items": ["E27"]}, headers=SA)).json()
    assert st["pid"] == sp["id"] and len(FakeBot.sent) == sent and not (await db.task(st["id"]))["checklist_msg_id"]
    acc = await (await client.get(f"/api/projects/{sp['id']}/access", headers=SA)).json()
    assert acc["people"] == [], "the maker is not listed as let in"
    assert (await client.delete(f"/api/projects/{sp['id']}/access/5", headers=SA)).status == 400
    n_dm = len(FakeBot.dms)
    j = await (await client.post("/api/join", json={"token": acc["link"].split("join_")[1]}, headers=SB)).json()
    assert j["id"] == sp["id"] and len(FakeBot.sent) == sent, "no chat to announce in"
    assert FakeBot.dms[n_dm][0] == 5 and "«Личные дела»" in FakeBot.dms[n_dm][1], FakeBot.dms[n_dm:]
    assert (await client.get(f"/api/tasks/{st['id']}", headers=SB)).status == 200
    assert (await client.patch(f"/api/projects/{sp['id']}", json={"name": "Дом"}, headers=SB)).status == 200
    assert next(p for p in await (await client.get("/api/projects", headers=SA)).json() if p["id"] == sp["id"])["name"] == "Дом"
    assert (await client.patch(f"/api/projects/{CHAT}", json={"name": "x"}, headers=H)).status == 400, "a chat is named by /project"
    assert (await client.patch(f"/api/projects/{sp2['id']}", json={"name": "x"}, headers=SB)).status == 404
    await client.delete(f"/api/projects/{sp['id']}/access/6", headers=SA)
    assert (await client.get(f"/api/projects/{sp['id']}/tasks", headers=SB)).status == 404
    from app.inbox import my_projects
    assert {p["chat_id"] for p in await my_projects(bot, 5)} >= {sp["id"], sp2["id"]}, "forwarding offers it"
    assert mine[sp["id"]]["owner"] is True
    await client.post(f"/api/join", json={"token": (await (await client.get(f"/api/projects/{sp['id']}/access", headers=SA)).json())["link"].split("join_")[1]}, headers=SB)
    assert next(p for p in await (await client.get("/api/projects", headers=SB)).json() if p["id"] == sp["id"])["owner"] is False
    assert (await client.delete(f"/api/projects/{sp['id']}", headers=SB)).status == 403, "only the maker deletes"
    assert (await client.delete(f"/api/projects/{CHAT}", headers=H)).status == 403, "a chat's project is not deleted from the app"
    d = await (await client.delete(f"/api/projects/{sp['id']}", headers=SA)).json()
    assert d == {"deleted": sp["id"], "tasks": 1}, d
    assert await db.task(st["id"]) is None and not await db.project(sp["id"]) and not await db.has_access(sp["id"], 6)
    assert (await client.get(f"/api/projects/{sp['id']}/tasks", headers=SA)).status == 404
    assert (await client.delete(f"/api/projects/{sp['id']}", headers=SA)).status == 404
    await client.delete(f"/api/projects/{sp2['id']}", headers=SA)

    # «Связанные задачи»: the same thing in two projects; each person sees only what they may
    pa = await (await client.post("/api/projects", json={"name": "R&D"}, headers=SA)).json()
    pb = await (await client.post("/api/projects", json={"name": "Сайт"}, headers=SB)).json()
    ta = await (await client.post(f"/api/projects/{pa['id']}/tasks", json={"title": "Добавить подарочные сертификаты"}, headers=SA)).json()
    tb = await (await client.post(f"/api/projects/{pb['id']}/tasks", json={"title": "Электронные подарочные сертификаты к Новому году"}, headers=SB)).json()
    tz = await (await client.post(f"/api/projects/{pb['id']}/tasks", json={"title": "Поменять шрифт"}, headers=SB)).json()
    rel = await (await client.get(f"/api/tasks/{ta['id']}/related", headers=SA)).json()
    assert tb["id"] not in [x["id"] for x in rel["rows"]], "not in that project yet"
    acc = await (await client.get(f"/api/projects/{pb['id']}/access", headers=SB)).json()
    await client.post("/api/join", json={"token": acc["link"].split("join_")[1]}, headers=SA)
    rel = await (await client.get(f"/api/tasks/{ta['id']}/related", headers=SA)).json()
    ids = [x["id"] for x in rel["rows"]]
    assert rel["similar"] and ids and ids[0] == tb["id"] and tz["id"] not in ids and ta["id"] not in ids, rel
    assert rel["rows"][0]["project"] == "Сайт" and rel["rows"][0]["status"] == "open", rel["rows"][0]
    rel = await (await client.get(f"/api/tasks/{ta['id']}/related?q=шрифт", headers=SA)).json()
    assert [x["id"] for x in rel["rows"]] == [tz["id"]] and not rel["similar"], rel
    assert (await client.post(f"/api/tasks/{ta['id']}/links", json={"id": ta["id"]}, headers=SA)).status == 400
    assert (await client.post(f"/api/tasks/{ta['id']}/links", json={"id": "x"}, headers=SA)).status == 400
    la = await (await client.post(f"/api/tasks/{ta['id']}/links", json={"id": tb["id"]}, headers=SA)).json()
    assert [x["id"] for x in la["links"]] == [tb["id"]] and la["links_hidden"] == 0, la["links"]
    assert la["history"][0]["kind"] == "linked" and la["history"][0]["text"] == tb["title"], la["history"][0]
    again = await (await client.post(f"/api/tasks/{ta['id']}/links", json={"id": tb["id"]}, headers=SA)).json()
    assert len(again["links"]) == 1 and sum(e["kind"] == "linked" for e in again["history"]) == 1, "linking twice is one link"
    lb = await (await client.get(f"/api/tasks/{tb['id']}", headers=SA)).json()
    assert [x["id"] for x in lb["links"]] == [ta["id"]] and lb["links"][0]["project"] == "R&D"
    rel = await (await client.get(f"/api/tasks/{ta['id']}/related", headers=SA)).json()
    assert tb["id"] not in [x["id"] for x in rel["rows"]], "linked already"
    vb = await (await client.get(f"/api/tasks/{tb['id']}", headers=SB)).json()   # 6 is not in R&D
    assert vb["links"] == [] and vb["links_hidden"] == 1, vb["links"]
    ev = next(e for e in vb["history"] if e["kind"] == "linked")
    assert ev["text"] is None and ev["extra"] == {"hidden": True}, ev
    assert (await client.post(f"/api/tasks/{tb['id']}/links", json={"id": ta["id"]}, headers=SB)).status == 404
    assert (await client.get(f"/api/tasks/{ta['id']}/related", headers=SB)).status == 404
    ag = await (await client.get(f"/agent/tasks/{tb['id']}", headers=A)).json()
    assert [x["id"] for x in ag["links"]] == [ta["id"]], "the agent sees the link"
    ub = await (await client.delete(f"/api/tasks/{tb['id']}/links/{ta['id']}", headers=SB)).json()
    assert ub["links"] == [] and ub["links_hidden"] == 0 and ub["history"][0]["kind"] == "unlinked"
    assert (await (await client.get(f"/api/tasks/{ta['id']}", headers=SA)).json())["links"] == []
    await client.post(f"/api/tasks/{ta['id']}/links", json={"id": tz["id"]}, headers=SA)
    await client.delete(f"/api/tasks/{tz['id']}", headers=SB)
    assert not await db.all("SELECT 1 FROM task_links WHERE a=? OR b=?", (tz["id"], tz["id"])), "a deleted task leaves no links"
    for pid_, hh in ((pa["id"], SA), (pb["id"], SB)):
        await client.delete(f"/api/projects/{pid_}", headers=hh)

    # assignee from another of my projects: offered with where they're from; picking them lets them in
    CHAT2 = -1009990001
    await db.upsert_project(CHAT2, "Магазин")
    await db.grant_access(CHAT2, 7, "Маша")
    ppl = await (await client.get(f"/api/projects/{CHAT}/people", headers=H)).json()
    masha = next(p for p in ppl if p["id"] == 7)
    assert masha["outside"] and masha["via"] == "Магазин", masha
    assert not any(p.get("outside") for p in ppl if p["id"] in (1, 2)), ppl
    O = {"Authorization": "tma " + init_data(2)}          # Олег sees only this project: Маша is a stranger to him
    t3 = await (await client.post(f"/api/projects/{CHAT}/tasks", json={"title": "Маше"}, headers=H)).json()
    assert (await client.patch(f"/api/tasks/{t3['id']}", json={"assignee": 7}, headers=O)).status == 400
    assert not await db.has_access(CHAT, 7)
    n_dm = len(bot.dms)
    j = await (await client.patch(f"/api/tasks/{t3['id']}", json={"assignee": 7}, headers=H)).json()
    assert j["assignee"]["id"] == 7 and not await db.has_access(CHAT, 7), j          # the task, not the project
    assert bot.dms[n_dm][0] == 7 and "только к ней" in bot.dms[n_dm][1], bot.dms[n_dm:]
    M = {"Authorization": "tma " + init_data(7)}
    r = await client.get(f"/api/tasks/{t3['id']}", headers=M)
    assert r.status == 200 and (await r.json())["guest"], await r.text()
    assert "guest" not in await (await client.get(f"/api/tasks/{t3['id']}", headers=H)).json()
    assert (await client.get(f"/api/projects/{CHAT}/tasks", headers=M)).status == 404
    other = next(x for x in await (await client.get(f"/api/projects/{CHAT}/tasks", headers=H)).json() if x["id"] != t3["id"])
    assert (await client.get(f"/api/tasks/{other['id']}", headers=M)).status == 404               # not the others
    assert (await client.patch(f"/api/tasks/{t3['id']}", json={"status": "progress"}, headers=M)).status == 200
    assert (await client.patch(f"/api/tasks/{t3['id']}", json={"assignee": None}, headers=M)).status == 403
    assert (await client.patch(f"/api/tasks/{t3['id']}", json={"archived": True}, headers=M)).status == 403
    assert (await client.delete(f"/api/tasks/{t3['id']}", headers=M)).status == 403
    mine = await (await client.get("/api/people/tasks", headers=M)).json()
    assert [x["id"] for x in mine["tasks"] if x["pid"] == CHAT] == [t3["id"]], mine["tasks"]
    ppl = await (await client.get(f"/api/projects/{CHAT}/people", headers=H)).json()
    assert next(p for p in ppl if p["id"] == 7).get("outside"), ppl                       # still not in the project
    t4 = await (await client.post(f"/api/projects/{CHAT}/tasks", json={"title": "Тоже Маше", "assignee": 7}, headers=H)).json()
    assert t4["assignee"]["id"] == 7 and not await db.has_access(CHAT, 7), t4                 # at creation too
    await client.patch(f"/api/tasks/{t3['id']}", json={"assignee": None}, headers=H)
    assert (await client.get(f"/api/tasks/{t3['id']}", headers=M)).status == 404             # no longer theirs
    assert (await client.get(f"/api/tasks/{t4['id']}", headers=M)).status == 200
    ppl = await (await client.get(f"/agent/projects/{CHAT}/people", headers=A)).json()
    assert next(p for p in ppl if p["id"] == 7)["outside"] == "Магазин", ppl
    for x in (t3, t4):
        assert (await client.delete(f"/api/tasks/{x['id']}", headers=H)).status == 200
    await db.revoke_access(CHAT2, 7)
    await db.revoke_access(CHAT, 2)

    # polling: the same answer again is a 304; text goes gzipped; the page is served with an ETag too
    r = await client.get("/api/projects", headers=H)
    tag = r.headers.get("ETag")
    assert r.status == 200 and tag, r.headers
    r = await client.get("/api/projects", headers={**H, "If-None-Match": tag})
    assert r.status == 304 and await r.read() == b"", r.status
    r = await client.get(f"/api/projects/{CHAT}/tasks", headers={**H, "If-None-Match": tag})
    assert r.status == 200                                             # another answer, another tag
    r = await client.get("/", headers={"Accept-Encoding": "gzip"}, auto_decompress=False)
    assert r.status == 200 and r.headers.get("Content-Encoding") == "gzip" and r.headers.get("ETag"), r.headers
    page = await (await client.get("/")).text()
    assert "static/fonts/tiktok-sans-cyrillic.woff2" in page and "fonts.googleapis" not in page
    r = await client.get("/static/fonts/tiktok-sans-cyrillic.woff2")
    assert r.content_type == "font/woff2", r.content_type
    assert r.status == 200 and "immutable" in r.headers.get("Cache-Control", ""), r.headers

    from app import web as webmod
    # «Уведомления»: what other people did in my tasks, by section, with unread numbers
    webmod.note_member(CHAT, 50, True)
    U = {"Authorization": "tma " + init_data(50)}
    nt = await (await client.post(f"/api/projects/{CHAT}/tasks", json={"title": "Про уведомления"}, headers=H)).json()
    async def notif(hd, q=""):
        r = await client.get(f"/api/notifications{q}", headers=hd)
        assert r.status == 200, await r.text()
        return await r.json()
    base = (await notif(H))["unread"]
    qj = await (await client.post(f"/api/tasks/{nt['id']}/comments", json={"text": "Когда сдаём?", "question": True}, headers=U)).json()
    await client.post(f"/api/tasks/{nt['id']}/comments", json={"text": "Посмотрел"}, headers=U)
    j = await notif(H)
    assert j["unread"]["q"] == base["q"] + 1 and j["unread"]["c"] == base["c"] + 1, (base, j["unread"])
    assert j["events"][0]["kind"] == "commented" and j["events"][0]["task"] == "Про уведомления", j["events"][:2]
    assert not any(e["kind"] == "created" and e["task_id"] == nt["id"] for e in j["events"]), "my own changes are not news to me"
    qs = await notif(H, "?section=q")
    assert qs["events"][0]["kind"] == "asked" and all(e["kind"] in ("asked", "answered", "reasked") for e in qs["events"]), qs
    assert qs["events"][0]["unread"] is True
    assert (await client.get("/api/notifications?section=nope", headers=H)).status == 400
    assert (await notif(H, "?section=q"))["unread"]["q"] == base["q"] + 1, "looking at a section does not read it"
    ev = qs["events"][0]["id"]
    r = await (await client.post(f"/api/notifications/{ev}/read", json={"read": True}, headers=H)).json()   # tapped
    assert r["unread"]["q"] == base["q"] and r["unread"]["c"] == base["c"] + 1, r
    assert (await notif(H, "?section=q"))["events"][0]["unread"] is False
    r = await (await client.post(f"/api/notifications/{ev}/read", json={"read": False}, headers=H)).json()  # swiped back
    assert r["unread"]["q"] == base["q"] + 1 and (await notif(H, "?section=q"))["events"][0]["unread"] is True, r
    assert (await client.post(f"/api/notifications/{ev}/read", json={"read": True},
                              headers={"Authorization": "tma " + init_data(2)})).status == 404, "only my own notifications"
    r = await (await client.post("/api/notifications/seen", json={"section": "q", "upto": ev}, headers=H)).json()
    assert r["unread"]["q"] == 0, r
    await client.post(f"/api/notifications/{ev}/read", json={"read": False}, headers=H)   # an old one made unread again
    assert (await notif(H))["unread"]["q"] == 1
    assert (await client.post("/api/notifications/seen", json={"section": "q"}, headers=H)).status == 400
    # the one who asked hears back: the answer comes to their «Вопросы»
    cid = next(c["id"] for c in qj["comments"] if c["question"])
    await client.post(f"/api/comments/{cid}/resolve", json={"resolved": True}, headers=H)
    uq = await notif(U, "?section=q")
    assert uq["events"][0]["kind"] == "answered" and uq["unread"]["q"] >= 1, uq
    await client.patch(f"/api/tasks/{nt['id']}", json={"status": "progress"}, headers=H)
    assert (await notif(U, "?section=s"))["events"][0]["kind"] == "status"
    await client.patch(f"/api/tasks/{nt['id']}", json={"status": "review"}, headers=H)   # «На проверку»: a section of its own
    rv = await notif(U, "?section=r")
    assert rv["events"][0]["extra"]["to"] == "review" and rv["unread"]["r"] == 1, rv
    assert all(e["extra"].get("to") != "review" for e in (await notif(U, "?section=s"))["events"]), "not among the statuses"
    await client.patch(f"/api/tasks/{nt['id']}", json={"status": "progress"}, headers=H)   # back to work after the check
    assert (await notif(U, "?section=r"))["events"][0]["extra"].get("from") == "review"
    after = await (await client.post("/api/notifications/seen", json={"section": "all", "upto": 2 ** 40}, headers=U)).json()
    assert not any(after["unread"].values()), after
    await client.post(f"/api/tasks/{nt['id']}/comments", json={"text": "После «прочитать все»"}, headers=H)
    assert (await notif(U))["unread"]["c"] == 1, "«Прочитать все» does not swallow what comes later"
    assert (await notif({"Authorization": "tma " + init_data(2)}))["events"] == [], "outsiders hear nothing"

    await client.close()

    # ---------- empty result & AI failure free the messages again ----------
    await db.save_message(rec(10, text="спасибо!"))

    async def empty_ai(c, s, ids=None): return {"title": "", "items": []}
    ai.make_checklist = empty_ai
    await tasks.create_task(bot, CHAT, 1, "Иван", [10])
    assert bot.edits[-1] == "Не нашёл задач в этих сообщениях"
    assert (await db.message(CHAT, 10))["task_id"] is None

    async def broken_ai(c, s, ids=None): raise ai.AIError("down")
    ai.make_checklist = broken_ai
    await tasks.create_task(bot, CHAT, 1, "Иван", [10])
    assert bot.edits[-1].startswith("Не получилось")

    # ---------- links in item text ----------
    from app.render import linkify
    long = "https://shop.example.org/catalog/claudio_canzian_italiya/kole_claudio_canzian/kole-kristall-goluboy-medovyy-10kh2-5-sm-45-sm-italiya-claudio-canzian-002-05-664/"
    out = linkify(f"Проверить товар на странице {long}, вернуть <второй>")
    assert out == (f'Проверить товар на странице <a href="{long}">shop.example.org/…/kole-kristall-goluboy-…</a>, '
                   'вернуть &lt;второй&gt;'), out

    # ---------- AI answer parsing ----------
    v = ai._validate(ai._extract_json('```json\n{"title":"T","items":[{"text":"a","screenshot":5,"messages":[7,"8",99]},"b"]}\n```'),
                     shots=2, msg_ids={7, 8})
    assert v == {"title": "T", "items": [{"text": "a", "shot": None, "src": [7, 8]}, {"text": "b", "shot": None, "src": []}], "due": None}, v
    from app import deadlines
    v = ai._validate({"title": "T", "due": "2026-11-30", "items": ["a"]}, shots=0)
    assert v["due"] == deadlines.parse("2026-11-30") and v["due"][1] is True, v
    assert ai._validate({"title": "T", "due": "30.11", "items": ["a"]}, shots=0)["due"] is None
    assert deadlines.parse("2026-11-30T18:00") == deadlines.parse("2026-11-30", "18:00")
    assert deadlines.parse("2026-11-30", "18:00")[1] is False and deadlines.parse("") is None

    # ---------- auto mode ----------
    from app import auto
    CH2 = -1009999
    await db.upsert_project(CH2, "Лендинг")
    await db.save_message(rec(100, chat_id=CH2, text="старое, до включения"))
    await db.set_auto(CH2, True)
    old = int(time.time()) - 3600
    await db.save_message(rec(101, chat_id=CH2, date=old, text="Привет!"))
    await db.save_message(rec(102, chat_id=CH2, date=old, text="Поменяйте цену на капучино на 250"))
    await db.save_message(rec(103, chat_id=CH2, date=old, kind="photo", file_id="p9", text="вот меню"))
    await db.save_message(rec(104, chat_id=CH2, date=old, text="спасибо"))
    await db.save_message(rec(105, chat_id=CH2, date=old, kind="voice", file_id="v1"))

    seen = {}

    async def fake_transcribe(data, name): return "и добавьте сезонное меню"
    ai.transcribe = fake_transcribe

    async def fake_find(transcript, valid, open_ids):
        seen["t"] = transcript
        return [{"type": "new", "task_id": None, "message_ids": [102, 103, 105]}]
    ai.find_tasks = fake_find

    async def menu_ai(content, shots, ids=None):
        return {"title": "Меню и цены", "items": [{"text": "Цена капучино 250", "shot": 1}, {"text": "Сезонное меню", "shot": None}]}
    ai.make_checklist = menu_ai

    sent_before = len(bot.sent)
    await auto.scan_chat(bot, CH2)
    assert "[100]" not in seen["t"].split("Новая переписка")[1], "history before /auto on must not be scanned"
    assert "[103] Анна: [фото] вот меню" in seen["t"] and "[голосовое] и добавьте сезонное меню" in seen["t"], seen["t"]
    assert (await db.message(CH2, 105))["transcript"] == "и добавьте сезонное меню"  # cached, not re-transcribed
    # created right away, without asking: the bot message becomes the checklist
    t2 = (await db.tasks_of_chat(CH2))[0]
    assert t2["title"] == "Меню и цены" and t2["total"] == 2 and t2["auto"] == 1
    assert (await db.message(CH2, 103))["task_id"] == t2["id"]
    assert len(bot.sent) == sent_before + 1 and "Меню и цены" in bot.edits[-1]
    h = await db.events(t2["id"])
    assert h[0]["kind"] == "created" and h[0]["actor"] == auto.BOT_ACTOR

    # nothing new -> second scan does nothing
    await auto.scan_chat(bot, CH2)
    assert len(bot.sent) == sent_before + 1

    # an addition to the open task goes in at once and can be taken back
    await db.save_message(rec(106, chat_id=CH2, date=old + 60, kind="photo", file_id="p10", text="и латте 270"))

    async def find_add(transcript, valid, open_ids):
        assert f"#{t2['id']} Меню и цены" in transcript
        return [{"type": "add", "task_id": t2["id"], "message_ids": [106]}]
    ai.find_tasks = find_add

    async def latte_ai(content, shots, ids=None):
        return {"title": "Латте", "items": [{"text": "Цена латте 270", "shot": 1}]}
    ai.make_checklist = latte_ai
    await auto.scan_chat(bot, CH2)
    assert bot.sent[-1].startswith("Добавил в задачу «Меню и цены»"), bot.sent[-1]
    items = await db.items(t2["id"])
    assert [i["text"] for i in items][-1] == "Цена латте 270" and items[-1]["shot"] == 2, items
    assert [m["idx"] for m in await db.media(t2["id"])] == [1, 2]
    sug2 = await db.one("SELECT id FROM suggestions WHERE chat_id=? ORDER BY id DESC", (CH2,))
    assert await auto.undo_add(bot, sug2["id"], 1, "Иван") == "Отменил"
    assert [i["text"] for i in await db.items(t2["id"])] == ["Цена капучино 250", "Сезонное меню"]
    assert [m["idx"] for m in await db.media(t2["id"])] == [1]
    assert (await db.message(CH2, 106))["task_id"] is None
    assert await auto.undo_add(bot, sug2["id"], 1, "Иван") == "Уже обработано"

    # «Отдельной задачей»: the items leave the open task and become a task of their own
    await db.save_message(rec(110, chat_id=CH2, date=old + 150, text="сделаю квадратный логотип"))
    async def find_add2(transcript, valid, open_ids):
        return [{"type": "add", "task_id": t2["id"], "message_ids": [110]}]
    ai.find_tasks = find_add2
    async def logo_ai(content, shots, ids=None): return {"title": "Квадратный логотип", "items": [{"text": "Сделать квадратный логотип"}]}
    ai.make_checklist = logo_ai
    await auto.scan_chat(bot, CH2)
    n_items = len(await db.items(t2["id"]))
    sug4 = await db.one("SELECT id FROM suggestions WHERE chat_id=? ORDER BY id DESC", (CH2,))
    assert await auto.undo_add(bot, sug4["id"], 1, "Иван", as_new=True) == "Сделал отдельной задачей"
    assert len(await db.items(t2["id"])) == n_items - 1, "items go back out of the open task"
    logo = next(t for t in await db.tasks_of_chat(CH2) if t["title"] == "Квадратный логотип")
    assert (await db.message(CH2, 110))["task_id"] == logo["id"]

    # «Не задача» deletes an automatic task; not once someone has ticked an item
    await db.save_message(rec(111, chat_id=CH2, date=old + 170, text="сделайте что-нибудь"))
    async def find_new(transcript, valid, open_ids):
        return [{"type": "new", "task_id": None, "message_ids": [111]}]
    ai.find_tasks = find_new
    async def any_ai(content, shots, ids=None): return {"title": "Что-нибудь", "items": [{"text": "Сделать что-нибудь"}]}
    ai.make_checklist = any_ai
    await auto.scan_chat(bot, CH2)
    junk = next(t for t in await db.tasks_of_chat(CH2) if t["title"] == "Что-нибудь")
    assert await auto.undo_create(bot, junk["id"], 1, "Иван") == "Убрал"
    assert await db.task(junk["id"]) is None and (await db.message(CH2, 111))["task_id"] is None
    await db.toggle_item_by_pos(logo["id"], 0)
    assert "уже есть отметки" in await auto.undo_create(bot, logo["id"], 1, "Иван")

    # a chat still talking is not scanned until the pause
    await db.save_message(rec(108, chat_id=CH2, text="поменяйте шрифт"))  # date = now
    calls = []

    async def find_spy(*a): calls.append(1); return []
    ai.find_tasks = find_spy
    await auto.scan_chat(bot, CH2)
    assert not calls, "must wait for silence"

    # building a task downloads its files side by side, and keeps their order
    from app import media as media_mod
    real_dl = media_mod.download

    async def slow_dl(b, fid):
        await asyncio.sleep(0.3); return b"\xff\xd8" + fid.encode()
    media_mod.download = slow_dl
    pics = [{"message_id": 500 + i, "user_name": "А", "text": "", "kind": "photo", "file_size": 10, "file_id": f"p{i}",
             "mime": "image/jpeg", "chat_id": CHAT, "file_name": None, "thumb_id": None} for i in range(4)]
    t0 = time.time()
    parts, shots, *_ = await tasks.build_content(bot, pics)
    took = time.time() - t0
    media_mod.download = real_dl
    assert took < 0.8, f"downloads not parallel: {took:.2f}s"
    assert [x["file_id"] for x in shots] == ["p0", "p1", "p2", "p3"] and [x["idx"] for x in shots] == [1, 2, 3, 4], shots

    # cache: files nobody opened for CACHE_DAYS go, opened ones stay; membership seen by the bot needs no API call
    from app import media as tgmedia_mod, web as webmod
    cache = Path(os.environ["CACHE_DIR"]); cache.mkdir(parents=True, exist_ok=True)
    old, fresh = cache / "old.jpg", cache / "fresh.jpg"
    old.write_bytes(b"x" * 10); fresh.write_bytes(b"y")
    os.utime(old, (time.time() - 40 * 86400,) * 2)
    n, size = tgmedia_mod.prune_cache(str(cache), webmod.CACHE_DAYS)
    assert n == 1 and size == 10 and not old.exists() and fresh.exists(), (n, size)
    f = await webmod.cached_file(bot, "chatpic", "chat_", ".jpg")
    os.utime(f, (time.time() - 40 * 86400,) * 2)
    assert await webmod.cached_file(bot, "chatpic", "chat_", ".jpg") == f and time.time() - f.stat().st_mtime < 60  # use refreshes it
    webmod.note_member(-777, 42, True)
    assert await webmod.in_chat(bot, -777, 42)            # FakeBot would say «left» for 42
    webmod.note_member(-777, 42, False)
    assert not await webmod.in_chat(bot, -777, 42)

    await check_chat_upgrade(bot)
    print("ALL OK")


async def run():
    # always close the database: its worker thread would keep a failed run alive forever (and CI with it)
    try:
        await main()
    finally:
        await db.close()


asyncio.run(run())
