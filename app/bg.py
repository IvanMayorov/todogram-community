"""Fire-and-forget work (a task from ✍, a private notification …) that nobody awaits.

asyncio keeps only a weak reference to a running task: one nobody holds can be garbage-collected halfway
and vanish without a trace. spawn() holds it until it finishes.
"""
import asyncio

_running: set[asyncio.Task] = set()


def spawn(coro) -> asyncio.Task:
    t = asyncio.get_running_loop().create_task(coro)
    _running.add(t)
    t.add_done_callback(_running.discard)
    return t
