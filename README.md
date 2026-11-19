# Local Chat & Voice Assistant

A private AI assistant that runs on your own computer. Type to it, talk to it, or wake it with a phrase. Speech recognition, speech synthesis and wake-word detection all run locally. The language model can also be local (Ollama), or you can point it at Claude, with an API key or with your Claude subscription.

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
- **Agents**: turn on helpers that work on a message before the reply: a researcher that searches the web, a document reader, a task planner and a coder that can read your project folder, propose file changes as diffs and test Python in a locked-down sandbox. They only read and draft; every email, event, code run or file change waits for your approval.
- **Sessions**: create, rename and delete conversations. Voice turns go into the same history.
- **Runtime settings**: switch provider, model, system prompt and temperature without restarting.
- **Full-screen chat**: the chat fills the window, with conversations, plans, the schedule, service status and Settings in the sidebar on the left. On a phone the sidebar becomes a drawer behind the person icon. The pop-out icon opens the chat in its own small window.
- **Any language, and translation**: choose the language the assistant replies and talks in, from the list or by typing any language's name. Anyone following a chat can translate a message, or any text they select, into their own language.
- **Web search, links and related topics**: questions are looked up on the web before the assistant answers. Under the reply you get the websites to read more and related topics to explore next; click a topic to ask about it. Search runs on your own SearXNG container, so no account or API key is needed.
- **Exchange rates and gold prices**: the chart button in the header opens a dashboard with Vietcombank's rates against VND, PNJ's gold prices (SJC bars, ring gold, 24K and 18K, per lượng), the world gold price and a currency converter, with the time each was last updated. No account or API key is needed.
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
| `services/chat` | Conversations, history (SQLite), LLM streaming, agents, tasks, web search, translation, runtime config | `POST /api/translate`, `GET/POST /api/sessions`, `GET/PATCH/DELETE /api/sessions/{id}`, `POST /api/sessions/{id}/messages[/stream]`, `POST /api/sessions/{id}/actions`, `POST /api/actions/{id}/approve\|reject`, `GET /api/agents`, `GET /api/rates`, `GET/PATCH /api/config`, `GET /api/models`, `GET /health` |
| `services/voice` | Endpointing, STT, TTS, orchestration of a voice turn | `WS /ws/voice`, `POST /api/stt`, `POST /api/tts`, `GET /api/voices`, `GET /health` |
| `searxng` | Web search for the chat service (Docker only, no published port) | `GET /search?format=json` |
| `services/wakeword` | Always-on wake-phrase detection | `WS /ws/wakeword`, `GET /api/models`, `GET /health` |
| `services/sandbox` | Runs approved Python for the code helper (Docker only: no internet, no published port) | `POST /run`, `GET /health` |
| `services/bridge` | Claude with your subscription: runs Claude Code on this computer for the chat service (native only, 127.0.0.1:8005) | `POST /v1/messages`, `GET /health` |
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
- **Agents:** the team button next to the globe lets helpers work on a message before the assistant answers (off by default, kept per device). A lead assistant decides who should help, hands each a task, and writes the reply from what they report:
  - **Researcher** searches the web and reads pages. Its links appear under the reply as with web search.
  - **Document reader** reads, quotes and translates the documents attached in the chat.
  - **Task planner** drafts emails, events, documents and workflow runs, and checks your schedule.
  - **Coder** writes, explains and fixes code. With `PROJECT_DIR` set it reads your project folder and proposes changes to its files; with the code sandbox it proposes running Python to test it.
  - While they work, a line under the reply says who is busy ("Coder is working: read a project file…"). Afterwards **Worked with …** opens the list of steps, tools and short reports, saved with the reply.
  - Helpers only read and draft. Every email, event, document, workflow run, **code run** and **file change** they prepare shows up as a review card, and nothing happens until you approve it. A file change shows as a diff; you can edit the new text before approving. If the file changed since the draft, it isn't written and you're asked to try again.
  - Approved code runs in the sandbox: Python's standard library only, no internet, none of your files, 10 seconds and 256 MB by default. The output appears on the card, and the coder can read it on your next message ("did it work?"). Python code blocks in replies get a **Run** button that makes the same kind of card, and the lightning menu has **Run Python code**.
  - Helpers take longer than a plain reply, especially on small local models; models with tool support (`qwen2.5`, `llama3.1`, Claude) work best. `AGENT_MODE=always` uses them for every message instead (small talk skips them), including voice, where only the final answer is spoken. `AGENT_MODE=off` removes the button.
  - Text from web pages, documents and files is treated as information, never as instructions. The worst a page that tries to trick the helpers can do is make a draft, which you see before anything happens.
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
- **Languages:** in Settings → *Language*, choose what the assistant **replies in**: a language from the list, *Other language…* to type any other one, or the default, which answers in whatever language you use. It applies to typed chats, dictation and voice on that device.
  - Speech recognition then listens for that language. The default `base.en` model only understands English, so for other languages set `STT_MODEL=base` (or `small` for better accuracy) in `.env` and restart the voice service. The app warns you if you forget.
  - Replies are spoken with a Piper voice for that language, downloaded the first time (about 60 MB). Languages Piper has no voice for, such as Japanese, Korean and Thai, are spoken by the browser's own voices. Choose other voices with `PIPER_VOICES`.
  - How well the assistant writes a language depends on the model. Small local models such as `llama3.2` handle the major European languages; for others, try `qwen2.5:7b`, `gemma3:4b` or Claude.
