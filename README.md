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
- **Tasks with review**: draft an email, add an event to your schedule, compile a document (for example, notes from the current chat), or trigger a workflow webhook. The assistant only drafts. You get a notification, review and edit the draft, and nothing is sent, saved or run until you press **Approve**.
- **Sessions**: create, rename and delete conversations. Voice turns go into the same history.
- **Runtime settings**: switch provider, model, system prompt and temperature without restarting.
- **Compact chat window**: the chat sits in a floating card. Conversations, plans, the schedule, service status and Settings are in the drawer behind the person icon. The pop-out icon opens the chat in its own small window.
- **Pet**: a cat, dog or bunny sits next to the chat. While the assistant is offline (the chat service or its model is down) it falls asleep; point at it to hear it snore, or tap it on a phone. Choose the pet, or none, in Settings.

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

Follow progress with `docker compose logs -f ollama-pull voice`. The status dots in the drawer (person icon) turn green when each service is ready.

- **Port 8080 already in use?** Set `APP_PORT=8088` in `.env`.
- **NVIDIA GPU for the LLM:** `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build`. Check it took effect with `docker compose exec ollama ollama ps` (PROCESSOR should say GPU). If it still says CPU and `docker compose logs ollama` mentions `failed to initialize CUDA`, update Docker Desktop and run `wsl --update`, or use the next option.
- **Use Ollama installed on the host** (simplest way to get the GPU on Windows/macOS): install Ollama, run `ollama pull llama3.2`, set `OLLAMA_DOCKER_URL=http://host.docker.internal:11434` in `.env`, and `docker compose up -d chat`.
- The backend ports are bound to `127.0.0.1`. The web UI is also served over HTTPS on port 8443 for phones, tablets and other computers on your network, and every device except this computer has to sign in with a passcode first (see below). Until `APP_PASSCODE` is set, other devices are turned away.

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

## Phones, tablets and other computers

The same app works on iPhone, iPad, Android phones and tablets (Samsung included) and other computers on the same Wi-Fi: chat, pictures from the camera roll, dictation, voice mode, tasks and the notification bell. The layout adapts to the screen, and the assistant can be added to the home screen like an app. Everything still runs on this computer, so it has to be on.

1. On this computer, run `scripts\lan.ps1` (Windows) or `scripts/lan.sh`. It finds the computer's LAN address and writes `TLS_HOSTS`, `APP_URL` and a random `APP_PASSCODE` into `.env`, then prints the address and passcode.
2. Restart: `docker compose up -d --build frontend chat`.
3. On the phone or tablet, open the printed address, for example **https://192.168.1.10:8443**, and enter the passcode. The device stays signed in for 90 days.

**Trust the certificate (once per device).** The connection is encrypted with a certificate the assistant made for itself, so the first visit shows a warning. You can tap through it, but browsers keep home-screen install and notifications off (and some the microphone) until the certificate is trusted. To trust it, open `https://<address>:8443/ca.crt` on the device, then:

- **iPhone / iPad:** allow the download. Settings → General → *VPN & Device Management* → *Local Assistant CA* → Install. Then Settings → General → About → *Certificate Trust Settings* → turn on *Local Assistant CA*.
- **Android / Samsung:** Settings → *Security and privacy* → *More security settings* (Samsung: *Other security settings*) → *Install from device storage* → *CA certificate* → pick `local-assistant-ca.crt`. Menu names vary; searching Settings for "CA certificate" finds it.

The certificate can only vouch for private network addresses (192.168.x.x, 10.x.x.x and the like) and `.local` names, so trusting it can't be used to impersonate a real website. Remove it in the same settings at any time.

**Add to the home screen:** Safari → Share → *Add to Home Screen*; Chrome or Samsung Internet → menu → *Add to Home screen* / *Install app*. It then opens full screen, and on Android shows notifications while it's open in the background. (iPhone and iPad only allow web notifications from the home-screen app.)

**Signing out:** Settings → *Phones and tablets* has *Sign out* on the device itself and *Sign out all other devices* everywhere. Changing `APP_PASSCODE` and restarting also signs every device out. Five wrong passcodes in a row pause sign-in for 15 minutes, and wrong passcodes and new devices show up as notifications.

**Away from home:** don't forward the port on your router. Install [Tailscale](https://tailscale.com) on the computer and the phone instead, add the computer's Tailscale address (`100.x.y.z`) to `TLS_HOSTS` (comma-separated), restart the frontend, and open `https://100.x.y.z:8443`.

