"""Sending alerts to Telegram users.

The Sender works through queued notifications: it re-checks that the user still wants the alert, sends
it (photo with caption when the feed gave an image, text otherwise), and records the result. Temporary
errors are retried with exponential backoff; Telegram's retry_after is respected; permanent errors and
users who blocked the bot are not retried. The Digester sends each digest user one message a day.
"""
import asyncio
import html
import logging
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest, ChatMigrated, Forbidden, NetworkError, RetryAfter, TelegramError

import config
import metrics
import texts
from db import Article, Category, Notification, User, db, now_utc, to_local

log = logging.getLogger(__name__)

MUTE_FROM_ALERT = "mua:"  # mua:<category_id>, handled in bot.py
CAPTION_LIMIT = 1024
TEXT_LIMIT = 4096
BATCH = 30
BASE_RETRY_SECONDS = 30


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def format_alert(article: Article, provider_name: str, category: Category | None, language: str | None,
                 limit: int) -> tuple[str, InlineKeyboardMarkup]:
    """The alert text (HTML) and its buttons. Only what the provider gave us is shown; when it gives no
    publication time we say when we found the story instead of inventing one."""
    L = texts.get(language)
    head = f"{category.emoji} <b>{html.escape(category.name(language))}</b>\n" if category else ""
    title = f"<b>{html.escape(article.title)}</b>"
    when_label, when_value = (L.PUBLISHED, article.published_at) if article.published_at else (L.FOUND, article.fetched_at)
    footer = (f"\n\n📰 {L.SOURCE}: {html.escape(provider_name)}"
              f"\n🕒 {when_label}: {L.format_time(to_local(when_value))}")
    raw_summary = article.summary or ""
    while True:
        body = f"\n\n{html.escape(raw_summary)}" if raw_summary else ""
        text = head + title + body + footer
        if len(text) <= limit or not raw_summary:
            break
        # Shorten the raw summary (not the escaped HTML) so an entity is never cut in half.
        raw_summary = _truncate(raw_summary, len(raw_summary) - (len(text) - limit) - 1) if len(raw_summary) > 20 else ""
    if len(text) > limit:  # a very long headline: shorten it too
        title = f"<b>{html.escape(_truncate(article.title, max(40, limit - len(head) - len(footer) - 20)))}</b>"
        text = head + title + footer
    rows = [[InlineKeyboardButton(L.READ_FULL, url=article.canonical_url)]]
    if category:
        rows.append([InlineKeyboardButton(L.mute_button(category.name(language)),
                                          callback_data=f"{MUTE_FROM_ALERT}{category.id}")])
    return text, InlineKeyboardMarkup(rows)


def _retry_after_seconds(e: RetryAfter) -> float:
    value = e.retry_after
    return float(value if isinstance(value, (int, float)) else value.total_seconds())


def _is_photo_problem(e: BadRequest) -> bool:
    message = str(e).lower()
    return any(s in message for s in ("photo", "image", "url", "file", "webpage", "media", "dimensions"))


