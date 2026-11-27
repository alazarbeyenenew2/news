"""Amharic texts. texts_en.py must define exactly the same names (see texts.py; a test checks it).

Texts are HTML (Telegram parse mode). Anything inserted from users or providers (names, headlines,
summaries, source names) must be escaped with html.escape before it is passed in.
"""
import config

LINE = "──────────────"
BOT = config.BOT_NAME

# ---------- reply keyboard (the buttons under the text box) ----------
BTN_TOPICS = "🗞 የኔ ርዕሶች"
BTN_SETTINGS = "⚙️ ቅንብሮች"
BTN_PAUSE = "⏸ ማሳወቂያ አቁም"
BTN_RESUME = "▶️ ማሳወቂያ ቀጥል"
BTN_DIGEST = "📰 ዕለታዊ ማጠቃለያ"
BTN_SOURCES = "📚 ምንጮች"
BTN_HELP = "❓ እገዛ"
BTN_ADMIN = "🛠 አስተዳደር"
KEYBOARD_PLACEHOLDER = "ከምናሌው ይምረጡ…"

# ---------- command menu ----------
CMD_START = "መጀመሪያ / ዋና ምናሌ"
CMD_INTERESTS = "ርዕሶችዎን ይምረጡ"
CMD_SOURCES = "የዜና ምንጮች"
CMD_LANGUAGE = "ቋንቋ ቀይር"
CMD_DIGEST = "ፈጣን ማሳወቂያ ወይስ ዕለታዊ ማጠቃለያ"
CMD_PAUSE = "ማሳወቂያ አቁም"
CMD_RESUME = "ማሳወቂያ ቀጥል"
CMD_SETTINGS = "ቅንብሮች"
CMD_HELP = "እንዴት እንደሚሠራ"
CMD_ADMIN = "አስተዳደር፦ ሁኔታ፣ ምንጮች፣ ርዕሶች"
CMD_BACKUP = "ዋና አስተዳዳሪ፦ ምትኬ"


# ---------- welcome / onboarding ----------
def welcome(name: str) -> str:
    return (f"👋 ሰላም {name}!\nወደ <b>{BOT}</b> እንኳን በደህና መጡ።\n{LINE}\n"
            "ከታማኝ የዜና ምንጮች በሚሰበሰቡ፣ እርስዎ በሚፈልጓቸው ርዕሶች ላይ ማሳወቂያ ያገኛሉ። "
            "እያንዳንዱ ማሳወቂያ ወደ ዋናው ጽሑፍ ይወስዳል።\n\n")


def welcome_back(name: str) -> str:
    return f"👋 እንኳን ደህና ተመለሱ፣ {name}!\n{LINE}\n"


TOPICS_PROMPT = ("🗞 <b>ርዕሶችዎን ይምረጡ</b>\n"
                 "የፈለጉትን ያህል ይንኩ፣ ከዚያ <b>💾 አስቀምጥ</b>ን ይጫኑ። በማንኛውም ጊዜ መቀየር ይችላሉ።")


def topics_selected(count: int) -> str:
    return f"\n\n<i>የተመረጡ፦ {count}</i>"


TOPIC_SAVE = "💾 አስቀምጥ"
TOPIC_SKIP = "አሁን እለፈው"
TOPIC_CANCEL = "✖️ ሰርዝ"
TOPIC_ALL = "☑️ ሁሉንም ምረጥ"
TOPIC_NONE = "⬜ አጽዳ"
TOPIC_NEED_ONE = "ቢያንስ አንድ ርዕስ ይምረጡ፣ ወይም “አሁን እለፈው”ን ይንኩ።"

DELIVERY_PROMPT = (f"📬 <b>ዜና እንዴት ይድረስዎ?</b>\n{LINE}\n"
                   "⚡ <b>ፈጣን</b>፦ አዲስ ዜና እንደተገኘ ለእያንዳንዱ ማሳወቂያ።\n"
                   "📰 <b>ዕለታዊ ማጠቃለያ</b>፦ በቀን አንድ መልእክት ከዕለቱ ዜናዎች ጋር።\n\n"
                   "<i>በኋላ በ⚙️ ቅንብሮች መቀየር ይችላሉ።</i>")
DELIVERY_INSTANT = "⚡ ፈጣን ማሳወቂያ"
DELIVERY_DIGEST = "📰 ዕለታዊ ማጠቃለያ"


