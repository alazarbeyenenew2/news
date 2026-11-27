"""Telegram profiles for the admin Users page: each user's bio, profile photo and current name/username,
read from the Bot API (getChat, getFile) and cached in memory so the page stays fast and Telegram isn't
asked on every page load. Photos are served through the admin page, so the bot token never reaches the
browser."""
import asyncio
import logging
import time
from dataclasses import dataclass

from telegram import Bot
from telegram.error import TelegramError

log = logging.getLogger(__name__)

PROFILE_TTL = 6 * 60 * 60    # re-read a profile after 6 hours
MISS_TTL = 30 * 60           # a failed lookup is retried after 30 minutes
PAGE_BUDGET_SECONDS = 4.0    # the Users page waits at most this long for profiles it hasn't cached
CONCURRENCY = 8
MAX_PHOTO_BYTES = 512 * 1024


@dataclass
class Profile:
    bio: str = ""
    first_name: str = ""
    last_name: str = ""
    username: str = ""
    photo_file_id: str = ""      # small (160×160) version
    photo_unique_id: str = ""    # changes when the user changes their photo; used to bust the browser cache
    fetched_at: float = 0.0
    ok: bool = True

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.last_name) if p)


class ProfileCache:
    def __init__(self):
        self._profiles: dict[int, Profile] = {}
        self._photos: dict[str, bytes] = {}   # photo_unique_id -> JPEG
        self._locks: dict[int, asyncio.Lock] = {}
        self._limit = asyncio.Semaphore(CONCURRENCY)

    def cached(self, telegram_id: int) -> Profile | None:
        return self._profiles.get(telegram_id)

    def _fresh(self, profile: Profile | None) -> bool:
        return bool(profile) and time.monotonic() - profile.fetched_at < (PROFILE_TTL if profile.ok else MISS_TTL)

    async def get(self, bot: Bot, telegram_id: int) -> Profile:
        profile = self._profiles.get(telegram_id)
        if self._fresh(profile):
            return profile
        lock = self._locks.setdefault(telegram_id, asyncio.Lock())
        async with lock:
            profile = self._profiles.get(telegram_id)
            if self._fresh(profile):
                return profile
            async with self._limit:
                try:
                    chat = await bot.get_chat(telegram_id)
                except TelegramError as e:  # blocked the bot, deleted account, network…
                    log.info("No Telegram profile for %s: %s", telegram_id, e)
                    profile = profile or Profile(ok=False)
                    profile.fetched_at, profile.ok = time.monotonic(), False
                    self._profiles[telegram_id] = profile
                    return profile
            photo = chat.photo
            profile = Profile(bio=chat.bio or "", first_name=chat.first_name or "", last_name=chat.last_name or "",
                              username=chat.username or "",
                              photo_file_id=photo.small_file_id if photo else "",
                              photo_unique_id=photo.small_file_unique_id if photo else "",
                              fetched_at=time.monotonic())
            self._profiles[telegram_id] = profile
            return profile

    async def get_many(self, bot: Bot, telegram_ids: list[int]) -> dict[int, Profile]:
        """Profiles for a page of users. Ones not ready within PAGE_BUDGET_SECONDS are left out (whatever
        was cached before is used) and finish in the background for the next page load."""
        tasks = {tid: asyncio.ensure_future(self.get(bot, tid)) for tid in telegram_ids}
        if tasks:
            await asyncio.wait(tasks.values(), timeout=PAGE_BUDGET_SECONDS)
        result = {}
        for tid, task in tasks.items():
            if task.done() and not task.cancelled() and task.exception() is None:
                result[tid] = task.result()
            elif self._profiles.get(tid):
                result[tid] = self._profiles[tid]
        return result

    async def photo(self, bot: Bot, telegram_id: int) -> tuple[bytes, str] | None:
        """(JPEG bytes, unique id) of the user's small profile photo, or None if they have none."""
        profile = await self.get(bot, telegram_id)
        if not profile.photo_file_id:
            return None
        cached = self._photos.get(profile.photo_unique_id)
        if cached is not None:
            return cached, profile.photo_unique_id
        async with self._limit:
            try:
                file = await bot.get_file(profile.photo_file_id)
                if file.file_size and file.file_size > MAX_PHOTO_BYTES:
                    return None
                data = bytes(await file.download_as_bytearray())
            except TelegramError as e:
                log.info("Could not download the profile photo of %s: %s", telegram_id, e)
                return None
        if len(data) > MAX_PHOTO_BYTES:
            return None
        if len(self._photos) > 2000:  # plenty for an admin page; keep memory bounded
            self._photos.clear()
        self._photos[profile.photo_unique_id] = data
        return data, profile.photo_unique_id

