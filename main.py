"""Runs everything in one process: the Telegram bot (long polling), the news collector, the alert sender,
the daily digests, the nightly backup, and the web admin page with /health, /ready and /metrics."""
import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

# Logging first: importing db connects to the database and logs while it waits for it.
logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

import config  # noqa: E402
from bot import OnboardingAutosave, build_application, nightly_backup_loop  # noqa: E402
from collector import Collector  # noqa: E402
from db import db, load_seed, load_technologies  # noqa: E402
from delivery import Digester, Sender  # noqa: E402
from web import create_web_app  # noqa: E402

log = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Tables: apply any new migrations before anything touches the database.
    applied = db.migrate()
    log.info("Database schema %s (version %s)", f"migrated: applied {', '.join(applied)}" if applied
             else "up to date", db.schema_version())
    # 2. Topics and providers new in seed/ (existing ones are left as the admins set them).
    added_categories, added_providers = db.seed(*load_seed())
    added_technologies = db.seed_technologies(load_technologies()[1])
    if added_categories or added_providers or added_technologies:
        log.info("Seeded %s topics, %s providers and %s technologies", added_categories, added_providers,
                 added_technologies)

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
                  asyncio.create_task(Digester(tg_app.bot).run_forever(), name="digest"),
                  asyncio.create_task(OnboardingAutosave(tg_app.bot).run_forever(), name="autosave")]
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
