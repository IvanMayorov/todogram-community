"""SQLite storage. Plain SQL, so moving to Postgres later is a driver swap."""
import json
import logging
import secrets
import time
from pathlib import Path

import aiosqlite

from . import live
from .config import settings

# Projects made in the mini app, with no Telegram chat behind them, get ids from here down: far below any
# group or supergroup id (-100xxxxxxxxxx), still negative (a project, not «Без проекта») and JS-safe.
STANDALONE_BASE = -2 * 10**15


def standalone(chat_id: int) -> bool:
    return chat_id <= STANDALONE_BASE


def has_chat(chat_id: int) -> bool:
    """A real group chat the bot can post to: not «Без проекта» (positive) and not a project without a chat."""
    return STANDALONE_BASE < chat_id < 0


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS projects (
    chat_id     INTEGER PRIMARY KEY,
    chat_title  TEXT,
    name        TEXT,              -- set by /project, else chat title
    added_at    INTEGER
);

CREATE TABLE IF NOT EXISTS chat_upgrades (
    old_id INTEGER PRIMARY KEY,
    new_id INTEGER NOT NULL UNIQUE
);

-- Buffer of every group message (kept BUFFER_DAYS)
CREATE TABLE IF NOT EXISTS messages (
    chat_id         INTEGER,
    message_id      INTEGER,
    user_id         INTEGER,
    user_name       TEXT,
    date            INTEGER,
    kind            TEXT,          -- text/photo/image_doc/voice/audio/video/video_note/animation/document/other
    text            TEXT,          -- text or caption
    file_id         TEXT,
    file_size       INTEGER,
    file_name       TEXT,
    thumb_id        TEXT,          -- video / video note preview frame
    mime            TEXT,
    media_group_id  TEXT,
    task_id         INTEGER,       -- set once used in a task
    PRIMARY KEY (chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS ix_messages_album ON messages(chat_id, media_group_id);
CREATE INDEX IF NOT EXISTS ix_messages_date ON messages(date);

-- Per-user draft: messages marked with ✍ but not yet turned into a task
CREATE TABLE IF NOT EXISTS drafts (
    chat_id     INTEGER,
    user_id     INTEGER,
    message_id  INTEGER,
    added_at    REAL,
    PRIMARY KEY (chat_id, user_id, message_id)
);

CREATE TABLE IF NOT EXISTS tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id         INTEGER,
    title           TEXT,
    author_id       INTEGER,
    author_name     TEXT,
    created_at      INTEGER,
    archived        INTEGER DEFAULT 0,
    reply_to_id     INTEGER,       -- first source message
    checklist_msg_id INTEGER,      -- bot message with the checklist
    transcripts     TEXT DEFAULT '[]',  -- JSON [{author, text}]
    notes           TEXT DEFAULT '[]'   -- JSON ["файл не обработан: ..."]
);
CREATE INDEX IF NOT EXISTS ix_tasks_chat ON tasks(chat_id);

CREATE TABLE IF NOT EXISTS items (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER,
    pos     INTEGER,
    text    TEXT,
    done    INTEGER DEFAULT 0,
    shot    INTEGER                -- screenshot number (1-based) or NULL
);
CREATE INDEX IF NOT EXISTS ix_items_task ON items(task_id, pos);

-- Auto mode: tasks the bot found on its own, waiting for «Создать» / «Не задача»
CREATE TABLE IF NOT EXISTS suggestions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER,
    kind        TEXT,              -- new / add
    task_id     INTEGER,           -- for kind=add
    data        TEXT,              -- JSON {title, items, shots, transcripts, notes, message_ids}
    msg_id      INTEGER,           -- bot message with the buttons
    status      TEXT DEFAULT 'pending',
    created_at  INTEGER
);

-- Screenshots / video previews of a task, numbered 1..n
CREATE TABLE IF NOT EXISTS media (
    task_id     INTEGER,
    idx         INTEGER,           -- 1-based, matches items.shot
    kind        TEXT,              -- photo / video
    file_id     TEXT,              -- photo file_id or video thumbnail file_id
    message_id  INTEGER,
    PRIMARY KEY (task_id, idx)
);

-- History of a task: who did what and when (ticks, status, renames …)
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER,
    ts          INTEGER,
    actor       TEXT,              -- name shown in the history
    actor_id    INTEGER,           -- Telegram user id; -1 = AI agent via MCP; 0 = bot itself
    kind        TEXT,              -- created / done / undone / closed / reopened / status / archived / ...
    item_id     INTEGER,
    text        TEXT,              -- item text or new title at the time of the event
    extra       TEXT               -- JSON with details (status, count …)
);
CREATE INDEX IF NOT EXISTS events_task ON events(task_id, id);
CREATE INDEX IF NOT EXISTS events_actor ON events(actor_id, task_id);

-- «Уведомления» in the app: up to which event each section was read, per person (section: see NOTIF_SECTIONS)
CREATE TABLE IF NOT EXISTS notif_seen (
    user_id     INTEGER,
    section     TEXT,
    last_id     INTEGER,
    PRIMARY KEY (user_id, section)
);
-- …and one notification read or made unread again by hand: it wins over the section's line (1 = read, 0 = unread)
CREATE TABLE IF NOT EXISTS notif_state (
    user_id     INTEGER,
    event_id    INTEGER,
    read        INTEGER,
    PRIMARY KEY (user_id, event_id)
);

-- Personal reminders about a task's deadline (kind: day / hour / due; snooze = one-off «через час» at `at`)
CREATE TABLE IF NOT EXISTS reminders (
    task_id     INTEGER,
    user_id     INTEGER,
    kind        TEXT,
    sent_for    INTEGER,           -- the deadline it was already sent for; another deadline arms it again
    at          INTEGER,           -- snooze only
    PRIMARY KEY (task_id, user_id, kind)
);

-- People let into a project without being in its chat (by the project's invite link): they see and work
-- with its tasks just like the chat's members
CREATE TABLE IF NOT EXISTS project_access (
    chat_id     INTEGER,
    user_id     INTEGER,
    name        TEXT,
    added_at    INTEGER,
    PRIMARY KEY (chat_id, user_id)
);

-- People who let the bot write to them in private: task changes come there
-- Comments on a task: who wrote what and when
CREATE TABLE IF NOT EXISTS comments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER,
    user_id     INTEGER,
    author      TEXT,
    text        TEXT,
    ts          INTEGER
);
CREATE INDEX IF NOT EXISTS ix_comments_task ON comments(task_id, id);

-- Signing in to the site (outside Telegram) through the bot: a one-time code the bot confirms, then a session.
-- Only hashes of the secrets are kept.
CREATE TABLE IF NOT EXISTS logins (
    token_hash  TEXT PRIMARY KEY,
    created     INTEGER,
    user_id     INTEGER,             -- set when the person taps «Войти» in the bot
    name        TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     INTEGER,
    name        TEXT,
    created     INTEGER,
    seen        INTEGER
);

-- Time spent: every change of a task's hours is a row (the difference, by whom, when). tasks.hours is the total;
-- the «Время» dashboard sums these rows by period, project and person.
CREATE TABLE IF NOT EXISTS time_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER,
    user_id     INTEGER,
    user_name   TEXT,
    hours       REAL,
    ts          INTEGER
);
CREATE INDEX IF NOT EXISTS time_log_task ON time_log(task_id);

-- «Связанные задачи»: two tasks about the same thing (often in different chats), each stays in its own project.
-- One row per pair, a < b.
CREATE TABLE IF NOT EXISTS task_links (
    a           INTEGER,
    b           INTEGER,
    by_id       INTEGER,
    ts          INTEGER,
    PRIMARY KEY (a, b)
);
CREATE INDEX IF NOT EXISTS task_links_b ON task_links(b);

-- The waiting list of the alpha: people who asked for access from the landing (t.me/<bot>?start=waitlist)
CREATE TABLE IF NOT EXISTS waitlist (
    user_id     INTEGER PRIMARY KEY,
    username    TEXT,
    name        TEXT,
    created     INTEGER
);

