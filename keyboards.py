"""Laying out inline buttons as a grid, like checkboxes.

grid() puts buttons in rows of up to three, keeping their order: short labels share a row three at a
time, medium ones two at a time, and a label too long for a half-width button gets a row of its own.
Telegram gives every button in a row the same width, so a row fits when its widest label fits
ROW_WIDTH / (buttons in the row).
"""
import unicodedata

from telegram import InlineKeyboardButton

ROW_WIDTH = 40      # how much label fits across one row of a phone screen, in Latin letters
MAX_PER_ROW = 3
VARIATION_SELECTORS = ("︀", "️")
EMOJI_STYLE = "️"
FLAG_LETTERS = ("\U0001F1E6", "\U0001F1FF")
ETHIOPIC = (("ሀ", "᎟"), ("ⶀ", "⷟"))


def width(text: str) -> float:
    """Roughly how wide text looks, in Latin letters: emoji and wide characters count 2, Ethiopic
    (Amharic) letters 1.5, joiners and accents 0."""
    total = 0.0
    for i, ch in enumerate(text):
        if unicodedata.category(ch) in ("Mn", "Me", "Cf") or VARIATION_SELECTORS[0] <= ch <= VARIATION_SELECTORS[1]:
            continue
        if FLAG_LETTERS[0] <= ch <= FLAG_LETTERS[1]:  # a flag is two of these, shown as one emoji
            total += 1
            continue
        if any(lo <= ch <= hi for lo, hi in ETHIOPIC):
            total += 1.5
            continue
        emoji = (i + 1 < len(text) and text[i + 1] == EMOJI_STYLE) or ch >= "\U0001F000"
        total += 2 if emoji or unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return total


def fits(labels: list[str], row_width: int = ROW_WIDTH) -> bool:
    """Whether these labels fit side by side in one row."""
    return len(labels) <= MAX_PER_ROW and max(width(t) for t in labels) * len(labels) <= row_width


def grid(buttons: list[InlineKeyboardButton], max_per_row: int = MAX_PER_ROW, row_width: int = ROW_WIDTH,
         wide_last: bool = False) -> list[list[InlineKeyboardButton]]:
    """Buttons in order, as many per row (up to max_per_row) as fit. wide_last (for checkbox lists, where
    order matters less): labels too long to share a row go at the end, so they don't break up the grid."""
    if wide_last:
        narrow = [b for b in buttons if fits([b.text, b.text], row_width)]
        wide = [b for b in buttons if not fits([b.text, b.text], row_width)]
        return grid(narrow, max_per_row, row_width) + [[b] for b in wide]
    rows: list[list[InlineKeyboardButton]] = []
    for button in buttons:
        row = rows[-1] if rows else None
        if row is not None and len(row) < max_per_row and fits([b.text for b in row] + [button.text], row_width):
            row.append(button)
        else:
            rows.append([button])
    return rows