class Sender:
    def __init__(self, bot):
        self.bot = bot
        self._wake = asyncio.Event()
        self._paused_until: datetime | None = None
        self.last_loop_at: datetime | None = None

    def wake(self) -> None:
        self._wake.set()

    async def run_forever(self) -> None:
        log.info("Sender started")
        interval = 1.0 / max(config.MESSAGES_PER_SECOND, 0.1)
        while True:
            self.last_loop_at = now_utc()
            try:
                if self._paused_until and now_utc() < self._paused_until:
                    await asyncio.sleep((self._paused_until - now_utc()).total_seconds())
                due = db.due_notifications(BATCH)
                for notification in due:
                    await self.send_one(notification)
                    await asyncio.sleep(interval)
                if len(due) == BATCH:
                    continue
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Sender loop error")
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass

    def _eligible_category(self, n: Notification, user: User | None, article: Article | None) -> int | None | bool:
        """The category to show, or False if the alert should be skipped (user paused, unsubscribed,
        muted, blocked the bot, switched to the digest…). Uses the same rules as queuing."""
        if not user or not article:
            return False
        matches = dict(db.matching_users(article))
        if user.telegram_id not in matches:
            return False
        followed = db.interests(user.telegram_id)
        return n.category_id if followed.get(n.category_id) else matches[user.telegram_id]

    async def send_one(self, n: Notification) -> str:
        """Send one queued alert. Returns the new status."""
        user = db.get_user(n.user_id)
        article = db.get_article(n.article_id)
        if user and user.delivery_mode == "digest":
            db.mark_notification(n.id, "digest", count_attempt=False)
            return "digest"
        category_id = self._eligible_category(n, user, article)
        if category_id is False:
            db.mark_notification(n.id, "skipped", "no longer eligible", count_attempt=False)
            metrics.inc("notifications_skipped")
            return "skipped"
        provider = db.get_provider(article.provider_id)
        category = db.get_category(category_id) if category_id else None
        try:
            message_id = await self._send(user, article, provider.name if provider else "", category)
        except RetryAfter as e:
            wait = _retry_after_seconds(e)
            self._paused_until = now_utc() + timedelta(seconds=wait)
            db.mark_notification(n.id, "queued", f"rate limited ({wait:g}s)",
                                 retry_at=now_utc() + timedelta(seconds=wait), count_attempt=False)
            metrics.inc("telegram_rate_limited")
            log.warning("Telegram rate limit: waiting %ss", wait)
            return "queued"
        except Forbidden as e:
            # The user blocked the bot or deleted their account: stop sending to them.
            db.update_user(user.telegram_id, status="blocked")
            db.mark_notification(n.id, "failed", f"forbidden: {e}")
            db.skip_queued_for_user(user.telegram_id, "user blocked the bot")
            metrics.inc("notifications_failed")
            log.info("User %s blocked the bot (%s)", user.telegram_id, e)
            return "failed"
        except (BadRequest, ChatMigrated) as e:
            db.mark_notification(n.id, "failed", f"permanent: {e}")
            metrics.inc("notifications_failed")
            log.warning("Alert %s to %s failed permanently: %s", n.id, user.telegram_id, e)
            return "failed"
        except (NetworkError, TelegramError) as e:
            attempts = n.attempt_count + 1
            if attempts >= config.MAX_SEND_ATTEMPTS:
                db.mark_notification(n.id, "failed", f"gave up after {attempts} attempts: {e}")
                metrics.inc("notifications_failed")
                return "failed"
            delay = BASE_RETRY_SECONDS * 2 ** (attempts - 1)
            db.mark_notification(n.id, "queued", str(e), retry_at=now_utc() + timedelta(seconds=delay))
            metrics.inc("notifications_retried")
            log.info("Alert %s to %s will be retried in %ss: %s", n.id, user.telegram_id, delay, e)
            return "queued"
        db.mark_notification(n.id, "sent", message_id=message_id)
        metrics.inc("notifications_sent")
        metrics.observe_delivery(article)
        return "sent"

    async def _send(self, user: User, article: Article, provider_name: str, category: Category | None) -> int:
        if article.image_url:
            caption, markup = format_alert(article, provider_name, category, user.language, CAPTION_LIMIT)
            try:
                message = await self.bot.send_photo(user.chat_id, article.image_url, caption=caption,
                                                    parse_mode=ParseMode.HTML, reply_markup=markup)
                return message.message_id
            except BadRequest as e:
                if not _is_photo_problem(e):
                    raise
                # Telegram couldn't use the image (gone, too big, not an image): send text instead.
                metrics.inc("image_fallbacks")
                log.info("Image for article %s not usable (%s); sending text", article.id, e)
        text, markup = format_alert(article, provider_name, category, user.language, TEXT_LIMIT)
        message = await self.bot.send_message(user.chat_id, text, parse_mode=ParseMode.HTML, reply_markup=markup,
                                              disable_web_page_preview=True)
        return message.message_id


class Digester:
    """Once a minute: users whose digest hour has come (in TIMEZONE) and who haven't had today's digest
    get one message listing their held stories."""

    def __init__(self, bot):
        self.bot = bot

    async def run_forever(self) -> None:
        while True:
            try:
                await self.run_once(datetime.now(config.TIMEZONE))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Digest loop error")
            await asyncio.sleep(60)

    async def run_once(self, local_now: datetime) -> int:
        sent = 0
        today = local_now.date().isoformat()
        for user in db.digest_users_due(local_now.hour, today):
            items = db.digest_items(user.telegram_id)
            db.update_user(user.telegram_id, last_digest_date=today)
            if not items:
                continue
            L = texts.get(user.language)
            shown = items[:config.DIGEST_MAX_ARTICLES]
            providers = {p.id: p.name for p in db.providers()}
            lines = [L.digest_title(local_now.strftime("%d/%m/%Y"), len(items))]
            for i, (_, a) in enumerate(shown, 1):
                lines.append(f"{i}. <a href=\"{html.escape(a.canonical_url, quote=True)}\">"
                             f"{html.escape(_truncate(a.title, 140))}</a> — {html.escape(providers.get(a.provider_id, ''))}")
            if len(items) > len(shown):
                lines.append(L.digest_more(len(items) - len(shown)))
            text = "\n".join(lines)
            if len(text) > TEXT_LIMIT:
                text = text[:TEXT_LIMIT - 1].rsplit("\n", 1)[0]
            try:
                await self.bot.send_message(user.chat_id, text, parse_mode=ParseMode.HTML,
                                            disable_web_page_preview=True)
            except Forbidden:
                db.update_user(user.telegram_id, status="blocked")
                db.skip_queued_for_user(user.telegram_id, "user blocked the bot")
                continue
            except TelegramError as e:
                log.warning("Digest to %s failed: %s", user.telegram_id, e)
                db.update_user(user.telegram_id, last_digest_date=None)  # try again next minute
                continue
            for n, _ in shown:
                db.mark_notification(n.id, "sent")
            for n, _ in items[len(shown):]:
                db.mark_notification(n.id, "skipped", "over the digest limit", count_attempt=False)
            metrics.inc("digests_sent")
            sent += 1
        return sent
