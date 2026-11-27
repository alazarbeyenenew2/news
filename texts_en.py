"""English texts. texts_am.py must define exactly the same names (see texts.py; a test checks it).

Texts are HTML (Telegram parse mode). Anything inserted from users or providers (names, headlines,
summaries, source names) must be escaped with html.escape before it is passed in.
"""
import config

LINE = "──────────────"
BOT = config.BOT_NAME


# ---------- reply keyboard (the buttons under the text box) ----------
BTN_TOPICS = "🗞 My topics"
BTN_SETTINGS = "⚙️ Settings"
BTN_PAUSE = "⏸ Pause alerts"
BTN_RESUME = "▶️ Resume alerts"
BTN_DIGEST = "📰 Digest"
BTN_SOURCES = "📚 Sources"
BTN_HELP = "❓ Help"
BTN_ADMIN = "🛠 Admin"
KEYBOARD_PLACEHOLDER = "Choose from the menu…"

# ---------- command menu ----------
CMD_START = "Start / main menu"
CMD_INTERESTS = "Choose your topics"
CMD_SOURCES = "News sources"
CMD_LANGUAGE = "Change language"
CMD_DIGEST = "Instant alerts or daily digest"
CMD_PAUSE = "Pause alerts"
CMD_RESUME = "Resume alerts"
CMD_SETTINGS = "Settings"
CMD_HELP = "How it works"
CMD_ADMIN = "Admin: status, sources, topics"
CMD_BACKUP = "Super admin: backup"


# ---------- welcome / onboarding ----------
def welcome(name: str) -> str:
    return (f"👋 Hi {name}!\nWelcome to <b>{BOT}</b>.\n{LINE}\n"
            "Get alerts about the topics you care about, collected from trusted news providers. "
            "Every alert links to the original article.\n\n")


def welcome_back(name: str) -> str:
    return f"👋 Welcome back, {name}!\n{LINE}\n"


TOPICS_PROMPT = ("🗞 <b>Choose your topics</b>\n"
                 "Tap to select as many as you like, then <b>💾 Save</b>. You can change them any time.")


def topics_selected(count: int) -> str:
    return f"\n\n<i>Selected: {count}</i>"


TOPIC_SAVE = "💾 Save"
TOPIC_SKIP = "Skip for now"
TOPIC_CANCEL = "✖️ Cancel"
TOPIC_ALL = "☑️ Select all"
TOPIC_NONE = "⬜ Clear"
TOPIC_NEED_ONE = "Choose at least one topic, or tap “Skip for now”."

DELIVERY_PROMPT = (f"📬 <b>How should news reach you?</b>\n{LINE}\n"
                   "⚡ <b>Instant</b>: an alert for each new story, as soon as we find it.\n"
                   "📰 <b>Daily digest</b>: one message a day with the day's stories.\n\n"
                   "<i>You can change this later in ⚙️ Settings.</i>")
DELIVERY_INSTANT = "⚡ Instant alerts"
DELIVERY_DIGEST = "📰 Daily digest"


def onboarding_done(topics: str, instant: bool, hour: int) -> str:
    how = "⚡ instantly" if instant else f"📰 in a daily digest at {hour:02d}:00"
    return (f"✅ <b>You're all set!</b>\n{LINE}\n"
            f"Topics: {topics}\nYou'll get news {how}.\n\n"
            "Only stories published from now on are sent, so there's no flood of old news.\n"
            "Use the buttons below to change anything.")


ONBOARDING_SKIPPED = (f"👌 No topics yet.\n{LINE}\n"
                      "You won't get alerts until you choose some. Tap <b>🗞 My topics</b> "
                      "(or send /interests) whenever you're ready.")
MENU_HINT = "Use the buttons below."


# ---------- interests ----------
def interests_saved(topics: str) -> str:
    return f"✅ <b>Topics saved.</b>\nYou'll get alerts for: {topics}"


