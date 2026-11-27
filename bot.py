"""Telegram bot: onboarding, topics, settings, pause / resume, sources, and admin tools.

Alerts themselves are sent by delivery.py; news is collected by collector.py. Every text shown in
Telegram lives in texts_am.py / texts_en.py; each person sees their own language.
"""
import asyncio
import html
import logging
import unicodedata
from datetime import datetime

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.error import RetryAfter, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import backup
import config
import texts
import metrics
from db import User, day_start_utc, db, local_today, now_utc, parse_iso, to_local
from delivery import MUTE_FROM_ALERT, send_alert

log = logging.getLogger(__name__)

# Inline button callback data. Everything after the prefix is checked before it's used.
SET_LANGUAGE = "lang:"   # lang:<code>
TOPIC = "tp:"            # tp:<o|i>:<category_id>   o = onboarding, i = /interests
TOPIC_ALL = "tpa:"       # tpa:<o|i>
TOPIC_NONE = "tpn:"      # tpn:<o|i>
TOPIC_SAVE = "tps:"      # tps:<o|i>
TOPIC_SKIP = "tpx:"      # tpx:<o|i>   skip (onboarding) or cancel (/interests)
ONBOARD_DELIVERY = "ob:"  # ob:<instant|digest>
SETTINGS = "st:"         # st:<screen or action>[:<value>]
DIGEST = "dg:"           # dg:<instant|digest> | dg:h:<hour>
ADMIN = "ad:"            # ad:<action>[:<id>][:<page>]
BACKUP = "bk:"           # bk:now | bk:on | bk:off
PULL_MORE = "pull:more"  # the "more" button under a 📥 Latest news batch
NOOP = "noop"
PULL_PAGE = 5  # stories per 📥 Latest news tap

DIGEST_HOURS = (6, 7, 8, 9, 12, 13, 18, 20, 21)
DAILY_LIMITS = (10, 20, 30, 50, 100, 0)
PROVIDERS_PER_PAGE = 8


def tr(user: User | None):
    """The texts module for a person's language."""
    return texts.get(user.language if user else None)


def _button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)


PAD = "⠀"  # Braille blank: invisible, and Telegram doesn't trim it like spaces


def _width(text: str) -> int:
    """Roughly how wide text looks: emoji and wide characters count 2, joiners and accents 0."""
    width = 0
    for i, ch in enumerate(text):
        if unicodedata.category(ch) in ("Mn", "Me", "Cf") or "\ufe00" <= ch <= "\ufe0f":
            continue
        if "\U0001f1e6" <= ch <= "\U0001f1ff":  # a flag is two of these, shown as one emoji
            width += 1
            continue
        emoji_style = (i + 1 < len(text) and text[i + 1] == "\ufe0f") or ch >= "\U0001f000"
        width += 2 if emoji_style or unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def _column(items: list[tuple[str, str]]) -> list[list[InlineKeyboardButton]]:
    """One button per row with the text lined up on the left. Telegram centres button text, so every
    label is padded to the same width with an invisible character (as in the report bot's users list)."""
    width = max((_width(label) for label, _ in items), default=0)
    return [[_button(label + PAD * (width - _width(label)), data)] for label, data in items]


def _pairs(buttons: list, size: int = 2) -> list[list]:
    return [buttons[i:i + size] for i in range(0, len(buttons), size)]


def _commands(user: User) -> list[BotCommand]:
    L = tr(user)
    commands = [BotCommand("start", L.CMD_START), BotCommand("latest", L.CMD_LATEST),
                BotCommand("interests", L.CMD_INTERESTS),
                BotCommand("settings", L.CMD_SETTINGS), BotCommand("digest", L.CMD_DIGEST),
                BotCommand("pause", L.CMD_PAUSE), BotCommand("resume", L.CMD_RESUME),
                BotCommand("sources", L.CMD_SOURCES), BotCommand("language", L.CMD_LANGUAGE),
                BotCommand("help", L.CMD_HELP)]
    if user.is_admin:
        commands.append(BotCommand("admin", L.CMD_ADMIN))
    if user.is_super_admin:
        commands.append(BotCommand("backup", L.CMD_BACKUP))
    return commands


def main_keyboard(user: User) -> ReplyKeyboardMarkup:
    L = tr(user)
    rows = [[KeyboardButton(L.BTN_PULL)],
            [KeyboardButton(L.BTN_TOPICS), KeyboardButton(L.BTN_SETTINGS)],
            [KeyboardButton(L.BTN_PAUSE if user.notifications_enabled else L.BTN_RESUME),
             KeyboardButton(L.BTN_DIGEST)],
            [KeyboardButton(L.BTN_SOURCES), KeyboardButton(L.BTN_HELP)]]
    last = [KeyboardButton(texts.LANGUAGE_BUTTON)]
    if user.is_admin:
        last.append(KeyboardButton(L.BTN_ADMIN))
    rows.append(last)
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True,
                               input_field_placeholder=L.KEYBOARD_PLACEHOLDER)


def language_picker() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[_button(name, f"{SET_LANGUAGE}{code}")
                                  for code, name in texts.LANGUAGE_NAMES.items()]])


# ---------- helpers ----------
def _register(update: Update) -> User:
    tg = update.effective_user
    return db.upsert_user(tg.id, update.effective_chat.id, tg.username, tg.full_name)


