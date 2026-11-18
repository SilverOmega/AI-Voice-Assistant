"""Runs Claude Code (`claude -p`) for one reply and turns its stream-json output into events.

Every run is locked down: no built-in tools, no MCP servers, no hooks/CLAUDE.md/plugins
(--safe-mode), no saved session, an empty temporary working folder, and API keys removed
from its environment so it always uses the signed-in Claude subscription.

Events yielded to the chat service, one JSON object per line:
  {"type": "text", "text": "..."}
  {"type": "done", "truncated": false, "structured_output": {...} | null}
  {"type": "error", "code": "...", "message": "..."}   (message is shown to the user)
"""

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Settings

log = logging.getLogger(__name__)

MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\[\]-]{0,63}$")
# Windows allows 32,767 characters on a command line; longer instructions go in the message.
MAX_INLINE_SYSTEM = 20_000
STATUS_TTL = 30.0
REMOVED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT")

NOT_FOUND = (
    "Claude Code isn't installed on this computer. Install it from claude.com/code, "
    "or set CLAUDE_CLI_PATH to the claude program."
)
NOT_SIGNED_IN = (
    "Claude Code isn't signed in on this computer. Open a terminal, run claude, "
    "and sign in with your Claude account."
)


class CliUnavailable(Exception):
    """Claude Code can't be started; the message is shown to the user."""


@dataclass
class Image:
    media_type: str
    data: str  # base64


@dataclass
class Message:
    role: str  # user | assistant
    content: str
    images: list[Image] = field(default_factory=list)


@dataclass
class Request:
    model: str
    system: str
    messages: list[Message]
    max_tokens: int = 4096
    json_schema: dict[str, Any] | None = None


# ---- finding the program ---------------------------------------------------------------


def _version_key(path: Path) -> tuple[int, ...]:
    match = re.search(r"claude-code-(\d+(?:\.\d+)*)", str(path))
    return tuple(int(p) for p in match.group(1).split(".")) if match else ()


def find_cli(configured: str = "") -> list[str]:
    """The command that starts Claude Code, as a list of arguments."""
    if configured:
        path = Path(configured).expanduser()
        if not path.is_file():
            raise CliUnavailable(f"CLAUDE_CLI_PATH points to {configured}, which isn't a file.")
        return _command_for(path)
    found = shutil.which("claude")
    if found:
        return _command_for(Path(found))
    home = Path.home()
    exe = "claude.exe" if sys.platform == "win32" else "claude"
    local = home / ".local" / "bin" / exe
    if local.is_file():
        return [str(local)]
    bundled = [
        p
        for ext_dir in (".vscode", ".vscode-insiders", ".cursor")
        for p in (home / ext_dir / "extensions").glob(f"anthropic.claude-code-*/resources/native-binary/{exe}")
        if p.is_file()
    ]
    if bundled:
        return [str(max(bundled, key=_version_key))]
    raise CliUnavailable(NOT_FOUND)


def _command_for(path: Path) -> list[str]:
    # A .cmd/.bat shim runs through cmd.exe, which would interpret characters in the
    # instructions (they include web pages). Run the JavaScript entry point with node instead.
    if path.suffix.lower() in (".cmd", ".bat", ".ps1"):
        script = path.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
        node = shutil.which("node")
        if script.is_file() and node:
            return [node, str(script)]
        raise CliUnavailable(
            "Found Claude Code only as a .cmd script. Install the native version "
            "(claude install), or set CLAUDE_CLI_PATH to claude.exe."
        )
    return [str(path)]


def child_env(max_tokens: int | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in REMOVED_ENV}
    env["DISABLE_AUTOUPDATER"] = "1"
    if max_tokens:
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(max_tokens)
    return env


# ---- building the run ------------------------------------------------------------------


HISTORY_NOTE = (
    "The conversation so far is inside <earlier_conversation> at the start of the user's "
    "message. Continue it: reply only to the user's latest message, which follows it."
)
LONG_SYSTEM_NOTE = "Follow the instructions inside <instructions> at the start of the user's message."


def build_args(command: Sequence[str], req: Request) -> list[str]:
    if not MODEL_PATTERN.match(req.model):
        raise ValueError("Unexpected model name")
    system = _system(req)
    args = [
        *command,
        "-p",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--model", req.model,
        "--tools", "",
        "--strict-mcp-config",
        "--safe-mode",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--permission-prompts", "none",
        "--system-prompt", system if len(system) <= MAX_INLINE_SYSTEM else LONG_SYSTEM_NOTE,
    ]
    if req.json_schema is not None:
        args += ["--json-schema", json.dumps(req.json_schema, separators=(",", ":"))]
    return args


def _system(req: Request) -> str:
    system = req.system.strip()
    if len(req.messages) > 1:
        system = f"{system}\n\n{HISTORY_NOTE}".strip()
    return system


