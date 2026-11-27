"""Lining up inline button text on the left.

Telegram centres the text on inline buttons, so labels of different lengths start in different places.
align() pads every label to the width of the widest one in rows of the same size (all one-button rows
together, all two-button rows together, …) with an invisible Braille blank, which Telegram doesn't trim
like spaces. The bot sends every inline keyboard through it, so all menus line up (the report bot's
users list uses the same trick).
"""
import unicodedata

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

PAD = "⠀"  # U+2800 Braille blank


def width(text: str) -> int:
    """Roughly how wide text looks: emoji and wide characters count 2, joiners and accents 0."""
    total = 0
    for i, ch in enumerate(text):
        if ch == PAD:
            total += 1
            continue
        if unicodedata.category(ch) in ("Mn", "Me", "Cf") or "︀" <= ch <= "️":
            continue
        if "\U0001f1e6" <= ch <= "\U0001f1ff":  # a flag is two of these, shown as one emoji
            total += 1
            continue
        emoji_style = (i + 1 < len(text) and text[i + 1] == "️") or ch >= "\U0001f000"
        total += 2 if emoji_style or unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return total


def _blank(text: str) -> bool:
    return not text.strip(" " + PAD)


def align(markup):
    """The same keyboard with every label padded to line up on the left. Anything that isn't an inline
    keyboard (a reply keyboard, None) is returned unchanged."""
    if not isinstance(markup, InlineKeyboardMarkup):
        return markup
    rows = [list(row) for row in markup.inline_keyboard]
    widest: dict[int, int] = {}
    for row in rows:
        for b in row:
            if not _blank(b.text):
                widest[len(row)] = max(widest.get(len(row), 0), width(b.text.rstrip(PAD)))
    out = []
    for row in rows:
        new_row = []
        for b in row:
            label = b.text.rstrip(PAD)
            if not _blank(label):
                label += PAD * (widest[len(row)] - width(label))
            new_row.append(InlineKeyboardButton(label, callback_data=b.callback_data, url=b.url))
        out.append(new_row)
    return InlineKeyboardMarkup(out)