- **Translate:** for anyone following a conversation, for example on a phone next to you.
  - Point at a message (or tap below it on a phone) and press **Translate** to show its translation inside the bubble. Press it again to hide it.
  - Select any part of the conversation and press the **Translate** button that appears next to it. The translation opens in a small panel with a language menu, so you can read the same text in several languages, and a **Copy** button.
  - Each device keeps its own *Translate messages into* language (Settings → *Language*, or the menu in the panel). It starts as the browser's language, so two people can follow the same chat in different languages.
  - The active model translates, so nothing leaves this computer when you use Ollama. Translations aren't saved in the chat. Repeat requests are answered from a cache, and if the page closes mid-translation the model stops.
- **Web search:** the globe button next to the paperclip turns it on or off for this device (on by default).
  - While it's on, each question is searched first ("Searching the web for …" under the reply). The model answers from the results, which helps with anything recent.
  - Under the reply, **Read more on the web** lists the pages found: site, title and a short excerpt. They open in a new tab.
  - **Related topics** are suggestions from the search engine. Click one to ask about it in the same chat.
  - Small talk ("thanks", "hi there") and messages with pictures or documents aren't searched. Voice questions are searched too; the links appear in the chat, and the spoken reply doesn't read them out.
  - The links are saved with the reply, so they're still there later and on other devices.
  - What is searched: your message as you typed it. It goes from your SearXNG container to the search engines it uses (Google, Bing, DuckDuckGo, Wikipedia and others), without your browser's cookies or sign-ins, though they do see your internet connection's address. Turn the globe off for questions you'd rather not send.
- **Rates:** press the chart button in the header to open the rates dashboard (Esc or ✕ closes it).
  - The cards at the top show the dollar, the SJC gold bar and the world gold price at a glance.
  - **Converter**: type an amount and pick a currency. Changing foreign money into đồng uses the bank's cash buy rate (or its transfer rate when it doesn't buy that currency in cash); buying foreign money with đồng uses its sell rate. The rate used is shown under the result.
  - **Gold** lists the buy and sell price per lượng (37.5 g) at PNJ for SJC bars, ring gold 999.9, 24K and 18K jewellery gold, and the gap between them (spread). The world price is in US dollars per ounce, with what that comes to per lượng at Vietcombank's dollar rate. Vietnamese gold usually costs more than that.
  - **Currencies** lists Vietcombank's cash buy, transfer and sell rates in đồng, and the spread as a percentage. A dash means the bank doesn't buy that currency in cash.
  - Prices are fetched when you open the dashboard and kept for 10 minutes (`RATES_CACHE_MINUTES`). The refresh button asks again right away. If a source can't be reached, the dashboard says so and keeps showing the last prices it had.
  - The prices are indicative: check with the bank or shop before you buy or sell. Choose the currencies with `RATES_CURRENCIES`.
- **Voice:** click the wave button at the top of the chat to show the voice bar, pick a mode, then click the round mic button.
  - *Push to talk:* hold the button, or hold **Space** when not typing.
  - *Hands-free:* just talk. A pause of about 0.8 s ends your turn.
  - *Wake word:* say "Hey Jarvis". A chime confirms. After the reply you have a follow-up window (8 s by default) to keep talking before it waits for the wake word again. *Talk now* skips the wake word.
