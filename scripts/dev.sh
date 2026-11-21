#!/bin/bash
# Web-only demo of the Todogram mini app in a regular browser.
# Does not start Telegram polling and does not touch the real BOT_TOKEN
# or data/bot.sqlite3: those stay unused even though app.config loads .env
# (existing environment variables win over it).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${ROOT}/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "No .venv at $ROOT/.venv — create it first." >&2
  exit 1
fi

HOST="${WEB_HOST:-127.0.0.1}"
PORT="${WEB_PORT:-8765}"
DEMO_DB="${DEMO_DB:-/tmp/todogram-demo.sqlite3}"
DEMO_CACHE="${DEMO_CACHE:-/tmp/todogram-demo-cache}"
mkdir -p "$DEMO_CACHE"

# Must be exported before Python imports app.config.
export BOT_TOKEN="123456:TEST-token"
export DB_PATH="$DEMO_DB"
export CACHE_DIR="$DEMO_CACHE"
export WEB_HOST="$HOST"
export WEB_PORT="$PORT"
# Avoid picking up a real AI key from the environment / accidentally
# calling a provider; the browser demo does not need one.
export AI_API_KEY="x"

echo "Todogram demo: http://${HOST}:${PORT}/?demo"
echo "In-browser demo data only (not production). Press Ctrl+C to stop."
echo "DB: $DEMO_DB"

exec "$PY" - "$HOST" "$PORT" << 'PY'
import asyncio
import signal
import sys

from aiohttp import web

from app.db import db
from app.web import make_app


class FakeBot:
    """Stand-in so the web server starts without talking to Telegram."""

    async def get_chat(self, chat_id):
        class C:
            photo = None
        return C()

    async def get_user_profile_photos(self, user_id, limit=1):
        class R:
            photos = []
        return R()

    async def get_chat_member(self, chat_id, user_id):
        class M:
            status = "left"
        return M()

    async def me(self):
        class U:
            username = "testbot"
            id = 999
        return U()

    async def download(self, file_id, destination):
        destination.write(b"\xff\xd8fake")


async def main(host: str, port: int) -> None:
    await db.connect()
    runner = web.AppRunner(make_app(FakeBot()))
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    print(f"READY http://{host}:{port}/?demo", flush=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    await runner.cleanup()
    await db.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], int(sys.argv[2])))
PY