async def _show(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str,
                keyboard: InlineKeyboardMarkup | None) -> None:
    """Replace the tapped message (keeps the chat tidy), or send a new one."""
    query = update.callback_query
    if query:
        try:
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard,
                                          disable_web_page_preview=True)
            return
        except TelegramError as e:
            if "not modified" in str(e).lower():
                return
    await context.bot.send_message(update.effective_chat.id, text, parse_mode=ParseMode.HTML,
                                   reply_markup=keyboard, disable_web_page_preview=True)


async def _send(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, reply_markup=None) -> None:
    await context.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, reply_markup=reply_markup,
                                   disable_web_page_preview=True)


async def set_commands_for(app: Application, user: User) -> None:
    """The command menu, in the user's language; admin commands only for admins."""
    try:
        await app.bot.set_my_commands(_commands(user), scope=BotCommandScopeChat(user.chat_id))
    except TelegramError as e:
        log.warning("Could not set commands for %s: %s", user.telegram_id, e)


async def _active_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> User | None:
    """Register the caller. Returns None (after replying) if they were turned off by an admin or haven't
    picked a language yet (then they're asked for one)."""
    user = _register(update)
    if user.status == "disabled":
        if update.callback_query:
            await update.callback_query.answer(tr(user).DISABLED, show_alert=True)
        else:
            await _send(context, update.effective_chat.id, tr(user).DISABLED, ReplyKeyboardRemove())
        return None
    if not user.language:
        if update.callback_query:
            await update.callback_query.answer()
        await _send(context, update.effective_chat.id, texts.LANGUAGE_PROMPT, language_picker())
        return None
    return user


def _topic_names(user: User, only_enabled: bool = False) -> str:
    interests = db.interests(user.telegram_id)
    names = [c.label(user.language) for c in db.categories()
             if c.id in interests and (interests[c.id] or not only_enabled)]
    return ", ".join(names) if names else tr(user).NONE


# ---------- start / language ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _active_user(update, context)
    if not user:
        return
    await set_commands_for(context.application, user)
    if not user.onboarding_completed:
        await _start_onboarding(update, context, user)
        return
    L = tr(user)
    name = html.escape(update.effective_user.first_name or "")
    await _send(context, update.effective_chat.id, L.welcome_back(name) + L.MENU_HINT, main_keyboard(user))


async def _start_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    context.user_data["topics_draft"] = set(db.interests(user.telegram_id))
    text, keyboard = _topic_picker(user, "o", context.user_data["topics_draft"],
                                   update.effective_user.first_name)
    await _send(context, update.effective_chat.id, text, keyboard)


async def choose_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The 🌐 button or /language: show the language picker."""
    _register(update)
    await _send(context, update.effective_chat.id, texts.LANGUAGE_PROMPT, language_picker())


async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """lang:<code>: save the language, then continue onboarding or confirm the change."""
    query = update.callback_query
    code = query.data[len(SET_LANGUAGE):]
    if code not in texts.LANGUAGES:
        await query.answer()
        return
    user = _register(update)
    if user.status == "disabled":
        await query.answer(tr(user).DISABLED, show_alert=True)
        return
    user = db.update_user(user.telegram_id, language=code)
    await query.answer()
    await _show(update, context, f"🌐 {texts.LANGUAGE_NAMES[code]}", None)
    await set_commands_for(context.application, user)
    if not user.onboarding_completed:
        await _start_onboarding(update, context, user)
    else:
        await _send(context, update.effective_chat.id, tr(user).LANGUAGE_SET, main_keyboard(user))


# ---------- topics (onboarding and /interests) ----------
def _topic_picker(user: User, mode: str, draft: set[int], first_name: str | None = None
                  ) -> tuple[str, InlineKeyboardMarkup]:
    L = tr(user)
    categories = db.categories()
    text = (L.welcome(html.escape(first_name or "")) if mode == "o" else "") + L.TOPICS_PROMPT
    text += L.topics_selected(len(draft & {c.id for c in categories}))
    rows = _column([(("✅ " if c.id in draft else "⬜ ") + c.label(user.language), f"{TOPIC}{mode}:{c.id}")
                    for c in categories])
    rows.append([_button(L.TOPIC_ALL, f"{TOPIC_ALL}{mode}"), _button(L.TOPIC_NONE, f"{TOPIC_NONE}{mode}")])
    rows.append([_button(L.TOPIC_SAVE, f"{TOPIC_SAVE}{mode}"),
                 _button(L.TOPIC_SKIP if mode == "o" else L.TOPIC_CANCEL, f"{TOPIC_SKIP}{mode}")])
    return text, InlineKeyboardMarkup(rows)


async def interests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🗞 My topics or /interests: the topic picker with the saved topics ticked."""
    user = await _active_user(update, context)
    if not user:
        return
    if update.callback_query:
        await update.callback_query.answer()
    context.user_data["topics_draft"] = set(db.interests(user.telegram_id))
    text, keyboard = _topic_picker(user, "i", context.user_data["topics_draft"])
    await _send(context, update.effective_chat.id, text, keyboard)


def _draft(context: ContextTypes.DEFAULT_TYPE, user: User) -> set[int]:
    """The selection being edited. After a restart it starts again from the saved topics."""
    if "topics_draft" not in context.user_data:
        context.user_data["topics_draft"] = set(db.interests(user.telegram_id))
    return context.user_data["topics_draft"]


