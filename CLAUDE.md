# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

A local chat + voice assistant: five FastAPI services (`services/chat`, `services/voice`, `services/wakeword`, `services/sandbox`, `services/bridge`) and a React/TypeScript/Tailwind frontend (`frontend`). README.md is the user-facing reference (features, every env var, WebSocket protocols, troubleshooting). Read it before changing behaviour users can see, and keep it in sync.

## Commands

Python 3.11 is required (openWakeWord has no wheels for newer versions). All three services share one virtualenv at `.venv` (created by `scripts/setup.ps1` / `setup.sh` from `requirements-dev.txt`).

```powershell
scripts\test.ps1                      # all suites: pytest x5, vitest, tsc -b  (scripts/test.sh on Unix)
scripts\dev.ps1                       # native: chat/voice/wakeword on :8001-8003, Claude bridge on :8005, Vite on :5173
scripts\bridge.ps1                    # only the Claude bridge (for claude_cli while the rest runs in Docker)

# One service's tests (run from the service dir; pytest.ini sets asyncio_mode=auto, testpaths=tests)
cd services\chat; ..\..\.venv\Scripts\python.exe -m pytest -q
..\..\.venv\Scripts\python.exe -m pytest tests/test_api.py::test_name -q

# Frontend (from frontend/)
npm test                              # vitest run (node env, *.test.ts next to sources in src/lib, src/audio)
npx vitest run src/lib/sse.test.ts    # single file
npx tsc -b                            # type-check (also part of `npm run build`)
```

There is no linter or formatter configured.

Docker: `docker compose up -d --build` (add `-f docker-compose.gpu.yml` for NVIDIA). On this machine `.env` sets `APP_PORT=8088`, so the UI is at http://localhost:8088. After any frontend change, rebuild that container: `docker compose up -d --build frontend`. Rebuild `chat`/`voice`/`wakeword` the same way after backend changes.

Smoke tests against real models (not part of the test suite): `python -m scripts.smoke_test` in `services/voice` or `services/wakeword`, and `scripts/voice_client.py` for a full voice turn against a running stack.

## Architecture

**Single origin.** The browser only talks to one origin and uses the prefixes `/chat`, `/voice`, `/wakeword`. In dev, Vite proxies them (`frontend/vite.config.ts`, stripping the prefix); in Docker, nginx does the same (`frontend/nginx.conf` + `frontend/assistant.conf`). A new backend route is reachable from the browser as `/chat/api/...` with no proxy change. A new top-level prefix needs both the Vite config and `assistant.conf` updated.

**Two doors and auth.** nginx serves port 8080 (localhost only, no sign-in) and 8443 (HTTPS for LAN devices). On 8443 nginx sets `X-Remote-Access: 1` and gates `/voice` and `/wakeword` through `auth_request` to the chat service's `/api/auth/check`. The chat service also enforces sign-in itself: every route in `routes.py` is mounted with `Depends(auth.require_sign_in)`, and only `auth.router` (`/api/auth/*`) is open. The session cookie is HMAC-signed with a key derived from a stored secret plus `APP_PASSCODE`, so changing the passcode invalidates every device (`services/chat/app/auth.py`).

