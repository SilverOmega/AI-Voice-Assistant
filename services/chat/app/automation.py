"""Task automation: the model drafts an action, the user reviews it, and only then does it run.

Nothing here runs on the model's say-so. A draft is stored as a pending action; `execute`
is called only from the approve endpoint, with the payload as the user edited it.
"""

import asyncio
import imaplib
import re
import smtplib
import ssl
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formatdate
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from pydantic import ValidationError

from .config import Settings
from .repository import Repository
from .schemas import DRAFT_MODELS, ActionOut

LOCAL_TIME = "%Y-%m-%dT%H:%M"
_EMAIL = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")

DRAFT_PROMPTS = {
    "email": (
        "Draft an email from the user. Write a clear subject and a polite, concise plain-text "
        "body. Only use recipient addresses the user gave; leave 'to' empty if none were given."
    ),
    "event": (
        "Turn the request into a calendar event. Resolve relative dates such as 'tomorrow' "
        "from the current time. Leave 'end' empty if no duration or end time was given."
    ),
    "document": (
        "Write the requested document in Markdown, with a title and clear headings. When the "
        "user asks to compile or summarize the conversation, use the conversation so far."
    ),
    "workflow": (
        "Pick the workflow that matches the request and describe in 'input' what it should do."
    ),
}


class ActionInvalid(Exception):
    """The approved payload can't be run as is; the message tells the user what to fix."""


class CredentialsRejected(ActionInvalid):
    """The mail server refused the username or password."""


def draft_schema(kind: str, workflows: list[str]) -> dict[str, Any]:
    schema = DRAFT_MODELS[kind].model_json_schema()
    schema["required"] = list(schema["properties"])  # structured output fills every field
    if kind == "workflow":
        schema["properties"]["workflow"]["enum"] = workflows
    return schema


def draft_system_prompt(kind: str, now: str, workflows: list[str]) -> str:
    when = datetime.strptime(now[:16], LOCAL_TIME)
    lines = [
        "You prepare tasks for the user to review before anything is sent or saved.",
        DRAFT_PROMPTS[kind],
        f"The user's current local time is {when:%A, %Y-%m-%d %H:%M}.",
    ]
    if kind == "workflow":
        lines.append("Available workflows: " + ", ".join(workflows) + ".")
    return "\n".join(lines)


def parse_local_time(value: str) -> datetime | None:
    """Models write times in several shapes ('2026-11-02 15:00', '...T15:00:00Z')."""
    text = value.strip().replace(" ", "T", 1)
    try:
        return datetime.fromisoformat(text).replace(tzinfo=None, second=0, microsecond=0)
    except ValueError:
        return None