-- Personal order of projects in the mini app list (each user has their own)
CREATE TABLE IF NOT EXISTS project_order (
    user_id     INTEGER NOT NULL,
    chat_id     INTEGER NOT NULL,
    pos         INTEGER NOT NULL,
    PRIMARY KEY (user_id, chat_id)
);
CREATE INDEX IF NOT EXISTS ix_project_order_user ON project_order(user_id, pos);

CREATE TABLE IF NOT EXISTS notify (
    user_id     INTEGER PRIMARY KEY,
    enabled     INTEGER DEFAULT 1,
    updated_at  INTEGER
);
"""

# Project-wide history feed: SQL condition per filter chip. «all» is status-level changes only —
# ticks on single items would drown them, so those have their own chip.
_STATUS_TO = "(e.kind='status' AND json_extract(e.extra,'$.to')='{}')"
HISTORY_FILTERS = {
    "all": "e.kind IN ('created','closed','reopened','status','archived','unarchived')",
    "closed": "e.kind='closed'",
    "open": "(e.kind='reopened' OR " + _STATUS_TO.format("open") + ")",
    "progress": _STATUS_TO.format("progress"),
    "backlog": _STATUS_TO.format("backlog"),
    "review": _STATUS_TO.format("review"),
    "created": "e.kind='created'",
    "archive": "e.kind IN ('archived','unarchived')",
    "items": "e.kind IN ('done','undone','done_all')",
}

# «Уведомления»: what happened in my tasks (made, assigned to me or touched by me), by section, the most important first
_REVIEW = ("(e.kind='status' AND (json_extract(e.extra,'$.to')='review' OR json_extract(e.extra,'$.from')='review'))")
NOTIF_SECTIONS = {   # section → SQL condition on the event (alias e)
    "q": "e.kind IN ('asked','answered','reasked')",                                              # Вопросы
    "r": _REVIEW,                                                                                 # На проверку: sent, or back to work after it
    "c": "e.kind='commented'",                                                                    # Комментарии
    "s": f"(e.kind IN ('status','closed','reopened','report','archived','unarchived') AND NOT {_REVIEW})",   # Статусы
    "a": "e.kind IN ('assigned','due')",                                                          # Исполнитель и срок
    "e": "e.kind IN ('created','renamed','descr','item_added','items_added','done_all','shot_added','linked','moved')",   # Изменения
}
NOTIF_ANY = "(" + " OR ".join(f"({c})" for c in NOTIF_SECTIONS.values()) + ")"
NOTIF_FRESH = 3 * 86400   # a newcomer to the screen sees the last three days as unread, not the whole past


class DB:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None
        self.on_event = None   # callback(event: dict) after every logged event — private notifications

    async def connect(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        await self._migrate()
        await self.conn.commit()

    async def _migrate(self):
        """Add columns introduced after the first release."""
        wanted = {
            "projects": {"auto": "INTEGER DEFAULT 0", "auto_cursor": "INTEGER DEFAULT 0",
                         "invite": "TEXT",    # token of the invite link (startapp=join_<token>); NULL = not made yet
                         "owner_id": "INTEGER",    # who made a project without a chat (see standalone); NULL = a chat
                         "pic": "TEXT"},           # the project's own picture, a file in media/ (over the chat's photo); NULL = none
            "chat_upgrades": {"old_invite": "TEXT"},
            "messages": {"transcript": "TEXT", "duration": "INTEGER"},
            "waitlist": {"approved": "INTEGER"},
            "notify": {"kinds": "TEXT"},
            "comments": {"item_id": "INTEGER",              # a comment on one item of the checklist; NULL = on the task
                         "question": "INTEGER DEFAULT 0",   # 1 = a question: shown above the comments until someone marks it answered
                         "resolved_at": "INTEGER",          # when the question was marked answered; NULL = still open
                         "resolved_by": "TEXT",
                         "files": "TEXT",
                         "edited_at": "INTEGER"},   # the author changed the text (shown as «изменено»)          # JSON [{"f": "<name>", "kind": "photo" | "video"}]: screenshots and videos (media/task_<id>/c/)   # JSON list of what to send (see notify.KINDS); NULL = everything
            "items": {"src": "TEXT", "source_chat_id": "INTEGER"},
            "media": {"source_chat_id": "INTEGER"},
            "tasks": {"descr": "TEXT",                  # the task's description: free text, links in it are clickable
                      "voice_summaries": "TEXT NOT NULL DEFAULT '{}'",  # transcript hash → structured summary
                      "legacy_chat_id": "INTEGER",      # where the original Telegram messages and checklist were posted
                      "backlog": "INTEGER DEFAULT 0",    # 1 = «на потом», not in work
                      "progress": "INTEGER DEFAULT 0",   # 1 = «в работе»: someone took it
                      "auto": "INTEGER DEFAULT 0",       # 1 = created by the bot itself (/auto on), can be undone
                      "files": "TEXT",                    # JSON [{name, size, mime, file_id, message_id, author}]; NULL = not filled yet
                      "due_at": "INTEGER",                # deadline, unix time (server time zone)
                      "due_allday": "INTEGER DEFAULT 0",  # 1 = only the date, no time
                      "rank": "INTEGER",                  # place in the list, higher = upper; NULL = its id (new ones on top)
                      "assignee_id": "INTEGER",           # who does the task (Telegram user id); NULL = nobody yet
                      "assignee_name": "TEXT",
                      "closed": "INTEGER DEFAULT 0",
                      "result_url": "TEXT",               # asked when closing: a link to the result (optional)
                      "hours": "REAL",
                      "result_note": "TEXT",
                      "req_id": "TEXT",
                      "review_at": "INTEGER",              # «на проверке» since (unix time); NULL = not on review. Comes with progress=1
                      "review_n": "INTEGER DEFAULT 0"},    # reminders to the author sent while it waits   # X-Request-Id of the app's «Создать»: sent again after a dropped connection, no twin             # and what was done, in words (optional)                   # and the hours spent (optional)     # 1 = closed: by a status, or on its own when every item got ticked
        }
        for table, cols in wanted.items():
            have = {r[1] for r in await (await self.conn.execute(f"PRAGMA table_info({table})")).fetchall()}
            for name, decl in cols.items():
                if name not in have:
                    await self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                    if (table, name) == ("tasks", "closed"):   # before it, «closed» meant «every item ticked»
                        await self.conn.execute(
                            "UPDATE tasks SET closed=1 WHERE EXISTS (SELECT 1 FROM items i WHERE i.task_id=tasks.id) "
                            "AND NOT EXISTS (SELECT 1 FROM items i WHERE i.task_id=tasks.id AND i.done=0)")

        await self._search_index()
        await self._heal_progress()
        await self._seed_time_log()
        await self._split_questions()

    async def _heal_progress(self):
        """Once: tasks taken into work before «в работу → вы исполнитель» (13.11) have nobody on them — the one who
        last put them into work becomes the assignee, so «В работе» never sits under «Ничьи»."""
        if (await self.one("PRAGMA user_version"))["user_version"] >= 1:
            return
        last = ("(SELECT e.{} FROM events e WHERE e.task_id=tasks.id AND e.kind='status' "
                "AND json_extract(e.extra,'$.to')='progress' AND e.actor_id>0 ORDER BY e.id DESC LIMIT 1)")
        await self.conn.execute(
            f"UPDATE tasks SET assignee_id={last.format('actor_id')}, assignee_name={last.format('actor')} "
            f"WHERE progress=1 AND COALESCE(backlog,0)=0 AND COALESCE(closed,0)=0 AND assignee_id IS NULL "
            f"AND {last.format('actor_id')} IS NOT NULL")
        await self.conn.execute("PRAGMA user_version=1")

    async def _seed_time_log(self):
        """Once: hours given at closing before the time log — one row each, on whoever does the task (else its
        author), at the time it was closed or reported."""
        if (await self.one("PRAGMA user_version"))["user_version"] >= 2:
            return
        when = ("(SELECT MAX(e.ts) FROM events e WHERE e.task_id=t.id AND e.kind IN ('closed','report') "
                "AND json_extract(e.extra,'$.hours') IS NOT NULL)")
        await self.conn.execute(
            "INSERT INTO time_log(task_id,user_id,user_name,hours,ts) "
            f"SELECT t.id, COALESCE(t.assignee_id,t.author_id), COALESCE(t.assignee_name,t.author_name), t.hours, "
            f"COALESCE({when}, t.created_at) FROM tasks t WHERE t.hours>0 "
            "AND NOT EXISTS (SELECT 1 FROM time_log l WHERE l.task_id=t.id)")
        await self.conn.execute("PRAGMA user_version=2")

    async def _split_questions(self):
        """Once: questions became a kind of their own in «what the bot sends» — whoever had comments on keeps questions too."""
        if (await self.one("PRAGMA user_version"))["user_version"] >= 3:
            return
        for r in await self.all("SELECT user_id, kinds FROM notify WHERE kinds IS NOT NULL"):
            k = json.loads(r["kinds"])
            if "comments" in k and "questions" not in k:
                await self.conn.execute("UPDATE notify SET kinds=? WHERE user_id=?", (json.dumps(["questions", *k]), r["user_id"]))
        await self.conn.execute("PRAGMA user_version=3")

    async def link_tasks(self, x: int, y: int, by_id: int) -> bool:
        a, b = sorted((x, y))
        cur = await self.x("INSERT OR IGNORE INTO task_links(a,b,by_id,ts) VALUES(?,?,?,?)", (a, b, by_id, int(time.time())))
        return cur.rowcount > 0

    async def unlink_tasks(self, x: int, y: int) -> bool:
        a, b = sorted((x, y))
        cur = await self.x("DELETE FROM task_links WHERE a=? AND b=?", (a, b))
        return cur.rowcount > 0

    async def task_links(self, task_id: int) -> list[int]:
        """The tasks linked to this one, oldest link first."""
        rows = await self.all("SELECT a, b FROM task_links WHERE a=? OR b=? ORDER BY ts, a, b", (task_id, task_id))
        return [r["b"] if r["a"] == task_id else r["a"] for r in rows]

    async def log_time(self, task_id: int, user_id: int, user_name: str, delta: float):
        await self.x("INSERT INTO time_log(task_id,user_id,user_name,hours,ts) VALUES(?,?,?,?,?)",
                     (task_id, user_id, user_name, round(delta, 2), int(time.time())))

    async def time_rows(self, chat_ids: list[int], since: int = 0, until: int | None = None) -> list[dict]:
        """Time log rows of these projects from `since` up to (not including) `until`, with their task and project."""
        if not chat_ids:
            return []
        q = ",".join("?" * len(chat_ids))
        return await self.all(
            "SELECT l.id, l.task_id, l.user_id, l.user_name, l.hours, l.ts, t.title, t.chat_id, "
            "t.assignee_id, t.closed, t.archived FROM time_log l JOIN tasks t ON t.id=l.task_id "
            f"WHERE t.chat_id IN ({q}) AND l.ts>=? AND l.ts<? ORDER BY l.ts DESC", (*chat_ids, since, until or 2**62))

    # ---------- search ----------
    # One FTS5 table for everything that can be found: task titles, result notes, checklist items, comments,
    # project names. Triggers keep it in step with the tables; ё is stored as е so «елка» finds «Ёлка».
    _NORM = "replace(replace({},'ё','е'),'Ё','Е')"
    _FTS_ROWS = {   # kind: (table, ref, task, text)
        "task": ("tasks", "id", "id", "title"),
        "result": ("tasks", "id", "id", "result_note"),
        "item": ("items", "id", "task_id", "text"),
        "comment": ("comments", "id", "task_id", "text"),
        "project": ("projects", "chat_id", "NULL", "COALESCE(name, chat_title)"),
    }

    async def _search_index(self):
        self.fts = False
        try:
            fresh = not await self.one("SELECT 1 FROM sqlite_master WHERE name='search'")
            await self.conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5("
                "kind UNINDEXED, ref UNINDEXED, task UNINDEXED, body, tokenize='unicode61 remove_diacritics 2')")
        except Exception as e:   # SQLite built without FTS5: search falls back to LIKE
            logging.warning("no FTS5, search uses LIKE: %s", e)
            return
        e = lambda x: self._NORM.format(x)   # noqa: E731
        ins = {
            "task": f"INSERT INTO search(kind,ref,task,body) SELECT 'task', new.id, new.id, {e('new.title')} WHERE COALESCE(new.title,'')<>'';",
            "result": f"INSERT INTO search(kind,ref,task,body) SELECT 'result', new.id, new.id, {e('new.result_note')} WHERE COALESCE(new.result_note,'')<>'';",
            "item": f"INSERT INTO search(kind,ref,task,body) SELECT 'item', new.id, new.task_id, {e('new.text')} WHERE COALESCE(new.text,'')<>'';",
            "comment": f"INSERT INTO search(kind,ref,task,body) SELECT 'comment', new.id, new.task_id, {e('new.text')} WHERE COALESCE(new.text,'')<>'';",
            "project": f"INSERT INTO search(kind,ref,task,body) SELECT 'project', new.chat_id, NULL, {e('COALESCE(new.name, new.chat_title)')} "
                       "WHERE COALESCE(new.name, new.chat_title,'')<>'' AND new.chat_id<0;",
        }
        for table, kinds, ref in (("tasks", ("task", "result"), "id"), ("items", ("item",), "id"),
                                  ("comments", ("comment",), "id"), ("projects", ("project",), "chat_id")):
            add = " ".join(ins[k] for k in kinds)
            drop = f"DELETE FROM search WHERE kind IN ({','.join(repr(k) for k in kinds)}) AND ref=old.{ref};"
            await self.conn.execute(f"CREATE TRIGGER IF NOT EXISTS fts_{table}_i AFTER INSERT ON {table} BEGIN {add} END")
            await self.conn.execute(f"CREATE TRIGGER IF NOT EXISTS fts_{table}_u AFTER UPDATE ON {table} BEGIN {drop} {add} END")
            await self.conn.execute(f"CREATE TRIGGER IF NOT EXISTS fts_{table}_d AFTER DELETE ON {table} BEGIN {drop} END")
        if fresh:
            for k, (table, ref, task, text) in self._FTS_ROWS.items():
                where = f"COALESCE({text},'')<>''" + (" AND chat_id<0" if k == "project" else "")
                await self.conn.execute(
                    f"INSERT INTO search(kind,ref,task,body) SELECT '{k}', {ref}, {task}, {e(text)} FROM {table} WHERE {where}")
        self.fts = True

    async def search(self, terms: list[str], limit: int = 400, any_term: bool = False) -> list[dict]:
        """Rows of the index that have every term (any of them with any_term) as a word start, best first: {kind, ref, task}."""
        if not terms:
            return []
        if self.fts:
            q = (" OR " if any_term else " ").join('"' + t.replace('"', '') + '"*' for t in terms)
            return await self.all("SELECT kind, ref, task FROM search WHERE search MATCH ? ORDER BY bm25(search) LIMIT ?",
                                  (q, limit))
        out = []
        for k, (table, ref, task, text) in self._FTS_ROWS.items():
            conds = (" OR " if any_term else " AND ").join(f"{self._NORM.format(text)} LIKE ?" for _ in terms)
            conds = f"({conds})"
            extra = " AND chat_id<0" if k == "project" else ""
            out += await self.all(f"SELECT '{k}' AS kind, {ref} AS ref, {task} AS task FROM {table} WHERE {conds}{extra} LIMIT ?",
                                  (*[f"%{t}%" for t in terms], limit))
        return out[:limit]

    async def close(self):
        if self.conn:
            await self.conn.close()
            self.conn = None

    async def _commit(self, sql: str | None = None):
        await self.conn.commit()
        live.changed(sql)   # open apps refresh now (writes nobody sees stay quiet)

    async def x(self, sql: str, args=()):
        cur = await self.conn.execute(sql, args)
        await self._commit(sql)
        return cur

    async def all(self, sql: str, args=()) -> list[dict]:
        cur = await self.conn.execute(sql, args)
        return [dict(r) for r in await cur.fetchall()]

    async def one(self, sql: str, args=()) -> dict | None:
        cur = await self.conn.execute(sql, args)
        r = await cur.fetchone()
        return dict(r) if r else None

    # ---------- projects ----------
    async def upsert_project(self, chat_id: int, title: str):
        await self.x(
            "INSERT INTO projects(chat_id, chat_title, added_at) SELECT ?,?,? "
            "WHERE NOT EXISTS (SELECT 1 FROM chat_upgrades WHERE old_id=?) "
            "ON CONFLICT(chat_id) DO UPDATE SET chat_title=excluded.chat_title",
            (chat_id, title, int(time.time()), chat_id),
        )

    async def create_standalone(self, owner_id: int, owner_name: str, name: str) -> int:
        """A project without a chat: its maker is let in like someone who came by the invite link."""
        r = await self.one("SELECT MIN(chat_id) AS m FROM projects WHERE chat_id<=?", (STANDALONE_BASE,))
        chat_id = (r["m"] if r and r["m"] is not None else STANDALONE_BASE + 1) - 1
        await self.x("INSERT INTO projects(chat_id, chat_title, added_at, owner_id) VALUES(?,?,?,?)",
                     (chat_id, name, int(time.time()), owner_id))
        await self.grant_access(chat_id, owner_id, owner_name)
        return chat_id

    async def set_project_name(self, chat_id: int, name: str):
        await self.x("UPDATE projects SET name=? WHERE chat_id=?", (name, chat_id))

    async def project(self, chat_id: int) -> dict | None:
        return await self.one("SELECT *, COALESCE(name, chat_title) AS display FROM projects WHERE chat_id=?", (chat_id,))

    async def projects(self) -> list[dict]:
        return await self.all("SELECT *, COALESCE(name, chat_title) AS display FROM projects "
                              "WHERE chat_id NOT IN (SELECT old_id FROM chat_upgrades) ORDER BY added_at")

    async def project_order_ids(self, user_id: int) -> list[int]:
        rows = await self.all("SELECT chat_id FROM project_order WHERE user_id=? ORDER BY pos", (user_id,))
        return [r["chat_id"] for r in rows]

    async def set_project_order(self, user_id: int, chat_ids: list[int]):
        """Save this user's project list order (top first). Ids not in the list keep their
        relative place after the ones that were moved."""
        ids = list(dict.fromkeys(chat_ids))
        old = await self.project_order_ids(user_id)
        full = ids + [c for c in old if c not in ids]
        await self.conn.execute("DELETE FROM project_order WHERE user_id=?", (user_id,))
        if full:
            await self.conn.executemany(
                "INSERT INTO project_order(user_id, chat_id, pos) VALUES(?,?,?)",
                [(user_id, cid, i) for i, cid in enumerate(full)])
        await self._commit("UPDATE project_order")

    async def upgrade_chat(self, old_id: int, new_id: int, title: str) -> int:
        """Move a basic group's project to its supergroup, keeping the original message namespace."""
        if not -10**12 < old_id < 0 or not has_chat(new_id) or not str(new_id).startswith("-100"):
            raise ValueError("expected a basic group and a supergroup")

        # Our main connection is shared with request handlers, which can commit their own writes.
        async with aiosqlite.connect(self.path, timeout=15) as conn:
            await conn.execute("BEGIN IMMEDIATE")
            cur = await conn.execute("SELECT new_id FROM chat_upgrades WHERE old_id=?", (old_id,))
            row = await cur.fetchone()
            if row and row[0] != new_id:
                raise ValueError("group already migrated to another supergroup")
            await conn.execute("INSERT OR IGNORE INTO projects(chat_id, chat_title, added_at) VALUES(?,?,?)",
                               (new_id, title, int(time.time())))
            if not row:
                await conn.execute(
                    "INSERT INTO chat_upgrades(old_id,new_id,old_invite) VALUES(?,?,"
                    "(SELECT invite FROM projects WHERE chat_id=?))", (old_id, new_id, old_id))
                await conn.execute(
                    "UPDATE projects AS dst SET "
                    "name=COALESCE(dst.name,src.name), pic=COALESCE(dst.pic,src.pic), "
                    "invite=COALESCE(dst.invite,src.invite), added_at=MIN(dst.added_at,src.added_at), "
                    "auto=MAX(COALESCE(dst.auto,0),COALESCE(src.auto,0)) "
                    "FROM projects AS src WHERE dst.chat_id=? AND src.chat_id=?", (new_id, old_id))
                await conn.execute(
                    "INSERT OR IGNORE INTO project_access(chat_id,user_id,name,added_at) "
                    "SELECT ?,user_id,name,added_at FROM project_access WHERE chat_id=?", (new_id, old_id))
                await conn.execute("DELETE FROM project_access WHERE chat_id=?", (old_id,))
                await conn.execute("DELETE FROM projects WHERE chat_id=?", (old_id,))
            cur = await conn.execute(
                "UPDATE tasks SET chat_id=?, legacy_chat_id=? WHERE chat_id=?",
                (new_id, old_id, old_id))
            await conn.commit()
        live.changed()
        return cur.rowcount

    # ---------- access without the chat ----------
    async def has_access(self, chat_id: int, user_id: int) -> bool:
        return bool(await self.one("SELECT 1 FROM project_access WHERE chat_id=? AND user_id=?", (chat_id, user_id)))

    async def access_list(self, chat_id: int) -> list[dict]:
        return await self.all("SELECT user_id, name, added_at FROM project_access WHERE chat_id=? ORDER BY added_at", (chat_id,))

    async def grant_access(self, chat_id: int, user_id: int, name: str) -> bool:
        """True if the person is new to the project."""
        new = not await self.has_access(chat_id, user_id)
        await self.x("INSERT INTO project_access(chat_id,user_id,name,added_at) VALUES(?,?,?,?) "
                     "ON CONFLICT(chat_id,user_id) DO UPDATE SET name=excluded.name", (chat_id, user_id, name, int(time.time())))
        return new

    async def people(self, chat_id: int) -> list[dict]:
        """Everyone the bot has seen in the project: wrote in the chat, made or touched a task, came in by the
        invite link. Telegram gives bots no member list, so this is who can be picked as an assignee
        (the caller still checks they are in the project). Newest name wins, most recently active first."""
        rows = await self.all(
            "SELECT user_id AS id, user_name AS name, date AS ts FROM messages WHERE chat_id=? AND user_id>0 "
            "UNION ALL SELECT author_id, author_name, created_at FROM tasks WHERE chat_id=? AND author_id>0 "
            "UNION ALL SELECT assignee_id, assignee_name, created_at FROM tasks WHERE chat_id=? AND assignee_id>0 "
            "UNION ALL SELECT user_id, name, added_at FROM project_access WHERE chat_id=? "
            "UNION ALL SELECT e.actor_id, e.actor, e.ts FROM events e JOIN tasks t ON t.id=e.task_id "
            " WHERE t.chat_id=? AND e.actor_id>0",
            (chat_id,) * 5)
        best: dict[int, dict] = {}
        for r in rows:
            if r["name"] and (r["id"] not in best or (r["ts"] or 0) >= (best[r["id"]]["ts"] or 0)):
                best[r["id"]] = r
        return sorted(best.values(), key=lambda r: -(r["ts"] or 0))

    async def revoke_access(self, chat_id: int, user_id: int):
        await self.x("DELETE FROM project_access WHERE chat_id=? AND user_id=?", (chat_id, user_id))

    async def invite_token(self, chat_id: int, reset: bool = False) -> str:
        p = await self.project(chat_id)
        if p["invite"] and not reset:
            return p["invite"]
        tok = secrets.token_urlsafe(12)
        if reset:
            await self.x("UPDATE chat_upgrades SET old_invite=NULL WHERE new_id=?", (chat_id,))
        await self.x("UPDATE projects SET invite=? WHERE chat_id=?", (tok, chat_id))
        return tok

    async def project_by_invite(self, token: str) -> dict | None:
        return await self.one("SELECT *, COALESCE(name, chat_title) AS display FROM projects WHERE invite=? "
                              "OR chat_id=(SELECT new_id FROM chat_upgrades WHERE old_invite=?)", (token, token))

    async def set_auto(self, chat_id: int, on: bool):
        # start from "now": history before switching on is not scanned
        row = await self.one("SELECT COALESCE(MAX(message_id),0) AS m FROM messages WHERE chat_id=?", (chat_id,))
        await self.x("UPDATE projects SET auto=?, auto_cursor=? WHERE chat_id=?", (1 if on else 0, row["m"], chat_id))

    async def auto_projects(self) -> list[dict]:
        return await self.all("SELECT * FROM projects WHERE auto=1 "
                              "AND chat_id NOT IN (SELECT old_id FROM chat_upgrades)")

    async def set_cursor(self, chat_id: int, message_id: int):
        await self.x("UPDATE projects SET auto_cursor=MAX(auto_cursor, ?) WHERE chat_id=?", (message_id, chat_id))

    async def new_for_auto(self, chat_id: int, cursor: int, limit: int) -> list[dict]:
        """Messages after the cursor that are not in a task and not in anyone's ✍ draft."""
        return await self.all(
            "SELECT * FROM messages m WHERE chat_id=? AND message_id>? AND task_id IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM drafts d WHERE d.chat_id=m.chat_id AND d.message_id=m.message_id) "
            "ORDER BY message_id LIMIT ?",
            (chat_id, cursor, limit),
        )

    async def before(self, chat_id: int, message_id: int, limit: int) -> list[dict]:
        rows = await self.all("SELECT * FROM messages WHERE chat_id=? AND message_id<=? ORDER BY message_id DESC LIMIT ?",
                              (chat_id, message_id, limit))
        return rows[::-1]

    async def set_transcript(self, chat_id: int, message_id: int, text: str):
        await self.x("UPDATE messages SET transcript=? WHERE chat_id=? AND message_id=?", (text, chat_id, message_id))

    async def open_tasks(self, chat_id: int) -> list[dict]:
        return [t for t in await self.tasks_of_chat(chat_id) if not t["archived"] and not t["closed"]]

    # ---------- suggestions (auto mode) ----------
    async def add_suggestion(self, chat_id: int, kind: str, task_id: int | None, data: dict) -> int:
        cur = await self.x(
            "INSERT INTO suggestions(chat_id,kind,task_id,data,created_at) VALUES(?,?,?,?,?)",
            (chat_id, kind, task_id, json.dumps(data, ensure_ascii=False), int(time.time())),
        )
        return cur.lastrowid

    async def suggestion(self, sid: int) -> dict | None:
        s = await self.one("SELECT * FROM suggestions WHERE id=?", (sid,))
        if s:
            s["data"] = json.loads(s["data"])
        return s

    async def set_suggestion(self, sid: int, **fields):
        allowed = {k: v for k, v in fields.items() if k in ("msg_id", "status", "data")}
        if "data" in allowed:
            allowed["data"] = json.dumps(allowed["data"], ensure_ascii=False)
        sets = ", ".join(f"{k}=?" for k in allowed)
        await self.x(f"UPDATE suggestions SET {sets} WHERE id=?", (*allowed.values(), sid))

    async def append_items(self, task_id: int, items: list[dict]):
        row = await self.one("SELECT COALESCE(MAX(pos),-1)+1 AS p FROM items WHERE task_id=?", (task_id,))
        await self.conn.executemany(
            "INSERT INTO items(task_id,pos,text,shot,src,source_chat_id) VALUES(?,?,?,?,?,?)",
            [(task_id, row["p"] + i, it["text"], it.get("shot"), json.dumps(it.get("src") or []), it.get("source_chat_id"))
             for i, it in enumerate(items)],
        )
        await self._commit()

    async def add_transcripts(self, task_id: int, transcripts: list, notes: list, files: list | None = None):
        t = await self.task(task_id)
        tr = json.loads(t["transcripts"] or "[]") + transcripts
        nt = json.loads(t["notes"] or "[]") + notes
        fl = json.loads(t.get("files") or "[]") + (files or [])
        await self.x("UPDATE tasks SET transcripts=?, notes=?, files=? WHERE id=?",
                     (json.dumps(tr, ensure_ascii=False), json.dumps(nt, ensure_ascii=False),
                      json.dumps(fl, ensure_ascii=False), task_id))

    async def set_files(self, task_id: int, files: list):
        await self.x("UPDATE tasks SET files=? WHERE id=?", (json.dumps(files, ensure_ascii=False), task_id))

    async def remove_project(self, chat_id: int):
        await self.x("DELETE FROM projects WHERE chat_id=?", (chat_id,))

    # ---------- buffer ----------
    async def save_message(self, m: dict):
        await self.x(
            "INSERT OR IGNORE INTO messages(chat_id,message_id,user_id,user_name,date,kind,text,file_id,file_size,"
            "file_name,thumb_id,mime,media_group_id,duration) "
            "VALUES(:chat_id,:message_id,:user_id,:user_name,:date,:kind,:text,:file_id,:file_size,"
            ":file_name,:thumb_id,:mime,:media_group_id,:duration)",
            {"duration": None, **m},
        )

    async def message(self, chat_id: int, message_id: int) -> dict | None:
        return await self.one("SELECT * FROM messages WHERE chat_id=? AND message_id=?", (chat_id, message_id))

    async def task_messages(self, chat_id: int, task_id: int, extra_ids=()) -> list[dict]:
        """Messages of the chat a task was made from (marked with its id), plus these ids, oldest first."""
        ids = list(dict.fromkeys(extra_ids))
        marks = ",".join("?" * len(ids)) or "NULL"
        return await self.all(f"SELECT * FROM messages WHERE chat_id=? AND (task_id=? OR message_id IN ({marks})) "
                              "ORDER BY date, message_id", (chat_id, task_id, *ids))

    async def album(self, chat_id: int, media_group_id: str) -> list[dict]:
        return await self.all(
            "SELECT * FROM messages WHERE chat_id=? AND media_group_id=? ORDER BY message_id", (chat_id, media_group_id)
        )

    async def mark_used(self, chat_id: int, message_ids: list[int], task_id: int):
        await self.conn.executemany(
            "UPDATE messages SET task_id=? WHERE chat_id=? AND message_id=?",
            [(task_id, chat_id, mid) for mid in message_ids],
        )
        await self._commit()

    async def purge_old(self):
        cutoff = int(time.time()) - settings.buffer_days * 86400
        # messages a task was made from stay: the app shows them as the task's original wording
        await self.x("DELETE FROM messages WHERE date < ? AND task_id IS NULL", (cutoff,))

    # ---------- drafts ----------
    async def draft_add(self, chat_id: int, user_id: int, message_id: int):
        await self.x(
            "INSERT INTO drafts(chat_id,user_id,message_id,added_at) VALUES(?,?,?,?) "
            "ON CONFLICT(chat_id,user_id,message_id) DO UPDATE SET added_at=excluded.added_at",
            (chat_id, user_id, message_id, time.time()),
        )
        # every new ✍ extends the wait for the whole draft
        await self.x("UPDATE drafts SET added_at=? WHERE chat_id=? AND user_id=?", (time.time(), chat_id, user_id))

    async def draft_remove(self, chat_id: int, user_id: int, message_id: int):
        await self.x("DELETE FROM drafts WHERE chat_id=? AND user_id=? AND message_id=?", (chat_id, user_id, message_id))

    async def draft_take(self, chat_id: int, user_id: int) -> list[int]:
        rows = await self.all("SELECT message_id FROM drafts WHERE chat_id=? AND user_id=?", (chat_id, user_id))
        await self.x("DELETE FROM drafts WHERE chat_id=? AND user_id=?", (chat_id, user_id))
        return [r["message_id"] for r in rows]

    async def drafts_due(self, older_than: float) -> list[tuple[int, int]]:
        rows = await self.all(
            "SELECT chat_id, user_id, MAX(added_at) AS last FROM drafts "
            "WHERE chat_id NOT IN (SELECT old_id FROM chat_upgrades) GROUP BY chat_id, user_id HAVING last < ?",
            (older_than,),
        )
        return [(r["chat_id"], r["user_id"]) for r in rows]

    # ---------- tasks ----------
    async def create_task(self, chat_id, author_id, author_name, reply_to_id) -> int:
        cur = await self.x(
            "INSERT INTO tasks(chat_id,title,author_id,author_name,created_at,reply_to_id,legacy_chat_id) "
            "SELECT COALESCE(u.new_id, ?), '', ?, ?, ?, ?, u.old_id "
            "FROM (SELECT 1) LEFT JOIN chat_upgrades u ON u.old_id=?",
            (chat_id, author_id, author_name, int(time.time()), reply_to_id, chat_id),
        )
        return cur.lastrowid

    async def fill_task(self, task_id: int, title: str, items: list[dict], transcripts: list, notes: list,
                        files: list | None = None):
        await self.x(
            "UPDATE tasks SET title=?, transcripts=?, notes=?, files=? WHERE id=?",
            (title, json.dumps(transcripts, ensure_ascii=False), json.dumps(notes, ensure_ascii=False),
             json.dumps(files or [], ensure_ascii=False), task_id),
        )
        await self.conn.executemany(
            "INSERT INTO items(task_id,pos,text,shot,src) VALUES(?,?,?,?,?)",
            [(task_id, i, it["text"], it.get("shot"), json.dumps(it.get("src") or [])) for i, it in enumerate(items)],
        )
        await self._commit()

    async def add_media(self, task_id: int, media: list[dict]):
        await self.conn.executemany(
            "INSERT OR REPLACE INTO media(task_id,idx,kind,file_id,message_id,source_chat_id) VALUES(?,?,?,?,?,?)",
            [(task_id, m["idx"], m["kind"], m["file_id"], m["message_id"], m.get("source_chat_id")) for m in media],
        )
        await self._commit()

    async def delete_media(self, task_id: int, idx: int):
        """Remove one screenshot and close the gap: later ones move down by one, items follow their screenshot."""
        await self.conn.execute("DELETE FROM media WHERE task_id=? AND idx=?", (task_id, idx))
        await self.conn.execute("UPDATE items SET shot=NULL WHERE task_id=? AND shot=?", (task_id, idx))
        await self.conn.execute("UPDATE items SET shot=shot-1 WHERE task_id=? AND shot>?", (task_id, idx))
        # through negative numbers: (task_id, idx) is the key, and a row must not land on one not yet moved
        await self.conn.execute("UPDATE media SET idx=-(idx-1) WHERE task_id=? AND idx>?", (task_id, idx))
        await self.conn.execute("UPDATE media SET idx=-idx WHERE task_id=? AND idx<0", (task_id,))
        await self._commit()

    async def delete_task(self, task_id: int):
        for t in ("items", "media", "events", "reminders", "comments", "time_log"):
            await self.x(f"DELETE FROM {t} WHERE task_id=?", (task_id,))
        await self.x("DELETE FROM task_links WHERE a=? OR b=?", (task_id, task_id))
        await self.x("DELETE FROM tasks WHERE id=?", (task_id,))
        await self.x("UPDATE messages SET task_id=NULL WHERE task_id=?", (task_id,))

    # ---------- signing in to the site ----------
    async def login_new(self, token_hash: str):
        await self.x("DELETE FROM logins WHERE created < ?", (int(time.time()) - 3600,))
        await self.x("INSERT INTO logins(token_hash, created) VALUES(?,?)", (token_hash, int(time.time())))

    async def login(self, token_hash: str) -> dict | None:
        return await self.one("SELECT * FROM logins WHERE token_hash=?", (token_hash,))

    async def login_confirm(self, token_hash: str, user_id: int, name: str):
        await self.x("UPDATE logins SET user_id=?, name=? WHERE token_hash=? AND user_id IS NULL", (user_id, name, token_hash))

    async def login_drop(self, token_hash: str):
        await self.x("DELETE FROM logins WHERE token_hash=?", (token_hash,))

    async def session_new(self, token_hash: str, user_id: int, name: str):
        now = int(time.time())
        await self.x("INSERT INTO sessions(token_hash,user_id,name,created,seen) VALUES(?,?,?,?,?)", (token_hash, user_id, name, now, now))

    async def session(self, token_hash: str) -> dict | None:
        return await self.one("SELECT * FROM sessions WHERE token_hash=?", (token_hash,))

    async def session_seen(self, token_hash: str):
        await self.x("UPDATE sessions SET seen=? WHERE token_hash=?", (int(time.time()), token_hash))

    async def session_drop(self, token_hash: str):
        await self.x("DELETE FROM sessions WHERE token_hash=?", (token_hash,))

    # ---------- comments ----------
    async def comments(self, task_id: int) -> list[dict]:
        return await self.all("SELECT * FROM comments WHERE task_id=? ORDER BY id", (task_id,))

    async def add_comment(self, task_id: int, user_id: int, author: str, text: str, item_id: int | None = None,
                          question: bool = False, files: list | None = None) -> int:
        cur = await self.x("INSERT INTO comments(task_id,user_id,author,text,ts,item_id,question,files) VALUES(?,?,?,?,?,?,?,?)",
                           (task_id, user_id, author, text, int(time.time()), item_id, 1 if question else 0,
                            json.dumps(files, ensure_ascii=False) if files else None))
        return cur.lastrowid

    async def edit_comment(self, comment_id: int, text: str):
        await self.x("UPDATE comments SET text=?, edited_at=? WHERE id=?", (text, int(time.time()), comment_id))

    async def resolve_comment(self, comment_id: int, by: str | None):
        """A question answered (by = who said so) or open again (by = None)."""
        await self.x("UPDATE comments SET resolved_at=?, resolved_by=? WHERE id=?",
                     (int(time.time()) if by else None, by, comment_id))

    async def open_questions(self, task_id: int) -> int:
        r = await self.one("SELECT COUNT(*) AS n FROM comments WHERE task_id=? AND question=1 AND resolved_at IS NULL", (task_id,))
        return r["n"] if r else 0

    async def comments_feed(self, chat_ids: list[int], extra_task_ids: list[int], limit: int = 100) -> list[dict]:
        """The newest comments of these projects' tasks (and of these tasks), with the task and the item."""
        if not chat_ids and not extra_task_ids:
            return []
        cm, tm = ",".join("?" * len(chat_ids)) or "NULL", ",".join("?" * len(extra_task_ids)) or "NULL"
        return await self.all(
            "SELECT c.*, t.title AS task_title, t.chat_id, i.text AS item_text FROM comments c "
            "JOIN tasks t ON t.id=c.task_id LEFT JOIN items i ON i.id=c.item_id "
            f"WHERE t.title<>'' AND (t.chat_id IN ({cm}) OR t.id IN ({tm})) ORDER BY c.id DESC LIMIT ?",
            (*chat_ids, *extra_task_ids, limit))

    async def comment(self, comment_id: int) -> dict | None:
        return await self.one("SELECT * FROM comments WHERE id=?", (comment_id,))

    async def delete_comment(self, comment_id: int):
        await self.x("DELETE FROM comments WHERE id=?", (comment_id,))

    # ---------- history ----------
    async def log_event(self, task_id: int, kind: str, actor: str, actor_id: int = 0,
                        item_id: int | None = None, text: str | None = None, extra: dict | None = None,
                        ts: int | None = None):
        cur = await self.x(
            "INSERT INTO events(task_id,ts,actor,actor_id,kind,item_id,text,extra) VALUES(?,?,?,?,?,?,?,?)",
            (task_id, ts or int(time.time()), actor, actor_id, kind, item_id, text,
             json.dumps(extra, ensure_ascii=False) if extra else None),
        )
        if self.on_event:
            try:
                self.on_event({"id": cur.lastrowid, "task_id": task_id, "kind": kind, "actor": actor,
                               "actor_id": actor_id, "text": text, "extra": extra or {}})
            except Exception:
                logging.getLogger("db").exception("on_event failed")

    async def event_exists(self, event_id: int) -> bool:
        return bool(await self.one("SELECT 1 FROM events WHERE id=?", (event_id,)))

    # ---- private notifications ----
    async def enable_alpha(self):
        """Preserve established users once, before the hosted service begins enforcing admission."""
        await self.conn.execute("CREATE TABLE IF NOT EXISTS alpha_initialized (id INTEGER PRIMARY KEY)")
        marker = await self.conn.execute("INSERT OR IGNORE INTO alpha_initialized VALUES(1)")
        if marker.rowcount:
            await self.conn.execute("""
                INSERT INTO waitlist(user_id, created, approved)
                SELECT uid, unixepoch(), unixepoch() FROM (
                    SELECT user_id AS uid FROM sessions UNION SELECT user_id FROM project_access
                    UNION SELECT owner_id FROM projects UNION SELECT actor_id FROM events
                    UNION SELECT author_id FROM tasks UNION SELECT assignee_id FROM tasks
                    UNION SELECT user_id FROM notify UNION SELECT user_id FROM project_order
                    UNION SELECT user_id FROM comments
                ) WHERE uid > 0
                ON CONFLICT(user_id) DO UPDATE SET approved=excluded.approved
            """)
        await self.conn.commit()

    async def alpha_allowed(self, uid: int) -> bool:
        return (not settings.closed_alpha or uid == settings.admin_id or
                bool(await self.one("SELECT 1 FROM waitlist WHERE user_id=? AND approved IS NOT NULL", (uid,))))

    async def join_waitlist(self, user_id: int, username: str | None, name: str) -> bool:
        cur = await self.x("INSERT OR IGNORE INTO waitlist(user_id,username,name,created) VALUES(?,?,?,?)",
                           (user_id, username, name, int(time.time())))
        return cur.rowcount > 0

    async def set_notify(self, user_id: int, enabled: bool):
        await self.x("INSERT INTO notify(user_id,enabled,updated_at) VALUES(?,?,?) "
                     "ON CONFLICT(user_id) DO UPDATE SET enabled=excluded.enabled, updated_at=excluded.updated_at",
                     (user_id, 1 if enabled else 0, int(time.time())))

    async def notify_kinds(self, user_id: int) -> list[str] | None:
        """What this person wants to get; None = everything."""
        r = await self.one("SELECT kinds FROM notify WHERE user_id=?", (user_id,))
        return json.loads(r["kinds"]) if r and r["kinds"] else None

    async def set_notify_kinds(self, user_id: int, kinds: list[str]):
        await self.x("INSERT INTO notify(user_id,enabled,updated_at,kinds) VALUES(?,0,?,?) "
                     "ON CONFLICT(user_id) DO UPDATE SET kinds=excluded.kinds, updated_at=excluded.updated_at",
                     (user_id, int(time.time()), json.dumps(kinds)))

    async def notify_enabled(self, user_id: int) -> bool:
        return bool(await self.one("SELECT 1 FROM notify WHERE user_id=? AND enabled=1", (user_id,)))

    async def task_people(self, task_id: int) -> list[int]:
        """Who takes part in a task and has notifications on: its author, its assignee and everyone who has done
    something in it."""
        rows = await self.all(
            "SELECT DISTINCT n.user_id FROM notify n WHERE n.enabled=1 AND n.user_id IN ("
            "SELECT author_id FROM tasks WHERE id=? UNION SELECT assignee_id FROM tasks WHERE id=? "
            "UNION SELECT actor_id FROM events WHERE task_id=? AND actor_id>0)",
            (task_id, task_id, task_id))
        return [r["user_id"] for r in rows]

    async def log_toggle(self, task_id: int, kind: str, opposite: str, actor: str, actor_id: int,
                         item_id: int | None = None, text: str | None = None, window: int = 120):
        """Tick → untick → tick by the same person within a couple of minutes is one change, not three."""
        last = await self.one(
            "SELECT * FROM events WHERE task_id=? AND actor_id=? AND kind IN (?,?) AND item_id IS ? AND ts>=? "
            "ORDER BY id DESC LIMIT 1",
            (task_id, actor_id, kind, opposite, item_id, int(time.time()) - window),
        )
        if last:
            await self.x("DELETE FROM events WHERE id=?", (last["id"],))
            if last["kind"] == opposite:      # it just went back to how it was
                return
        await self.log_event(task_id, kind, actor, actor_id, item_id, text)

    async def events(self, task_id: int, limit: int = 200) -> list[dict]:
        return await self.all("SELECT * FROM events WHERE task_id=? ORDER BY id DESC LIMIT ?", (task_id, limit))

    async def history_feed(self, chat_ids: list[int], flt: str, before: int | None, limit: int) -> list[dict]:
        """Events of these projects, newest first; `before` is the id of the last event already shown."""
        if not chat_ids:
            return []
        cond = HISTORY_FILTERS[flt]
        marks = ",".join("?" * len(chat_ids))
        return await self.all(
            "SELECT e.*, t.title AS task_title, t.chat_id AS pid, COALESCE(p.name, p.chat_title) AS project "
            "FROM events e JOIN tasks t ON t.id=e.task_id LEFT JOIN projects p ON p.chat_id=t.chat_id "
            f"WHERE t.chat_id IN ({marks}) AND t.title<>'' AND {cond} AND e.id<? ORDER BY e.id DESC LIMIT ?",
            (*chat_ids, before if before is not None else 2 ** 62, limit),
        )

    # ---- «Уведомления» ----
    async def notif_tasks(self, user_id: int) -> list[dict]:
        """Tasks this person takes part in: made it, does it, or has done something in it (a comment is an event too)."""
        return await self.all(
            "SELECT id, chat_id, assignee_id FROM tasks WHERE title<>'' AND (author_id=? OR assignee_id=? "
            "OR id IN (SELECT task_id FROM events WHERE actor_id=?))", (user_id, user_id, user_id))

    async def notif_seen(self, user_id: int) -> dict[str, int]:
        rows = await self.all("SELECT section, last_id FROM notif_seen WHERE user_id=?", (user_id,))
        seen = {r["section"]: r["last_id"] for r in rows}
        if len(seen) < len(NOTIF_SECTIONS):
            r = await self.one("SELECT COALESCE(MAX(id), 0) AS m FROM events WHERE ts<?", (int(time.time()) - NOTIF_FRESH,))
            for sec in NOTIF_SECTIONS:
                if sec not in seen:
                    seen[sec] = r["m"]
                    await self.x("INSERT OR IGNORE INTO notif_seen(user_id,section,last_id) VALUES(?,?,?)", (user_id, sec, r["m"]))
        return seen

    async def notif_mark(self, user_id: int, sections, upto: int):
        """Everything up to `upto` in these sections is read (and hand marks under it are forgotten)."""
        await self.notif_seen(user_id)
        for sec in sections:
            await self.x("UPDATE notif_seen SET last_id=MAX(last_id, ?) WHERE user_id=? AND section=?", (upto, user_id, sec))
            await self.x(f"DELETE FROM notif_state WHERE user_id=? AND event_id<=? AND event_id IN "
                         f"(SELECT e.id FROM events e WHERE {NOTIF_SECTIONS[sec]})", (user_id, upto))

    async def notif_set(self, user_id: int, event_id: int, read: bool):
        await self.x("INSERT INTO notif_state(user_id,event_id,read) VALUES(?,?,?) "
                     "ON CONFLICT(user_id,event_id) DO UPDATE SET read=excluded.read", (user_id, event_id, 1 if read else 0))

    @staticmethod
    def _unread_sql(seen: dict[str, int]) -> str:
        """1 when the event (alias e, its hand mark — alias s) is unread for the person."""
        line = " ".join(f"WHEN {c} THEN e.id>{int(seen[sec])}" for sec, c in NOTIF_SECTIONS.items())
        return f"(CASE WHEN s.read IS NOT NULL THEN s.read=0 ELSE (CASE {line} ELSE 0 END) END)"

    async def notif_unread(self, user_id: int, task_ids: list[int]) -> dict[str, int]:
        seen = await self.notif_seen(user_id)
        if not task_ids:
            return {sec: 0 for sec in NOTIF_SECTIONS}
        u = self._unread_sql(seen)
        sums = ", ".join(f"SUM({c} AND {u}) AS {sec}" for sec, c in NOTIF_SECTIONS.items())
        r = await self.one(
            f"SELECT {sums} FROM events e LEFT JOIN notif_state s ON s.user_id=? AND s.event_id=e.id "
            "WHERE e.task_id IN (SELECT value FROM json_each(?)) AND e.actor_id<>?",
            (user_id, json.dumps(task_ids), user_id))
        return {sec: int(r[sec] or 0) for sec in NOTIF_SECTIONS}

    async def notif_feed(self, user_id: int, task_ids: list[int], section: str, before: int | None, limit: int) -> list[dict]:
        if not task_ids:
            return []
        cond = NOTIF_SECTIONS.get(section, NOTIF_ANY)
        u = self._unread_sql(await self.notif_seen(user_id))
        return await self.all(
            f"SELECT e.*, t.title AS task_title, t.chat_id AS pid, COALESCE(p.name, p.chat_title) AS project, {u} AS unread "
            "FROM events e JOIN tasks t ON t.id=e.task_id LEFT JOIN projects p ON p.chat_id=t.chat_id "
            "LEFT JOIN notif_state s ON s.user_id=? AND s.event_id=e.id "
            f"WHERE e.task_id IN (SELECT value FROM json_each(?)) AND e.actor_id<>? AND {cond} "
            "AND e.id<? ORDER BY e.id DESC LIMIT ?",
            (user_id, json.dumps(task_ids), user_id, before if before is not None else 2 ** 62, limit))

    async def set_checklist_msg(self, task_id: int, msg_id: int):
        await self.x("UPDATE tasks SET checklist_msg_id=? WHERE id=?", (msg_id, task_id))

    async def task(self, task_id: int) -> dict | None:
        return await self.one("SELECT * FROM tasks WHERE id=?", (task_id,))

    async def items(self, task_id: int) -> list[dict]:
        return await self.all("SELECT * FROM items WHERE task_id=? ORDER BY pos, id", (task_id,))

    async def media(self, task_id: int) -> list[dict]:
        return await self.all("SELECT * FROM media WHERE task_id=? ORDER BY idx", (task_id,))

    async def project_stats(self, since: int) -> dict[int, dict]:
        """Per project, in one pass: open tasks, ticked / all items of tasks not archived, tasks made since `since`."""
        rows = await self.all(
            "SELECT t.chat_id, "
            " SUM(CASE WHEN COALESCE(t.archived,0)=0 AND COALESCE(t.backlog,0)=0 AND COALESCE(t.closed,0)=0 THEN 1 ELSE 0 END) AS open, "
            " SUM(CASE WHEN COALESCE(t.archived,0)=0 THEN COALESCE(i.done_cnt,0) ELSE 0 END) AS items_done, "
            " SUM(CASE WHEN COALESCE(t.archived,0)=0 THEN COALESCE(i.total,0) ELSE 0 END) AS items_total, "
            " SUM(CASE WHEN t.created_at > ? THEN 1 ELSE 0 END) AS today "
            "FROM tasks t LEFT JOIN (SELECT task_id, COUNT(*) AS total, SUM(done=1) AS done_cnt FROM items GROUP BY task_id) i "
            " ON i.task_id=t.id WHERE t.title<>'' GROUP BY t.chat_id",
            (since,))
        return {r.pop("chat_id"): r for r in rows}

    async def tasks_of_chat(self, chat_id: int) -> list[dict]:
        return await self.all(
            "SELECT t.*, "
            " (SELECT COUNT(*) FROM items i WHERE i.task_id=t.id) AS total, "
            " (SELECT COUNT(*) FROM items i WHERE i.task_id=t.id AND i.done=1) AS done_cnt "
            "FROM tasks t WHERE chat_id=? AND title<>'' ORDER BY COALESCE(t.rank, t.id) DESC, t.id DESC",
            (chat_id,),
        )

    async def toggle_item_by_pos(self, task_id: int, pos_index: int) -> bool:
        items = await self.items(task_id)
        if not 0 <= pos_index < len(items):
            return False
        it = items[pos_index]
        await self.x("UPDATE items SET done=1-done WHERE id=?", (it["id"],))
        return True

    async def item(self, item_id: int) -> dict | None:
        return await self.one("SELECT * FROM items WHERE id=?", (item_id,))

    async def update_item(self, item_id: int, **fields):
        allowed = {k: v for k, v in fields.items() if k in ("text", "done", "shot")}
        if not allowed:
            return
        sets = ", ".join(f"{k}=?" for k in allowed)
        await self.x(f"UPDATE items SET {sets} WHERE id=?", (*allowed.values(), item_id))

    async def add_item(self, task_id: int, text: str) -> int:
        row = await self.one("SELECT COALESCE(MAX(pos),-1)+1 AS p FROM items WHERE task_id=?", (task_id,))
        cur = await self.x("INSERT INTO items(task_id,pos,text) VALUES(?,?,?)", (task_id, row["p"], text))
        return cur.lastrowid

    async def delete_item(self, item_id: int):
        await self.x("DELETE FROM items WHERE id=?", (item_id,))
        await self.x("UPDATE comments SET item_id=NULL WHERE item_id=?", (item_id,))   # its comments stay on the task

    async def reorder(self, task_id: int, item_ids: list[int]):
        await self.conn.executemany(
            "UPDATE items SET pos=? WHERE id=? AND task_id=?", [(i, iid, task_id) for i, iid in enumerate(item_ids)]
        )
        await self._commit()

    async def reorder_tasks(self, chat_id: int, task_ids: list[int]):
        """Put these tasks of the chat in this order (top first). They swap the places they already hold,
        so tasks in other tabs keep theirs and a new task still lands on top."""
        marks = ",".join("?" * len(task_ids))
        rows = await self.all(f"SELECT id, COALESCE(rank, id) AS r FROM tasks WHERE chat_id=? AND id IN ({marks})",
                              (chat_id, *task_ids))
        have = {r["id"] for r in rows}
        ids = [i for i in dict.fromkeys(task_ids) if i in have]
        ranks = sorted((r["r"] for r in rows), reverse=True)
        await self.conn.executemany("UPDATE tasks SET rank=? WHERE id=?", list(zip(ranks, ids)))
        await self._commit()

    async def update_task(self, task_id: int, **fields):
        allowed = {k: v for k, v in fields.items() if k in ("title", "archived", "backlog", "progress", "auto", "closed", "result_url", "hours", "result_note", "descr", "req_id", "review_at", "review_n",
                                                                     "assignee_id", "assignee_name")}
        if not allowed:
            return
        sets = ", ".join(f"{k}=?" for k in allowed)
        await self.x(f"UPDATE tasks SET {sets} WHERE id=?", (*allowed.values(), task_id))


db = DB(settings.db_path)