INTERESTS_EMPTY = ("⚠️ <b>No topics selected.</b>\nYou won't get alerts until you choose some "
                   "(🗞 My topics or /interests).")
INTERESTS_UNCHANGED = "No changes."
CATEGORY_GONE = "That topic isn't available any more."

# ---------- pause / resume ----------
PAUSED = ("⏸ <b>Alerts paused.</b>\nYour topics are kept. Stories found while paused won't be sent later. "
          "Tap <b>▶️ Resume alerts</b> to start again.")
RESUMED = "▶️ <b>Alerts are on again.</b>\nYou'll get new stories for your topics."
ALREADY_PAUSED = "Alerts are already paused."
ALREADY_ON = "Alerts are already on."
RESUMED_NO_TOPICS = "\n\n⚠️ You have no topics yet: tap <b>🗞 My topics</b>."

# ---------- settings ----------
SETTINGS_TITLE = "⚙️ <b>Settings</b>"
ON = "✅ On"
OFF = "⏸ Paused"
INSTANT = "⚡ Instant"
NO_LIMIT = "no limit"
ANY_LANGUAGE = "All languages"
NEWS_LANGUAGES = {"any": "🌐 All languages", "en": "🇬🇧 English only", "am": "🇪🇹 Amharic only"}


def digest_at(hour: int) -> str:
    return f"📰 Daily digest at {hour:02d}:00"


def settings_text(alerts: str, delivery: str, limit: str, news_language: str, topics: int, muted: int) -> str:
    muted_text = f" ({muted} muted)" if muted else ""
    return (f"{SETTINGS_TITLE}\n{LINE}\n"
            f"🔔 Alerts: <b>{alerts}</b>\n"
            f"📬 Delivery: <b>{delivery}</b>\n"
            f"🔢 Daily limit: <b>{limit}</b>\n"
            f"🗣 News language: <b>{news_language}</b>\n"
            f"🗞 Topics: <b>{topics}</b>{muted_text}")


def limit_text(n: int) -> str:
    return NO_LIMIT if not n else f"{n} alerts"


SET_PAUSE = "⏸ Pause"
SET_RESUME = "▶️ Resume"
SET_TO_DIGEST = "📰 Switch to digest"
SET_TO_INSTANT = "⚡ Switch to instant"
SET_HOUR = "🕗 Digest time"
SET_LIMIT = "🔢 Daily limit"
SET_NEWS_LANGUAGE = "🗣 News language"
SET_MUTE = "🔕 Mute topics"
SET_TOPICS = "🗞 Topics"
SET_DELETE = "🗑 Delete my data"
BACK = "⬅️ Back"
CLOSE = "✖️ Close"

HOUR_PROMPT = f"🕗 <b>When should the daily digest arrive?</b>\n<i>Times are {config.TIMEZONE.key}.</i>"
LIMIT_PROMPT = ("🔢 <b>Most alerts per day</b>\nAfter this many, the rest of the day's stories are skipped "
                "(they're not sent later).")
NEWS_LANGUAGE_PROMPT = "🗣 <b>Which news languages do you want?</b>"
MUTE_PROMPT = ("🔕 <b>Mute topics</b>\nA muted topic stays in your list but sends no alerts. "
               "Tap to mute or unmute.")
MUTE_NONE = "You have no topics yet."
DELETE_CONFIRM = ("🗑 <b>Delete your data?</b>\n"
                  "Your topics, settings and alert history will be deleted and you'll get no more alerts. "
                  "This can't be undone. Send /start to use the bot again later.")
DELETE_YES = "🗑 Yes, delete everything"
CANCEL = "✖️ Cancel"
DELETED = "✅ Your data has been deleted. Send /start if you want to come back."

# ---------- digest ----------
def digest_menu(instant: bool, hour: int) -> str:
    now = INSTANT if instant else digest_at(hour)
    return (f"📰 <b>Daily digest</b>\n{LINE}\n"
            f"Now: <b>{now}</b>\n\n"
            "With the digest you get one message a day listing the stories for your topics, "
            "instead of one alert per story.")