- **Model:** the chip button next to the mic in the message box shows the model in use. Click it to switch between your pulled Ollama models, Claude models (with an API key or with your subscription) and test mode in one click. Claude models stay greyed out until a key is added, right there in the menu; Claude with your subscription stays greyed out until the bridge is running (see *Using Claude instead of a local model*). If a model you pick turns out not to be ready, the assistant stays on the one it was using and tells you why. The choice applies to every chat and device until the chat service restarts; set `LLM_PROVIDER` / `LLM_MODEL` in `.env` to keep it.
- **Settings** (in the drawer behind the person icon):
  - Model: provider, model, system prompt, temperature.
  - Language: the reply language and the translation language.
  - (Web search is the globe button in the message box, not a setting.)
  - Voice: speak replies, barge-in.
  - Pet: cat, dog, bunny or none.
  - Wake word: phrase, sensitivity threshold, follow-up window.

## Configuration

All settings are environment variables. Put them in `.env`; Docker and the native scripts both read it.

| Variable | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | `ollama`, `anthropic` (Claude with an API key), `claude_cli` (Claude with your subscription, through the bridge) or `echo` (offline test mode) |
| `LLM_MODEL` | *(provider default)* | Overrides `OLLAMA_MODEL` / `ANTHROPIC_MODEL` / `CLAUDE_CLI_MODEL` |
| `OLLAMA_MODEL` | `llama3.2` | Pulled automatically in Docker |
| `OLLAMA_KEEP_ALIVE` | `24h` | How long the model stays loaded after a reply (`-1m` = never unload). The chat service also preloads the model at startup and when you switch models. |
| `OLLAMA_NUM_THREAD` | `0` | CPU threads for generation (`0` = one per core). Set it below the number of free cores when other programs keep the CPU busy; oversubscribed threads can slow replies 10–30×. Ignored when the model runs on a GPU. |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Native runs only |
| `OLLAMA_DOCKER_URL` | `http://ollama:11434` | Docker: point at a host Ollama via `http://host.docker.internal:11434` |
| `ANTHROPIC_API_KEY` | — | Required for Claude, unless you add the key from the model menu instead. Set here, it takes precedence |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` | |
| `ANTHROPIC_EFFORT` | `low` | `low`…`max`; low keeps voice latency down |
| `CLAUDE_CLI_MODEL` | `claude-sonnet-5-5` | Default model for Claude with your subscription |
| `CLAUDE_BRIDGE_TOKEN` | — | Shared by the chat service and the bridge. `scripts/bridge.ps1` / `bridge.sh` create it the first time; the bridge refuses every request without it |
| `CLAUDE_BRIDGE_URL` | `http://127.0.0.1:8005` | Native runs: where the chat service finds the bridge. Empty turns it off |
| `CLAUDE_BRIDGE_DOCKER_URL` | `http://host.docker.internal:8005` | Docker: the bridge on this computer, as the chat container sees it. Empty turns it off |
| `CLAUDE_CLI_PATH` | *(found automatically)* | Bridge only: the `claude` program. Otherwise it looks on `PATH`, in `~/.local/bin`, then in the VS Code extension |
| `CLAUDE_BRIDGE_PARALLEL` | `2` | Bridge only: replies Claude Code writes at the same time |
| `CLAUDE_BRIDGE_TIMEOUT_SECONDS` | `300` | Bridge only: longest wait for one reply |
| `WEB_SEARCH` | `searxng` | `searxng` (the bundled container), `brave` ([Brave Search API](https://brave.com/search/api/), needs `BRAVE_API_KEY`) or `off` |
| `BRAVE_API_KEY` | — | For `WEB_SEARCH=brave` |
| `WEB_SEARCH_RESULTS` | `5` | Pages found per question (1–10), shown under the reply and given to the model |
| `SEARXNG_URL` | `http://localhost:8888` | Native runs only: your own SearXNG with `json` in `search.formats` (see `services/searxng/settings.yml`). Docker uses the bundled container |
| `RATES_CURRENCIES` | `USD,EUR,JPY,GBP,AUD,SGD,KRW,CNY,THB` | Currencies in the Rates panel, in this order (codes from Vietcombank's table) |
| `RATES_CACHE_MINUTES` | `10` | How long fetched rates and gold prices are kept before they're fetched again (a failed fetch is retried after a minute) |
| `RATES_FX_URL` | Vietcombank's rate table (XML) | Exchange rates against VND. Empty hides them |
| `RATES_GOLD_URL` | PNJ's gold prices (JSON) | Vietnamese gold prices. Empty hides them |
| `RATES_WORLD_GOLD_URL` | `https://api.gold-api.com/price/XAU` | The world gold price in USD per ounce. Empty hides it |
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
| `AGENT_MODE` | `toggle` | `toggle` (Agents button in the message box), `always` (every message; small talk skips them) or `off` |
| `AGENT_MAX_STEPS` | `4` | Helpers the lead assistant may call for one message (1–10) |
| `AGENT_MAX_TOOL_CALLS` | `6` | Tool uses per helper (1–30); then it reports what it has |
| `AGENT_TIMEOUT_SECONDS` | `180` | Time the helpers get before the reply is written from what they found |
| `PROJECT_DIR` | — | A folder the coder may read and, after you approve each change, edit. Docker mounts it at `/workspace`. `.env` files, keys, `.git`, `node_modules` and similar stay hidden |
| `SANDBOX_URL` | Docker: `http://sandbox:8004` | The code sandbox. Native runs: empty = no code runs (the sandbox needs Linux or macOS) |
| `SANDBOX_TOKEN` | — | Any long random text; the chat service and the sandbox must share it |
| `SANDBOX_TIMEOUT_SECONDS` / `SANDBOX_MEMORY_MB` / `SANDBOX_MAX_OUTPUT` | `10` / `256` / `65536` | Limits for one code run: seconds, memory, and bytes of output kept |
| `STT_MODEL` | `base.en` | `tiny.en`, `small.en`, `medium.en`, or multilingual `base`, `small`, `large-v3`… |
| `STT_LANGUAGE` | `en` | Empty = auto-detect (use a multilingual model) |
| `STT_DEVICE` / `STT_COMPUTE_TYPE` | `cpu` / `int8` | Native: `cuda` / `float16` with CUDA 12 + cuDNN installed |
| `TTS_ENGINE` | `piper` | `browser` uses the Web Speech API instead; `none` disables speech |
| `PIPER_VOICE` | `en_US-lessac-medium` | Any [Piper voice](https://huggingface.co/rhasspy/piper-voices); downloaded on first use |
| `PIPER_VOICES` | — | Voices for other reply languages, as JSON: `{"vi": "vi_VN-vais1000-medium", "de": "de_DE-thorsten-high"}`. Overrides the built-in choice per language code |
| `VAD_SILENCE_MS` | `800` | Silence that ends a hands-free turn |
| `WAKEWORD_DEFAULT` | `hey_jarvis` | Also offered: `alexa`, `hey_mycroft` |
| `WAKEWORD_THRESHOLD` | `0.5` | Lower = more sensitive |
| `APP_PORT` | `8080` | Docker UI port on this computer (no sign-in) |
| `APP_PASSCODE` | — | Passcode other devices enter once. At least 6 characters; without it they're turned away. Changing it signs every device out |
| `TLS_HOSTS` | — | This computer's LAN (or Tailscale) address or `name.local`, comma-separated, for the HTTPS certificate |
| `APP_HTTPS_PORT` | `8443` | HTTPS port for phones, tablets and other computers |
| `REMOTE_BIND` | `0.0.0.0` | `127.0.0.1` keeps the HTTPS port on this computer only |

**Other languages:** choosing a reply language in Settings is usually enough, with a multilingual `STT_MODEL` such as `base` or `small`. To make a language the default for every device, also set `STT_LANGUAGE=vi` (for example) and a matching `PIPER_VOICE` such as `vi_VN-vais1000-medium`. Either way, pick an LLM that speaks the language.

**Custom wake words:** train an openWakeWord model and drop the `.onnx` file into `models/wakeword/`. It appears in Settings under its file name. In Docker, more pretrained models can be baked in with `WAKEWORD_MODELS_BUILD="hey_jarvis alexa hey_rhasspy"`.

### Using Claude instead of a local model

The quickest way: open the model menu next to the mic, paste your API key (from [console.anthropic.com](https://console.anthropic.com/settings/keys)) into the Claude section and press **Add**, then pick a Claude model. The key is checked with Anthropic first, kept in the chat service's database on this computer, and never shown again or sent to the browser. It can only be added or removed from this computer, not from a phone or tablet. Alternatively, set `ANTHROPIC_API_KEY` in `.env` (it wins over a key added in the app) and either set `LLM_PROVIDER=anthropic` or switch at runtime. The provider uses the official `anthropic` SDK. It streams with adaptive thinking at the configured effort and has server-side refusal fallback enabled. Note that conversation text is then sent to the Anthropic API. Speech processing stays local.

#### With your Claude subscription (no API key)

If you have a Claude Pro or Max plan, the assistant can use it through [Claude Code](https://claude.com/code) instead of an API key. Replies then count against your plan's usage limits, not API billing.

1. Install Claude Code on this computer, run `claude` once in a terminal and sign in with your Claude account. The copy that comes with the VS Code extension works too.
2. Start the bridge and leave it running: `scripts\bridge.ps1` (Windows) or `scripts/bridge.sh`. On Windows, `scripts\bridge.ps1 -Install` runs it hidden now and every time you sign in, so you don't have to think about it (`-Uninstall` undoes that; `-Background` runs it hidden just this once). The first time, it adds `CLAUDE_BRIDGE_TOKEN` to `.env`; if the assistant runs in Docker, then restart the chat service with `docker compose up -d chat`. For native runs, `scripts\dev.ps1` starts the bridge too.
3. In the model menu next to the mic, pick a model under **Claude (your subscription)**. The chip then shows "· plan".

The bridge runs on this computer, not in Docker, because that's where Claude Code is signed in. It listens on 127.0.0.1 only, and every request needs the token. Each reply is one `claude -p` run with no tools (it can't read files, run commands or browse), no MCP servers, hooks or plugins, in an empty temporary folder, with nothing saved. Any `ANTHROPIC_API_KEY` is removed from its environment, so it always uses your subscription. Agents work too: their tool choices come back as structured answers. Each reply starts Claude Code afresh, which adds a second or two. Your plan is for your own use, so don't let other people use the assistant through it. As with the API, conversation text goes to Anthropic.

## Chat stream events

`POST /api/sessions/{id}/messages/stream` answers with Server-Sent Events, each `event: <name>` plus one line of JSON `data`. The browser and the voice service read the same stream, and both ignore events they don't know.

| Event | Data | When |
|---|---|---|
| `start` | `{user_message, provider, model}` | First |
| `searching` | `{query}` | A web search starts (the globe, or the researcher) |
| `web` | `{query, links, related, error?}` | Its results |
| `plan` | `{steps: [{agent, title, task}]}` | Agents: the helpers chosen, in order |
| `agent` | `{step, agent, title, task, status, summary}` | A helper starts (`running`) or ends (`done`, `failed`, `skipped`) |
| `tool` | `{step, index, agent, tool, input, output, status, action_id?}` | A helper starts or finishes using a tool (short previews) |
| `action` | `{action}` | A helper stored a draft for review |
| `token` | `{text}` | A piece of the reply. Only the final answer is sent as tokens, so voice speaks nothing else |
| `done` | `{message}` | The saved reply, with `web` and `agent` (the steps) |
| `error` | `{message}` | Instead of `done` |

Send `"agents": true` (and `"now": "YYYY-MM-DDTHH:MM"`, your local time) in the request to use the helpers. See [docs/agents.md](docs/agents.md) for how they work.

## WebSocket protocols

**`/ws/voice`**

Client to server:
- **Audio:** binary frames of 16 kHz mono PCM16 little-endian.
- **Configuration:** `{"type":"start","session_id":..,"mode":"vad"|"ptt","tts":bool,"barge_in":bool,"voice":..,"language":"Vietnamese","language_code":"vi","search":bool}`. `language` is what the reply is written in; `language_code` sets speech recognition and picks the voice. Leave both out for the defaults. `search: true` looks questions up on the web first.
- **Push-to-talk boundaries:** `{"type":"ptt_start"}` / `{"type":"ptt_end"}`
- **Typed turn:** `{"type":"text","text":".."}` runs a turn from text, and the reply is spoken.
- **Interrupt:** `{"type":"cancel"}` aborts the current turn.

Server to client, as JSON events:
- `ready`, `state` (`listening` | `recording` | `transcribing` | `thinking` | `speaking`), `vad`
- `session`, `transcript`
- `assistant_start`, `token`, `assistant_done`
- `speak` (browser TTS, with `lang` when a reply language is set), `tts_done`, `turn_done`
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

This runs the pytest suites for every service and the frontend's vitest and type-check. The tests use fake engines and a mock LLM, so no models or network are needed.

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
  chat/      app/{main,routes,chat_service,repository,schemas,config,automation,workspace}.py, app/llm/{ollama,anthropic_provider,claude_cli,echo}.py
             app/agents/{orchestrator,specialists,tools,prompts}.py (lead assistant, helpers, their tools)
  voice/     app/{main,pipeline,vad,sentences,engines,chat_client,config}.py, scripts/smoke_test.py
  wakeword/  app/{main,detector,config}.py, scripts/smoke_test.py
  sandbox/   app/{main,runner,config}.py (runs approved Python with limits)
  bridge/    app/{main,cli,config}.py (Claude with your subscription: runs Claude Code on this computer)
frontend/    src/{App.tsx, components/, hooks/, audio/ (mic, player, recorder, pcm, voiceController), lib/ (api, sse, types, agentTrace)}
             nginx.conf + assistant.conf (local and HTTPS ports), certs.sh (HTTPS certificate), public/ (icons, manifest, sw.js)
scripts/     setup/dev/test for Windows and Unix, lan (phones and tablets), bridge (Claude subscription), voice_client.py
docs/        agents.md (how the agents work, and how to add a tool or helper)
workspace/   the default (empty) project folder for the coder
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
| "Web search didn't work" | `docker compose ps searxng` should say *running*; check `docker compose logs searxng`. If the engines it uses block it for a while, results come back empty: try again later, or switch to `WEB_SEARCH=brave`. |
| Search results are off-topic for follow-ups | The message is searched as typed, so "what about the price?" lacks the topic. Name it ("what does the Pixel 10 cost?"), or turn the globe off for that question. |
| Agents take a long time | Each helper is a few extra model calls. Use a model with tool support (`ollama pull qwen2.5:7b`), lower `AGENT_MAX_TOOL_CALLS`, or turn Agents off for simple questions. Models without tool support still work, more slowly, by picking tools as JSON. |
| No Agents button | `AGENT_MODE` is `off` or `always` in `.env`. |
| "Code can't be run here" | The sandbox isn't set up: in Docker check `docker compose ps sandbox`; natively there is none (it needs Linux or macOS). |
| The coder can't see my project | Set `PROJECT_DIR` in `.env` and restart the chat service (Docker: `docker compose up -d chat`). Hidden files such as `.env` and folders like `node_modules` are skipped on purpose. |
| "The file changed since this edit was drafted" | The file was edited after the helper read it. Ask again so it works from the current version. |
| "The Claude bridge isn't running" | Start `scripts\bridge.ps1` (or `bridge.sh`) and keep it open, or run `scripts\bridge.ps1 -Install` once so it starts with Windows. Then open the model menu again. |
| "Claude Code isn't signed in" | Run `claude` in a terminal on this computer and sign in with your Claude account. |
| "CLAUDE_BRIDGE_TOKEN doesn't match" | The chat service started before the token was created. Docker: `docker compose up -d chat`; natively, restart `scripts\dev.ps1`. |
| "You've reached your Claude plan's usage limit" | Wait until the time it gives, or switch to Ollama or Claude with an API key in the model menu. |
| Bridge works natively but not from Docker on Linux | The container reaches the computer through the Docker network, not 127.0.0.1: start it with `CLAUDE_BRIDGE_HOST=0.0.0.0 scripts/bridge.sh`. |
| Too many or too few wake-ups | Adjust the sensitivity in Settings (threshold 0.3–0.7). |
| No speech heard | Check "Speak replies". Some browsers need one click on the page before audio can play. |

## Limitations

- Single-user: one passcode for every other device, no separate accounts. This computer never needs it. Runtime settings changes are not persisted across restarts; use `.env` for permanent settings.
- End-of-speech detection is energy based and adapts to steady background noise. In very noisy environments, push-to-talk is more reliable.
- GPU acceleration in Docker covers Ollama only. Whisper and Piper run on CPU in the containers, which is fast enough for the default model sizes.