def onboarding_done(topics: str, instant: bool, hour: int) -> str:
    how = "⚡ ወዲያውኑ" if instant else f"📰 በየቀኑ {hour:02d}:00 ላይ በማጠቃለያ"
    return (f"✅ <b>ሁሉም ተዘጋጅቷል!</b>\n{LINE}\n"
            f"ርዕሶች፦ {topics}\nዜና {how} ይደርስዎታል።\n\n"
            "ከአሁን በኋላ የሚወጡ ዜናዎች ብቻ ይላካሉ፤ የቆዩ ዜናዎች አይጎርፉም።\n"
            "ማንኛውንም ለመቀየር ከታች ያሉትን ቁልፎች ይጠቀሙ።")


ONBOARDING_SKIPPED = (f"👌 እስካሁን ርዕስ አልመረጡም።\n{LINE}\n"
                      "ርዕስ እስኪመርጡ ማሳወቂያ አይደርስዎትም። ዝግጁ ሲሆኑ <b>🗞 የኔ ርዕሶች</b>ን ይንኩ "
                      "(ወይም /interests ይላኩ)።")
MENU_HINT = "ከታች ያሉትን ቁልፎች ይጠቀሙ።"


# ---------- interests ----------
def interests_saved(topics: str) -> str:
    return f"✅ <b>ርዕሶች ተቀምጠዋል።</b>\nማሳወቂያ የሚደርስዎት ለ፦ {topics}"


INTERESTS_EMPTY = ("⚠️ <b>ምንም ርዕስ አልተመረጠም።</b>\nርዕስ እስኪመርጡ ማሳወቂያ አይደርስዎትም "
                   "(🗞 የኔ ርዕሶች ወይም /interests)።")
INTERESTS_UNCHANGED = "ምንም አልተቀየረም።"
CATEGORY_GONE = "ይህ ርዕስ ከአሁን በኋላ የለም።"

# ---------- pause / resume ----------
PAUSED = ("⏸ <b>ማሳወቂያ ቆሟል።</b>\nርዕሶችዎ ተጠብቀዋል። በቆመበት ጊዜ የተገኙ ዜናዎች በኋላ አይላኩም። "
          "እንደገና ለመጀመር <b>▶️ ማሳወቂያ ቀጥል</b>ን ይንኩ።")
RESUMED = "▶️ <b>ማሳወቂያ እንደገና በርቷል።</b>\nለርዕሶችዎ አዳዲስ ዜናዎች ይደርስዎታል።"
ALREADY_PAUSED = "ማሳወቂያ ቀድሞውኑ ቆሟል።"
ALREADY_ON = "ማሳወቂያ ቀድሞውኑ በርቷል።"
RESUMED_NO_TOPICS = "\n\n⚠️ እስካሁን ርዕስ የለዎትም፦ <b>🗞 የኔ ርዕሶች</b>ን ይንኩ።"

# ---------- settings ----------
SETTINGS_TITLE = "⚙️ <b>ቅንብሮች</b>"
ON = "✅ በርቷል"
OFF = "⏸ ቆሟል"
INSTANT = "⚡ ፈጣን"
NO_LIMIT = "ገደብ የለም"
ANY_LANGUAGE = "ሁሉም ቋንቋዎች"
NEWS_LANGUAGES = {"any": "🌐 ሁሉም ቋንቋዎች", "en": "🇬🇧 እንግሊዝኛ ብቻ", "am": "🇪🇹 አማርኛ ብቻ"}


def digest_at(hour: int) -> str:
    return f"📰 ዕለታዊ ማጠቃለያ {hour:02d}:00 ላይ"


def settings_text(alerts: str, delivery: str, limit: str, news_language: str, topics: int, muted: int) -> str:
    muted_text = f" ({muted} ድምፅ የጠፋ)" if muted else ""
    return (f"{SETTINGS_TITLE}\n{LINE}\n"
            f"🔔 ማሳወቂያ፦ <b>{alerts}</b>\n"
            f"📬 አደራረስ፦ <b>{delivery}</b>\n"
            f"🔢 የቀን ገደብ፦ <b>{limit}</b>\n"
            f"🗣 የዜና ቋንቋ፦ <b>{news_language}</b>\n"
            f"🗞 ርዕሶች፦ <b>{topics}</b>{muted_text}")


def limit_text(n: int) -> str:
    return NO_LIMIT if not n else f"{n} ማሳወቂያዎች"


