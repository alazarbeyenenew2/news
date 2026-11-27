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
BTN_CONTACT = "💬 Contact us"
BTN_ADMIN = "🛠 Admin"
KEYBOARD_PLACEHOLDER = "Choose from the menu…"

# ---------- command menu ----------
CMD_START = "Start / main menu"
CMD_INTERESTS = "Choose your topics"
CMD_SOURCES = "News sources"
CMD_LANGUAGE = "Change language"
CMD_PAUSE = "Pause alerts"
CMD_RESUME = "Resume alerts"
CMD_SETTINGS = "Settings"
CMD_HELP = "How it works"
CMD_SUPPORT = "Contact support"
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
                 "Tap as many as you like (each tap is saved right away), then <b>➡️ Next</b>. "
                 "You can change them any time.")
TOPICS_EDIT_PROMPT = ("🗞 <b>Your topics</b>\n"
                      "Tap a topic to add or remove it. Changes are saved right away.")


def topics_selected(count: int) -> str:
    return f"\n\n<i>Selected: {count}</i>"


TOPIC_SKIP = "Skip for now"
TOPIC_ALL = "☑️ Select all"
NEXT = "➡️ Next"
DONE = "✅ Done"
TOPICS_ALL_ON = "☑️ All topics selected"
TOPICS_CLEARED = "⬜ All topics cleared"


def topic_added(topic: str) -> str:
    return f"✅ {topic} added"


def topic_removed(topic: str) -> str:
    return f"⬜ {topic} removed"


TOPIC_NONE = "⬜ Clear"
TOPIC_NEED_ONE = "Choose at least one topic, or tap “Skip for now”."


def onboarding_done(topics: str) -> str:
    return (f"✅ <b>You're all set!</b>\n{LINE}\n"
            f"Topics: {topics}\nYou'll get each story ⚡ as soon as it's published.\n\n"
            "Only stories published from now on are sent, so there's no flood of old news.\n"
            "Use the buttons below to change anything.")


def auto_saved(topics: str) -> str:
    return (f"✅ <b>Your choices are saved.</b>\n{LINE}\n"
            f"Topics: {topics}\nYou'll get each story ⚡ as soon as it's published.\n\n"
            "Change anything with the buttons below: 🗞 My topics, 📚 Sources, ⚙️ Settings.")


DIGEST_REMOVED = ("📰 The daily digest has been retired: every story now arrives ⚡ instantly, as soon as "
                  "it's published. Tap 📥 Latest news any time for today's stories you haven't seen.")
WELCOME_NEWS_INTRO = "📰 <b>The latest news</b> to get you started:"

ONBOARDING_SKIPPED = (f"👌 No topics yet.\n{LINE}\n"
                      "You won't get alerts until you choose some. Tap <b>🗞 My topics</b> "
                      "(or send /interests) whenever you're ready.")
MENU_HINT = "Use the buttons below."


# ---------- interests ----------
def interests_saved(topics: str) -> str:
    return f"✅ <b>Topics saved.</b>\nYou'll get alerts for: {topics}"


INTERESTS_EMPTY = ("⚠️ <b>No topics selected.</b>\nYou won't get alerts until you choose some "
                   "(🗞 My topics or /interests).")
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
NO_LIMIT = "no limit"
ANY_LANGUAGE = "All languages"
NEWS_LANGUAGES = {"any": "🌐 All languages", "en": "🇬🇧 English only", "am": "🇪🇹 Amharic only"}


def settings_text(alerts: str, limit: str, news_language: str, topics: int, muted: int) -> str:
    muted_text = f" ({muted} muted)" if muted else ""
    return (f"{SETTINGS_TITLE}\n{LINE}\n"
            f"🔔 Alerts: <b>{alerts}</b>\n"
            f"🔢 Daily limit: <b>{limit}</b>\n"
            f"🗣 News language: <b>{news_language}</b>\n"
            f"🗞 Topics: <b>{topics}</b>{muted_text}")


def limit_text(n: int) -> str:
    return NO_LIMIT if not n else f"{n} alerts"