def clean_draft(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    try:
        draft = DRAFT_MODELS[kind].model_validate(data).model_dump()
    except ValidationError:
        return DRAFT_MODELS[kind]().model_dump()  # an empty draft the user can fill in
    if kind == "event":
        for key in ("start", "end"):  # the review form's date pickers need YYYY-MM-DDTHH:MM
            when = parse_local_time(draft[key])
            draft[key] = when.strftime(LOCAL_TIME) if when else ""
    return draft


def _addresses(values: Any, field: str) -> list[str]:
    if isinstance(values, str):
        values = re.split(r"[,;\s]+", values)
    out = [v.strip() for v in values or [] if v and v.strip()]
    bad = [v for v in out if not _EMAIL.match(v)]
    if bad:
        raise ActionInvalid(f"Not a valid email address in '{field}': {', '.join(bad)}")
    return out


def _local_time(value: str, field: str) -> datetime:
    when = parse_local_time(value)
    if when is None:
        raise ActionInvalid(f"'{field}' must be a date and time like 2026-11-02T15:00")
    return when


def validate_payload(kind: str, payload: dict[str, Any], workflows: dict[str, str]) -> dict[str, Any]:
    """Strict checks on what the user approved. Returns the normalized payload."""
    try:
        draft = DRAFT_MODELS[kind].model_validate(payload).model_dump()
    except ValidationError as exc:
        raise ActionInvalid(exc.errors()[0]["msg"]) from None
    if kind == "email":
        draft["to"] = _addresses(draft["to"], "to")
        draft["cc"] = _addresses(draft["cc"], "cc")
        if not draft["to"]:
            raise ActionInvalid("Add at least one recipient.")
        if not draft["subject"].strip() and not draft["body"].strip():
            raise ActionInvalid("The email is empty.")
    elif kind == "event":
        if not draft["title"].strip():
            raise ActionInvalid("Give the event a title.")
        start = _local_time(draft["start"], "start")
        end = _local_time(draft["end"], "end") if draft["end"].strip() else start + timedelta(hours=1)
        if end <= start:
            raise ActionInvalid("The event must end after it starts.")
        draft["start"], draft["end"] = start.strftime(LOCAL_TIME), end.strftime(LOCAL_TIME)
    elif kind == "document":
        if not draft["content"].strip():
            raise ActionInvalid("The document is empty.")
        draft["title"] = draft["title"].strip() or "Document"
    elif kind == "workflow":
        if draft["workflow"] not in workflows:
            raise ActionInvalid(f"Unknown workflow '{draft['workflow']}'.")
    return draft


class Automation:
    def __init__(self, repo: Repository, settings: Settings, http: httpx.AsyncClient | None = None):
        self.repo = repo
        self.settings = settings
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(15.0))

    @property
    def email_delivery(self) -> str:
        s = self.settings
        if s.smtp_host:
            return "smtp"
        if s.imap_username and s.imap_password:
            return "drafts"
        return s.email_compose

    async def execute(self, action: ActionOut, payload: dict[str, Any]) -> dict[str, Any]:
        """Run an approved action and return its result for the review card."""
        if action.kind == "email":
            return await self._email(payload)
        if action.kind == "event":
            event_id = await self.repo.add_event(action.id, payload)
            when = datetime.strptime(payload["start"], LOCAL_TIME)
            return {
                "message": f"Added to your schedule for {when:%a %d %b, %H:%M}.",
                "event_id": event_id,
                "download_url": f"/api/events/{event_id}/ics",
            }
        if action.kind == "document":
            doc_id = await self.repo.add_document(action.id, payload["title"], payload["content"])
            return {"message": "Document saved.", "download_url": f"/api/documents/{doc_id}/download"}
        return await self._workflow(action, payload)

    async def _email(self, p: dict[str, Any]) -> dict[str, Any]:
        recipients = ", ".join(p["to"] + p["cc"])
        delivery = self.email_delivery
        if delivery in ("gmail", "outlook", "mail_app"):
            urls = compose_urls(p)
            where = {"gmail": "Gmail", "outlook": "Outlook", "mail_app": "your mail app"}[delivery]
            return {
                "message": f"Opened in {where} for {recipients}. Check it there and press Send.",
                "open_url": urls[delivery],
                "compose_urls": urls,
            }
        s = self.settings
        msg = EmailMessage()
        msg["From"] = s.smtp_from or s.smtp_username or s.imap_username
        msg["To"] = ", ".join(p["to"])
        if p["cc"]:
            msg["Cc"] = ", ".join(p["cc"])
        msg["Subject"] = p["subject"]
        msg["Date"] = formatdate(localtime=True)
        msg.set_content(p["body"])
        if delivery == "drafts":
            await asyncio.to_thread(self.save_draft, msg)
            result = {"message": f"Saved to Drafts in {s.imap_username}, to {recipients}. Check it there and press Send."}
            if s.imap_host == "imap.gmail.com":
                result["drafts_url"] = "https://mail.google.com/mail/u/0/#drafts"
            return result
        await asyncio.to_thread(self.send_smtp, msg)
        return {"message": f"Sent to {recipients}."}

    def save_draft(self, msg: EmailMessage) -> None:
        """Put the message in the mailbox's Drafts folder, where Gmail and other mail apps
        show it ready to send. Nothing is sent."""
        s = self.settings
        try:
            with imaplib.IMAP4_SSL(
                s.imap_host, s.imap_port, ssl_context=ssl.create_default_context(), timeout=30
            ) as imap:
                imap.login(s.imap_username, s.imap_password)
                folder = _mailbox(s.imap_drafts_folder) if s.imap_drafts_folder else drafts_folder(imap)
                status, data = imap.append(
                    folder, r"(\Draft \Seen)", imaplib.Time2Internaldate(time.time()), msg.as_bytes()
                )
                if status != "OK":
                    raise ActionInvalid(f"Could not save the draft: {_imap_text(data)}")
        except imaplib.IMAP4.error as exc:
            if "AUTH" in str(exc).upper():
                raise CredentialsRejected(
                    f"Could not save the draft: {_imap_text(exc.args)}. "
                    "For Gmail, IMAP_PASSWORD must be an app password."
                ) from exc
            raise ActionInvalid(f"Could not save the draft: {_imap_text(exc.args)}.") from exc
        except OSError as exc:
            raise ActionInvalid(f"Could not reach {s.imap_host}: {exc}") from exc

    def send_smtp(self, msg: EmailMessage) -> None:
        s = self.settings
        try:
            if s.smtp_security == "ssl":
                server: smtplib.SMTP = smtplib.SMTP_SSL(
                    s.smtp_host, s.smtp_port, timeout=30, context=ssl.create_default_context()
                )
            else:
                server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30)
            with server:
                if s.smtp_security == "starttls":
                    server.starttls(context=ssl.create_default_context())
                if s.smtp_username:
                    server.login(s.smtp_username, s.smtp_password)
                server.send_message(msg)
        except smtplib.SMTPAuthenticationError as exc:
            raise CredentialsRejected(
                f"The mail server refused the login for {s.smtp_username}. "
                "For Gmail, SMTP_PASSWORD must be an app password."
            ) from exc
        except (OSError, smtplib.SMTPException) as exc:
            raise ActionInvalid(f"Could not send the email: {exc}") from exc

    async def _workflow(self, action: ActionOut, p: dict[str, Any]) -> dict[str, Any]:
        url = self.settings.workflows[p["workflow"]]
        body = {"workflow": p["workflow"], "input": p["input"], "action_id": action.id}
        try:
            resp = await self._http.post(url, json=body)
        except httpx.HTTPError as exc:
            raise ActionInvalid(f"Workflow '{p['workflow']}' could not be reached: {exc}") from exc
        if resp.status_code >= 400:
            raise ActionInvalid(f"Workflow '{p['workflow']}' failed with HTTP {resp.status_code}.")
        return {"message": f"Workflow '{p['workflow']}' started (HTTP {resp.status_code})."}

    async def aclose(self) -> None:
        await self._http.aclose()


