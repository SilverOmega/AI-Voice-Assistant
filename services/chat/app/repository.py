"""SQLite persistence for chat sessions and messages."""

import json
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from .llm.base import ImageData
from .schemas import (
    ActionOut,
    EventOut,
    ImageRef,
    MessageOut,
    NotificationOut,
    Role,
    SessionDetail,
    SessionOut,
    Source,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'text',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);
CREATE TABLE IF NOT EXISTS images (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    media_type TEXT NOT NULL,
    data BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_images_message ON images(message_id, position);
CREATE TABLE IF NOT EXISTS actions (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    instruction TEXT NOT NULL,
    payload TEXT NOT NULL,
    result TEXT,
    error TEXT,
    reminded_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_actions_session ON actions(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_actions_status ON actions(status);
-- Events and documents outlive the chat they were made in.
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    action_id TEXT,
    title TEXT NOT NULL,
    start TEXT NOT NULL,
    "end" TEXT NOT NULL,
    location TEXT NOT NULL,
    notes TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(start);
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    action_id TEXT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    level TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    session_id TEXT,
    created_at TEXT NOT NULL,
    read_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_at);
-- Browsers and phones that have opened the app (each keeps a random id in local storage).
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    ip TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS preferences (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

KEEP_NOTIFICATIONS = 200

DEFAULT_TITLE = "New chat"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


class Repository:
    def __init__(self, path: str):
        self._path = path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA foreign_keys = ON")
        await self._db.execute("PRAGMA journal_mode = WAL")
        await self._db.executescript(SCHEMA)
        await self._add_missing_columns("actions", {"reminded_at": "TEXT"})
        # An action that was running when the service stopped goes back for review.
        await self._db.execute(
            "UPDATE actions SET status = 'pending', error = ? WHERE status = 'running'",
            ("Interrupted by a restart. Check whether it ran before approving again.",),
        )
        await self._db.commit()

    async def _add_missing_columns(self, table: str, columns: dict[str, str]) -> None:
        """Columns added after a table first shipped (CREATE TABLE IF NOT EXISTS skips them)."""
        cursor = await self.db.execute(f"PRAGMA table_info({table})")
        have = {row["name"] for row in await cursor.fetchall()}
        for name, sql_type in columns.items():
            if name not in have:
                await self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Repository is not connected")
        return self._db

    # ---- sessions -------------------------------------------------------

    async def create_session(self, title: str | None = None) -> SessionOut:
        now = _now()
        sid = _new_id()
        await self.db.execute(
            "INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (sid, title or DEFAULT_TITLE, now, now),
        )
        await self.db.commit()
        return SessionOut(id=sid, title=title or DEFAULT_TITLE, created_at=now, updated_at=now)

    async def list_sessions(self) -> list[SessionOut]:
        cursor = await self.db.execute(
            """
            SELECT s.*, COUNT(m.id) AS message_count
            FROM sessions s LEFT JOIN messages m ON m.session_id = s.id
            GROUP BY s.id ORDER BY s.updated_at DESC
            """
        )
        return [SessionOut(**dict(row)) for row in await cursor.fetchall()]

    async def get_session(self, session_id: str) -> SessionOut | None:
        cursor = await self.db.execute(
            """
            SELECT s.*, COUNT(m.id) AS message_count
            FROM sessions s LEFT JOIN messages m ON m.session_id = s.id
            WHERE s.id = ? GROUP BY s.id
            """,
            (session_id,),
        )
        row = await cursor.fetchone()
        return SessionOut(**dict(row)) if row else None

    async def get_session_detail(self, session_id: str) -> SessionDetail | None:
        session = await self.get_session(session_id)
        if session is None:
            return None
        messages = await self.list_messages(session_id)
        actions = await self.list_actions(session_id=session_id)
        return SessionDetail(**session.model_dump(), messages=messages, actions=actions)

    async def rename_session(self, session_id: str, title: str) -> SessionOut | None:
        await self.db.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title, _now(), session_id),
        )
        await self.db.commit()
        return await self.get_session(session_id)

    async def delete_session(self, session_id: str) -> bool:
        cursor = await self.db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        await self.db.commit()
        return cursor.rowcount > 0

    # ---- messages -------------------------------------------------------

    async def add_message(
        self,
        session_id: str,
        role: Role,
        content: str,
        source: Source = "text",
        images: Sequence[ImageData] = (),
    ) -> MessageOut:
        now = _now()
        mid = _new_id()
        await self.db.execute(
            "INSERT INTO messages (id, session_id, role, content, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (mid, session_id, role, content, source, now),
        )
        refs = [ImageRef(id=_new_id(), media_type=img.media_type) for img in images]
        await self.db.executemany(
            "INSERT INTO images (id, message_id, position, media_type, data) VALUES (?, ?, ?, ?, ?)",
            [(ref.id, mid, i, img.media_type, img.data) for i, (ref, img) in enumerate(zip(refs, images))],
        )
        # Auto-title a fresh session from its first user message.
        if role == "user":
            await self.db.execute(
                "UPDATE sessions SET title = ? WHERE id = ? AND title = ?",
                (_title_from(content) or "Image", session_id, DEFAULT_TITLE),
            )
        await self.db.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
        await self.db.commit()
        return MessageOut(
            id=mid,
            session_id=session_id,
            role=role,
            content=content,
            source=source,
            created_at=now,
            images=refs,
        )

    async def list_messages(self, session_id: str, limit: int | None = None) -> list[MessageOut]:
        if limit is None:
            cursor = await self.db.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at, rowid",
                (session_id,),
            )
            rows = await cursor.fetchall()
        else:
            cursor = await self.db.execute(
                "SELECT * FROM messages WHERE session_id = ? "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (session_id, limit),
            )
            rows = list(reversed(await cursor.fetchall()))
        refs: dict[str, list[ImageRef]] = {}
        ids = [row["id"] for row in rows]
        if ids:
            cursor = await self.db.execute(
                f"SELECT id, message_id, media_type FROM images "
                f"WHERE message_id IN ({','.join('?' * len(ids))}) ORDER BY position",
                ids,
            )
            for img in await cursor.fetchall():
                refs.setdefault(img["message_id"], []).append(
                    ImageRef(id=img["id"], media_type=img["media_type"])
                )
        return [MessageOut(**dict(row), images=refs.get(row["id"], [])) for row in rows]

    async def image_data(self, message_ids: Sequence[str]) -> dict[str, list[ImageData]]:
        """The stored images of these messages, in order, for sending to the model."""
        if not message_ids:
            return {}
        cursor = await self.db.execute(
            f"SELECT message_id, media_type, data FROM images "
            f"WHERE message_id IN ({','.join('?' * len(message_ids))}) ORDER BY position",
            list(message_ids),
        )
        out: dict[str, list[ImageData]] = {}
        for row in await cursor.fetchall():
            out.setdefault(row["message_id"], []).append(ImageData(row["media_type"], row["data"]))
        return out

    async def get_image(self, image_id: str) -> ImageData | None:
        cursor = await self.db.execute(
            "SELECT media_type, data FROM images WHERE id = ?", (image_id,)
        )
        row = await cursor.fetchone()
        return ImageData(row["media_type"], row["data"]) if row else None

    # ---- actions --------------------------------------------------------

    async def add_action(
        self, session_id: str, kind: str, instruction: str, payload: dict[str, Any]
    ) -> ActionOut:
        now = _now()
        aid = _new_id()
        await self.db.execute(
            "INSERT INTO actions (id, session_id, kind, status, instruction, payload, "
            "created_at, updated_at) VALUES (?, ?, ?, 'pending', ?, ?, ?, ?)",
            (aid, session_id, kind, instruction, json.dumps(payload), now, now),
        )
        await self.db.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
        await self.db.commit()
        action = await self.get_action(aid)
        assert action is not None
        return action

    async def get_action(self, action_id: str) -> ActionOut | None:
        cursor = await self.db.execute("SELECT * FROM actions WHERE id = ?", (action_id,))
        row = await cursor.fetchone()
        return _action(row) if row else None

    async def list_actions(
        self, session_id: str | None = None, status: str | None = None
    ) -> list[ActionOut]:
        where, args = [], []
        if session_id is not None:
            where.append("session_id = ?")
            args.append(session_id)
        if status is not None:
            where.append("status = ?")
            args.append(status)
        sql = "SELECT * FROM actions"
        if where:
            sql += " WHERE " + " AND ".join(where)
        cursor = await self.db.execute(sql + " ORDER BY created_at, rowid", args)
        return [_action(row) for row in await cursor.fetchall()]

    async def actions_to_remind(self, created_before: str) -> list[ActionOut]:
        """Pending drafts older than the cutoff that haven't had a reminder yet."""
        cursor = await self.db.execute(
            "SELECT * FROM actions WHERE status = 'pending' AND reminded_at IS NULL "
            "AND created_at < ? ORDER BY created_at",
            (created_before,),
        )
        return [_action(row) for row in await cursor.fetchall()]

    async def mark_reminded(self, action_id: str) -> None:
        await self.db.execute("UPDATE actions SET reminded_at = ? WHERE id = ?", (_now(), action_id))
        await self.db.commit()

    async def set_action_status(self, action_id: str, status: str, *, expect: str) -> bool:
        """Atomically move an action from `expect` to `status`. False if it wasn't `expect`,
        which is what stops a double click from sending an email twice."""
        cursor = await self.db.execute(
            "UPDATE actions SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
            (status, _now(), action_id, expect),
        )
        await self.db.commit()
        return cursor.rowcount == 1

    async def finish_action(
        self,
        action_id: str,
        payload: dict[str, Any],
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> ActionOut:
        """Record a run: done with its result, or back to pending with the error to fix."""
        await self.db.execute(
            "UPDATE actions SET status = ?, payload = ?, result = ?, error = ?, updated_at = ? "
            "WHERE id = ?",
            (
                "pending" if error else "done",
                json.dumps(payload),
                json.dumps(result) if result is not None else None,
                error,
                _now(),
                action_id,
            ),
        )
        await self.db.commit()
        action = await self.get_action(action_id)
        assert action is not None
        return action

    # ---- events and documents -------------------------------------------

    async def add_event(self, action_id: str | None, e: dict[str, Any]) -> str:
        eid = _new_id()
        await self.db.execute(
            'INSERT INTO events (id, action_id, title, start, "end", location, notes, created_at) '
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (eid, action_id, e["title"], e["start"], e["end"], e["location"], e["notes"], _now()),
        )
        await self.db.commit()
        return eid

    async def list_events(self, since: str | None = None) -> list[EventOut]:
        sql, args = "SELECT * FROM events", []
        if since:
            sql, args = sql + ' WHERE "end" >= ?', [since]
        cursor = await self.db.execute(sql + " ORDER BY start", args)
        return [EventOut(**dict(row)) for row in await cursor.fetchall()]

    async def get_event(self, event_id: str) -> EventOut | None:
        cursor = await self.db.execute("SELECT * FROM events WHERE id = ?", (event_id,))
        row = await cursor.fetchone()
        return EventOut(**dict(row)) if row else None

    async def delete_event(self, event_id: str) -> bool:
        cursor = await self.db.execute("DELETE FROM events WHERE id = ?", (event_id,))
        await self.db.commit()
        return cursor.rowcount > 0

    async def add_document(self, action_id: str | None, title: str, content: str) -> str:
        did = _new_id()
        await self.db.execute(
            "INSERT INTO documents (id, action_id, title, content, created_at) VALUES (?, ?, ?, ?, ?)",
            (did, action_id, title, content, _now()),
        )
        await self.db.commit()
        return did

    async def get_document(self, doc_id: str) -> tuple[str, str] | None:
        cursor = await self.db.execute("SELECT title, content FROM documents WHERE id = ?", (doc_id,))
        row = await cursor.fetchone()
        return (row["title"], row["content"]) if row else None

    # ---- notifications ----------------------------------------------------

    async def add_notification(
        self, category: str, level: str, title: str, body: str, session_id: str | None
    ) -> NotificationOut:
        nid, now = _new_id(), _now()
        await self.db.execute(
            "INSERT INTO notifications (id, category, level, title, body, session_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (nid, category, level, title, body, session_id, now),
        )
        await self.db.execute(
            "DELETE FROM notifications WHERE id NOT IN "
            "(SELECT id FROM notifications ORDER BY created_at DESC LIMIT ?)",
            (KEEP_NOTIFICATIONS,),
        )
        await self.db.commit()
        return NotificationOut(
            id=nid, category=category, level=level, title=title, body=body,
            session_id=session_id, created_at=now,
        )

    async def has_recent_notification(self, category: str, title: str, body: str, since: str) -> bool:
        cursor = await self.db.execute(
            "SELECT 1 FROM notifications WHERE category = ? AND title = ? AND body = ? "
            "AND created_at >= ? LIMIT 1",
            (category, title, body, since),
        )
        return await cursor.fetchone() is not None

    async def list_notifications(self, limit: int = 50) -> tuple[list[NotificationOut], int]:
        cursor = await self.db.execute(
            "SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        items = [_notification(row) for row in await cursor.fetchall()]
        cursor = await self.db.execute("SELECT COUNT(*) FROM notifications WHERE read_at IS NULL")
        return items, (await cursor.fetchone())[0]

    async def mark_notifications_read(self, ids: Sequence[str] | None = None) -> None:
        if ids is None:
            await self.db.execute("UPDATE notifications SET read_at = ? WHERE read_at IS NULL", (_now(),))
        elif ids:
            marks = ",".join("?" * len(ids))
            await self.db.execute(
                f"UPDATE notifications SET read_at = ? WHERE read_at IS NULL AND id IN ({marks})",
                (_now(), *ids),
            )
        await self.db.commit()

    async def clear_notifications(self) -> None:
        await self.db.execute("DELETE FROM notifications")
        await self.db.commit()

    async def touch_device(self, device_id: str, label: str, ip: str) -> bool:
        """Record that a device opened the app. Returns True the first time it's seen."""
        now = _now()
        cursor = await self.db.execute(
            "INSERT INTO devices (id, label, ip, first_seen, last_seen) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO NOTHING",
            (device_id, label, ip, now, now),
        )
        new = cursor.rowcount == 1
        if not new:
            await self.db.execute(
                "UPDATE devices SET label = ?, ip = ?, last_seen = ? WHERE id = ?",
                (label, ip, now, device_id),
            )
        await self.db.commit()
        return new

    async def get_preference(self, key: str) -> Any:
        cursor = await self.db.execute("SELECT value FROM preferences WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return json.loads(row["value"]) if row else None

    async def set_preference(self, key: str, value: Any) -> None:
        await self.db.execute(
            "INSERT INTO preferences (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )
        await self.db.commit()


def _notification(row: aiosqlite.Row) -> NotificationOut:
    data = dict(row)
    data["read"] = data.pop("read_at") is not None
    return NotificationOut(**data)


def _action(row: aiosqlite.Row) -> ActionOut:
    data = dict(row)
    data["payload"] = json.loads(data["payload"])
    data["result"] = json.loads(data["result"]) if data["result"] else None
    data.pop("reminded_at", None)
    return ActionOut(**data)


def _title_from(content: str, max_len: int = 60) -> str:
    text = " ".join(content.split())
    return text if len(text) <= max_len else text[: max_len - 1].rstrip() + "…"