**chat service (:8001) is the hub.** It owns all persistent state in one SQLite DB (`repository.py`). Tables are created with `CREATE TABLE IF NOT EXISTS`. Columns added later go through the repository's add-missing-columns helper, not a migration framework, so new columns must be registered there. `main.create_app(settings, registry)` builds everything in a lifespan and stores it on `app.state` (chat, automation, auth, notifications, notifier). Tests inject a fake `ProviderRegistry` through that factory.
- `ChatService.stream_reply` runs one turn under a per-session lock: optional web search → build history (documents prepended with a char budget, images, reply-language instruction) → stream from the provider → persist. It yields `ChatEvent`s (`searching`, `web`, `start`, `token`, `done`, `error`, and with agents `plan`, `agent`, `tool`, `action`), which routes emit as SSE (documented in README → *Chat stream events*). Both the frontend (`lib/sse.ts` + `dispatchStreamEvent` in `lib/api.ts`) and the voice service (`chat_client.py`) consume this same SSE stream and ignore unknown events, so changes to existing event names or payloads must be made in all three places.
- LLM providers implement `LLMProvider` in `app/llm/base.py` (`stream`, `check`, optional `complete_json`, `warm_up`). `ProviderRegistry` holds one instance each of `ollama`, `anthropic`, `claude_cli`, `echo`, and the active provider/model can be switched at runtime via `PATCH /api/config` (this isn't persisted). `normalize_turns` fixes history so it alternates roles. `LLMError` messages are shown to users. `LLMAuthError` triggers a "rejected key" notification. A reply cut at max tokens ends with `TRUNCATED_NOTE`, which the service detects.
- Task automation (`automation.py`) is draft-then-approve: the model only produces a draft (via `complete_json` with `DRAFT_MODELS` schemas from `schemas.py`) stored as a pending action. `execute` runs only from `POST /api/actions/{id}/approve` with the user-edited payload. Kinds: email, event, document, workflow, `code` (POSTs to the sandbox service) and `file_edit` (writes into `PROJECT_DIR` through `workspace.py`; path and base hash come from the stored draft, not the approved payload). Keep that invariant: nothing is sent, run or written because the model said so.
- Agents (`app/agents/`, design in `docs/agents.md`): with `agents: true` in the chat request (or `AGENT_MODE=always`), `Orchestrator` plans with `complete_json`, runs each specialist's tool loop through `LLMProvider.run_tools_step` (native tools on Ollama/Claude, JSON fallback otherwise), then the normal streaming path writes the reply with the reports in the system prompt. Only the final answer is `token` events; progress is `plan`/`agent`/`tool`/`action`. Tools live in `agents/tools.py`; anything with a side effect must be a `propose_*` tool that calls `Toolbox.propose` (a pending action), never the side effect itself. The trace is stored in `messages.agent`.
- `notifications.py` (in-app feed + SSE `/api/notifications/stream`, categories, dedupe, reminder loop) sits on top of `notifier.py` (outbound ntfy/Twilio).
- `search.py` (SearXNG/Brave + `worth_searching` heuristic), `documents.py` (text extraction from PDF/Office/links, with SSRF protection), `translate.py` (model-driven translation with a cache).

**voice service (:8002)** is stateless apart from per-connection `VoiceSession` (`pipeline.py`): PCM16 frames over `/ws/voice` → energy-based `Endpointer` (`vad.py`) → faster-whisper → streams the transcript to the chat service over HTTP/SSE (`chat_client.py`, which also creates the session if needed) → `SentenceChunker` splits tokens → Piper synthesizes each sentence → one binary WAV frame per sentence. Engines live in `engines.py` and are injectable through `create_app(settings, stt, tts, chat)`. Tests use `FakeSTT`/`FakeTTS`/`FakeChatBackend` (httpx MockTransport) in `tests/conftest.py`. Voice turns are stored in the same chat history as typed turns. Composer dictation uses a separate socket, `/ws/dictate` (`dictation.py`, browser side `audio/dictation.ts`): `partial` text while a phrase goes on (at most one Whisper run in flight; partials wait while a `final` is pending), a `final` per phrase at each pause, `done` after `stop`. It never talks to the chat service.

**sandbox service (:8004)**: runs approved Python (`POST /run`) for `code` actions. `create_app(settings, runner)`; `SubprocessRunner` applies rlimits (POSIX only, so it refuses on Windows unless `sandbox_allow_unlimited`, which tests set). In Docker it sits on an `internal: true` network with no published port, a read-only filesystem and no capabilities; natively it isn't started and `SANDBOX_URL` stays empty.

**bridge service (:8005)**: Claude through the user's Claude subscription. Runs natively on the host (where Claude Code is signed in), never in Docker. The `claude_cli` provider (`app/llm/claude_cli.py`) reaches it at `CLAUDE_BRIDGE_URL` (Docker: `host.docker.internal`). `POST /v1/messages` turns one request into a locked-down `claude -p` run (`--tools ""`, `--strict-mcp-config`, `--safe-mode`, `--no-session-persistence`, empty temp cwd, API keys stripped from env) and streams NDJSON `text`/`done`/`error` events. History goes as a transcript in one user message; JSON output goes through `--json-schema`. Bearer `CLAUDE_BRIDGE_TOKEN` is mandatory. `create_app(settings, runner)`; tests drive the real `CliRunner` against `tests/fake_claude.py`. Never run the CLI through a `.cmd` shim (cmd.exe would interpret prompt text).

**wakeword service (:8003)**: openWakeWord over `/ws/wakeword`. `create_app(settings, factory)` takes a detector factory so tests run without models. Custom `.onnx` models are read from `models/wakeword/` (`WAKEWORD_CUSTOM_DIR` natively, a mounted volume in Docker).

**Frontend.** `App.tsx` wires together hooks (`useChat`, `useVoice`, `useSchedule`, `useNotifications`, `useHealth`, `useLanguage`, `useWebSearch`, `usePet`) and holds the cross-cutting state (pending actions, toasts). All HTTP goes through `lib/api.ts` and all shared types are in `lib/types.ts`, which mirror the Pydantic models in `services/chat/app/schemas.py`. Change both together. `audio/voiceController.ts` is the browser-side voice state machine (phases off/wake/conversation, modes ptt/handsfree/wake). It switches the mic stream between the wakeword and voice sockets and keeps the mic half-duplex unless barge-in is on. Mic capture is an AudioWorklet (`public/capture-worklet.js`) producing 16 kHz PCM16. The app is also a PWA (`public/sw.js`, manifest).

**Configuration.** Every service reads env vars via pydantic-settings with `env_file=("../../.env", ".env")`, so native runs pick up the repo-root `.env` when started from the service directory. `docker-compose.yml` passes each variable explicitly with its default. A new setting has to be added to the service's `config.py`, to `docker-compose.yml`, to `.env.example`, and to the README configuration table.

## Conventions

- Tests never need real models or the network: fake providers/engines are injected via each service's `create_app` factory. Follow that pattern rather than mocking module globals.
- User-facing strings (error messages, notifications, README) use plain, non-technical language. Match the existing tone.
- Commit messages: short imperative summary lines in the style of `git log`. Don't add AI attribution trailers.