If the computer's address changes, run the script again and restart the frontend; phones keep the trusted certificate.

## Using it

- **Text:** type and press Enter. Shift+Enter adds a new line. On phones and tablets Enter adds a new line and the send button sends. *Stop* aborts generation, and the partial reply is kept.
- **Dictation:** click the mic inside the message box, speak, then click again. The text appears in the box for editing.
- **Documents:** click the paperclip left of the message box (or paste or drop files onto it) and ask about them. Up to 5 per message, 20 MB each.
  - **Photos and files** opens the device's file picker. On iPhone, iPad and Android it also reaches iCloud Drive, Google Drive, OneDrive and Dropbox (through their apps); on a computer, their synced folders.
  - **From a link** fetches a file shared as "anyone with the link" from Google Drive, Google Docs/Sheets/Slides (exported as Word, Excel or PowerPoint), OneDrive or Dropbox, or any public file or web page. The assistant can't sign in to your cloud account, so private files need sharing or downloading first. Links into your own computer or network are refused.
  - It reads PDF (with a text layer: scans need OCR first), Word `.docx`, Excel `.xlsx`, PowerPoint `.pptx`, OpenDocument, and text formats (TXT, Markdown, CSV, JSON, HTML, code). Old `.doc`/`.xls`/`.ppt` files: save them in the new format or as PDF.
  - The text goes to the model with your question, and later questions in the same chat see it too. Up to `DOCUMENT_MAX_CHARS` characters are sent per request, newest documents first; a longer document is cut, and the model is told so. With Ollama the context window grows to fit (up to `OLLAMA_MAX_CTX`), which reloads the model once and needs more memory; small local models answer best about short documents.
  - The original file stays with the conversation: tap its chip to download it.
- **Pictures:** pick them with the paperclip, or paste or drop images onto the message box. They're resized to at most 1568 px in the browser and stored with the conversation. The default `llama3.2` is text-only, so pull a vision model first (`docker compose exec ollama ollama pull gemma3:4b`) and select it in Settings, or use Claude.
- **Tasks:** click the lightning button left of the message box and pick a task, then describe it ("Email anna@example.com that I'll be 10 minutes late", "Dentist tomorrow at 3pm", "Summarize this chat as meeting notes"). The assistant drafts it into a review card:
  - Edit any field, then press **Approve** to run it, or **Reject** to discard it.
  - An approved email is saved to your **Gmail Drafts** if `IMAP_USERNAME` and `IMAP_PASSWORD` (a Gmail app password) are set: nothing is sent until you open it in Gmail and press Send. With SMTP settings it's sent directly instead. With neither, it opens in a new tab in Gmail's compose window (or Outlook.com, or your default mail program; see `EMAIL_COMPOSE`), filled in, so you can check it and press Send.
  - Events go into **Upcoming** in the drawer, with a reminder 10 minutes before. Each has an `.ics` file for your calendar app.
  - Documents are saved and downloadable as Markdown.
  - When a draft is ready you get an in-app alert, and a desktop notification if the tab is in the background (allow notifications when the browser asks). Chats with drafts waiting show an amber dot.
  - **On your phone:** set `NOTIFY_PHONE` (SMS through Twilio) or `NTFY_TOPIC` (free push through the ntfy app) to get a text whenever a task needs review, and one more reminder if it's still waiting after `NOTIFY_REMIND_MINUTES`. The text names the task (for example *Email to anna@example.com: "Running late"*). It only reminds you; approving still happens in the app. Settings → *Notifications* has a **Send test notification** button.
- **Notifications:** the bell in the header lists what happened, with an unread count, on every device that has the app open. New ones pop up live, and as a desktop notification when the tab is in the background. They also go to your phone (ntfy push or SMS, set up as above) for the categories you switch on in Settings → *Notifications*:

  | When | Browser | Phone (default) |
  |---|---|---|
  | **Sign-ins:** a new browser or phone opens the assistant, a device signs in with the passcode, or a wrong passcode is entered (with its IP address) | ✓ | ✓ |
  | **Rejected keys and tokens:** Claude's API key, the Gmail app password, the SMTP login or the Twilio/ntfy credentials are refused | ✓ | ✓ |
  | **Tasks to review:** a drafted email or task waits for approval, plus one reminder | ✓ | ✓ |
  | **Task results:** an approved task is done or failed | ✓ | |
  | **Photo limit:** more than 4 pictures picked for one message | ✓ | |
  | **Token limit:** a reply is cut off at `MAX_OUTPUT_TOKENS` | ✓ | |

  The same problem within 10 minutes is reported once. There are no user accounts: each browser keeps a random id in local storage, so a private window or cleared site data counts as a new device. Behind Docker Desktop every visitor shows the same internal address, so the IP is left out there.