def user_line(req: Request) -> str:
    """The one stream-json user message: earlier turns as a transcript, then the latest."""
    messages = list(req.messages)
    latest = messages.pop() if messages and messages[-1].role == "user" else Message("user", "Continue.")
    parts: list[str] = []
    system = _system(req)
    if len(system) > MAX_INLINE_SYSTEM:
        parts.append(f"<instructions>\n{system}\n</instructions>")
    if messages:
        lines = ["<earlier_conversation>"]
        for m in messages:
            tag = "assistant" if m.role == "assistant" else "user"
            lines.append(f"<{tag}>\n{m.content}\n</{tag}>")
        lines.append("</earlier_conversation>")
        parts.append("\n".join(lines))
    parts.append(latest.content or "(no text)")
    content: list[dict[str, Any]] = [
        {"type": "image", "source": {"type": "base64", "media_type": i.media_type, "data": i.data}}
        for i in latest.images
    ]
    content.append({"type": "text", "text": "\n\n".join(parts)})
    return json.dumps({"type": "user", "message": {"role": "user", "content": content}}) + "\n"


# ---- reading its output ----------------------------------------------------------------


class StreamParser:
    """Turns Claude Code stream-json lines into bridge events."""

    def __init__(self, model: str, want_json: bool):
        self.model = model
        self.want_json = want_json
        self.sent_text = False
        self.truncated = False
        self.error_code = ""
        self.error_text = ""
        self.limit: dict[str, Any] | None = None
        self.result: dict[str, Any] | None = None

    def feed(self, line: str) -> list[dict[str, Any]]:
        try:
            data = json.loads(line)
        except ValueError:
            return []
        if not isinstance(data, dict):
            return []
        kind = data.get("type")
        if kind == "stream_event" and data.get("parent_tool_use_id") is None:
            event = data.get("event") or {}
            delta = event.get("delta") or {}
            if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                if not self.want_json and delta.get("text"):
                    self.sent_text = True
                    return [{"type": "text", "text": delta["text"]}]
            elif event.get("type") == "message_delta" and delta.get("stop_reason") == "max_tokens":
                self.truncated = True
        elif kind == "rate_limit_event":
            info = data.get("rate_limit_info") or {}
            if info.get("status") == "rejected":
                self.limit = info
        elif kind == "assistant" and data.get("error"):
            self.error_code = str(data["error"])
            blocks = (data.get("message") or {}).get("content") or []
            self.error_text = " ".join(b.get("text", "") for b in blocks if isinstance(b, dict)).strip()
        elif kind == "result":
            self.result = data
        return []

    def finish(self, exit_code: int | None, stderr: str) -> list[dict[str, Any]]:
        r = self.result
        if r and not r.get("is_error") and not self.error_code:
            events: list[dict[str, Any]] = []
            structured = r.get("structured_output")
            if self.want_json:
                if not isinstance(structured, dict):
                    return [self._error("failed", "Claude didn't give the structured answer that was asked for.")]
            elif not self.sent_text and r.get("result"):
                events.append({"type": "text", "text": str(r["result"])})
            truncated = self.truncated or r.get("stop_reason") == "max_tokens"
            events.append({"type": "done", "truncated": truncated, "structured_output": structured})
            return events
        return [self._classify(r, exit_code, stderr)]

    def _classify(self, r: dict[str, Any] | None, exit_code: int | None, stderr: str) -> dict[str, Any]:
        status = (r or {}).get("api_error_status")
        text = self.error_text or str((r or {}).get("result") or "") or stderr.strip()
        lowered = f"{self.error_code} {text}".lower()
        if self.error_code == "authentication_failed" or status in (401, 403) or "log in" in lowered or "login" in lowered:
            return self._error("not_signed_in", NOT_SIGNED_IN)
        if self.limit or self.error_code == "rate_limit" or status == 429:
            return self._error("usage_limit", usage_limit_message(self.limit))
        if self.error_code == "model_not_found" or status == 404:
            return self._error("model", f"Your Claude plan can't use {self.model} here. Pick another Claude model.")
        if self.error_code == "billing_error":
            return self._error("billing", "Your Claude plan needs attention. Check your account at claude.ai.")
        if status in (500, 502, 503, 529) or self.error_code == "server_error" or "overloaded" in lowered:
            return self._error("busy", "Claude is busy right now. Try again in a moment.")
        detail = " ".join(text.split())[:300] or f"it stopped with exit code {exit_code}"
        return self._error("failed", f"Claude Code couldn't answer: {detail}")

    @staticmethod
    def _error(code: str, message: str) -> dict[str, Any]:
        return {"type": "error", "code": code, "message": message}


def usage_limit_message(info: dict[str, Any] | None) -> str:
    message = "You've reached your Claude plan's usage limit for now."
    resets = (info or {}).get("resetsAt")
    if isinstance(resets, (int, float)) and resets > 0:
        when = datetime.fromtimestamp(resets)
        fmt = "%H:%M" if when.date() == datetime.now().date() else "%a %d %b, %H:%M"
        message += f" It resets at {when.strftime(fmt)}."
    return message


# ---- running it ------------------------------------------------------------------------


