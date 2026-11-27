"""Runs everything in one process: the Telegram bot (long polling), the news collector, the alert sender,
the daily digests, the nightly backup, and the web admin page with /health, /ready and /metrics."""
import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

import config
from bot import build_application, nightly_backup_loop
from collector import Collector
from db import db, load_seed
from delivery import Digester, Sender
from web import create_web_app

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    added_categories, added_providers = db.seed(*load_seed())
    if added_categories or added_providers:
        log.info("Seeded %s topics and %s providers", added_categories, added_providers)

    tg_app = build_application()
    await tg_app.initialize()
    await tg_app.start()
    await tg_app.updater.start_polling(drop_pending_updates=False)
    app.state.telegram = tg_app
    log.info("Bot started as @%s", tg_app.bot.username)

    tasks = [asyncio.create_task(nightly_backup_loop(tg_app), name="backup")]
    sender = Sender(tg_app.bot)
    app.state.sender = tg_app.bot_data["sender"] = sender
    if config.DELIVERY_ENABLED:
        tasks += [asyncio.create_task(sender.run_forever(), name="sender"),
                  asyncio.create_task(Digester(tg_app.bot).run_forever(), name="digest")]
    collector = Collector(on_queued=sender.wake)
    app.state.collector = tg_app.bot_data["collector"] = collector
    if config.COLLECTOR_ENABLED:
        tasks.append(asyncio.create_task(collector.run_forever(), name="collector"))
    try:
        yield
    finally:
        # Graceful shutdown: stop taking work, let the current send finish, then stop Telegram.
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()
        db.close()
        log.info("Stopped")


app = create_web_app(lifespan=lifespan)

if __name__ == "__main__":
    if not config.ADMIN_PASSWORD:
        log.warning("ADMIN_PASSWORD is empty: the web admin page will refuse all logins.")
    uvicorn.run(app, host=config.WEB_HOST, port=config.WEB_PORT)
