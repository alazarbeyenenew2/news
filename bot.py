"""Telegram bot: onboarding, topics, settings, sources, and admin tools.

Alerts themselves are sent by delivery.py; news is collected by collector.py. Every text shown in
Telegram lives in texts_am.py / texts_en.py; each person sees their own language.
"""
import asyncio
import csv
import hashlib
import html
import io
import logging
import re
from datetime import datetime, timedelta

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
from telegram.constants import ChatAction, ChatMemberStatus, ParseMode
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
from db import (CVE_MIN_SEVERITIES, User, day_start_utc, db, load_technologies, local_today, now_utc, parse_iso,
                to_local)
from delivery import CVE_MENU, MUTE_FROM_ALERT, send_alert
from keyboards import grid

log = logging.getLogger(__name__)

# Inline button callback data. Everything after the prefix is checked before it's used.
SET_LANGUAGE = "lang:"   # lang:<code>
TOPIC = "tp:"            # tp:<o|i>:<category_id>   o = onboarding, i = /interests
TOPIC_ALL = "tpa:"       # tpa:<o|i>
TOPIC_NONE = "tpn:"      # tpn:<o|i>
TOPIC_SAVE = "tps:"      # tps:<o|i>
TOPIC_SKIP = "tpx:"      # tpx:<o|i>   skip (onboarding) or cancel (/interests)
ONBOARD_DELIVERY = "ob:"  # ob:<instant|digest>: from older onboarding messages, finishes onboarding
SOURCE = "sr:"           # sr:<o|s>:<provider_id>   o = onboarding, s = 📚 Sources / settings
SOURCE_ALL = "sra:"      # sra:<o|s>
SOURCE_NONE = "srn:"     # srn:<o|s>
SOURCE_SAVE = "srs:"     # srs:<o|s>   ➡️ Next (onboarding) or 💾 Save
SOURCE_SKIP = "srx:"     # srx:<o|s>   skip with every source on (onboarding) or cancel
SETTINGS = "st:"         # st:<screen or action>[:<value>]
DIGEST = "dg:"           # buttons of the removed daily digest, in older messages
ADMIN = "ad:"            # ad:<action>[:<id>][:<page>]
BACKUP = "bk:"           # bk:now | bk:on | bk:off
PULL_MORE = "pull:more"  # the "Show more" button under older 📥 Latest news batches
CATCH_UP_PER_MINUTE = 20  # users whose missing welcome news starts each minute
USER_COUNT_MINUTES = 10  # how often the user count in the bot's description is refreshed
NOOP = "noop"
PULL_REFRESH_SECONDS = 12  # how long 📥 Latest news waits for the feeds it re-checks
CVE = "cv:"  # cv:menu|on|off|all|sev[:<level>]|p:<page>|t:<page>:<tech id>|r:<page>:<hash>|add|addx|csv|close
CVE_ADAPTERS = ("nvd", "kev")  # CVE providers: managed on the CVE screen, not in 📚 Sources
MAX_OWN_TECHNOLOGIES = 50
TERM_CHARS = re.compile(r"[\w .+#/\-]+")
TECH_GROUPS = load_technologies()[0]  # group key -> {"en": name, "am": name}, in display order

DAILY_LIMITS = (10, 20, 30, 50, 100, 0)
PROVIDERS_PER_PAGE = 8


def tr(user: User | None):
    """The texts module for a person's language."""
    return texts.get(user.language if user else None)


def _button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)


def _grid(items: list[tuple[str, str]], checkboxes: bool = False) -> list[list[InlineKeyboardButton]]:
    """(label, callback data) pairs as a grid: 3 per row when short, 2 when medium, 1 when long
    (see keyboards.grid). checkboxes: a ✅/⬜ list, where labels too long to share a row go last."""
    return grid([_button(label, data) for label, data in items], wide_last=checkboxes)


def _commands(user: User) -> list[BotCommand]:
    L = tr(user)
    commands = [BotCommand("start", L.CMD_START), BotCommand("latest", L.CMD_LATEST),
                BotCommand("interests", L.CMD_INTERESTS),
                BotCommand("settings", L.CMD_SETTINGS),
                BotCommand("sources", L.CMD_SOURCES), BotCommand("language", L.CMD_LANGUAGE),
                BotCommand("cve", L.CMD_CVE), BotCommand("help", L.CMD_HELP)]
    if config.SUPPORT_USERNAME:
        commands.append(BotCommand("support", L.CMD_SUPPORT))
    if user.is_admin:
        commands.append(BotCommand("admin", L.CMD_ADMIN))
    if user.is_super_admin:
        commands.append(BotCommand("backup", L.CMD_BACKUP))
    return commands


def main_keyboard(user: User) -> ReplyKeyboardMarkup:
    L = tr(user)
    rows = [[KeyboardButton(L.BTN_PULL)],
            [KeyboardButton(L.BTN_TOPICS), KeyboardButton(L.BTN_SETTINGS)],
            [KeyboardButton(L.BTN_SOURCES), KeyboardButton(L.BTN_CVE)],
            [KeyboardButton(L.BTN_HELP)],
            [KeyboardButton(texts.LANGUAGE_BUTTON)]]
    if config.SUPPORT_USERNAME:
        rows[-2].append(KeyboardButton(L.BTN_CONTACT))
    if user.is_admin:
        rows[-1].append(KeyboardButton(L.BTN_ADMIN))
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
    if user.cve_alerts:
        names.append(tr(user).BTN_CVE)
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
    text, keyboard = _topic_picker(user, "o", update.effective_user.first_name)
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
# Every tap is saved straight away and answered with a short pop-up; ✅ Done / ➡️ Next just moves on.
def _topic_picker(user: User, mode: str, first_name: str | None = None) -> tuple[str, InlineKeyboardMarkup]:
    L = tr(user)
    categories = db.categories()
    chosen = set(db.interests(user.telegram_id))
    text = (L.welcome(html.escape(first_name or "")) + L.TOPICS_PROMPT) if mode == "o" else L.TOPICS_EDIT_PROMPT
    text += L.topics_selected(len(chosen & {c.id for c in categories}) + int(user.cve_alerts))
    items = [(("✅ " if c.id in chosen else "⬜ ") + c.label(user.language), f"{TOPIC}{mode}:{c.id}")
             for c in categories]
    items.append((("✅ " if user.cve_alerts else "⬜ ") + L.BTN_CVE, f"{TOPIC}{mode}:cve"))  # 🛡 CVE reports
    rows = _grid(items, checkboxes=True)
    rows.append([_button(L.TOPIC_ALL, f"{TOPIC_ALL}{mode}"), _button(L.TOPIC_NONE, f"{TOPIC_NONE}{mode}")])
    if mode == "o":
        rows.append([_button(L.NEXT, f"{TOPIC_SAVE}o"), _button(L.TOPIC_SKIP, f"{TOPIC_SKIP}o")])
    else:
        rows.append([_button(L.DONE, f"{TOPIC_SAVE}i")])
    return text, InlineKeyboardMarkup(rows)


