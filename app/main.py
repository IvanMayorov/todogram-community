"""Entry point: Telegram bot (long polling) + mini app web server in one process."""
import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiohttp import web

from . import auto, deadlines, notify, review, tasks
from .config import settings
from .db import db
from .handlers import alpha_gate, router
from .web import make_app


async def main():
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("main")
    if not settings.bot_token:
        sys.exit("BOT_TOKEN is empty: put it into .env")
    if not settings.ai_api_key:
        log.warning("AI_API_KEY is empty: tasks will fail until it is set in .env")

    if settings.closed_alpha and settings.admin_id <= 0:
        log.warning("Closed alpha is collecting applications; set ADMIN_ID to enable approvals")
    await db.connect()
    if settings.closed_alpha:
        await db.enable_alpha()
    bot = Bot(settings.bot_token, default=DefaultBotProperties(link_preview_is_disabled=True))
    me = await bot.me()
    tasks.BOT_USERNAME = me.username
    log.info("bot @%s started", me.username)
    notify.setup(bot)

    dp = Dispatcher()
    if settings.closed_alpha:
        dp.update.outer_middleware(alpha_gate)
    dp.include_router(router)

    runner = web.AppRunner(make_app(bot))
    await runner.setup()
    await web.TCPSite(runner, settings.web_host, settings.web_port).start()
    log.info("mini app on http://%s:%s", settings.web_host, settings.web_port)

    loops = [asyncio.create_task(tasks.drafts_loop(bot)), asyncio.create_task(auto.auto_loop(bot)),
             asyncio.create_task(deadlines.loop(bot)), asyncio.create_task(review.loop(bot))]
    try:
        # message_reaction is not delivered unless it is requested explicitly
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        for t in loops:
            t.cancel()
        await notify.flush_all()   # changes still waiting to be sent go out now
        await runner.cleanup()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
