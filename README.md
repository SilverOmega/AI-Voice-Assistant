# Local Chat & Voice Assistant

A private AI assistant that runs on your own computer. Type to it, talk to it, or wake it with a phrase. Speech recognition, speech synthesis and wake-word detection all run locally. The language model can also be local (Ollama), or you can point it at Claude.

- **Text chat** with streaming replies, markdown rendering and persistent conversation history.
- **Voice conversation**: speech-to-text (faster-whisper), then the LLM, then text-to-speech (Piper). Replies are spoken sentence by sentence while the model is still writing, so speech starts quickly.
- **Three voice modes**:
  - *Push to talk* (hold the button or Space).
  - *Hands-free* (end-of-speech detection).
  - *Wake word* ("Hey Jarvis", "Alexa", "Hey Mycroft", or your own trained model), with a follow-up window.
- **Dictation**: the composer's mic button records a clip with MediaRecorder and transcribes it into the text box.
- **Pictures**: attach up to 4 images per message (button, paste or drag and drop) and ask about them. Needs a vision model such as `gemma3:4b`, or Claude.
- **Interrupt**: a Stop button, plus optional barge-in (talk over the assistant).
- **Sessions**: create, rename and delete conversations. Voice turns go into the same history.
- **Runtime settings**: switch provider, model, system prompt and temperature without restarting.

## Architecture

```
                         Browser (React + TypeScript + Tailwind)
   mic -> AudioWorklet -> 16 kHz PCM16 frames            speaker <- Web Audio (WAV queue)
            |                         |                       ^
            | wake phase              | conversation phase    | WAV per sentence
            v                         v                       |
   +-----------------+     +----------------------------------+------+     +------------------+
   | wakeword :8003  |     | voice :8002                              | --> | chat :8001       |
   | openWakeWord    |     | endpointing (VAD) -> faster-whisper STT  | SSE | sessions, SQLite |
   | WS /ws/wakeword |     | -> chat stream -> sentence split -> Piper|     | LLM providers:   |
   +-----------------+     | WS /ws/voice, POST /api/stt, /api/tts    |     | Ollama | Claude  |
                           +------------------------------------------+     | | echo           |
                                                                            +------------------+
           All browser traffic goes through one origin: nginx (Docker) or the Vite dev server
           proxies /chat -> :8001, /voice -> :8002, /wakeword -> :8003.
```

| Service | Responsibility | Key endpoints |
|---|---|---|
| `services/chat` | Conversations, history (SQLite), LLM streaming, runtime config | `GET/POST /api/sessions`, `GET/PATCH/DELETE /api/sessions/{id}`, `POST /api/sessions/{id}/messages[/stream]`, `GET/PATCH /api/config`, `GET /api/models`, `GET /health` |
| `services/voice` | Endpointing, STT, TTS, orchestration of a voice turn | `WS /ws/voice`, `POST /api/stt`, `POST /api/tts`, `GET /api/voices`, `GET /health` |
| `services/wakeword` | Always-on wake-phrase detection | `WS /ws/wakeword`, `GET /api/models`, `GET /health` |
| `frontend` | UI, mic capture, playback, voice-mode state machine | — |

Interactive API docs are at `http://localhost:800{1,2,3}/docs` while the services run.

**One voice turn:**
1. The mic streams 80 ms PCM frames to `/ws/voice`.
2. The endpointer notices speech, then ~0.8 s of silence.
3. Whisper transcribes the utterance.
4. The voice service streams the transcript to the chat service, which saves both messages.
5. Tokens stream back. Each complete sentence is synthesized by Piper and sent as a WAV while the rest of the reply is still generating.
6. The browser queues the WAVs gap-free.

By default the mic is half-duplex: it isn't forwarded while the assistant speaks, so the assistant can't hear itself.

## Quick start: Docker (recommended)

Requires Docker Desktop (or Docker Engine + Compose v2).

```bash
cp .env.example .env          # optional: edit models, voice, wake word, port
docker compose up -d --build
```

Open **http://localhost:8080**. The first start downloads several things:
- the Ollama model (`llama3.2`, ~2 GB, pulled by the `ollama-pull` one-shot container)
- the Whisper model (~150 MB)
- the Piper voice (~60 MB)

Follow progress with `docker compose logs -f ollama-pull voice`. The sidebar's status dots turn green when each service is ready.