async def interests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🗞 My topics or /interests: the topic picker with the saved topics ticked."""
    user = await _active_user(update, context)
    if not user:
        return
    if update.callback_query:
        await update.callback_query.answer()
    await _send(context, update.effective_chat.id, *_topic_picker(user, "i"))


async def topic_tap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """tp:/tpa:/tpn: add or remove topics, saved at once, with a pop-up saying what changed."""
    query = update.callback_query
    user = await _active_user(update, context)
    if not user:
        return
    L = tr(user)
    prefix, rest = query.data.split(":", 1)
    mode = rest[0]
    active = {c.id: c for c in db.categories()}
    chosen = set(db.interests(user.telegram_id))
    alert = False
    if prefix == "tpa":
        chosen, toast = set(active), L.TOPICS_ALL_ON
        user = _cve_turn_on(user)
    elif prefix == "tpn":
        chosen, toast = set(), L.TOPICS_CLEARED
        user = _cve_turn_off(user)
    elif rest.endswith(":cve"):  # the 🛡 CVE reports checkbox
        if user.cve_alerts:
            user, toast = _cve_turn_off(user), L.topic_removed(L.BTN_CVE)
        else:
            user, toast = _cve_turn_on(user), L.topic_added(L.BTN_CVE)
    else:
        category = active.get(int(rest.split(":")[1]))
        if not category:
            toast, alert = L.CATEGORY_GONE, True
        elif category.id in chosen:
            chosen.discard(category.id)
            toast = L.topic_removed(category.label(user.language))
        else:
            chosen.add(category.id)
            toast = L.topic_added(category.label(user.language))
    if not alert:
        db.set_interests(user.telegram_id, chosen)
    await query.answer(toast, show_alert=alert)
    await _show(update, context, *_topic_picker(user, mode, update.effective_user.first_name))


async def topic_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """tps:o ➡️ Next (onboarding: on to sources) or tps:i ✅ Done. Topics are already saved."""
    query = update.callback_query
    user = await _active_user(update, context)
    if not user:
        return
    L = tr(user)
    mode = query.data[len(TOPIC_SAVE):]
    chosen = db.interests(user.telegram_id)
    if mode == "o" and not chosen and not user.cve_alerts:
        await query.answer(L.TOPIC_NEED_ONE, show_alert=True)
        return
    await query.answer()
    log.info("User %s has %s topics (CVE reports %s)", user.telegram_id, len(chosen),
             "on" if user.cve_alerts else "off")
    if mode == "o":
        if user.cve_alerts and not user.cve_all_technologies and not db.user_technologies(user.telegram_id):
            # CVE reports ticked: pick technologies first (➡️ Next there continues onboarding).
            context.user_data["cve_onboarding"] = True
            await _show(update, context, *_cve_picker(user, 0, onboarding=True))
            return
        await _onboarding_after_topics(update, context, user)
        return
    user = db.update_user(user.telegram_id, onboarding_completed=True)
    await _show(update, context, L.interests_saved(_topic_names(user)) if chosen or user.cve_alerts
                else L.INTERESTS_EMPTY, None)


async def _onboarding_after_topics(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    """Onboarding after topics (and technologies): the sources step if any source covers the topics,
    then onboarding is done."""
    if _source_groups(user, "o"):
        await _show_sources(update, context, user, "o")
    else:
        await _finish_onboarding(update, context, user)


async def topic_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """tpx:o skips the rest of onboarding (topics already ticked are kept). tpx:i comes from older
    messages' ✖️ Cancel button and now works like ✅ Done."""
    query = update.callback_query
    if query.data == f"{TOPIC_SKIP}i":
        await topic_save(update, context)
        return
    user = await _active_user(update, context)
    if not user:
        return
    L = tr(user)
    await query.answer()
    user = db.update_user(user.telegram_id, onboarding_completed=True)
    text = (L.interests_saved(_topic_names(user)) if db.interests(user.telegram_id) or user.cve_alerts
            else L.ONBOARDING_SKIPPED)
    await _show(update, context, text, None)
    await _send(context, update.effective_chat.id, L.MENU_HINT, main_keyboard(user))
    start_welcome_news(context.bot, user)  # everyone gets the latest news, topics or not


# ---------- news sources (onboarding step, 📚 Sources, settings) ----------
def _source_groups(user: User, mode: str) -> list[dict]:
    """Enabled providers grouped by the name people see (BBC News has several feeds), each with the user's
    topics it covers. Onboarding shows only sources covering the topics just chosen; 📚 Sources shows them
    all, those covering the user's topics first. The group's id is its lowest provider id."""
    followed = db.interests(user.telegram_id)
    categories = {c.key: c for c in db.categories()}
    groups: dict[str, dict] = {}
    for p in db.providers(enabled_only=True):
        if p.adapter_key in CVE_ADAPTERS:
            continue
        g = groups.setdefault(p.name, {"name": p.name, "ids": set(), "topics": []})
        g["ids"].add(p.id)
        for key in p.categories:
            c = categories.get(key)
            if c and c.id in followed and c not in g["topics"]:
                g["topics"].append(c)
    for g in groups.values():
        g["id"] = min(g["ids"])
    result = [g for g in groups.values() if g["topics"] or mode != "o"]
    return sorted(result, key=lambda g: (not g["topics"], g["name"].casefold()))