SET_PAUSE = "⏸ Pause"
SET_RESUME = "▶️ Resume"
SET_LIMIT = "🔢 Daily limit"
SET_NEWS_LANGUAGE = "🗣 News language"
SET_MUTE = "🔕 Mute topics"
SET_TOPICS = "🗞 Topics"
BACK = "⬅️ Back"
CLOSE = "✖️ Close"

LIMIT_PROMPT = ("🔢 <b>Most alerts per day</b>\nAfter this many, the rest of the day's stories are skipped "
                "(they're not sent later).")
NEWS_LANGUAGE_PROMPT = "🗣 <b>Which news languages do you want?</b>"
MUTE_PROMPT = ("🔕 <b>Mute topics</b>\nA muted topic stays in your list but sends no alerts. "
               "Tap to mute or unmute.")
MUTE_NONE = "You have no topics yet."


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
                  "Tap a source to turn it on (✅) or off (⬜); changes are saved right away. New sources start on.\n"
                  "<i>We send headlines and short descriptions from each provider's own feed; read the full "
                  "story on their website.</i>")
SOURCES_SKIP = "⏭ Skip (all sources)"
SOURCES_NEED_ONE = "Keep at least one source on."
SOURCES_ALL_ON = "☑️ All sources on"
SOURCES_EMPTY = "No sources are active right now."
SOURCE_GONE = "That source isn't available any more."
SET_SOURCES = "📚 Sources"


def sources_on(on: int, total: int) -> str:
    return f"\n\n<i>On: {on} of {total}</i>"


def sources_saved(on: int, total: int) -> str:
    return f"✅ <b>Sources saved.</b>\nGetting news from {on} of {total} sources."

def source_on(name: str) -> str:
    return f"✅ {name} on"


def source_off(name: str) -> str:
    return f"⬜ {name} off"


# ---------- help ----------
HELP = (f"❓ <b>How {BOT} works</b>\n{LINE}\n"
        "We check news providers' official feeds every few minutes. When a new story matches one of "
        "your topics, you get an alert with the headline, a short description, the source, the time "
        "and a button to read the full article on the provider's site.\n\n"
        "<b>Commands</b>\n"
        "/latest — today's stories you haven't seen yet (📥 Latest news)\n"
        "/interests — choose your topics\n"
        "/settings — alerts, daily limit, news language, sources\n"
        "/pause, /resume — stop or restart alerts\n"
        "/sources — choose your news sources\n"
        "/cve — CVE reports: vulnerabilities in technologies you choose (nginx, Windows…)\n"
        "/language — change the bot language\n\n"
        "<i>The same story from several providers is sent once. Topics don't say anything about "
        "your views and are only used to choose your alerts.</i>")

BTN_SUPPORT = "💬 Contact support"


def support(username: str) -> str:
    return (f"💬 <b>Support</b>\nQuestions, problems or ideas? Message "
            f"<a href=\"https://t.me/{username}\">@{username}</a> on Telegram.")


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
            f"📬 Queue: <b>{s['queued']}</b> (oldest {oldest})\n"
            f"✅ Sent today: <b>{s['sent_today']}</b> · 📥 pulled {s.get('pulled_today', 0)} · ❌ failed {s['failed_today']} · skipped {s['skipped_today']}\n"
            f"🛡 CVEs today: <b>{s.get('cves_today', 0)}</b> · users with CVE reports {s.get('cve_users', 0)}")
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


# ---------- 🛡 CVE reports ----------
BTN_CVE = "🛡 CVE reports"
CMD_CVE = "CVE reports for your technologies"
SET_CVE = "🛡 CVE reports"
CVE_TITLE = "CVE report"
SEVERITY_NAMES = {"CRITICAL": "Critical", "HIGH": "High", "MEDIUM": "Medium", "LOW": "Low", "NONE": "None"}
SEVERITY_UNKNOWN = "Not rated yet"
KEV_LABEL = "Actively exploited"
RANSOMWARE_LABEL = "💀 used by ransomware"
CVE_MATCHES = "Matches"
CVE_ALL_TECH = "all technologies"
CVE_MATCH_SOURCES = {"product": "NVD product data", "description": "mentioned in the description"}
CVE_DETAILS = "📖 Details (NVD)"
CVE_SETTINGS_BUTTON = "🛡 CVE settings"
CVE_TURN_ON = "🔔 Turn on"
CVE_TURN_OFF = "⏸ Turn off"
CVE_PICK = "🧩 Popular technologies"
CVE_ADD = "✍️ Add your own"
CVE_ALL = "All technologies"
CVE_SEVERITY = "⚠️ Minimum severity"
CVE_CSV = "📄 CSV report"
CVE_CANCEL = "✖️ Cancel"
CVE_MIN_SEVERITY_LABELS = {"CRITICAL": "🔴 Critical only", "HIGH": "🟠 High and above",
                           "MEDIUM": "🟡 Medium and above", "ALL": "⚪ All"}