- **Port 8080 already in use?** Set `APP_PORT=8088` in `.env`.
- **NVIDIA GPU for the LLM:** `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build`. Check it took effect with `docker compose exec ollama ollama ps` (PROCESSOR should say GPU). If it still says CPU and `docker compose logs ollama` mentions `failed to initialize CUDA`, update Docker Desktop and run `wsl --update`, or use the next option.
- **Use Ollama installed on the host** (simplest way to get the GPU on Windows/macOS): install Ollama, run `ollama pull llama3.2`, set `OLLAMA_DOCKER_URL=http://host.docker.internal:11434` in `.env`, and `docker compose up -d chat`.
- All ports are bound to `127.0.0.1`. The assistant has no authentication, so don't expose it on a network.

## Quick start: native (no Docker)

Requires Python **3.11** (openWakeWord's Linux dependency has no wheels for newer Pythons), Node 20+, and [Ollama](https://ollama.com) if you use a local model (`ollama pull llama3.2`).

```powershell
# Windows
scripts\setup.ps1     # venv + pip + npm + .env
scripts\dev.ps1       # starts chat/voice/wakeword + Vite; Ctrl+C stops all
```
```bash
# macOS / Linux
scripts/setup.sh
scripts/dev.sh
```

Open **http://localhost:5173**. Microphone access requires `localhost` or HTTPS.

## Using it

- **Text:** type and press Enter. Shift+Enter adds a new line. *Stop* aborts generation, and the partial reply is kept.
- **Dictation:** click the mic inside the message box, speak, then click again. The text appears in the box for editing.
- **Pictures:** click the picture button left of the message box, or paste or drop images onto it. They're resized to at most 1568 px in the browser and stored with the conversation. The default `llama3.2` is text-only, so pull a vision model first (`docker compose exec ollama ollama pull gemma3:4b`) and select it in Settings, or use Claude.
- **Voice:** pick a mode in the voice bar, then click the round mic button.
  - *Push to talk:* hold the button, or hold **Space** when not typing.
  - *Hands-free:* just talk. A pause of about 0.8 s ends your turn.
  - *Wake word:* say "Hey Jarvis". A chime confirms. After the reply you have a follow-up window (8 s by default) to keep talking before it waits for the wake word again. *Talk now* skips the wake word.
- **Settings** (sidebar):
  - Model: provider, model, system prompt, temperature.
  - Voice: speak replies, barge-in.
  - Wake word: phrase, sensitivity threshold, follow-up window.

## Configuration

All settings are environment variables. Put them in `.env`; Docker and the native scripts both read it.

| Variable | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | `ollama`, `anthropic` (Claude) or `echo` (offline test mode) |
| `LLM_MODEL` | *(provider default)* | Overrides `OLLAMA_MODEL` / `ANTHROPIC_MODEL` |
| `OLLAMA_MODEL` | `llama3.2` | Pulled automatically in Docker |
| `OLLAMA_KEEP_ALIVE` | `24h` | How long the model stays loaded after a reply (`-1m` = never unload). The chat service also preloads the model at startup and when you switch models. |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Native runs only |
| `OLLAMA_DOCKER_URL` | `http://ollama:11434` | Docker: point at a host Ollama via `http://host.docker.internal:11434` |
| `ANTHROPIC_API_KEY` | — | Required for Claude |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` | |
| `ANTHROPIC_EFFORT` | `low` | `low`…`max`; low keeps voice latency down |
| `MAX_HISTORY_MESSAGES` | `30` | Messages of context sent to the model |
| `STT_MODEL` | `base.en` | `tiny.en`, `small.en`, `medium.en`, or multilingual `base`, `small`, `large-v3`… |
| `STT_LANGUAGE` | `en` | Empty = auto-detect (use a multilingual model) |
| `STT_DEVICE` / `STT_COMPUTE_TYPE` | `cpu` / `int8` | Native: `cuda` / `float16` with CUDA 12 + cuDNN installed |
| `TTS_ENGINE` | `piper` | `browser` uses the Web Speech API instead; `none` disables speech |
| `PIPER_VOICE` | `en_US-lessac-medium` | Any [Piper voice](https://huggingface.co/rhasspy/piper-voices); downloaded on first use |
| `VAD_SILENCE_MS` | `800` | Silence that ends a hands-free turn |
| `WAKEWORD_DEFAULT` | `hey_jarvis` | Also offered: `alexa`, `hey_mycroft` |
| `WAKEWORD_THRESHOLD` | `0.5` | Lower = more sensitive |
| `APP_PORT` | `8080` | Docker UI port |

**Other languages:** set `STT_MODEL=small`, `STT_LANGUAGE=vi` (for example) and a matching `PIPER_VOICE` such as `vi_VN-vais1000-medium`. Then pick an LLM that speaks the language.

**Custom wake words:** train an openWakeWord model and drop the `.onnx` file into `models/wakeword/`. It appears in Settings under its file name. In Docker, more pretrained models can be baked in with `WAKEWORD_MODELS_BUILD="hey_jarvis alexa hey_rhasspy"`.

### Using Claude instead of a local model

Set `ANTHROPIC_API_KEY` in `.env` and either set `LLM_PROVIDER=anthropic` or switch provider in Settings at runtime. The provider uses the official `anthropic` SDK. It streams with adaptive thinking at the configured effort and has server-side refusal fallback enabled. Note that conversation text is then sent to the Anthropic API. Speech processing stays local.

## WebSocket protocols

**`/ws/voice`**

Client to server:
- **Audio:** binary frames of 16 kHz mono PCM16 little-endian.
- **Configuration:** `{"type":"start","session_id":..,"mode":"vad"|"ptt","tts":bool,"barge_in":bool,"voice":..}`
- **Push-to-talk boundaries:** `{"type":"ptt_start"}` / `{"type":"ptt_end"}`
- **Typed turn:** `{"type":"text","text":".."}` runs a turn from text, and the reply is spoken.
- **Interrupt:** `{"type":"cancel"}` aborts the current turn.

Server to client, as JSON events:
- `ready`, `state` (`listening` | `recording` | `transcribing` | `thinking` | `speaking`), `vad`
- `session`, `transcript`
- `assistant_start`, `token`, `assistant_done`
- `speak` (browser TTS), `tts_done`, `turn_done`
- `interrupted`, `error`

Spoken audio arrives as binary frames, one WAV per sentence, in order.

**`/ws/wakeword?model=hey_jarvis&threshold=0.5&scores=1`**

Client to server: PCM16 frames as above. Server to client:
- `{"type":"ready"}`
- `{"type":"wake","model","score"}`
- `{"type":"score","score"}` (about 4 per second for level meters; only when `scores=1`)

## Testing

```powershell
scripts\test.ps1        # or scripts/test.sh
```

This runs the pytest suites for all three services and the frontend's vitest and type-check. The tests use fake engines and a mock LLM, so no models or network are needed.

Smoke tests against the real models:

```bash
# Piper speaks a phrase, Whisper must transcribe it back
cd services/voice && python -m scripts.smoke_test "Set a timer for ten minutes"
# real openWakeWord detection on a recording (any WAV)
cd services/wakeword && python -m scripts.smoke_test hey_jarvis.wav hey_jarvis
# full voice turn against a running stack, no browser: streams a WAV like a microphone
python scripts/voice_client.py question.wav --url ws://localhost:8002/ws/voice --out reply.wav
```

## Project layout

```
services/
  chat/      app/{main,routes,chat_service,repository,schemas,config}.py, app/llm/{ollama,anthropic_provider,echo}.py
  voice/     app/{main,pipeline,vad,sentences,engines,chat_client,config}.py, scripts/smoke_test.py
  wakeword/  app/{main,detector,config}.py, scripts/smoke_test.py
frontend/    src/{App.tsx, components/, hooks/, audio/ (mic, player, recorder, pcm, voiceController), lib/ (api, sse, types)}
scripts/     setup/dev/test for Windows and Unix, voice_client.py
docker-compose.yml, docker-compose.gpu.yml, .env.example
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Microphone access needs a secure context" | Open the UI via `http://localhost`, not a LAN IP, or serve it over HTTPS. |
| Replies are slow (a few words per second) | The LLM is on CPU. Use a GPU (see Docker quick start), a smaller model (`llama3.2:1b`), or Claude. |
| Chat status amber: "model not pulled" | `docker compose logs ollama-pull`; natively run `ollama pull <model>`. |
| Voice status "STT loading…" for a long time | First start downloads Whisper. Check `docker compose logs voice`. |
| Assistant answers itself or loops | Turn off barge-in, or use headphones. Echo cancellation can't remove everything from loud speakers. |
| Hands-free cuts you off / waits too long | Adjust `VAD_SILENCE_MS`. In a noisy room, try push-to-talk. |
| Too many or too few wake-ups | Adjust the sensitivity in Settings (threshold 0.3–0.7). |
| No speech heard | Check "Speak replies". Some browsers need one click on the page before audio can play. |

## Limitations

- Single-user and local: there's no authentication, and runtime settings changes are not persisted across restarts. Use `.env` for permanent settings.
- End-of-speech detection is energy based and adapts to steady background noise. In very noisy environments, push-to-talk is more reliable.
- GPU acceleration in Docker covers Ollama only. Whisper and Piper run on CPU in the containers, which is fast enough for the default model sizes.