def _groups_off(user: User, groups: list[dict]) -> set[int]:
    """Ids of the groups the user switched off."""
    excluded = db.source_exclusions(user.telegram_id)
    return {g["id"] for g in groups if g["ids"] <= excluded}


async def _show_sources(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User, mode: str,
                        new_message: bool = False) -> None:
    L = tr(user)
    groups = _source_groups(user, mode)
    if not groups:
        await _send(context, update.effective_chat.id, L.SOURCES_EMPTY, main_keyboard(user))
        return
    off = _groups_off(user, groups)
    rows = _grid([(("⬜ " if g["id"] in off else "✅ ") + g["name"], f"{SOURCE}{mode}:{g['id']}")
                  for g in groups], checkboxes=True)
    if mode == "o":
        rows.append([_button(L.TOPIC_ALL, f"{SOURCE_ALL}o")])
        rows.append([_button(L.NEXT, f"{SOURCE_SAVE}o"), _button(L.SOURCES_SKIP, f"{SOURCE_SKIP}o")])
    else:
        rows.append([_button(L.TOPIC_ALL, f"{SOURCE_ALL}s"), _button(L.DONE, f"{SOURCE_SAVE}s")])
    on = sum(1 for g in groups if g["id"] not in off)
    text = (L.SOURCES_PROMPT if mode == "o" else L.SOURCES_MANAGE) + L.sources_on(on, len(groups))
    if new_message:
        await _send(context, update.effective_chat.id, text, InlineKeyboardMarkup(rows))
    else:
        await _show(update, context, text, InlineKeyboardMarkup(rows))


async def sources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """📚 Sources or /sources: choose which news sources to get stories from."""
    user = await _active_user(update, context)
    if user:
        await _show_sources(update, context, user, "s", new_message=not update.callback_query)


def _set_groups(user: User, groups: list[dict], turn_off: set[int], turn_on: set[int]) -> None:
    """Switch groups of sources off / on (all the feeds in each group), saved at once."""
    by_id = {g["id"]: g for g in groups}
    excluded = db.source_exclusions(user.telegram_id)
    for gid in turn_off:
        excluded |= by_id[gid]["ids"]
    for gid in turn_on:
        excluded -= by_id[gid]["ids"]
    db.set_source_exclusions(user.telegram_id, excluded)


async def source_tap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """sr: turn one source on or off; sra: all on. Saved at once, with a pop-up. The last source left
    on can't be turned off."""
    query = update.callback_query
    user = await _active_user(update, context)
    if not user:
        return
    L = tr(user)
    prefix, rest = query.data.split(":", 1)
    mode = rest[0]
    groups = _source_groups(user, mode)
    off = _groups_off(user, groups)
    by_id = {g["id"]: g for g in groups}
    alert = False
    if prefix == "sra":
        _set_groups(user, groups, set(), set(by_id))
        toast = L.SOURCES_ALL_ON
    elif prefix == "srn":  # "clear" from older messages: everything off isn't allowed
        toast, alert = L.SOURCES_NEED_ONE, True
    else:
        group = by_id.get(int(rest.split(":")[1]))
        if not group:
            toast, alert = L.SOURCE_GONE, True
        elif group["id"] in off:
            _set_groups(user, groups, set(), {group["id"]})
            toast = L.source_on(group["name"])
        elif len(off) + 1 >= len(groups):
            toast, alert = L.SOURCES_NEED_ONE, True
        else:
            _set_groups(user, groups, {group["id"]}, set())
            toast = L.source_off(group["name"])
    await query.answer(toast, show_alert=alert)
    if not alert:
        await _show_sources(update, context, user, mode)


async def source_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """srs:o ➡️ Next, srx:o ⏭ Skip (every source on), then the delivery step; srs:s ✅ Done
    (srx:s, an older message's ✖️ Cancel, works like Done). Choices are already saved."""
    query = update.callback_query
    user = await _active_user(update, context)
    if not user:
        return
    L = tr(user)
    prefix, mode = query.data.split(":", 1)
    groups = _source_groups(user, mode)
    if prefix == "srx" and mode == "o":
        _set_groups(user, groups, set(), {g["id"] for g in groups})
    await query.answer()
    on = len(groups) - len(_groups_off(user, groups))
    log.info("User %s has %s of %s sources on", user.telegram_id, on, len(groups))
    if mode == "o":
        await _finish_onboarding(update, context, user)
    else:
        await _show(update, context, L.sources_saved(on, len(groups)), None)


async def _finish_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    """Last onboarding step: alerts are instant for everyone, so after sources the user is done."""
    L = tr(user)
    user = db.update_user(user.telegram_id, delivery_mode="instant", onboarding_completed=True)
    await _show(update, context, L.onboarding_done(_topic_names(user)), None)
    await _send(context, update.effective_chat.id, L.MENU_HINT, main_keyboard(user))
    log.info("User %s finished onboarding", user.telegram_id)
    start_welcome_news(context.bot, user)


