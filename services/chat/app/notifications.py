"""Notifications: one place where the app tells the user that something happened.

Every notification is stored (the bell in the web app lists them), streamed live to open
browsers, and sent to the phone (ntfy push / SMS, see notifier.py) when the user has that
turned on for its category. Sending never raises into the code that reported the event.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .notifier import Notifier, review_message
from .repository import Repository
from .schemas import (
    ActionOut,
    ChannelPrefs,
    NotificationCategoryOut,
    NotificationOut,
    NotificationSettings,
)

log = logging.getLogger(__name__)

PREFS_KEY = "notification_prefs"
# The same notification again within this window is dropped (a rejected key fails every turn).
DEDUPE_MINUTES = 10


@dataclass(frozen=True)
class Category:
    label: str
    description: str
    web: bool = True
    phone: bool = False


CATEGORIES: dict[str, Category] = {
    "sign_in": Category(
        "New device sign-in", "A browser or phone opens the assistant for the first time", phone=True
    ),
    "credentials": Category(
        "Rejected keys and tokens",
        "Claude's API key, the Gmail app password or the mail server login is refused",
        phone=True,
    ),
    "review": Category("Tasks to review", "A drafted email or task waits for your approval", phone=True),
    "task": Category("Task results", "An approved task is done, or failed"),
    "photo_limit": Category("Photo limit", "More pictures than one message can carry"),
    "token_limit": Category("Token limit", "A reply is cut off at the output token limit"),
}


def device_label(user_agent: str) -> str:
    """'Chrome on Android' from a User-Agent header; good enough to recognise a device."""
    ua = user_agent or ""
    browser = next(
        (name for pattern, name in (
            (r"Edg/", "Edge"), (r"OPR/|Opera", "Opera"), (r"SamsungBrowser", "Samsung Internet"),
            (r"Firefox/|FxiOS", "Firefox"), (r"Chrome/|CriOS", "Chrome"), (r"Safari/", "Safari"),
        ) if re.search(pattern, ua)),
        "A browser",
    )
    system = next(
        (name for pattern, name in (
            (r"Android", "Android"), (r"iPhone", "iPhone"), (r"iPad", "iPad"),
            (r"Windows", "Windows"), (r"Mac OS X|Macintosh", "Mac"), (r"CrOS", "ChromeOS"),
            (r"Linux", "Linux"),
        ) if re.search(pattern, ua)),
        "an unknown device",
    )
    return f"{browser} on {system}"


class NotificationCenter:
    def __init__(self, repo: Repository, notifier: Notifier):
        self.repo = repo
        self.notifier = notifier
        # Each open browser gets (notification, pop up?) pairs.
        self._subscribers: set[asyncio.Queue[tuple[NotificationOut, bool]]] = set()
        self._tasks: set[asyncio.Task] = set()

    # ---- preferences --------------------------------------------------------

    async def prefs(self) -> dict[str, ChannelPrefs]:
        saved = await self.repo.get_preference(PREFS_KEY) or {}
        out = {}
        for cid, cat in CATEGORIES.items():
            mine = saved.get(cid, {})
            out[cid] = ChannelPrefs(web=mine.get("web", cat.web), phone=mine.get("phone", cat.phone))
        return out

    async def set_prefs(self, changes: dict[str, ChannelPrefs]) -> None:
        prefs = await self.prefs()
        prefs.update({cid: p for cid, p in changes.items() if cid in CATEGORIES})
        await self.repo.set_preference(PREFS_KEY, {cid: p.model_dump() for cid, p in prefs.items()})

    async def settings(self) -> NotificationSettings:
        prefs = await self.prefs()
        return NotificationSettings(
            categories=[
                NotificationCategoryOut(id=cid, label=cat.label, description=cat.description, **prefs[cid].model_dump())
                for cid, cat in CATEGORIES.items()
            ],
            phone_channels=self.notifier.channels,
        )

    # ---- sending --------------------------------------------------------------

    async def emit(
        self,
        category: str,
        title: str,
        body: str,
        *,
        level: str = "info",
        session_id: str | None = None,
        phone: bool = True,
        dedupe: bool = True,
    ) -> NotificationOut | None:
        """Store, stream to browsers and (if turned on) send to the phone. Never raises."""
        try:
            if dedupe:
                since = (datetime.now(timezone.utc) - timedelta(minutes=DEDUPE_MINUTES)).isoformat()
                if await self.repo.has_recent_notification(category, title, body, since):
                    return None
            note = await self.repo.add_notification(category, level, title, body, session_id)
            prefs = (await self.prefs())[category]
            for queue in list(self._subscribers):
                if queue.full():  # a stalled browser; it reloads the list when it reconnects
                    continue
                queue.put_nowait((note, prefs.web))
            if phone and prefs.phone and self.notifier.channels:
                self._spawn(self._to_phone(note))
            return note
        except Exception:
            log.exception("could not record notification %r", title)
            return None

    async def _to_phone(self, note: NotificationOut) -> None:
        errors = await self.notifier.send(note.title, note.body)
        rejected = [e for e in errors if "HTTP 401" in e or "HTTP 403" in e]
        if rejected:
            # Shown in the browser only: sending it to the phone would fail the same way.
            await self.emit(
                "credentials", "Phone notifications are failing",
                f"{'; '.join(rejected)}. Check the Twilio or ntfy settings in .env.",
                level="error", phone=False,
            )

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def subscribe(self) -> asyncio.Queue[tuple[NotificationOut, bool]]:
        queue: asyncio.Queue[tuple[NotificationOut, bool]] = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[tuple[NotificationOut, bool]]) -> None:
        self._subscribers.discard(queue)

    # ---- events -----------------------------------------------------------------

    async def review_needed(self, action: ActionOut, reminder: bool = False) -> None:
        title, body = review_message(action, reminder)
        await self.emit("review", title[0].upper() + title[1:], body, session_id=action.session_id, dedupe=False)

    async def remind_loop(self, minutes: int, interval: float = 60.0) -> None:
        """Remind once about drafts still waiting after `minutes`."""
        if minutes <= 0:
            return
        while True:
            await asyncio.sleep(interval)
            try:
                cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
                for action in await self.repo.actions_to_remind(cutoff):
                    await self.repo.mark_reminded(action.id)
                    await self.review_needed(action, reminder=True)
            except Exception:
                log.exception("review reminder check failed")

    async def idle(self) -> None:
        """Wait for phone sends in flight (used by tests and shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
