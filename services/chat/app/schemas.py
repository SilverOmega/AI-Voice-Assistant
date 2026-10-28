"""Pydantic request/response models for the chat service API."""

import binascii
from base64 import b64decode
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr, model_validator

from .config import ProviderName
from .llm.base import ImageData

Role = Literal["user", "assistant", "system"]
Source = Literal["text", "voice"]
ImageMediaType = Literal["image/jpeg", "image/png"]

MAX_IMAGES_PER_MESSAGE = 4
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # Claude's per-image limit; the UI downsizes well below it
_MAGIC = {"image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG\r\n\x1a\n"}


class ImageIn(BaseModel):
    media_type: ImageMediaType
    data: str = Field(min_length=1, max_length=MAX_IMAGE_BYTES * 4 // 3 + 4, description="base64")
    _bytes: bytes = PrivateAttr(default=b"")

    @model_validator(mode="after")
    def _decode(self) -> "ImageIn":
        try:
            raw = b64decode(self.data, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("image data is not valid base64") from None
        if len(raw) > MAX_IMAGE_BYTES:
            raise ValueError("image is larger than 5 MB")
        if not raw.startswith(_MAGIC[self.media_type]):
            raise ValueError(f"image data is not a {self.media_type} file")
        self._bytes = raw
        return self

    def to_image_data(self) -> ImageData:
        return ImageData(self.media_type, self._bytes)


class ImageRef(BaseModel):
    id: str
    media_type: str


class MessageOut(BaseModel):
    id: str
    session_id: str
    role: Role
    content: str
    source: Source = "text"
    created_at: datetime
    images: list[ImageRef] = []


class SessionOut(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class SessionDetail(SessionOut):
    messages: list[MessageOut] = []


class SessionCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class SessionUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ChatRequest(BaseModel):
    content: str = Field(default="", max_length=32_000)
    source: Source = "text"
    images: list[ImageIn] = Field(default=[], max_length=MAX_IMAGES_PER_MESSAGE)

    @model_validator(mode="after")
    def _not_empty(self) -> "ChatRequest":
        if not self.content.strip() and not self.images:
            raise ValueError("a message needs text or at least one image")
        return self


class ChatResponse(BaseModel):
    user_message: MessageOut
    assistant_message: MessageOut | None


class RuntimeConfig(BaseModel):
    """Settings that can be changed while the service is running."""

    provider: ProviderName
    model: str
    system_prompt: str
    temperature: float = Field(ge=0.0, le=2.0)


class RuntimeConfigUpdate(BaseModel):
    provider: ProviderName | None = None
    model: str | None = None
    system_prompt: str | None = Field(default=None, max_length=20_000)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)


class ConfigOut(RuntimeConfig):
    available_providers: list[ProviderName]
    provider_status: dict[str, str]


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    service: str = "chat"
    provider: str
    model: str
    provider_ok: bool
    detail: str = ""