SET_PAUSE = "⏸ አቁም"
SET_RESUME = "▶️ ቀጥል"
SET_TO_DIGEST = "📰 ወደ ማጠቃለያ ቀይር"
SET_TO_INSTANT = "⚡ ወደ ፈጣን ቀይር"
SET_HOUR = "🕗 የማጠቃለያ ሰዓት"
SET_LIMIT = "🔢 የቀን ገደብ"
SET_NEWS_LANGUAGE = "🗣 የዜና ቋንቋ"
SET_MUTE = "🔕 ርዕስ ድምፅ አጥፋ"
SET_TOPICS = "🗞 ርዕሶች"
BACK = "⬅️ ተመለስ"
CLOSE = "✖️ ዝጋ"

HOUR_PROMPT = f"🕗 <b>ዕለታዊ ማጠቃለያው መቼ ይድረስዎ?</b>\n<i>ሰዓቶቹ በ{config.TIMEZONE.key} ናቸው።</i>"
LIMIT_PROMPT = ("🔢 <b>በቀን ከፍተኛው የማሳወቂያ ብዛት</b>\nከዚህ በኋላ የቀኑ ቀሪ ዜናዎች ይዘለላሉ "
                "(በኋላ አይላኩም)።")
NEWS_LANGUAGE_PROMPT = "🗣 <b>በየትኛው ቋንቋ ዜና ይፈልጋሉ?</b>"
MUTE_PROMPT = ("🔕 <b>ርዕስ ድምፅ አጥፋ</b>\nድምፁ የጠፋ ርዕስ በዝርዝርዎ ይቆያል፣ ግን ማሳወቂያ አይልክም። "
               "ለማጥፋት ወይም ለማብራት ይንኩ።")
MUTE_NONE = "እስካሁን ርዕስ የለዎትም።"


# ---------- digest ----------
def digest_menu(instant: bool, hour: int) -> str:
    now = INSTANT if instant else digest_at(hour)
    return (f"📰 <b>ዕለታዊ ማጠቃለያ</b>\n{LINE}\n"
            f"አሁን፦ <b>{now}</b>\n\n"
            "በማጠቃለያ ለእያንዳንዱ ዜና ማሳወቂያ ከመቀበል ይልቅ፣ ለርዕሶችዎ የወጡ ዜናዎችን "
            "የያዘ በቀን አንድ መልእክት ያገኛሉ።")


def digest_title(day: str, count: int) -> str:
    return f"📰 <b>የዕለቱ ማጠቃለያዎ</b> · {day}\n{count} ዜናዎች\n{LINE}"


def digest_more(count: int) -> str:
    return f"\n<i>…እና ሌሎች {count}።</i>"


# ---------- alerts ----------
SOURCE = "ምንጭ"
PUBLISHED = "የወጣበት"
FOUND = "የተገኘበት"  # when the provider gives no publication time
READ_FULL = "📖 ሙሉውን ያንብቡ"


def mute_button(topic: str) -> str:
    return f"🔕 {topic} ድምፅ አጥፋ"


def topic_muted(topic: str) -> str:
    return f"🔕 የ{topic} ድምፅ ጠፍቷል። በ⚙️ ቅንብሮች → 🔕 ርዕስ ድምፅ አጥፋ መመለስ ይችላሉ።"


def format_time(dt) -> str:
    return dt.strftime("%d/%m/%Y %H:%M")


# ---------- sources ----------
SOURCES_PROMPT = ("📚 <b>የዜና ምንጮችዎን ይምረጡ</b>\n"
                  "እነዚህ የመረጧቸውን ርዕሶች ይሸፍናሉ። ሁሉም በርተዋል፤ ለማጥፋት ይንኩ፣ ከዚያ <b>➡️ ቀጥል</b>ን ይጫኑ። "
                  "ወይም ሁሉንም ለማቆየት <b>⏭ እለፍ</b>ን ይንኩ።")
SOURCES_MANAGE = ("📚 <b>የዜና ምንጮች</b>\n"
                  "ምንጩን ለማብራት (✅) ወይም ለማጥፋት (⬜) ይንኩ፣ ከዚያ <b>💾 አስቀምጥ</b>ን ይጫኑ። አዳዲስ ምንጮች በርተው ይጀምራሉ።\n"
                  "<i>ከእያንዳንዱ ምንጭ የራሱ መጋቢ ርዕስና አጭር መግለጫ እንልካለን፤ ሙሉውን ዜና በድረ-ገጻቸው ያንብቡ።</i>")
SOURCES_NEXT = "➡️ ቀጥል"
SOURCES_SKIP = "⏭ እለፍ (ሁሉም ምንጮች)"
SOURCES_NEED_ONE = "ቢያንስ አንድ ምንጭ ይተዉ።"
SOURCES_UNCHANGED = "ምንም አልተቀየረም።"
SOURCES_EMPTY = "አሁን የሚሠሩ ምንጮች የሉም።"
SOURCE_GONE = "ይህ ምንጭ ከአሁን በኋላ የለም።"
SET_SOURCES = "📚 ምንጮች"