def digest_title(day: str, count: int) -> str:
    return f"📰 <b>Your daily digest</b> · {day}\n{count} {'story' if count == 1 else 'stories'}\n{LINE}"


def digest_more(count: int) -> str:
    return f"\n<i>…and {count} more.</i>"


# ---------- alerts ----------
SOURCE = "Source"
PUBLISHED = "Published"
FOUND = "Found"  # when the provider gives no publication time
READ_FULL = "📖 Read full article"


def mute_button(topic: str) -> str:
    return f"🔕 Mute {topic}"


def topic_muted(topic: str) -> str:
    return f"🔕 {topic} muted. Unmute it in ⚙️ Settings → 🔕 Mute topics."


def format_time(dt) -> str:
    return dt.strftime("%d %b %Y, %H:%M")


# ---------- sources ----------
SOURCES_PROMPT = ("📚 <b>Choose your news sources</b>\n"
                  "These cover the topics you picked. All are on: tap one to turn it off, then <b>➡️ Next</b>. "
                  "Or <b>⏭ Skip</b> to keep them all.")
SOURCES_MANAGE = ("📚 <b>News sources</b>\n"
                  "Tap a source to turn it on (✅) or off (⬜), then <b>💾 Save</b>. New sources start on.\n"
                  "<i>We send headlines and short descriptions from each provider's own feed; read the full "
                  "story on their website.</i>")
SOURCES_NEXT = "➡️ Next"
SOURCES_SKIP = "⏭ Skip (all sources)"
SOURCES_NEED_ONE = "Keep at least one source on."
SOURCES_UNCHANGED = "No changes."
SOURCES_EMPTY = "No sources are active right now."
SOURCE_GONE = "That source isn't available any more."
SET_SOURCES = "📚 Sources"


def sources_on(on: int, total: int) -> str:
    return f"\n\n<i>On: {on} of {total}</i>"


def sources_saved(on: int, total: int) -> str:
    return f"✅ <b>Sources saved.</b>\nGetting news from {on} of {total} sources."


# ---------- help ----------
HELP = (f"❓ <b>How {BOT} works</b>\n{LINE}\n"
        "We check news providers' official feeds every few minutes. When a new story matches one of "
        "your topics, you get an alert with the headline, a short description, the source, the time "
        "and a button to read the full article on the provider's site.\n\n"
        "<b>Commands</b>\n"
        "/latest — today's stories you haven't seen yet (📥 Latest news)\n"
        "/interests — choose your topics\n"
        "/settings — alerts, digest, daily limit, news language, delete your data\n"
        "/digest — instant alerts or one daily digest\n"
        "/pause, /resume — stop or restart alerts\n"
        "/sources — where the news comes from\n"
        "/language — change the bot language\n\n"
        "<i>The same story from several providers is sent once. Topics don't say anything about "
        "your views and are only used to choose your alerts.</i>")

LANGUAGE_SET = "✅ Language changed to <b>English</b>."
DISABLED = "⛔ Your account has been turned off by an admin."
UNKNOWN_MESSAGE = "I only understand the menu buttons and commands. Send /help to see them."
ONLY_ADMINS = "⛔ Only admins can do that."
ONLY_SUPER = "⛔ Only super admins can do that."