class CliRunner:
    """Starts one Claude Code process per reply, at most `parallel` at a time.

    Uses threads to read the process, so it works under any asyncio event loop on Windows.
    """

    def __init__(self, settings: Settings, command: Sequence[str] | None = None):
        self.settings = settings
        self._command = list(command) if command else None
        self._slots = asyncio.Semaphore(settings.claude_bridge_parallel)
        self._status: tuple[float, dict[str, Any]] | None = None

    def command(self) -> list[str]:
        if self._command is None:
            self._command = find_cli(self.settings.claude_cli_path)
        return self._command

    async def status(self) -> dict[str, Any]:
        """Is Claude Code installed and signed in? Cached briefly. Never includes the account's email."""
        if self._status and time.monotonic() - self._status[0] < STATUS_TTL:
            return self._status[1]
        try:
            command = self.command()
            version = (await asyncio.to_thread(self._run_quiet, [*command, "--version"])).split(" ")[0]
            raw = await asyncio.to_thread(self._run_quiet, [*command, "auth", "status", "--json"])
            info = json.loads(raw or "{}")
            out = {
                "cli": True,
                "version": version,
                "signed_in": bool(info.get("loggedIn")),
                "auth": info.get("authMethod") or "",
                "plan": info.get("subscriptionType") or "",
                "detail": "" if info.get("loggedIn") else NOT_SIGNED_IN,
            }
        except CliUnavailable as exc:
            self._command = None  # look again next time, it may have been installed
            out = {"cli": False, "version": "", "signed_in": False, "auth": "", "plan": "", "detail": str(exc)}
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            log.warning("couldn't ask Claude Code for its status: %r", exc)
            out = {
                "cli": True, "version": "", "signed_in": False, "auth": "", "plan": "",
                "detail": "Couldn't check Claude Code's sign-in. Run claude in a terminal to see what's wrong.",
            }
        self._status = (time.monotonic(), out)
        return out

    def _run_quiet(self, args: list[str]) -> str:
        done = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, env=child_env(), cwd=tempfile.gettempdir(), stdin=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
        )
        return done.stdout.strip()

    async def stream(self, req: Request) -> AsyncIterator[dict[str, Any]]:
        try:
            args = build_args(self.command(), req)
        except CliUnavailable as exc:
            yield {"type": "error", "code": "not_installed", "message": str(exc)}
            return
        async with self._slots:
            async for event in self._run(args, req):
                yield event

    async def _run(self, args: list[str], req: Request) -> AsyncIterator[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        lines: asyncio.Queue[str | None] = asyncio.Queue()
        stderr_tail: deque[str] = deque(maxlen=40)
        parser = StreamParser(req.model, req.json_schema is not None)
        workdir = tempfile.mkdtemp(prefix="claude-bridge-")
        started = time.perf_counter()
        proc: subprocess.Popen[bytes] | None = None
        try:
            try:
                proc = subprocess.Popen(
                    args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    cwd=workdir, env=child_env(req.max_tokens), creationflags=_NO_WINDOW,
                    start_new_session=sys.platform != "win32",
                )
            except OSError as exc:
                self._command = None
                log.warning("couldn't start Claude Code: %r", exc)
                yield {"type": "error", "code": "not_installed", "message": NOT_FOUND}
                return
            threading.Thread(target=_pump, args=(proc.stdout, lines, loop), daemon=True).start()
            threading.Thread(target=_tail, args=(proc.stderr, stderr_tail), daemon=True).start()
            await asyncio.to_thread(_feed, proc, user_line(req))
            deadline = started + self.settings.claude_bridge_timeout_seconds
            while True:
                try:
                    line = await asyncio.wait_for(lines.get(), timeout=max(0.1, deadline - time.perf_counter()))
                except TimeoutError:
                    yield {"type": "error", "code": "timeout", "message": "Claude took too long to answer. Try again."}
                    return
                if line is None:
                    break
                for event in parser.feed(line):
                    yield event
            exit_code = await asyncio.to_thread(proc.wait)
            for event in parser.finish(exit_code, "".join(stderr_tail)):
                if event["type"] == "error":
                    log.warning("Claude Code error (%s): %s", event["code"], event["message"])
                yield event
            log.info("reply model=%s secs=%.1f exit=%s", req.model, time.perf_counter() - started, exit_code)
        finally:
            if proc and proc.poll() is None:
                _kill(proc)
            shutil.rmtree(workdir, ignore_errors=True)


_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _pump(stream, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop) -> None:
    try:
        for raw in iter(stream.readline, b""):
            loop.call_soon_threadsafe(queue.put_nowait, raw.decode("utf-8", "replace"))
    except (OSError, ValueError):
        pass
    finally:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, None)
        except RuntimeError:  # the loop is gone
            pass


def _tail(stream, tail: deque[str]) -> None:
    try:
        for raw in iter(stream.readline, b""):
            tail.append(raw.decode("utf-8", "replace"))
    except (OSError, ValueError):
        pass


def _feed(proc: subprocess.Popen, line: str) -> None:
    try:
        proc.stdin.write(line.encode("utf-8"))
        proc.stdin.close()
    except (OSError, ValueError):  # it already exited; its output says why
        pass


def _kill(proc: subprocess.Popen) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, creationflags=_NO_WINDOW
            )
        else:
            os.killpg(proc.pid, 9)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
    except OSError:
        pass
