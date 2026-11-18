"""Pydantic request/response models for the chat service API."""

import binascii
from base64 import b64decode
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr, field_validator, model_validator

from .config import ProviderName
from .documents import MAX_FILES_PER_MESSAGE
from .llm.base import ImageData
from .translate import MAX_TRANSLATE_CHARS, clean_language, valid_language

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


class FileRef(BaseModel):
    """A document attached to a message."""

    id: str
    name: str
    media_type: str
    size: int


class FileOut(FileRef):
    """An uploaded document, before it's sent."""

    chars: int  # characters of text read from it
    pages: int | None = None  # PDF pages, slides or sheets


class FileImport(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


class WebLink(BaseModel):
    title: str
    url: str
    snippet: str = ""


class WebResults(BaseModel):
    """What a web search found for the user's message, shown under the reply."""

    query: str
    links: list[WebLink] = []
    # Topics to explore next; sending one asks about it.
    related: list[str] = []


AgentStatus = Literal["waiting", "running", "done", "failed", "skipped"]


class ToolTrace(BaseModel):
    """One tool a helper used, as shown under the reply."""

    tool: str
    input: str = ""  # short preview of the arguments
    output: str = ""  # short preview of the result
    status: AgentStatus = "done"
    action_id: str | None = None  # the draft it created, if any


class AgentStepTrace(BaseModel):
    agent: str  # e.g. "researcher"
    title: str  # e.g. "Researcher"
    task: str
    status: AgentStatus = "running"
    summary: str = ""
    tools: list[ToolTrace] = []


class AgentTrace(BaseModel):
    """How the helpers worked on a reply."""

    steps: list[AgentStepTrace] = []
    note: str = ""  # e.g. why it stopped early


class AgentInfo(BaseModel):
    name: str
    title: str
    description: str
    tools: list[str]


class AgentsOut(BaseModel):
    mode: Literal["off", "toggle", "always"]
    agents: list[AgentInfo]


class MessageOut(BaseModel):
    id: str
    session_id: str
    role: Role
    content: str
    source: Source = "text"
    created_at: datetime
    images: list[ImageRef] = []
    files: list[FileRef] = []
    web: WebResults | None = None
    agent: AgentTrace | None = None


class SessionOut(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class SessionDetail(SessionOut):
    messages: list[MessageOut] = []
    actions: list["ActionOut"] = []


class SessionCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class SessionUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ChatRequest(BaseModel):
    content: str = Field(default="", max_length=32_000)
    source: Source = "text"
    images: list[ImageIn] = Field(default=[], max_length=MAX_IMAGES_PER_MESSAGE)
    # Ids of documents uploaded with POST /api/files.
    files: list[str] = Field(default=[], max_length=MAX_FILES_PER_MESSAGE)
    # The language the assistant replies in, e.g. "Vietnamese"; empty or "auto" = the user's own.
    language: str | None = None
    # Search the web for the message and answer from the results. Skipped for small talk
    # ("thanks") and for messages with pictures or documents.
    search: bool = False
    # Let the helpers (agents) work on it. Ignored when AGENT_MODE is off; always on with "always".
    agents: bool = False
    # The user's local time, so helpers resolve "tomorrow at 3pm". Defaults to the server's.
    now: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")

    @field_validator("language")
    @classmethod
    def _language(cls, value: str | None) -> str | None:
        return _checked_language(value)

    @model_validator(mode="after")
    def _not_empty(self) -> "ChatRequest":
        if not self.content.strip() and not self.images and not self.files:
            raise ValueError("a message needs text, a picture or a document")
        return self


def _checked_language(value: str | None) -> str | None:
    value = clean_language(value)
    if value is not None and not valid_language(value):
        raise ValueError("not a language name")
    return value


class TranslateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TRANSLATE_CHARS)
    target: str = Field(min_length=1, description='Language to translate into, e.g. "Vietnamese"')

    @field_validator("target")
    @classmethod
    def _target(cls, value: str) -> str:
        checked = _checked_language(value)
        if checked is None:
            raise ValueError("choose a language to translate into")
        return checked

    @model_validator(mode="after")
    def _has_text(self) -> "TranslateRequest":
        if not self.text.strip():
            raise ValueError("nothing to translate")
        return self


class TranslateOut(BaseModel):
    translation: str
    target: str


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


class ProviderKey(BaseModel):
    """An API key added in the app. Stored on this computer, never sent back to the browser."""

    api_key: str = Field(min_length=20, max_length=500, pattern=r"^\s*\S+\s*$")


class ConfigOut(RuntimeConfig):
    available_providers: list[ProviderName]
    provider_status: dict[str, str]
    # The web search engine ("searxng", "brave"), or "" when web search is off.
    web_search: str = ""
    agent_mode: Literal["off", "toggle", "always"] = "off"


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    service: str = "chat"
    provider: str
    model: str
    provider_ok: bool
    detail: str = ""


# ---- task automation ------------------------------------------------------------

ActionKind = Literal["email", "event", "document", "workflow", "code", "file_edit"]
ActionStatus = Literal["pending", "running", "done", "rejected"]


# Drafts are lenient: the model may leave fields empty for the user to fill in on review.
# Strict checks run when the user approves (see automation.validate_payload).
class EmailDraft(BaseModel):
    to: list[str] = Field(default=[], description="Recipient email addresses")
    cc: list[str] = []
    subject: str = ""
    body: str = Field(default="", description="Plain-text email body, signed off by the user")


class EventDraft(BaseModel):
    title: str = ""
    start: str = Field(default="", description="Local start time, YYYY-MM-DDTHH:MM")
    end: str = Field(default="", description="Local end time, YYYY-MM-DDTHH:MM, or empty")
    location: str = ""
    notes: str = ""


class DocumentDraft(BaseModel):
    title: str = ""
    content: str = Field(default="", description="The document in Markdown")


class WorkflowDraft(BaseModel):
    workflow: str = Field(default="", description="Name of the workflow to run")
    input: str = Field(default="", description="What the workflow should do or act on")


class CodeDraft(BaseModel):
    language: str = Field(default="python", description='Programming language; only "python" runs')
    code: str = Field(default="", description="The complete program to run")
    stdin: str = Field(default="", description="Text given to the program as input, or empty")
    purpose: str = Field(default="", description="One line on what running it shows")


class FileEdit(BaseModel):
    old: str = Field(default="", description="Exact text to replace, copied from the file; empty for a new file")
    new: str = Field(default="", description="The text to put in its place")


class FileEditDraft(BaseModel):
    path: str = Field(default="", description="File path inside the project folder, e.g. src/app.py")
    create: bool = Field(default=False, description="True to create a new file")
    edits: list[FileEdit] = []
    # Filled in by the server when the draft is made, not by the model.
    base_sha256: str = ""  # the file as it was, so a later change is noticed before writing
    diff: str = ""  # unified diff for the review card


DRAFT_MODELS: dict[str, type[BaseModel]] = {
    "email": EmailDraft,
    "event": EventDraft,
    "document": DocumentDraft,
    "workflow": WorkflowDraft,
    "code": CodeDraft,
    "file_edit": FileEditDraft,
}


class ActionDraftRequest(BaseModel):
    kind: ActionKind
    instruction: str = Field(min_length=1, max_length=8_000)
    # The user's local time, so "tomorrow at 3pm" resolves correctly.
    now: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
    # A ready draft (e.g. the code of a code block): stored for review without asking the model.
    payload: dict[str, Any] | None = None


class ActionApprove(BaseModel):
    payload: dict[str, Any]  # the draft as the user edited it


class ActionOut(BaseModel):
    id: str
    session_id: str
    kind: ActionKind
    status: ActionStatus
    instruction: str
    payload: dict[str, Any]
    # After it runs: {"message": ..., and optionally "open_url" / "download_url"}
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class EventOut(BaseModel):
    id: str
    action_id: str | None
    title: str
    start: str
    end: str
    location: str
    notes: str


class AutomationInfo(BaseModel):
    email_delivery: Literal["smtp", "drafts", "gmail", "outlook", "mail_app"]
    workflows: list[str]
    # Where review reminders go, e.g. ["SMS to +84•••••567"]; empty = phone notifications off.
    notify_channels: list[str] = []
    notify_remind_minutes: int = 0
    code_runs: bool = False  # a code sandbox is set up (SANDBOX_URL)
    project: bool = False  # a project folder is set up (PROJECT_DIR)


class NotifyTestOut(BaseModel):
    channels: list[str]
    errors: list[str]


# ---- notifications --------------------------------------------------------------

NotificationCategory = Literal["sign_in", "credentials", "review", "task", "photo_limit", "token_limit"]
NotificationLevel = Literal["info", "warning", "error"]


class NotificationOut(BaseModel):
    id: str
    category: NotificationCategory
    level: NotificationLevel
    title: str
    body: str
    session_id: str | None = None  # the chat it's about, if any
    created_at: datetime
    read: bool = False


class NotificationList(BaseModel):
    items: list[NotificationOut]
    unread: int


class ChannelPrefs(BaseModel):
    web: bool  # pop-up and desktop notification in open browsers (the bell list always keeps it)
    phone: bool  # ntfy push / SMS, when a phone channel is set up


class NotificationCategoryOut(ChannelPrefs):
    id: NotificationCategory
    label: str
    description: str


class NotificationSettings(BaseModel):
    categories: list[NotificationCategoryOut]
    phone_channels: list[str]  # e.g. ["ntfy push"]; empty = no phone channel set up


class NotificationRead(BaseModel):
    ids: list[str] | None = Field(default=None, max_length=500)  # None = all


class NotificationReport(BaseModel):
    """Events only the browser sees. The server writes the text, not the client."""

    category: Literal["photo_limit"]
    count: int = Field(ge=0, le=1000)


class DeviceHello(BaseModel):
    device_id: str = Field(pattern=r"^[A-Za-z0-9-]{8,64}$")


class SignIn(BaseModel):
    passcode: str = Field(max_length=200)
    device_id: str = Field(pattern=r"^[A-Za-z0-9-]{8,64}$")


class AuthStatus(BaseModel):
    remote: bool  # reached over the network, not from this computer
    configured: bool  # APP_PASSCODE is set, so other devices can sign in
    signed_in: bool  # always true on this computer
    app_url: str  # the address other devices open, if set in .env