- **Voice:** pick a mode in the voice bar, then click the round mic button.
  - *Push to talk:* hold the button, or hold **Space** when not typing.
  - *Hands-free:* just talk. A pause of about 0.8 s ends your turn.
  - *Wake word:* say "Hey Jarvis". A chime confirms. After the reply you have a follow-up window (8 s by default) to keep talking before it waits for the wake word again. *Talk now* skips the wake word.
- **Settings** (in the drawer behind the person icon):
  - Model: provider, model, system prompt, temperature.
  - Voice: speak replies, barge-in.
  - Pet: cat, dog, bunny or none.
  - Wake word: phrase, sensitivity threshold, follow-up window.

## Configuration

All settings are environment variables. Put them in `.env`; Docker and the native scripts both read it.

| Variable | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | `ollama`, `anthropic` (Claude) or `echo` (offline test mode) |
| `LLM_MODEL` | *(provider default)* | Overrides `OLLAMA_MODEL` / `ANTHROPIC_MODEL` |
| `OLLAMA_MODEL` | `llama3.2` | Pulled automatically in Docker |
| `OLLAMA_KEEP_ALIVE` | `24h` | How long the model stays loaded after a reply (`-1m` = never unload). The chat service also preloads the model at startup and when you switch models. |
| `OLLAMA_NUM_THREAD` | `0` | CPU threads for generation (`0` = one per core). Set it below the number of free cores when other programs keep the CPU busy; oversubscribed threads can slow replies 10–30×. Ignored when the model runs on a GPU. |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Native runs only |
| `OLLAMA_DOCKER_URL` | `http://ollama:11434` | Docker: point at a host Ollama via `http://host.docker.internal:11434` |
| `ANTHROPIC_API_KEY` | — | Required for Claude |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` | |
| `ANTHROPIC_EFFORT` | `low` | `low`…`max`; low keeps voice latency down |
| `MAX_HISTORY_MESSAGES` | `30` | Messages of context sent to the model |
| `DOCUMENT_MAX_CHARS` | `40000` | Characters of attached document text sent with one request (about 10,000 tokens), newest documents first |
| `OLLAMA_MAX_CTX` | `16384` | Largest context window (tokens) requested from Ollama when a prompt outgrows its default, as with documents. More needs more memory; `0` leaves it to Ollama |
| `IMAP_USERNAME` / `IMAP_PASSWORD` | — | Your Gmail address and an [app password](https://myaccount.google.com/apppasswords): approved emails are saved to Gmail Drafts. Ignored when `SMTP_HOST` is set |
| `IMAP_HOST` / `IMAP_PORT` | `imap.gmail.com` / `993` | For another provider's mailbox (SSL) |
| `IMAP_DRAFTS_FOLDER` | auto | Drafts folder name, if the server doesn't mark one |
| `EMAIL_COMPOSE` | `gmail` | Without SMTP, where an approved email opens ready to send: `gmail`, `outlook` (Outlook.com) or `mail_app` (default `mailto:` program) |
| `SMTP_HOST` | — | Mail server that sends approved emails directly. Empty = save to Drafts (IMAP) or use `EMAIL_COMPOSE` |
| `SMTP_PORT` / `SMTP_SECURITY` | `587` / `starttls` | `ssl` for port 465, `none` for a local relay |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | — | For Gmail, use an [app password](https://support.google.com/accounts/answer/185833) |
| `SMTP_FROM` | `SMTP_USERNAME` | Sender address |
| `NOTIFY_PHONE` | — | Your mobile number in international format (`+84901234567`) for SMS review reminders. Needs the three Twilio settings |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | — | From the [Twilio console](https://console.twilio.com). A free trial can text your own verified number |
| `TWILIO_FROM` | — | Your Twilio number (`+1…`) or a Messaging Service SID (`MG…`) |
| `NTFY_TOPIC` | — | Free alternative: push through the [ntfy](https://ntfy.sh) app. Use a long, hard-to-guess topic, since anyone who knows it can read the messages. `NTFY_SERVER` (default `https://ntfy.sh`) and `NTFY_TOKEN` for a self-hosted or protected server |
| `NOTIFY_REMIND_MINUTES` | `30` | Remind once more if a task is still waiting; `0` = off |
| `APP_URL` | — | Link added to phone notifications and shown in Settings, e.g. `https://192.168.1.10:8443`. Set by `scripts/lan.ps1` |
| `WORKFLOWS` | `{}` | JSON `{"name": "webhook URL"}`. Approved runs POST `{"workflow", "input", "action_id"}` to that URL. Only listed URLs are ever called |
| `STT_MODEL` | `base.en` | `tiny.en`, `small.en`, `medium.en`, or multilingual `base`, `small`, `large-v3`… |
| `STT_LANGUAGE` | `en` | Empty = auto-detect (use a multilingual model) |
| `STT_DEVICE` / `STT_COMPUTE_TYPE` | `cpu` / `int8` | Native: `cuda` / `float16` with CUDA 12 + cuDNN installed |
| `TTS_ENGINE` | `piper` | `browser` uses the Web Speech API instead; `none` disables speech |
| `PIPER_VOICE` | `en_US-lessac-medium` | Any [Piper voice](https://huggingface.co/rhasspy/piper-voices); downloaded on first use |
| `VAD_SILENCE_MS` | `800` | Silence that ends a hands-free turn |
| `WAKEWORD_DEFAULT` | `hey_jarvis` | Also offered: `alexa`, `hey_mycroft` |
| `WAKEWORD_THRESHOLD` | `0.5` | Lower = more sensitive |
| `APP_PORT` | `8080` | Docker UI port on this computer (no sign-in) |
| `APP_PASSCODE` | — | Passcode other devices enter once. At least 6 characters; without it they're turned away. Changing it signs every device out |
| `TLS_HOSTS` | — | This computer's LAN (or Tailscale) address or `name.local`, comma-separated, for the HTTPS certificate |
| `APP_HTTPS_PORT` | `8443` | HTTPS port for phones, tablets and other computers |
| `REMOTE_BIND` | `0.0.0.0` | `127.0.0.1` keeps the HTTPS port on this computer only |

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
             nginx.conf + assistant.conf (local and HTTPS ports), certs.sh (HTTPS certificate), public/ (icons, manifest, sw.js)
scripts/     setup/dev/test for Windows and Unix, lan (phones and tablets), voice_client.py
docker-compose.yml, docker-compose.gpu.yml, .env.example
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Microphone access needs a secure context" | On this computer use `http://localhost:8080`; on other devices use the `https://…:8443` address, not `http://`. |
| Phone can't open the address | Same Wi-Fi as the computer? On Windows, set that network to *Private* (Settings → Network & internet), since a public network blocks incoming connections. Check the address with `scripts\lan.ps1`. Guest networks often keep devices apart. |
| Certificate warning every time, or no "Install app" | Trust the certificate on the device (see *Phones, tablets and other computers*). If the computer's address changed, rerun the script and restart the frontend. |
| Phone keeps asking for the passcode | Private browsing forgets the sign-in. Otherwise it was signed out from Settings or `APP_PASSCODE` changed. |
| Voice stops when the phone screen locks | Phones pause the microphone for pages in the background. The app keeps the screen on while voice is on; keep it in front. |
| Replies are slow (a few words per second) | The LLM is on CPU. Use a GPU (see Docker quick start), a smaller model (`llama3.2:1b`), or Claude. |
| Chat status amber: "model not pulled" | `docker compose logs ollama-pull`; natively run `ollama pull <model>`. |
| Voice status "STT loading…" for a long time | First start downloads Whisper. Check `docker compose logs voice`. |
| Assistant answers itself or loops | Turn off barge-in, or use headphones. Echo cancellation can't remove everything from loud speakers. |
| Hands-free cuts you off / waits too long | Adjust `VAD_SILENCE_MS`. In a noisy room, try push-to-talk. |
| Too many or too few wake-ups | Adjust the sensitivity in Settings (threshold 0.3–0.7). |
| No speech heard | Check "Speak replies". Some browsers need one click on the page before audio can play. |

## Limitations

- Single-user: one passcode for every other device, no separate accounts. This computer never needs it. Runtime settings changes are not persisted across restarts; use `.env` for permanent settings.
- End-of-speech detection is energy based and adapts to steady background noise. In very noisy environments, push-to-talk is more reliable.
- GPU acceleration in Docker covers Ollama only. Whisper and Piper run on CPU in the containers, which is fast enough for the default model sizes.
