"""Phone channels for notifications: SMS through Twilio (to NOTIFY_PHONE) and push through
ntfy (the ntfy phone app). What gets sent, and when, is decided in notifications.py.

They only inform: approving a task still happens in the app, so a message can never send anything.
"""

import asyncio
import logging

import httpx

from .config import Settings
from .schemas import ActionOut

log = logging.getLogger(__name__)

TWILIO_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"


def masked_phone(phone: str) -> str:
    return phone[:3] + "•" * max(0, len(phone) - 6) + phone[-3:] if len(phone) > 6 else phone


def _clip(text: str, limit: int = 80) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def review_message(action: ActionOut, reminder: bool = False) -> tuple[str, str]:
    """(title, body) for a pending action. Kept short: it may be an SMS."""
    p = action.payload
    if action.kind == "email":
        to = ", ".join(p.get("to") or []) or "no recipient yet"
        what = f'Email to {to}: "{_clip(p.get("subject") or "(no subject)")}"'
    elif action.kind == "event":
        what = f'Event "{_clip(p.get("title", ""))}" at {p.get("start") or "a time to pick"}'
    elif action.kind == "document":
        what = f'Document "{_clip(p.get("title", ""))}"'
    else:
        what = f'Workflow "{p.get("workflow", "")}"'
    verb = "sent" if action.kind == "email" else "saved" if action.kind != "workflow" else "run"
    title = ("Reminder: " if reminder else "") + f"review before it's {verb}"
    return title, f"{what}. Nothing is {verb} until you approve it in your assistant."


class Notifier:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None):
        self.settings = settings
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(15.0))

    @property
    def channels(self) -> list[str]:
        s = self.settings
        out = []
        if s.notify_phone and s.twilio_account_sid and s.twilio_auth_token and s.twilio_from:
            out.append(f"SMS to {masked_phone(s.notify_phone)}")
        if s.ntfy_topic:
            out.append("ntfy push")
        return out

    async def send(self, title: str, body: str) -> list[str]:
        """Send on every configured channel. Returns the errors; never raises."""
        s = self.settings
        if s.app_url:
            body = f"{body}\n{s.app_url}"
        jobs = []
        if s.notify_phone and s.twilio_account_sid and s.twilio_auth_token and s.twilio_from:
            jobs.append(self._sms(f"{title}: {body}"))
        if s.ntfy_topic:
            jobs.append(self._ntfy(title, body))
        errors = [str(e) for e in await asyncio.gather(*jobs, return_exceptions=True) if e]
        for error in errors:
            log.warning("phone notification failed: %s", error)
        return errors

    async def _sms(self, text: str) -> None:
        s = self.settings
        sender = "MessagingServiceSid" if s.twilio_from.startswith("MG") else "From"
        resp = await self._http.post(
            TWILIO_URL.format(sid=s.twilio_account_sid),
            data={"To": s.notify_phone, sender: s.twilio_from, "Body": text},
            auth=(s.twilio_account_sid, s.twilio_auth_token),
        )
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("message", "")
            except ValueError:
                detail = resp.text[:200]
            raise RuntimeError(f"SMS failed (HTTP {resp.status_code}): {detail}")

    async def _ntfy(self, title: str, body: str) -> None:
        s = self.settings
        # Query parameters rather than headers, which can't carry non-ASCII titles.
        params = {"title": title, "tags": "bell", "priority": "high"}
        if s.app_url:
            params["click"] = s.app_url
        headers = {"Authorization": f"Bearer {s.ntfy_token}"} if s.ntfy_token else {}
        resp = await self._http.post(
            f"{s.ntfy_server.rstrip('/')}/{s.ntfy_topic}",
            params=params,
            content=body.encode("utf-8"),
            headers=headers,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"ntfy push failed (HTTP {resp.status_code})")

    async def aclose(self) -> None:
        await self._http.aclose()
