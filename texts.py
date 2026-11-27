"""Bot languages. Every text the bot shows lives in texts_am.py (Amharic) or texts_en.py (English);
both files define the same names. Change wording there, not in bot.py.
"""
import texts_am
import texts_en

LANGUAGES = {"am": texts_am, "en": texts_en}
DEFAULT_LANGUAGE = "en"  # used until a user picks one

# Shown before a language is chosen, so it's in both languages.
LANGUAGE_BUTTON = "🌐 ቋንቋ / Language"
LANGUAGE_PROMPT = "🌐 <b>ቋንቋ ይምረጡ</b>\n<b>Choose your language</b>"
LANGUAGE_NAMES = {"am": "🇪🇹 አማርኛ", "en": "🇬🇧 English"}


def get(language: str | None):
    """The texts module for a language code (falls back to the default)."""
    return LANGUAGES.get(language or DEFAULT_LANGUAGE, LANGUAGES[DEFAULT_LANGUAGE])


def labels(name: str) -> list[str]:
    """Every label a keyboard button has, in every language, so any of them is recognised."""
    return list(dict.fromkeys(getattr(module, name) for module in LANGUAGES.values()))
