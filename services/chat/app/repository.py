"""SQLite persistence for chat sessions and messages."""

import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from .llm.base import ImageData
from .schemas import ImageRef, MessageOut, Role, SessionDetail, SessionOut, Source

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
"""

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
        await self._db.commit()

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
        return SessionDetail(**session.model_dump(), messages=messages)

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


def _title_from(content: str, max_len: int = 60) -> str:
    text = " ".join(content.split())
    return text if len(text) <= max_len else text[: max_len - 1].rstrip() + "…"