def sources_on(on: int, total: int) -> str:
    return f"\n\n<i>የበሩ፦ {on} ከ{total}</i>"


def sources_saved(on: int, total: int) -> str:
    return f"✅ <b>ምንጮች ተቀምጠዋል።</b>\nዜና ከ{on} ምንጮች ይመጣል (ከ{total})።"


# ---------- help ----------
HELP = (f"❓ <b>{BOT} እንዴት ይሠራል</b>\n{LINE}\n"
        "የዜና ምንጮችን ይፋዊ መጋቢዎች በየጥቂት ደቂቃው እንፈትሻለን። አዲስ ዜና ከርዕሶችዎ ጋር ሲገናኝ "
        "ርዕሱ፣ አጭር መግለጫ፣ ምንጩ፣ ሰዓቱ እና ሙሉውን በምንጩ ድረ-ገጽ ለማንበብ ቁልፍ ያለው ማሳወቂያ ይደርስዎታል።\n\n"
        "<b>ትዕዛዞች</b>\n"
        "/latest — ያላዩዋቸው የዛሬ ዜናዎች (📥 የቅርብ ዜናዎች)\n"
        "/interests — ርዕሶችዎን ይምረጡ\n"
        "/settings — ማሳወቂያ፣ ማጠቃለያ፣ የቀን ገደብ፣ የዜና ቋንቋ፣ ምንጮች\n"
        "/digest — ፈጣን ማሳወቂያ ወይስ ዕለታዊ ማጠቃለያ\n"
        "/pause, /resume — ማሳወቂያ አቁም ወይም ቀጥል\n"
        "/sources — የዜና ምንጮችዎን ይምረጡ\n"
        "/language — የቦቱን ቋንቋ ቀይር\n\n"
        "<i>አንድ ዜና ከብዙ ምንጮች ቢመጣም አንድ ጊዜ ብቻ ይላካል። ርዕሶችዎ ስለ አመለካከትዎ ምንም አይናገሩም፤ "
        "ማሳወቂያዎን ለመምረጥ ብቻ ያገለግላሉ።</i>")

LANGUAGE_SET = "✅ ቋንቋ ወደ <b>አማርኛ</b> ተቀይሯል።"
DISABLED = "⛔ መለያዎ በአስተዳዳሪ ተዘግቷል።"
UNKNOWN_MESSAGE = "የምረዳው የምናሌ ቁልፎችንና ትዕዛዞችን ብቻ ነው። ለማየት /help ይላኩ።"
ONLY_ADMINS = "⛔ ይህን ማድረግ የሚችሉት አስተዳዳሪዎች ብቻ ናቸው።"
ONLY_SUPER = "⛔ ይህን ማድረግ የሚችሉት ዋና አስተዳዳሪዎች ብቻ ናቸው።"


# ---------- admin ----------
def admin_status(s: dict, web_hint: bool) -> str:
    oldest = s.get("queue_age") or "—"
    text = (f"🛠 <b>አስተዳደር</b>\n{LINE}\n"
            f"👥 ተጠቃሚዎች፦ <b>{s['users']}</b> · ማሳወቂያ የሚቀበሉ {s['active_users']} · ያቆሙ {s['paused_users']}"
            f" · ቦቱን ያገዱ {s['blocked_users']}\n"
            f"📡 ምንጮች፦ <b>{s['providers_enabled']}</b> በርተዋል · {s['providers_failing']} እየወደቁ\n"
            f"📰 የዛሬ ዜናዎች፦ <b>{s['articles_today']}</b> · በሌላ ምንጭ የተደገሙ {s['duplicates_today']}"
            f" · ግምገማ የሚጠብቁ {s['low_confidence_today']}\n"
            f"📬 ወረፋ፦ <b>{s['queued']}</b> (የቆየው {oldest}) · ለማጠቃለያ የተያዙ {s['held_for_digest']}\n"
            f"✅ ዛሬ የተላኩ፦ <b>{s['sent_today']}</b> · 📥 የተጠየቁ {s.get('pulled_today', 0)} · ❌ ያልተሳኩ {s['failed_today']} · የተዘለሉ {s['skipped_today']}")
    if web_hint:
        text += "\n\n<i>ተጨማሪ (የዜና ምድቦች፣ ቁልፍ ቃላት፣ ተጠቃሚዎች) በድረ-ገጹ የአስተዳደር ገጽ ላይ።</i>"
    return text