_LIST_ROW = re.compile(rb'^\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$')


def drafts_folder(imap: imaplib.IMAP4) -> str:
    """The folder the server flags as \\Drafts (Gmail: "[Gmail]/Drafts", named in the
    account's language), else "Drafts". Returned in the quoted form the server used."""
    status, rows = imap.list()
    if status == "OK":
        for row in rows:
            m = _LIST_ROW.match(row) if isinstance(row, bytes) else None
            if m and b"\\drafts" in m["flags"].lower().split():
                return m["name"].decode()
    return "Drafts"


def _mailbox(name: str) -> str:
    if len(name) >= 2 and name[0] == name[-1] == '"':
        return name
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _imap_text(data: Any) -> str:
    parts = data if isinstance(data, (list, tuple)) else [data]
    return " ".join(p.decode(errors="replace") if isinstance(p, bytes) else str(p) for p in parts if p)


def compose_urls(p: dict[str, Any]) -> dict[str, str]:
    """A new message, filled in, in each place the user might write email. Gmail and
    Outlook keep it in Drafts once the compose window has opened and saved it."""
    to, cc = ",".join(p["to"]), ",".join(p["cc"])
    gmail = {"view": "cm", "fs": "1", "to": to, "su": p["subject"], "body": p["body"]}
    outlook = {"to": to, "subject": p["subject"], "body": p["body"]}
    if cc:
        gmail["cc"] = outlook["cc"] = cc
    mailto = f"subject={quote(p['subject'])}&body={quote(p['body'])}"
    if cc:
        mailto = f"cc={quote(cc)}&{mailto}"
    return {
        "gmail": "https://mail.google.com/mail/?" + urlencode(gmail, quote_via=quote),
        "outlook": "https://outlook.live.com/mail/0/deeplink/compose?" + urlencode(outlook, quote_via=quote),
        "mail_app": f"mailto:{','.join(quote(a, safe='@') for a in p['to'])}?{mailto}",
    }


def _ics_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    )


def event_ics(event: dict[str, Any]) -> str:
    """A one-event iCalendar file. Times are 'floating': local wherever it is imported."""

    def stamp(value: str) -> str:
        return datetime.strptime(value, LOCAL_TIME).strftime("%Y%m%dT%H%M00")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Local Assistant//EN",
        "BEGIN:VEVENT",
        f"UID:{event['id']}@local-assistant",
        f"DTSTAMP:{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}",
        f"DTSTART:{stamp(event['start'])}",
        f"DTEND:{stamp(event['end'])}",
        f"SUMMARY:{_ics_text(event['title'])}",
    ]
    if event["location"]:
        lines.append(f"LOCATION:{_ics_text(event['location'])}")
    if event["notes"]:
        lines.append(f"DESCRIPTION:{_ics_text(event['notes'])}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"