async def topic_tap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """tp:/tpa:/tpn: select or clear topics in the picker."""
    query = update.callback_query
    user = await _active_user(update, context)
    if not user:
        return
    L = tr(user)
    prefix, rest = query.data.split(":", 1)
    mode = rest[0]
    draft = _draft(context, user)
    active = {c.id for c in db.categories()}
    warning = None
    if prefix == "tpa":
        draft |= active
    elif prefix == "tpn":
        draft.clear()
    else:
        category_id = int(rest.split(":")[1])
        if category_id not in active:
            warning = L.CATEGORY_GONE
        else:
            draft.symmetric_difference_update({category_id})
    draft &= active
    await query.answer(warning, show_alert=bool(warning))
    text, keyboard = _topic_picker(user, mode, draft, update.effective_user.first_name)
    await _show(update, context, text, keyboard)


async def topic_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """tps:<mode>: save the selection. In onboarding, then ask how news should arrive."""
    query = update.callback_query
    user = await _active_user(update, context)
    if not user:
        return
    L = tr(user)
    mode = query.data[len(TOPIC_SAVE):]
    draft = _draft(context, user) & {c.id for c in db.categories()}
    if mode == "o" and not draft:
        await query.answer(L.TOPIC_NEED_ONE, show_alert=True)
        return
    await query.answer()
    db.set_interests(user.telegram_id, draft)
    context.user_data.pop("topics_draft", None)
    log.info("User %s saved %s topics", user.telegram_id, len(draft))
    if mode == "o":
        keyboard = InlineKeyboardMarkup([[_button(L.DELIVERY_INSTANT, f"{ONBOARD_DELIVERY}instant")],
                                         [_button(L.DELIVERY_DIGEST, f"{ONBOARD_DELIVERY}digest")]])
        await _show(update, context, L.DELIVERY_PROMPT, keyboard)
        return
    user = db.update_user(user.telegram_id, onboarding_completed=True)
    await _show(update, context, L.interests_saved(_topic_names(user)) if draft else L.INTERESTS_EMPTY, None)


async def topic_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """tpx:<mode>: skip onboarding (no topics yet), or cancel editing topics."""
    query = update.callback_query
    user = await _active_user(update, context)
    if not user:
        return
    L = tr(user)
    await query.answer()
    context.user_data.pop("topics_draft", None)
    if query.data[len(TOPIC_SKIP):] == "i":
        await _show(update, context, L.INTERESTS_UNCHANGED, None)
        return
    user = db.update_user(user.telegram_id, onboarding_completed=True)
    await _show(update, context, L.ONBOARDING_SKIPPED, None)
    await _send(context, update.effective_chat.id, L.MENU_HINT, main_keyboard(user))