# ---------- admin ----------
def admin_status(s: dict, web_hint: bool) -> str:
    oldest = s.get("queue_age") or "—"
    text = (f"🛠 <b>Admin</b>\n{LINE}\n"
            f"👥 Users: <b>{s['users']}</b> · getting alerts {s['active_users']} · paused {s['paused_users']}"
            f" · blocked the bot {s['blocked_users']}\n"
            f"📡 Sources: <b>{s['providers_enabled']}</b> on · {s['providers_failing']} failing\n"
            f"📰 Articles today: <b>{s['articles_today']}</b> · same story elsewhere {s['duplicates_today']}"
            f" · waiting for review {s['low_confidence_today']}\n"
            f"📬 Queue: <b>{s['queued']}</b> (oldest {oldest}) · held for digests {s['held_for_digest']}\n"
            f"✅ Sent today: <b>{s['sent_today']}</b> · 📥 pulled {s.get('pulled_today', 0)} · ❌ failed {s['failed_today']} · skipped {s['skipped_today']}")
    if web_hint:
        text += "\n\n<i>More (article categories, keywords, users) on the web admin page.</i>"
    return text


ADMIN_PROVIDERS = "📡 Sources"
ADMIN_CATEGORIES = "🗂 Topics"
ADMIN_FAILED = "⚠️ Failed alerts"
ADMIN_REFRESH = "🔄 Refresh"
ADMIN_FETCH_NOW = "⬇️ Fetch now"
ADMIN_ENABLE = "▶️ Enable"
ADMIN_DISABLE = "⏸ Disable"
ADMIN_RETRY_ALL = "🔁 Retry all"
PROVIDERS_TITLE = "📡 <b>Sources</b>\n<i>✅ on · ⏸ off · ⚠️ last fetch failed. Tap one for details.</i>"
CATEGORIES_TITLE = "🗂 <b>Topics</b>\n<i>Tap to turn a topic on or off for everyone.</i>"


def provider_card(name: str, key: str, enabled: bool, url: str, interval: int, last_ok: str, last_error: str,
                  total: int, categories: str, notes: str) -> str:
    return (f"📡 <b>{name}</b> <code>{key}</code>\n{LINE}\n"
            f"State: <b>{'✅ on' if enabled else '⏸ off'}</b>\n"
            f"Feed: {url}\n"
            f"Every {interval // 60} min · {total} articles so far\n"
            f"Last success: {last_ok}\n"
            f"Last error: {last_error}\n"
            f"Topics: {categories}\n"
            f"<i>{notes}</i>")


def fetch_started(name: str) -> str:
    return f"⬇️ Fetching {name}…"


def failed_text(count: int, lines: list[str]) -> str:
    if not count:
        return "⚠️ <b>Failed alerts</b>\nNone. 🎉"
    return f"⚠️ <b>Failed alerts</b>: {count}\n{LINE}\n" + "\n".join(lines)


def retried(count: int) -> str:
    return f"🔁 {count} alerts queued again."


NEVER = "never"
NONE = "—"


# ---------- new user alert for admins ----------
def new_user_alert(name: str, telegram_id: int) -> str:
    return f"🆕 <b>New user</b>: {name} (<code>{telegram_id}</code>)"


# ---------- backup ----------
def backup_caption(when: str, users: int, articles: int, nightly: bool) -> str:
    kind = "Nightly backup" if nightly else "Backup"
    return f"💾 <b>{kind}</b> · {when}\n👥 {users} users · 📰 {articles} articles"


BACKUP_TOO_BIG = "⚠️ The backup is over Telegram's 50 MB limit. Copy it from the server instead."
BACKUP_WORKING = "💾 Making a backup…"

# ---------- 📥 latest news ----------
BTN_PULL = "📥 Latest news"
CMD_LATEST = "Today's news you haven't seen yet"
PULL_NO_TOPICS = "🗞 Choose your topics first: tap <b>🗞 My topics</b>."
PULL_NOTHING = "✅ <b>You're up to date.</b>\nNo new stories today for your topics. New ones will arrive here."


def pull_done(sent: int, remaining: int) -> str:
    text = f"📥 {sent} {'story' if sent == 1 else 'stories'} from today."
    return text + (f"\n{remaining} more waiting." if remaining else "\n✅ That's everything for today so far.")


def pull_more(remaining: int) -> str:
    return f"📥 Show more ({remaining})"