ADMIN_PROVIDERS = "📡 ምንጮች"
ADMIN_CATEGORIES = "🗂 ርዕሶች"
ADMIN_FAILED = "⚠️ ያልተሳኩ ማሳወቂያዎች"
ADMIN_REFRESH = "🔄 አድስ"
ADMIN_FETCH_NOW = "⬇️ አሁን አምጣ"
ADMIN_ENABLE = "▶️ አብራ"
ADMIN_DISABLE = "⏸ አጥፋ"
ADMIN_RETRY_ALL = "🔁 ሁሉንም እንደገና ሞክር"
PROVIDERS_TITLE = "📡 <b>ምንጮች</b>\n<i>✅ በርቷል · ⏸ ጠፍቷል · ⚠️ የመጨረሻው ሙከራ አልተሳካም። ለዝርዝር ይንኩ።</i>"
CATEGORIES_TITLE = "🗂 <b>ርዕሶች</b>\n<i>ርዕሱን ለሁሉም ለማብራት ወይም ለማጥፋት ይንኩ።</i>"


def provider_card(name: str, key: str, enabled: bool, url: str, interval: int, last_ok: str, last_error: str,
                  total: int, categories: str, notes: str) -> str:
    return (f"📡 <b>{name}</b> <code>{key}</code>\n{LINE}\n"
            f"ሁኔታ፦ <b>{'✅ በርቷል' if enabled else '⏸ ጠፍቷል'}</b>\n"
            f"መጋቢ፦ {url}\n"
            f"በየ{interval // 60} ደቂቃው · እስካሁን {total} ዜናዎች\n"
            f"የመጨረሻው ስኬት፦ {last_ok}\n"
            f"የመጨረሻው ስህተት፦ {last_error}\n"
            f"ርዕሶች፦ {categories}\n"
            f"<i>{notes}</i>")


def fetch_started(name: str) -> str:
    return f"⬇️ {name} እየመጣ ነው…"


def failed_text(count: int, lines: list[str]) -> str:
    if not count:
        return "⚠️ <b>ያልተሳኩ ማሳወቂያዎች</b>\nየሉም። 🎉"
    return f"⚠️ <b>ያልተሳኩ ማሳወቂያዎች</b>፦ {count}\n{LINE}\n" + "\n".join(lines)


def retried(count: int) -> str:
    return f"🔁 {count} ማሳወቂያዎች እንደገና ወረፋ ገብተዋል።"


NEVER = "በጭራሽ"
NONE = "—"


# ---------- new user alert for admins ----------
def new_user_alert(name: str, telegram_id: int) -> str:
    return f"🆕 <b>አዲስ ተጠቃሚ</b>፦ {name} (<code>{telegram_id}</code>)"


# ---------- backup ----------
def backup_caption(when: str, users: int, articles: int, nightly: bool) -> str:
    kind = "የሌሊት ምትኬ" if nightly else "ምትኬ"
    return f"💾 <b>{kind}</b> · {when}\n👥 {users} ተጠቃሚዎች · 📰 {articles} ዜናዎች"


BACKUP_TOO_BIG = "⚠️ ምትኬው ከቴሌግራም 50 MB ገደብ በላይ ነው። ከአገልጋዩ ይቅዱት።"
BACKUP_WORKING = "💾 ምትኬ እየተዘጋጀ ነው…"

# ---------- 📥 latest news ----------
BTN_PULL = "📥 የቅርብ ዜናዎች"
CMD_LATEST = "ያላዩዋቸው የዛሬ ዜናዎች"
PULL_NO_TOPICS = "🗞 መጀመሪያ ርዕሶችዎን ይምረጡ፦ <b>🗞 የኔ ርዕሶች</b>ን ይንኩ።"
PULL_NOTHING = "✅ <b>ሁሉንም አይተዋል።</b>\nለርዕሶችዎ ዛሬ አዲስ ዜና የለም። አዳዲሶች እዚህ ይደርሳሉ።"


def pull_done(sent: int, remaining: int) -> str:
    text = f"📥 ከዛሬ {sent} ዜናዎች።"
    return text + (f"\nሌሎች {remaining} ይጠብቃሉ።" if remaining else "\n✅ እስካሁን ያሉት የዛሬ ዜናዎች እነዚህ ናቸው።")


def pull_more(remaining: int) -> str:
    return f"📥 ተጨማሪ አሳይ ({remaining})"
