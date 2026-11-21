"""«Something changed»: a push to every open mini app, so it refreshes now instead of on its next poll.

Every write to the database that the app can show calls changed(). Writes close together are merged into one
push (DEBOUNCE). The push carries no data: each app asks for its current screen the usual way (ETag → 304 when
nothing of its own changed), so nobody sees what they may not, and the server keeps one code path for answers.
"""
import asyncio
import re

from aiohttp import web

DEBOUNCE = 0.4      # seconds: a task with ten items is one push, not ten
HEARTBEAT = 25      # seconds: a comment line, so nginx and phone networks don't drop a quiet stream

# tables nobody sees in the app: the chat buffer gets every message of every chat, sign-in bookkeeping
_QUIET = {"messages", "drafts", "logins", "sessions", "waitlist"}
_TABLE = re.compile(r"^\s*(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|REPLACE\s+INTO|UPDATE|DELETE\s+FROM)\s+(\w+)", re.I)

_listeners: set[asyncio.Queue] = set()
_pending = False


def changed(sql: str | None = None):
    """Call after a write; `sql` lets writes to tables nobody sees stay quiet."""
    global _pending
    if sql is not None:
        m = _TABLE.match(sql)
        if m and m.group(1).lower() in _QUIET:
            return
    if _pending or not _listeners:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _pending = True
    loop.call_later(DEBOUNCE, _fire)


def _fire():
    global _pending
    _pending = False
    for q in _listeners:
        if q.empty():
            q.put_nowait(True)


async def close_all(_app=None):
    """Server shutting down: end every stream now, so the restart doesn't wait for them."""
    for q in _listeners:
        q.put_nowait(False)


async def stream(request: web.Request, user: dict) -> web.StreamResponse:
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache",
                                       "X-Accel-Buffering": "no"})   # nginx: pass every line through at once
    await resp.prepare(request)
    q: asyncio.Queue = asyncio.Queue()
    _listeners.add(q)
    try:
        await resp.write(b"retry: 3000\n\n")
        while True:
            try:
                go = await asyncio.wait_for(q.get(), HEARTBEAT)
            except asyncio.TimeoutError:
                await resp.write(b": ping\n\n")
                continue
            if not go:
                break
            await resp.write(b"data: changed\n\n")
    except ConnectionResetError:
        pass   # the app closed or the phone lost the network: it connects again by itself
    finally:
        _listeners.discard(q)
    return resp