async def onboarding_delivery(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ob:<instant|digest>: the delivery step of older onboarding messages; finishes onboarding."""
    user = await _active_user(update, context)
    if not user:
        return
    await update.callback_query.answer()
    await _finish_onboarding(update, context, user)


# ---------- pause (removed) ----------
async def pause_removed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/pause, /resume and the ⏸ / ▶️ buttons of older keyboards: alerts can't be paused any more.
    Turns alerts back on for anyone still paused and shows the current keyboard."""
    user = await _active_user(update, context)
    if not user:
        return
    if not user.notifications_enabled:
        user = db.update_user(user.telegram_id, notifications_enabled=True)
    await _send(context, update.effective_chat.id, tr(user).PAUSE_REMOVED, main_keyboard(user))


# ---------- settings ----------
def _settings_screen(user: User) -> tuple[str, InlineKeyboardMarkup]:
    L = tr(user)
    interests_ = db.interests(user.telegram_id)
    text = L.settings_text(
        L.limit_text(user.max_per_day),
        L.NEWS_LANGUAGES.get(user.news_language, user.news_language),
        len(interests_), sum(1 for on in interests_.values() if not on),
    )
    rows = _grid([
        (L.SET_LIMIT, f"{SETTINGS}limit"),
        (L.SET_NEWS_LANGUAGE, f"{SETTINGS}nl"),
        (L.SET_TOPICS, f"{SETTINGS}topics"),
        (L.SET_SOURCES, f"{SETTINGS}sources"),
        (L.SET_CVE, CVE_MENU),
        (L.SET_MUTE, f"{SETTINGS}mute"),
        (L.CLOSE, f"{SETTINGS}close"),
    ])
    return text, InlineKeyboardMarkup(rows)


def _mark(selected: bool, label: str) -> str:
    return ("✅ " if selected else "⬜ ") + label


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
    if action == "sources":
        await query.answer()
        await _show_sources(update, context, user, "s")
        return
    if action == "limit":
        if value is not None and value.isdigit() and int(value) in DAILY_LIMITS:
            user = db.update_user(user.telegram_id, max_per_day=int(value))
            await query.answer(L.limit_text(user.max_per_day))
        else:
            await query.answer()
            rows = _grid([(_mark(n == user.max_per_day, L.limit_text(n)), f"{SETTINGS}limit:{n}")
                          for n in DAILY_LIMITS])
            await _show(update, context, L.LIMIT_PROMPT, InlineKeyboardMarkup(rows + [back]))
            return
    elif action == "nl":
        if value in L.NEWS_LANGUAGES:
            user = db.update_user(user.telegram_id, news_language=value)
            await query.answer(L.NEWS_LANGUAGES[value])
        else:
            await query.answer()
            rows = _grid([(("✅ " if code == user.news_language else "⬜ ") + label, f"{SETTINGS}nl:{code}")
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
    else:  # "menu", or anything unexpected (e.g. an old "delete my data" button)
        await query.answer()
    await _show(update, context, *_settings_screen(user))


def _mute_screen(user: User) -> tuple[str, InlineKeyboardMarkup]:
    L = tr(user)
    interests_ = db.interests(user.telegram_id)
    categories = [c for c in db.categories() if c.id in interests_]
    rows = _grid([(("🔔 " if interests_[c.id] else "🔕 ") + c.label(user.language), f"{SETTINGS}mute:{c.id}")
                    for c in categories], checkboxes=True)
    rows.append([_button(L.BACK, f"{SETTINGS}menu")])
    return (L.MUTE_PROMPT if categories else L.MUTE_NONE), InlineKeyboardMarkup(rows)


async def digest_removed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/digest, 📰 Digest and dg:… buttons from before the daily digest was removed: say so and give the
    current keyboard."""
    user = await _active_user(update, context)
    if not user:
        return
    if update.callback_query:
        await update.callback_query.answer()
    await _send(context, update.effective_chat.id, tr(user).DIGEST_REMOVED, main_keyboard(user))


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
# News batches (📥 Latest news, welcome news) go out in the background, one story every NEWS_GAP_SECONDS,
# so a long batch never holds up the bot for other users. One batch per user at a time.
_batches: set[asyncio.Task] = set()
_sending: set[int] = set()


def _start_batch(user_id: int, job) -> bool:
    """Run job() (an async function) in the background unless this user already has a batch going."""
    if user_id in _sending:
        return False
    _sending.add(user_id)

    async def run():
        try:
            await job()
        except Exception:
            log.exception("News batch for %s failed", user_id)
        finally:
            _sending.discard(user_id)

    task = asyncio.get_running_loop().create_task(run())
    _batches.add(task)
    task.add_done_callback(_batches.discard)
    return True


async def wait_for_batches() -> None:
    """Wait until every news batch has been sent (tests, shutdown)."""
    while pending := [t for t in _batches if not t.done()]:
        await asyncio.gather(*pending, return_exceptions=True)


async def pull_news(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """📥 Latest news or /latest: re-check the user's sources, then send today's stories for their topics
    that they haven't received yet, newest first (up to LATEST_NEWS_MAX), one after another without any
    "show more" step."""
    user = await _active_user(update, context)
    if not user:
        return
    L = tr(user)
    chat_id = update.effective_chat.id
    query = update.callback_query
    if query:  # the "Show more" button under older batches
        await query.answer()
        try:
            await query.edit_message_reply_markup(None)
        except TelegramError:
            pass
    if not any(db.interests(user.telegram_id).values()):
        await _send(context, chat_id, L.PULL_NO_TOPICS, main_keyboard(user))
        return

    async def job():
        await _refresh_sources(context, user, chat_id)
        items, _ = db.pull_candidates(user, day_start_utc(local_today()), config.LATEST_NEWS_MAX)
        if not items:
            await _send(context, chat_id, L.PULL_NOTHING, main_keyboard(user))
            return
        sent = await _deliver_today(context.bot, user, items, gap=config.NEWS_GAP_SECONDS)
        log.info("User %s pulled %s stories", user.telegram_id, sent)

    _start_batch(user.telegram_id, job)  # a tap while a batch is still going is ignored


async def _deliver_today(bot, user: User, items: list, gap: float = 0.3) -> int:
    """Send today's stories (from db.pull_candidates) to a user, each claimed first so nothing is sent
    twice; a failed one can be tried again later. Returns how many were sent."""
    providers = {p.id: p.name for p in db.providers()}
    categories = {c.id: c for c in db.categories()}
    sent = 0
    for article, category_id in items:
        if not db.claim_pulled(user.telegram_id, article, category_id):
            continue  # already received meanwhile
        try:
            await _retry(lambda: send_alert(bot, user, article, providers.get(article.provider_id, ""),
                                            categories.get(category_id)))
            sent += 1
        except TelegramError as e:
            db.unclaim_pulled(user.telegram_id, article, str(e))
            log.warning("Today's article %s to %s failed: %s", article.id, user.telegram_id, e)
        if sent < len(items):
            await asyncio.sleep(gap)  # one story at a time
    metrics.inc("news_pulled", sent)
    return sent


def start_welcome_news(bot, user: User) -> None:
    """send_welcome_news in the background."""
    _start_batch(user.telegram_id, lambda: send_welcome_news(bot, user))


async def send_welcome_news(bot, user: User) -> int:
    """Once per user (when onboarding is done, or once for anyone who has seen fewer than
    WELCOME_NEWS_COUNT stories): WELCOME_NEWS_COUNT of the latest stories they haven't received, one after
    another. Today's for their topics come first; if there aren't enough, the last 24 hours for their
    topics, then today's latest from other topics. If nothing is available yet it's tried again later.
    Returns how many were sent."""
    user = db.get_user(user.telegram_id) or user
    if user.welcome_news_at:
        return 0
    L = tr(user)
    today = day_start_utc(local_today())
    items, _ = db.pull_candidates(user, today, config.WELCOME_NEWS_COUNT)
    seen = {a.story_cluster_id or a.id for a, _ in items}
    fills = [((now_utc() - timedelta(hours=24)).isoformat(), False), (today, True)]
    for since, any_topic in fills:
        if len(items) >= config.WELCOME_NEWS_COUNT:
            break
        more_items, _ = db.pull_candidates(user, since, config.WELCOME_NEWS_COUNT * 3, any_topic=any_topic)
        for article, category_id in more_items:
            if len(items) >= config.WELCOME_NEWS_COUNT:
                break
            if (article.story_cluster_id or article.id) not in seen:
                seen.add(article.story_cluster_id or article.id)
                items.append((article, category_id))
    if not items:
        return 0  # nothing collected yet: the background loop tries again in a minute
    db.update_user(user.telegram_id, welcome_news_at=now_utc().isoformat())
    try:
        await bot.send_message(user.chat_id, L.WELCOME_NEWS_INTRO, parse_mode=ParseMode.HTML,
                               reply_markup=main_keyboard(user))
    except TelegramError as e:
        log.warning("Welcome news to %s failed: %s", user.telegram_id, e)
        return 0
    sent = await _deliver_today(bot, user, items, gap=config.NEWS_GAP_SECONDS)
    log.info("Sent %s welcome stories to %s", sent, user.telegram_id)
    return sent


class UserCountBadge:
    """Keeps the number of users at the top of the bot in Telegram: in its profile line (short
    description) and in the text shown above an empty chat (description), in English and Amharic.
    Checked every USER_COUNT_MINUTES; Telegram is only told when the number changed."""

    def __init__(self, bot):
        self.bot = bot
        self.shown: int | None = None

    async def run_forever(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Updating the user count failed")
            await asyncio.sleep(USER_COUNT_MINUTES * 60)

    async def run_once(self) -> bool:
        count = db.user_count()
        if count == self.shown:
            return False
        number = f"{count:,}"
        try:
            for language, L in ((None, texts.get("en")), ("en", texts.get("en")), ("am", texts.get("am"))):
                await self.bot.set_my_short_description(L.bot_short_description(number)[:120], language_code=language)
                await self.bot.set_my_description(L.bot_description(number)[:512], language_code=language)
        except TelegramError as e:
            log.warning("Could not update the user count in the bot's description: %s", e)
            return False
        self.shown = count
        log.info("Bot description now shows %s users", count)
        return True


class OnboardingAutosave:
    """Every minute: people who chose topics (or CVE reports) during onboarding but never tapped
    ➡️ Next / Done get their choices saved after ONBOARDING_AUTOSAVE_MINUTES without a tap. Their
    remaining steps take the defaults (every source on, instant alerts); they're told so, get the main
    keyboard, and get today's news they haven't received. Also, a few at a time: anyone who finished
    onboarding but hasn't had their welcome news yet (joined before it existed, saw fewer than
    WELCOME_NEWS_COUNT stories, or nothing was collected when they joined) gets it now."""

    def __init__(self, bot):
        self.bot = bot

    async def run_forever(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Onboarding auto-save failed")
            await asyncio.sleep(60)

    async def run_once(self, now: datetime | None = None) -> int:
        now = now or now_utc()
        idle_since = (now - timedelta(minutes=config.ONBOARDING_AUTOSAVE_MINUTES)).isoformat()
        done = 0
        for user in db.onboarding_idle_users(idle_since):
            user = db.update_user(user.telegram_id, onboarding_completed=True, delivery_mode="instant")
            L = tr(user)
            try:
                await self.bot.send_message(user.chat_id, L.auto_saved(_topic_names(user)),
                                            parse_mode=ParseMode.HTML, reply_markup=main_keyboard(user))
            except TelegramError as e:
                log.warning("Auto-save message to %s failed: %s", user.telegram_id, e)
                continue
            log.info("Auto-saved onboarding for %s after %s min", user.telegram_id, config.ONBOARDING_AUTOSAVE_MINUTES)
            start_welcome_news(self.bot, user)
            done += 1
        for user in db.welcome_news_due(limit=CATCH_UP_PER_MINUTE):
            start_welcome_news(self.bot, user)
        return done


async def _refresh_sources(context: ContextTypes.DEFAULT_TYPE, user: User, chat_id: int) -> None:
    """Fetch the user's sources (those covering their topics, minus any they switched off) that weren't
    checked in the last minute, waiting up to PULL_REFRESH_SECONDS. A slow feed keeps going in the
    background and its stories arrive as alerts."""
    collector = context.application.bot_data.get("collector")
    if not collector:
        return
    excluded = db.source_exclusions(user.telegram_id)
    ids = {pid for g in _source_groups(user, "o") for pid in g["ids"]} - excluded
    if not ids:
        return
    try:
        await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    except TelegramError:
        pass
    task = asyncio.ensure_future(collector.refresh(ids))
    try:
        await asyncio.wait_for(asyncio.shield(task), PULL_REFRESH_SECONDS)
    except asyncio.TimeoutError:
        log.info("Latest news for %s: some feeds still loading after %ss", user.telegram_id, PULL_REFRESH_SECONDS)
    except Exception:
        log.exception("Refreshing sources for %s failed", user.telegram_id)


# ---------- 🛡 CVE reports ----------
def _term_hash(term: str) -> str:
    return hashlib.sha1(term.encode("utf-8")).hexdigest()[:8]


def _cve_screen(user: User) -> tuple[str, InlineKeyboardMarkup]:
    L = tr(user)
    techs = [t["name"] for t in db.user_technologies(user.telegram_id)]
    text = L.cve_screen(user.cve_alerts, L.CVE_MIN_SEVERITY_LABELS[user.cve_min_severity], techs,
                        user.cve_all_technologies)
    rows = _grid([
        (L.CVE_TURN_OFF if user.cve_alerts else L.CVE_TURN_ON, f"{CVE}{'off' if user.cve_alerts else 'on'}"),
        (L.CVE_PICK, f"{CVE}p:0"),
        (L.CVE_ADD, f"{CVE}add"),
        (("✅ " if user.cve_all_technologies else "⬜ ") + L.CVE_ALL, f"{CVE}all"),
        (L.CVE_SEVERITY, f"{CVE}sev"),
        (L.CVE_CSV, f"{CVE}csv"),
        (L.CLOSE, f"{CVE}close"),
    ])
    return text, InlineKeyboardMarkup(rows)


def _cve_pages() -> list[str]:
    """Group keys that have technologies, in the seed file's order, then "own" (the user's own words)."""
    present = {t.grp for t in db.technologies()}
    return [g for g in TECH_GROUPS if g in present] + ["own"]


def _cve_picker(user: User, page: int, onboarding: bool = False) -> tuple[str, InlineKeyboardMarkup]:
    """One page of technologies. In onboarding the last button is ➡️ Next (on with onboarding) instead of
    ✅ Done (back to the CVE screen)."""
    L = tr(user)
    pages = _cve_pages()
    page = min(max(page, 0), len(pages) - 1)
    mine = db.user_technologies(user.telegram_id)
    chosen = {t["technology_id"] for t in mine if t["technology_id"]}
    group = pages[page]
    if group == "own":
        own = [t for t in mine if not t["technology_id"]]
        rows = _grid([("✅ " + t["name"], f"{CVE}r:{page}:{_term_hash(t['term'])}") for t in own], checkboxes=True)
        rows.append([_button(L.CVE_ADD, f"{CVE}add")])
        title = L.CVE_OWN_GROUP
        body = L.CVE_OWN_HINT if own else L.CVE_OWN_EMPTY
    else:
        techs = [t for t in db.technologies() if t.grp == group]
        rows = _grid([(("✅ " if t.id in chosen else "⬜ ") + t.name, f"{CVE}t:{page}:{t.id}") for t in techs],
                     checkboxes=True)
        title = TECH_GROUPS[group].get(user.language or "en") or TECH_GROUPS[group]["en"]
        body = L.CVE_PICK_HINT
    rows.append([_button("◀️", f"{CVE}p:{page - 1}") if page > 0 else _button(" ", NOOP),
                 _button(f"{page + 1}/{len(pages)}", NOOP),
                 _button("▶️", f"{CVE}p:{page + 1}") if page < len(pages) - 1 else _button(" ", NOOP)])
    rows.append([_button(L.NEXT, f"{CVE}onext")] if onboarding else [_button(L.DONE, CVE_MENU)])
    head = (L.CVE_ONBOARDING_PROMPT + "\n\n") if onboarding and page == 0 else ""
    return (head + L.cve_picker_title(title, page + 1, len(pages), len(mine)) + "\n" + body,
            InlineKeyboardMarkup(rows))


def _cve_turn_on(user: User) -> User:
    if user.cve_alerts:
        return user
    log.info("User %s turned CVE reports on", user.telegram_id)
    return db.update_user(user.telegram_id, cve_alerts=True, cve_enabled_at=now_utc().isoformat())


def _cve_turn_off(user: User) -> User:
    """CVE reports off; CVE alerts still waiting to be sent are dropped."""
    if not user.cve_alerts:
        return user
    db.execute("UPDATE notifications n SET status = 'skipped', last_error = 'CVE reports turned off'"
               " FROM cves c WHERE c.article_id = n.article_id AND n.user_id = %s"
               " AND n.status IN ('queued', 'digest')", (user.telegram_id,))
    log.info("User %s turned CVE reports off", user.telegram_id)
    return db.update_user(user.telegram_id, cve_alerts=False)


async def cve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🛡 CVE reports or /cve."""
    user = await _active_user(update, context)
    if user:
        context.user_data.pop("cve_adding", None)
        await _send(context, update.effective_chat.id, *_cve_screen(user))


async def cve_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """cv:… buttons. Every change is saved at once and answered with a pop-up."""
    query = update.callback_query
    user = await _active_user(update, context)
    if not user:
        return
    L = tr(user)
    parts = query.data[len(CVE):].split(":")
    action = parts[0]
    toast, alert = None, False
    if action == "close":
        await query.answer()
        try:
            await query.message.delete()
        except TelegramError:
            await _show(update, context, L.CVE_TITLE, None)
        return
    if action == "csv":
        await _send_cve_csv(update, context, user)
        return
    if action == "onext":  # ➡️ Next on the onboarding technology picker
        context.user_data.pop("cve_onboarding", None)
        context.user_data.pop("cve_adding", None)
        await query.answer()
        await _onboarding_after_topics(update, context, user)
        return
    if action == "add":
        context.user_data["cve_adding"] = True
        await query.answer()
        await _show(update, context, L.CVE_ADD_PROMPT, InlineKeyboardMarkup([[_button(L.CVE_CANCEL, f"{CVE}addx")]]))
        return
    if action == "addx":
        context.user_data.pop("cve_adding", None)
        toast = L.CVE_ADD_CANCELLED
    elif action in ("on", "off"):
        if action == "on":
            user = _cve_turn_on(user)
            toast = L.CVE_ON_TOAST
        else:
            user = _cve_turn_off(user)
            toast = L.CVE_OFF_TOAST
    elif action == "all":
        user = db.update_user(user.telegram_id, cve_all_technologies=not user.cve_all_technologies)
        if user.cve_all_technologies:
            user = _cve_turn_on(user)
        toast = L.CVE_ALL_ON if user.cve_all_technologies else L.CVE_ALL_OFF
    elif action == "sev":
        if len(parts) > 1 and parts[1] in CVE_MIN_SEVERITIES:
            user = db.update_user(user.telegram_id, cve_min_severity=parts[1])
            toast = L.CVE_MIN_SEVERITY_LABELS[parts[1]]
        else:
            await query.answer()
            rows = _grid([(("✅ " if level == user.cve_min_severity else "⬜ ") + label, f"{CVE}sev:{level}")
                          for level, label in L.CVE_MIN_SEVERITY_LABELS.items()], checkboxes=True)
            rows.append([_button(L.BACK, CVE_MENU)])
            await _show(update, context, L.CVE_SEVERITY_PROMPT, InlineKeyboardMarkup(rows))
            return
        await query.answer(toast)
        rows = _grid([(("✅ " if level == user.cve_min_severity else "⬜ ") + label, f"{CVE}sev:{level}")
                      for level, label in L.CVE_MIN_SEVERITY_LABELS.items()], checkboxes=True)
        rows.append([_button(L.BACK, CVE_MENU)])
        await _show(update, context, L.CVE_SEVERITY_PROMPT, InlineKeyboardMarkup(rows))
        return
    elif action in ("p", "t", "r"):
        page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        if action == "t" and len(parts) > 2 and parts[2].isdigit():
            tech = next((t for t in db.technologies() if t.id == int(parts[2])), None)
            if not tech:
                toast, alert = L.CVE_TECH_GONE, True
            elif db.remove_user_technology(user.telegram_id, f"tech:{tech.key}"):
                toast = L.tech_removed(tech.name)
            else:
                db.add_user_technology(user.telegram_id, f"tech:{tech.key}", tech.id)
                user = _cve_turn_on(user)
                toast = L.tech_added(tech.name)
        elif action == "r" and len(parts) > 2:
            own = {_term_hash(t["term"]): t for t in db.user_technologies(user.telegram_id) if not t["technology_id"]}
            term = own.get(parts[2])
            if term and db.remove_user_technology(user.telegram_id, term["term"]):
                toast = L.tech_removed(term["name"])
            else:
                toast, alert = L.CVE_TECH_GONE, True
        await query.answer(toast, show_alert=alert)
        await _show(update, context, *_cve_picker(user, page, bool(context.user_data.get("cve_onboarding"))))
        return
    await query.answer(toast, show_alert=alert)
    await _show(update, context, *_cve_screen(user))


def _parse_terms(text: str) -> tuple[list[str], list[str]]:
    """Technology names typed by a user (separated by commas, semicolons or new lines): (good, rejected).
    A name is 2-50 letters, digits, spaces or . + # / - characters."""
    good, bad = [], []
    for raw in re.split(r"[,;\n]+", text):
        term = " ".join(raw.split())
        if not term:
            continue
        if 2 <= len(term) <= 50 and TERM_CHARS.fullmatch(term) and any(ch.isalnum() for ch in term):
            if term.casefold() not in [g.casefold() for g in good]:
                good.append(term)
        else:
            bad.append(term[:50])
    return good, bad


async def cve_add_text(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    """The message after ✍️ Add your own: add each name. A name that's in the catalog (or one of its
    aliases) ticks the catalog entry; anything else is matched as typed."""
    L = tr(user)
    context.user_data.pop("cve_adding", None)
    good, bad = _parse_terms(update.message.text or "")
    catalog = {}
    for t in db.technologies():
        for name in [t.name, *t.aliases]:
            catalog.setdefault(name.casefold(), t)
    mine = db.user_technologies(user.telegram_id)
    own_count = sum(1 for t in mine if not t["technology_id"])
    added, existing = [], []
    for term in good:
        tech = catalog.get(term.casefold())
        if tech:
            ok = db.add_user_technology(user.telegram_id, f"tech:{tech.key}", tech.id)
            name = tech.name
        elif own_count >= MAX_OWN_TECHNOLOGIES:
            bad.append(term)
            continue
        else:
            ok = db.add_user_technology(user.telegram_id, term.casefold())
            own_count += ok
            name = term.casefold()
        (added if ok else existing).append(name)
    if added:
        user = _cve_turn_on(user)
    log.info("User %s added %s technologies for CVE reports", user.telegram_id, len(added))
    onboarding = bool(context.user_data.get("cve_onboarding"))
    await _send(context, update.effective_chat.id,
                L.cve_added([html.escape(n) for n in added], [html.escape(n) for n in existing],
                            [html.escape(n) for n in bad]), None if onboarding else main_keyboard(user))
    if onboarding:  # back to the picker (its last page shows their own words) with ➡️ Next
        await _send(context, update.effective_chat.id, *_cve_picker(user, len(_cve_pages()) - 1, onboarding=True))
    else:
        await _send(context, update.effective_chat.id, *_cve_screen(user))


async def _send_cve_csv(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    """📄 CSV report: the user's CVE alerts as a spreadsheet."""
    L = tr(user)
    rows = db.recent_cves(user, 1000)
    if not rows:
        await update.callback_query.answer(L.CVE_CSV_EMPTY, show_alert=True)
        return
    await update.callback_query.answer()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["cve_id", "published", "severity", "cvss_score", "actively_exploited", "ransomware",
                     "matched", "matched_by", "products", "description", "nvd_link"])
    for r in rows:
        when = r["published_at"] or r["fetched_at"]
        writer.writerow([r["cve_id"], to_local(when).strftime("%Y-%m-%d %H:%M") if when else "",
                         r["severity"] or "", "" if r["cvss_score"] is None else r["cvss_score"],
                         "yes" if r["kev"] else "no", "yes" if r["kev_ransomware"] else "no",
                         "all technologies" if r["matched"] == "*" else (r["matched"] or ""),
                         r["match_source"] or "", "; ".join(r["products"] or []), r["description"], r["canonical_url"]])
    data = ("\ufeff" + out.getvalue()).encode("utf-8")  # BOM: Excel opens it as UTF-8
    name = f"cve-report_{local_today().isoformat()}.csv"
    await context.bot.send_document(update.effective_chat.id, document=data, filename=name,
                                    caption=L.cve_csv_caption(len(rows)), parse_mode=ParseMode.HTML)
    log.info("User %s downloaded a CVE report (%s rows)", user.telegram_id, len(rows))


async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Free text: technology names after ✍️ Add your own, otherwise the "I only understand…" reply."""
    if context.user_data.get("cve_adding"):
        user = await _active_user(update, context)
        if user:
            await cve_add_text(update, context, user)
        return
    await unknown(update, context)


# ---------- sources / help ----------
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _active_user(update, context)
    if user:
        L = tr(user)
        if config.SUPPORT_USERNAME:
            await _send(context, update.effective_chat.id, f"{L.HELP}\n\n{L.support(config.SUPPORT_USERNAME)}",
                        support_keyboard(user))
        else:
            await _send(context, update.effective_chat.id, L.HELP, main_keyboard(user))


def support_keyboard(user: User) -> InlineKeyboardMarkup:
    """A button that opens a chat with the support account."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(
        tr(user).BTN_SUPPORT, url=f"https://t.me/{config.SUPPORT_USERNAME}")]])