async def onboarding_delivery(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ob:<instant|digest>: last onboarding step."""
    query = update.callback_query
    user = await _active_user(update, context)
    if not user:
        return
    L = tr(user)
    mode = query.data[len(ONBOARD_DELIVERY):]
    await query.answer()
    user = db.update_user(user.telegram_id, delivery_mode=mode, onboarding_completed=True)
    await _show(update, context, L.DELIVERY_INSTANT if mode == "instant" else L.DELIVERY_DIGEST, None)
    await _send(context, update.effective_chat.id,
                L.onboarding_done(_topic_names(user), mode == "instant", user.digest_hour), main_keyboard(user))
    log.info("User %s finished onboarding (%s)", user.telegram_id, mode)


# ---------- pause / resume ----------
async def pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _active_user(update, context)
    if not user:
        return
    L = tr(user)
    if not user.notifications_enabled:
        await _send(context, update.effective_chat.id, L.ALREADY_PAUSED, main_keyboard(user))
        return
    user = _set_paused(user, True)
    await _send(context, update.effective_chat.id, L.PAUSED, main_keyboard(user))


async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _active_user(update, context)
    if not user:
        return
    L = tr(user)
    if user.notifications_enabled:
        await _send(context, update.effective_chat.id, L.ALREADY_ON, main_keyboard(user))
        return
    user = _set_paused(user, False)
    text = L.RESUMED + ("" if any(db.interests(user.telegram_id).values()) else L.RESUMED_NO_TOPICS)
    await _send(context, update.effective_chat.id, text, main_keyboard(user))


def _set_paused(user: User, paused: bool) -> User:
    if paused:
        # Nothing found while paused is sent later.
        db.skip_queued_for_user(user.telegram_id, "paused")
    log.info("User %s %s alerts", user.telegram_id, "paused" if paused else "resumed")
    return db.update_user(user.telegram_id, notifications_enabled=not paused)


# ---------- settings ----------
def _settings_screen(user: User) -> tuple[str, InlineKeyboardMarkup]:
    L = tr(user)
    interests_ = db.interests(user.telegram_id)
    instant = user.delivery_mode == "instant"
    text = L.settings_text(
        L.ON if user.notifications_enabled else L.OFF,
        L.INSTANT if instant else L.digest_at(user.digest_hour),
        L.limit_text(user.max_per_day),
        L.NEWS_LANGUAGES.get(user.news_language, user.news_language),
        len(interests_), sum(1 for on in interests_.values() if not on),
    )
    rows = [
        [_button(L.SET_PAUSE if user.notifications_enabled else L.SET_RESUME,
                 f"{SETTINGS}{'pause' if user.notifications_enabled else 'resume'}"),
         _button(L.SET_TO_DIGEST if instant else L.SET_TO_INSTANT,
                 f"{SETTINGS}mode:{'digest' if instant else 'instant'}")],
        [_button(L.SET_HOUR, f"{SETTINGS}hour"), _button(L.SET_LIMIT, f"{SETTINGS}limit")],
        [_button(L.SET_NEWS_LANGUAGE, f"{SETTINGS}nl"), _button(L.SET_MUTE, f"{SETTINGS}mute")],
        [_button(L.SET_TOPICS, f"{SETTINGS}topics"), _button(L.SET_DELETE, f"{SETTINGS}del")],
        [_button(L.CLOSE, f"{SETTINGS}close")],
    ]
    return text, InlineKeyboardMarkup(rows)


def _mark(selected: bool, label: str) -> str:
    return ("• " if selected else "") + label


async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """⚙️ Settings or /settings."""
    user = await _active_user(update, context)
    if not user:
        return
    text, keyboard = _settings_screen(user)
    await _send(context, update.effective_chat.id, text, keyboard)


async def settings_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """st:… buttons on the settings screen and its sub-screens (they all edit the same message)."""
    query = update.callback_query
    user = await _active_user(update, context)
    if not user:
        return
    L = tr(user)
    parts = query.data[len(SETTINGS):].split(":")
    action, value = parts[0], (parts[1] if len(parts) > 1 else None)
    back = [_button(L.BACK, f"{SETTINGS}menu")]

    if action == "close":
        await query.answer()
        try:
            await query.message.delete()
        except TelegramError:
            await _show(update, context, L.SETTINGS_TITLE, None)
        return
    if action == "topics":
        await interests(update, context)
        return
    if action in ("pause", "resume"):
        user = _set_paused(user, action == "pause")
        await query.answer()
        await _send(context, update.effective_chat.id, L.PAUSED if action == "pause" else L.RESUMED,
                    main_keyboard(user))
    elif action == "mode" and value in ("instant", "digest"):
        user = _set_mode(user, value)
        await query.answer(L.DELIVERY_INSTANT if value == "instant" else L.digest_at(user.digest_hour))
    elif action == "hour":
        if value is not None and value.isdigit() and int(value) in DIGEST_HOURS:
            user = db.update_user(user.telegram_id, digest_hour=int(value))
            await query.answer(L.digest_at(user.digest_hour))
        else:
            await query.answer()
            rows = _pairs([_button(_mark(h == user.digest_hour, f"{h:02d}:00"), f"{SETTINGS}hour:{h}")
                           for h in DIGEST_HOURS], 3)
            await _show(update, context, L.HOUR_PROMPT, InlineKeyboardMarkup(rows + [back]))
            return
    elif action == "limit":
        if value is not None and value.isdigit() and int(value) in DAILY_LIMITS:
            user = db.update_user(user.telegram_id, max_per_day=int(value))
            await query.answer(L.limit_text(user.max_per_day))
        else:
            await query.answer()
            rows = _pairs([_button(_mark(n == user.max_per_day, L.limit_text(n)), f"{SETTINGS}limit:{n}")
                           for n in DAILY_LIMITS], 3)
            await _show(update, context, L.LIMIT_PROMPT, InlineKeyboardMarkup(rows + [back]))
            return
    elif action == "nl":
        if value in L.NEWS_LANGUAGES:
            user = db.update_user(user.telegram_id, news_language=value)
            await query.answer(L.NEWS_LANGUAGES[value])
        else:
            await query.answer()
            rows = _column([(("✅ " if code == user.news_language else "⬜ ") + label, f"{SETTINGS}nl:{code}")
                            for code, label in L.NEWS_LANGUAGES.items()])
            await _show(update, context, L.NEWS_LANGUAGE_PROMPT, InlineKeyboardMarkup(rows + [back]))
            return
    elif action == "mute":
        if value is not None and value.isdigit():
            interests_ = db.interests(user.telegram_id)
            cid = int(value)
            if cid in interests_:
                db.set_interest_enabled(user.telegram_id, cid, not interests_[cid])
                await query.answer()
            else:
                await query.answer(L.CATEGORY_GONE, show_alert=True)
        else:
            await query.answer()
        await _show(update, context, *_mute_screen(user))
        return
    elif action == "del":
        await query.answer()
        await _show(update, context, L.DELETE_CONFIRM, InlineKeyboardMarkup(
            [[_button(L.DELETE_YES, f"{SETTINGS}delok")], [_button(L.CANCEL, f"{SETTINGS}menu")]]))
        return
    elif action == "delok":
        await query.answer()
        db.delete_user(user.telegram_id)
        context.user_data.clear()
        log.info("User %s deleted their data", user.telegram_id)
        await _show(update, context, L.DELETED, None)
        try:
            await context.bot.delete_my_commands(scope=BotCommandScopeChat(update.effective_chat.id))
        except TelegramError:
            pass
        await _send(context, update.effective_chat.id, "👋", ReplyKeyboardRemove())
        return
    else:  # "menu", or anything unexpected
        await query.answer()
    await _show(update, context, *_settings_screen(user))


def _mute_screen(user: User) -> tuple[str, InlineKeyboardMarkup]:
    L = tr(user)
    interests_ = db.interests(user.telegram_id)
    categories = [c for c in db.categories() if c.id in interests_]
    rows = _column([(("🔔 " if interests_[c.id] else "🔕 ") + c.label(user.language), f"{SETTINGS}mute:{c.id}")
                    for c in categories])
    rows.append([_button(L.BACK, f"{SETTINGS}menu")])
    return (L.MUTE_PROMPT if categories else L.MUTE_NONE), InlineKeyboardMarkup(rows)


def _set_mode(user: User, mode: str) -> User:
    """Instant -> digest: waiting alerts are held for the digest. Digest -> instant: the held ones are
    dropped rather than sent as a burst."""
    if mode == user.delivery_mode:
        return user
    return db.set_delivery_mode(user.telegram_id, mode)


# ---------- digest ----------
def _digest_screen(user: User) -> tuple[str, InlineKeyboardMarkup]:
    L = tr(user)
    instant = user.delivery_mode == "instant"
    rows = [[_button(_mark(instant, L.DELIVERY_INSTANT), f"{DIGEST}instant"),
             _button(_mark(not instant, L.DELIVERY_DIGEST), f"{DIGEST}digest")]]
    rows += _pairs([_button(_mark(not instant and h == user.digest_hour, f"🕗 {h:02d}:00"), f"{DIGEST}h:{h}")
                    for h in DIGEST_HOURS], 3)
    return L.digest_menu(instant, user.digest_hour), InlineKeyboardMarkup(rows)


async def digest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """📰 Digest or /digest."""
    user = await _active_user(update, context)
    if user:
        await _send(context, update.effective_chat.id, *_digest_screen(user))


async def digest_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """dg:instant, dg:digest, dg:h:<hour> (choosing an hour also turns the digest on)."""
    query = update.callback_query
    user = await _active_user(update, context)
    if not user:
        return
    value = query.data[len(DIGEST):]
    if value in ("instant", "digest"):
        user = _set_mode(user, value)
    elif value.startswith("h:") and value[2:].isdigit() and int(value[2:]) in DIGEST_HOURS:
        user = db.update_user(user.telegram_id, digest_hour=int(value[2:]))
        user = _set_mode(user, "digest")
    await query.answer()
    await _show(update, context, *_digest_screen(user))


# ---------- muting from an alert ----------
async def mute_from_alert(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """mua:<category_id>: the 🔕 button under an alert."""
    query = update.callback_query
    user = await _active_user(update, context)
    if not user:
        return
    L = tr(user)
    value = query.data[len(MUTE_FROM_ALERT):]
    category = db.get_category(int(value)) if value.isdigit() else None
    if not category or not db.set_interest_enabled(user.telegram_id, category.id, False):
        await query.answer(L.CATEGORY_GONE, show_alert=True)
        return
    await query.answer(L.topic_muted(category.name(user.language)), show_alert=True)
    log.info("User %s muted %s from an alert", user.telegram_id, category.key)


# ---------- 📥 latest news on demand ----------
async def pull_news(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """📥 Latest news, /latest, or the "more" button: today's stories for the user's topics that they
    haven't received yet, PULL_PAGE at a time, newest first. Works while alerts are paused too."""
    user = await _active_user(update, context)
    if not user:
        return
    L = tr(user)
    chat_id = update.effective_chat.id
    query = update.callback_query
    if query:
        await query.answer()
        try:
            await query.edit_message_reply_markup(None)  # the old "more" button can't be pressed twice
        except TelegramError:
            pass
    if not any(db.interests(user.telegram_id).values()):
        await _send(context, chat_id, L.PULL_NO_TOPICS, main_keyboard(user))
        return
    if context.user_data.get("pulling"):
        return  # a pull for this user is already sending
    context.user_data["pulling"] = True
    try:
        items, total = db.pull_candidates(user, day_start_utc(local_today()), PULL_PAGE)
        if not items:
            await _send(context, chat_id, L.PULL_NOTHING, main_keyboard(user))
            return
        providers = {p.id: p.name for p in db.providers()}
        categories = {c.id: c for c in db.categories()}
        sent = 0
        for article, category_id in items:
            if not db.claim_pulled(user.telegram_id, article, category_id):
                continue  # already received meanwhile
            try:
                await _retry(lambda: send_alert(context.bot, user, article, providers.get(article.provider_id, ""),
                                                categories.get(category_id)))
                sent += 1
            except TelegramError as e:
                db.unclaim_pulled(user.telegram_id, article, str(e))
                log.warning("Pulled article %s to %s failed: %s", article.id, user.telegram_id, e)
            await asyncio.sleep(0.3)  # keep a short burst gentle
        metrics.inc("news_pulled", sent)
        log.info("User %s pulled %s stories (%s more waiting)", user.telegram_id, sent, total - len(items))
        remaining = total - len(items)
        more = InlineKeyboardMarkup([[_button(L.pull_more(remaining), PULL_MORE)]]) if remaining else None
        await _send(context, chat_id, L.pull_done(sent, remaining), more or main_keyboard(user))
    finally:
        context.user_data.pop("pulling", None)


# ---------- sources / help ----------
async def sources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _active_user(update, context)
    if not user:
        return
    L = tr(user)
    categories = {c.key: c for c in db.categories()}
    grouped: dict[str, dict] = {}
    for p in db.providers(enabled_only=True):
        entry = grouped.setdefault(p.name, {"url": p.base_url, "topics": []})
        for key in p.categories:
            if key in categories and categories[key].label(user.language) not in entry["topics"]:
                entry["topics"].append(categories[key].label(user.language))
    lines = [f"• <a href=\"{html.escape(e['url'], quote=True)}\">{html.escape(name)}</a>"
             + (f" — {', '.join(e['topics'])}" if e["topics"] else "")
             for name, e in sorted(grouped.items())]
    await _send(context, update.effective_chat.id, L.sources_text(lines) if lines else L.SOURCES_EMPTY,
                main_keyboard(user))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _active_user(update, context)
    if user:
        await _send(context, update.effective_chat.id, tr(user).HELP, main_keyboard(user))


async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _active_user(update, context)
    if user:
        await _send(context, update.effective_chat.id, tr(user).UNKNOWN_MESSAGE, main_keyboard(user))


async def noop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()


async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The user blocked or unblocked the bot."""
    change: ChatMemberUpdated = update.my_chat_member
    if change.chat.type != "private":
        return
    user = db.get_user(change.from_user.id)
    if not user:
        return
    status = change.new_chat_member.status
    if status in (ChatMemberStatus.BANNED, ChatMemberStatus.LEFT) and user.status == "active":
        db.update_user(user.telegram_id, status="blocked")
        db.skip_queued_for_user(user.telegram_id, "user blocked the bot")
        log.info("User %s blocked the bot", user.telegram_id)
    elif status == ChatMemberStatus.MEMBER and user.status == "blocked":
        db.update_user(user.telegram_id, status="active")
        log.info("User %s unblocked the bot", user.telegram_id)


# ---------- admin ----------
def _ago(value: str | None, L) -> str:
    if not value:
        return L.NEVER
    minutes = int((now_utc() - parse_iso(value)).total_seconds() // 60)
    local = to_local(value)
    return f"{L.format_time(local)} ({minutes} min)" if minutes < 24 * 60 else L.format_time(local)


def _admin_screen(user: User) -> tuple[str, InlineKeyboardMarkup]:
    L = tr(user)
    stats = db.stats()
    if stats["queue_oldest"]:
        age = max(0, int((now_utc() - parse_iso(stats["queue_oldest"])).total_seconds()))
        stats["queue_age"] = f"{age // 60} min" if age >= 60 else f"{age} s"
    rows = [[_button(L.ADMIN_PROVIDERS, f"{ADMIN}prov:0"), _button(L.ADMIN_CATEGORIES, f"{ADMIN}cat")],
            [_button(f"{L.ADMIN_FAILED} ({stats['failed_today']})", f"{ADMIN}fail"),
             _button(L.ADMIN_REFRESH, f"{ADMIN}menu")]]
    return L.admin_status(stats, web_hint=True), InlineKeyboardMarkup(rows)


async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🛠 Admin or /admin."""
    user = await _active_user(update, context)
    if not user:
        return
    if not user.is_admin:
        await _send(context, update.effective_chat.id, tr(user).ONLY_ADMINS)
        return
    await _send(context, update.effective_chat.id, *_admin_screen(user))


def _providers_screen(user: User, page: int) -> tuple[str, InlineKeyboardMarkup]:
    L = tr(user)
    providers = db.providers()
    pages = max(1, -(-len(providers) // PROVIDERS_PER_PAGE))
    page = min(max(page, 0), pages - 1)
    chunk = providers[page * PROVIDERS_PER_PAGE:(page + 1) * PROVIDERS_PER_PAGE]
    rows = _column([(("✅" if p.is_enabled else "⏸") + ("⚠️" if p.consecutive_failures else PAD * 2)
                     + f" {p.name} · {p.key}", f"{ADMIN}pc:{p.id}:{page}") for p in chunk])
    if pages > 1:
        rows.append([_button("◀️", f"{ADMIN}prov:{page - 1}") if page > 0 else _button(" ", NOOP),
                     _button(f"{page + 1}/{pages}", NOOP),
                     _button("▶️", f"{ADMIN}prov:{page + 1}") if page < pages - 1 else _button(" ", NOOP)])
    rows.append([_button(L.BACK, f"{ADMIN}menu")])
    return L.PROVIDERS_TITLE, InlineKeyboardMarkup(rows)


def _provider_card(user: User, provider_id: int, page: int) -> tuple[str, InlineKeyboardMarkup] | None:
    L = tr(user)
    p = db.get_provider(provider_id)
    if not p:
        return None
    categories = {c.key: c for c in db.categories(active_only=False)}
    topics = ", ".join(f"{categories[k].label(user.language)} {v:g}" for k, v in p.categories.items()
                       if k in categories) or L.NONE
    text = L.provider_card(html.escape(p.name), html.escape(p.key), p.is_enabled, html.escape(p.feed_url),
                           p.polling_interval_seconds, _ago(p.last_success_at, L),
                           html.escape(p.last_error or L.NONE), p.articles_total, topics,
                           html.escape(p.notes or ""))
    rows = [[_button(L.ADMIN_DISABLE if p.is_enabled else L.ADMIN_ENABLE, f"{ADMIN}pt:{p.id}:{page}"),
             _button(L.ADMIN_FETCH_NOW, f"{ADMIN}pf:{p.id}:{page}")],
            [_button(L.BACK, f"{ADMIN}prov:{page}")]]
    return text, InlineKeyboardMarkup(rows)


def _categories_screen(user: User) -> tuple[str, InlineKeyboardMarkup]:
    L = tr(user)
    rows = _column([(("✅ " if c.is_active else "⏸ ") + c.label(user.language), f"{ADMIN}ct:{c.id}")
                    for c in db.categories(active_only=False)])
    rows.append([_button(L.BACK, f"{ADMIN}menu")])
    return L.CATEGORIES_TITLE, InlineKeyboardMarkup(rows)


def _failed_screen(user: User) -> tuple[str, InlineKeyboardMarkup]:
    L = tr(user)
    failed = db.failed_notifications(200)
    lines = [f"• {html.escape(f['first_name'] or str(f['user_id']))}: {html.escape(f['title'][:50])}"
             f"\n  <i>{html.escape((f['last_error'] or '')[:80])}</i>" for f in failed[:10]]
    rows = ([[_button(L.ADMIN_RETRY_ALL, f"{ADMIN}retry")]] if failed else []) + [[_button(L.BACK, f"{ADMIN}menu")]]
    return L.failed_text(len(failed), lines), InlineKeyboardMarkup(rows)


async def admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ad:… buttons. Every one checks that the caller is still an admin."""
    query = update.callback_query
    user = await _active_user(update, context)
    if not user:
        return
    L = tr(user)
    if not user.is_admin:
        await query.answer(L.ONLY_ADMINS, show_alert=True)
        return
    parts = query.data[len(ADMIN):].split(":")
    action, args = parts[0], [int(x) for x in parts[1:] if x.isdigit()]
    toast, alert = None, False
    if action == "prov":
        screen = _providers_screen(user, args[0] if args else 0)
    elif action in ("pc", "pt", "pf") and len(args) == 2:
        provider = db.get_provider(args[0])
        if provider and action == "pt":
            db.update_provider(provider.id, is_enabled=not provider.is_enabled, next_fetch_at=None)
            log.info("Admin %s turned provider %s %s", user.telegram_id, provider.key,
                     "off" if provider.is_enabled else "on")
            if not provider.is_enabled:
                _wake(context, "collector")
        elif provider and action == "pf":
            db.update_provider(provider.id, next_fetch_at=None)
            _wake(context, "collector")
            toast = L.fetch_started(provider.name)
        screen = _provider_card(user, args[0], args[1]) or _providers_screen(user, args[1])
    elif action == "cat":
        screen = _categories_screen(user)
    elif action == "ct" and args:
        category = db.get_category(args[0])
        if category:
            db.set_category_active(category.id, not category.is_active)
            log.info("Admin %s turned category %s %s", user.telegram_id, category.key,
                     "off" if category.is_active else "on")
        screen = _categories_screen(user)
    elif action == "fail":
        screen = _failed_screen(user)
    elif action == "retry":
        count = sum(db.retry_notification(f["id"]) for f in db.failed_notifications(500))
        _wake(context, "sender")
        log.info("Admin %s re-queued %s failed alerts", user.telegram_id, count)
        toast, alert = L.retried(count), True
        screen = _failed_screen(user)
    else:
        screen = _admin_screen(user)
    await query.answer(toast, show_alert=alert)
    await _show(update, context, *screen)


def _wake(context: ContextTypes.DEFAULT_TYPE, name: str) -> None:
    worker = context.application.bot_data.get(name)
    if worker:
        worker.wake()


# ---------- super admin: backup ----------
NIGHTLY_SETTING = "backup_nightly"   # "on" / "off"
LAST_BACKUP_SETTING = "last_backup"
LAST_NIGHTLY_SETTING = "last_nightly_backup_date"


def _backup_menu(user: User) -> tuple[str, InlineKeyboardMarkup]:
    nightly_on = db.get_setting(NIGHTLY_SETTING, "on") == "on"
    last = db.get_setting(LAST_BACKUP_SETTING)
    last_text = to_local(last).strftime("%d/%m/%Y %H:%M") if last else tr(user).NEVER
    text = (f"💾 <b>Backup</b>\nNightly at 00:00 ({config.TIMEZONE.key}): <b>{'on' if nightly_on else 'off'}</b>\n"
            f"Next: {backup.next_midnight():%d/%m/%Y %H:%M}\nLast: {last_text}")
    return text, InlineKeyboardMarkup([
        [_button("📦 Back up now", f"{BACKUP}now")],
        [_button("⏸ Turn nightly off" if nightly_on else "▶️ Turn nightly on", f"{BACKUP}{'off' if nightly_on else 'on'}")],
    ])


async def backup_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _active_user(update, context)
    if not user:
        return
    if not user.is_super_admin:
        await _send(context, update.effective_chat.id, tr(user).ONLY_SUPER)
        return
    await _send(context, update.effective_chat.id, *_backup_menu(user))


async def backup_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = _register(update)
    L = tr(user)
    if not user.is_super_admin:
        await query.answer(L.ONLY_SUPER, show_alert=True)
        return
    action = query.data[len(BACKUP):]
    await query.answer()
    if action in ("on", "off"):
        db.set_setting(NIGHTLY_SETTING, action)
        log.info("Super admin %s turned nightly backup %s", user.telegram_id, action)
    else:
        await _show(update, context, L.BACKUP_WORKING, None)
        await send_backup(context.application, [user.telegram_id], nightly=False)
    await _show(update, context, *_backup_menu(user))


async def send_backup(app: Application, recipients, nightly: bool) -> None:
    """Make one zipped backup and send it to each recipient."""
    # Runs on the event loop thread: the database connection isn't shared across threads.
    data, file_name, counts = backup.make_backup()
    when = datetime.now(config.TIMEZONE).strftime("%d/%m/%Y %H:%M")
    for chat_id in recipients:
        L = tr(db.get_user(chat_id))
        try:
            if len(data) > backup.TELEGRAM_UPLOAD_LIMIT:
                await app.bot.send_message(chat_id, L.BACKUP_TOO_BIG)
                continue
            await _retry(lambda: app.bot.send_document(
                chat_id, document=data, filename=file_name, parse_mode=ParseMode.HTML,
                caption=L.backup_caption(when, counts["users"], counts["articles"], nightly)))
        except TelegramError as e:
            log.warning("Could not send backup to %s: %s", chat_id, e)
    db.set_setting(LAST_BACKUP_SETTING, now_utc().isoformat())
    log.info("Backup %s sent to %s (%s bytes)", file_name, list(recipients), len(data))


async def nightly_backup_loop(app: Application) -> None:
    """Every night at 00:00 (TIMEZONE), send a backup to every super admin (unless turned off)."""
    while True:
        wait = (backup.next_midnight() - datetime.now(config.TIMEZONE)).total_seconds()
        await asyncio.sleep(max(wait, 1))
        today = datetime.now(config.TIMEZONE).date().isoformat()
        if db.get_setting(NIGHTLY_SETTING, "on") != "on" or db.get_setting(LAST_NIGHTLY_SETTING) == today:
            continue
        db.set_setting(LAST_NIGHTLY_SETTING, today)
        try:
            await send_backup(app, sorted(config.SUPER_ADMIN_IDS), nightly=True)
        except Exception:
            log.exception("Nightly backup failed")
        await asyncio.sleep(60)


async def _retry(send):
    """Run one Telegram send, waiting and retrying once if Telegram says we're sending too fast."""
    try:
        return await send()
    except RetryAfter as e:
        await asyncio.sleep(e.retry_after if isinstance(e.retry_after, (int, float))
                            else e.retry_after.total_seconds())
        return await send()


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Unhandled error while processing update", exc_info=context.error)


def build_application(request=None) -> Application:
    """request: a custom telegram.request.BaseRequest (tests use one that fakes the Bot API)."""
    if not config.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set (see .env.example)")
    builder = Application.builder().token(config.BOT_TOKEN)
    if request is not None:
        builder = builder.request(request).get_updates_request(request)
    app = builder.build()
    private = filters.ChatType.PRIVATE & filters.UpdateType.MESSAGE

    for command, handler in [("start", start), ("latest", pull_news), ("interests", interests), ("topics", interests),
                             ("settings", settings), ("digest", digest), ("pause", pause), ("resume", resume),
                             ("sources", sources), ("language", choose_language), ("help", help_command),
                             ("admin", admin), ("backup", backup_menu)]:
        app.add_handler(CommandHandler(command, handler, filters=private))

    # Reply keyboard buttons, in every language.
    for name, handler in [("BTN_TOPICS", interests), ("BTN_SETTINGS", settings), ("BTN_PAUSE", pause),
                          ("BTN_RESUME", resume), ("BTN_DIGEST", digest), ("BTN_SOURCES", sources),
                          ("BTN_HELP", help_command), ("BTN_ADMIN", admin), ("BTN_PULL", pull_news)]:
        app.add_handler(MessageHandler(private & filters.Text(texts.labels(name)), handler))
    app.add_handler(MessageHandler(private & filters.Text([texts.LANGUAGE_BUTTON]), choose_language))

    app.add_handler(CallbackQueryHandler(set_language, pattern=rf"^{SET_LANGUAGE}[a-z]{{2}}$"))
    app.add_handler(CallbackQueryHandler(topic_tap, pattern=rf"^({TOPIC}[oi]:\d+|{TOPIC_ALL}[oi]|{TOPIC_NONE}[oi])$"))
    app.add_handler(CallbackQueryHandler(topic_save, pattern=rf"^{TOPIC_SAVE}[oi]$"))
    app.add_handler(CallbackQueryHandler(topic_skip, pattern=rf"^{TOPIC_SKIP}[oi]$"))
    app.add_handler(CallbackQueryHandler(onboarding_delivery, pattern=rf"^{ONBOARD_DELIVERY}(instant|digest)$"))
    app.add_handler(CallbackQueryHandler(settings_action, pattern=rf"^{SETTINGS}[a-z]+(:[a-z0-9]+)?$"))
    app.add_handler(CallbackQueryHandler(digest_action, pattern=rf"^{DIGEST}(instant|digest|h:\d+)$"))
    app.add_handler(CallbackQueryHandler(mute_from_alert, pattern=rf"^{MUTE_FROM_ALERT}\d+$"))
    app.add_handler(CallbackQueryHandler(admin_action, pattern=rf"^{ADMIN}[a-z]+(:\d+){{0,2}}$"))
    app.add_handler(CallbackQueryHandler(backup_action, pattern=rf"^{BACKUP}(now|on|off)$"))
    app.add_handler(CallbackQueryHandler(pull_news, pattern=f"^{PULL_MORE}$"))
    app.add_handler(CallbackQueryHandler(noop, pattern=f"^{NOOP}$"))
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(private & ~filters.COMMAND, unknown))
    app.add_handler(MessageHandler(private & filters.COMMAND, unknown))
    app.add_error_handler(on_error)
    return app