CVE_SEVERITY_PROMPT = ("⚠️ <b>Minimum severity</b>\nCVEs below this CVSS severity aren't sent. "
                       "Ones CISA says are being exploited are always sent.")
CVE_PICK_HINT = "<i>Tap to add or remove. ◀️ ▶️ for more groups.</i>"
CVE_OWN_GROUP = "Your own"
CVE_OWN_HINT = "<i>Tap one to remove it.</i>"
CVE_OWN_EMPTY = "<i>None yet. Tap ✍️ Add your own to type names like nginx or Windows Server 2022.</i>"
CVE_ADD_PROMPT = ("✍️ <b>Add your own technologies</b>\n"
                  "Send their names, separated by commas or on separate lines, for example:\n"
                  "<code>nginx, Windows Server 2022, FortiGate, Moodle</code>\n\n"
                  "<i>A CVE matches when its description names one of them as a whole word.</i>")
CVE_ADD_CANCELLED = "✖️ Cancelled"
CVE_ONBOARDING_PROMPT = ("🛡 <b>Which technologies do you use?</b>\n"
                         "You'll get an alert when a new vulnerability (CVE) affects one of them. Tick them here or "
                         "add your own on the last page, then <b>➡️ Next</b>. You can change this any time in "
                         "🛡 CVE reports.")
CVE_TECH_GONE = "That technology isn't on your list any more."
CVE_ON_TOAST = "🔔 CVE reports on"
CVE_OFF_TOAST = "⏸ CVE reports off"
CVE_ALL_ON = "✅ All technologies"
CVE_ALL_OFF = "⬜ Only your technologies"
CVE_CSV_EMPTY = "No CVE reports yet. They'll appear here once one matches your technologies."


def cve_screen(on: bool, severity: str, techs: list[str], all_tech: bool) -> str:
    if all_tech:
        tech_text = "<b>all technologies</b>"
    elif techs:
        shown = ", ".join(techs[:25]) + (f" and {len(techs) - 25} more" if len(techs) > 25 else "")
        tech_text = f"<b>{len(techs)}</b>: {shown}"
    else:
        tech_text = "<b>none yet</b>: tap 🧩 or ✍️ below"
    return (f"🛡 <b>CVE reports</b>\n"
            "Alerts for newly published vulnerabilities (CVEs) in the technologies you use, from NIST's "
            "National Vulnerability Database. ⚠️ marks CVEs CISA says are being exploited; those are always sent.\n"
            f"{LINE}\n"
            f"🔔 Alerts: <b>{'✅ On' if on else '⏸ Off'}</b>\n"
            f"⚠️ Minimum severity: <b>{severity}</b>\n"
            f"🧩 Technologies: {tech_text}")


def cve_picker_title(group: str, page: int, pages: int, count: int) -> str:
    return f"🧩 <b>{group}</b> · {page}/{pages}\n<i>On your list: {count}</i>"


def tech_added(name: str) -> str:
    return f"✅ {name} added"


def tech_removed(name: str) -> str:
    return f"⬜ {name} removed"


def cve_added(added: list[str], existing: list[str], skipped: list[str]) -> str:
    lines = []
    if added:
        lines.append("✅ <b>Added:</b> " + ", ".join(added))
    if existing:
        lines.append("ℹ️ Already on your list: " + ", ".join(existing))
    if skipped:
        lines.append("⚠️ Not added (2-50 letters, digits, spaces or . + # / -; at most 50 of your own): "
                     + ", ".join(skipped))
    return "\n".join(lines) or "Nothing to add."


def cve_csv_caption(count: int) -> str:
    return f"📄 <b>CVE report</b>: your last {count} CVE {'alert' if count == 1 else 'alerts'}"