async def support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/support: who to message, with a tap-to-open button."""
    user = await _active_user(update, context)
    if not user:
        return
    if not config.SUPPORT_USERNAME:
        await help_command(update, context)
        return
    await _send(context, update.effective_chat.id, tr(user).support(config.SUPPORT_USERNAME), support_keyboard(user))


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
    rows = _grid([(L.ADMIN_PROVIDERS, f"{ADMIN}prov:0"), (L.ADMIN_CATEGORIES, f"{ADMIN}cat"),
                    (f"{L.ADMIN_FAILED} ({stats['failed_today']})", f"{ADMIN}fail"),
                    (L.ADMIN_REFRESH, f"{ADMIN}menu")])
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
    rows = _grid([(("✅" if p.is_enabled else "⏸") + ("⚠️" if p.consecutive_failures else "")
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
    rows = _grid([(L.ADMIN_DISABLE if p.is_enabled else L.ADMIN_ENABLE, f"{ADMIN}pt:{p.id}:{page}"),
                    (L.ADMIN_FETCH_NOW, f"{ADMIN}pf:{p.id}:{page}"),
                    (L.BACK, f"{ADMIN}prov:{page}")])
    return text, InlineKeyboardMarkup(rows)


def _categories_screen(user: User) -> tuple[str, InlineKeyboardMarkup]:
    L = tr(user)
    rows = _grid([(("✅ " if c.is_active else "⏸ ") + c.label(user.language), f"{ADMIN}ct:{c.id}")
                    for c in db.categories(active_only=False)], checkboxes=True)
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
                             ("settings", settings), ("digest", digest_removed), ("pause", pause_removed), ("resume", pause_removed),
                             ("sources", sources), ("language", choose_language), ("help", help_command),
                             ("admin", admin), ("backup", backup_menu), ("cve", cve), ("support", support)]:
        app.add_handler(CommandHandler(command, handler, filters=private))

    # Reply keyboard buttons, in every language.
    for name, handler in [("BTN_TOPICS", interests), ("BTN_SETTINGS", settings), ("BTN_PAUSE", pause_removed),
                          ("BTN_RESUME", pause_removed), ("BTN_DIGEST", digest_removed), ("BTN_SOURCES", sources),
                          ("BTN_HELP", help_command), ("BTN_ADMIN", admin), ("BTN_PULL", pull_news),
                          ("BTN_CVE", cve), ("BTN_CONTACT", support)]:
        app.add_handler(MessageHandler(private & filters.Text(texts.labels(name)), handler))
    app.add_handler(MessageHandler(private & filters.Text([texts.LANGUAGE_BUTTON]), choose_language))

    app.add_handler(CallbackQueryHandler(set_language, pattern=rf"^{SET_LANGUAGE}[a-z]{{2}}$"))
    app.add_handler(CallbackQueryHandler(topic_tap, pattern=rf"^({TOPIC}[oi]:(\d+|cve)|{TOPIC_ALL}[oi]|{TOPIC_NONE}[oi])$"))
    app.add_handler(CallbackQueryHandler(topic_save, pattern=rf"^{TOPIC_SAVE}[oi]$"))
    app.add_handler(CallbackQueryHandler(topic_skip, pattern=rf"^{TOPIC_SKIP}[oi]$"))
    app.add_handler(CallbackQueryHandler(onboarding_delivery, pattern=rf"^{ONBOARD_DELIVERY}(instant|digest)$"))
    app.add_handler(CallbackQueryHandler(source_tap, pattern=rf"^({SOURCE}[os]:\d+|{SOURCE_ALL}[os]|{SOURCE_NONE}[os])$"))
    app.add_handler(CallbackQueryHandler(source_save, pattern=rf"^({SOURCE_SAVE}|{SOURCE_SKIP})[os]$"))
    app.add_handler(CallbackQueryHandler(settings_action, pattern=rf"^{SETTINGS}[a-z]+(:[a-z0-9]+)?$"))
    app.add_handler(CallbackQueryHandler(digest_removed, pattern=rf"^{DIGEST}"))
    app.add_handler(CallbackQueryHandler(mute_from_alert, pattern=rf"^{MUTE_FROM_ALERT}\d+$"))
    app.add_handler(CallbackQueryHandler(admin_action, pattern=rf"^{ADMIN}[a-z]+(:\d+){{0,2}}$"))
    app.add_handler(CallbackQueryHandler(backup_action, pattern=rf"^{BACKUP}(now|on|off)$"))
    app.add_handler(CallbackQueryHandler(pull_news, pattern=f"^{PULL_MORE}$"))
    app.add_handler(CallbackQueryHandler(
        cve_action, pattern=rf"^{CVE}(menu|on|off|all|sev|sev:[A-Z]+|p:\d+|t:\d+:\d+|r:\d+:[0-9a-f]{{8}}|add|addx|csv|close|onext)$"))
    app.add_handler(CallbackQueryHandler(noop, pattern=f"^{NOOP}$"))
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, text_message))
    app.add_handler(MessageHandler(private & ~filters.COMMAND, unknown))
    app.add_handler(MessageHandler(private & filters.COMMAND, unknown))
    app.add_error_handler(on_error)
    return app
