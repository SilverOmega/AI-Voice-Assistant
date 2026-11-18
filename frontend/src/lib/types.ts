export type Role = "user" | "assistant" | "system";
export type Source = "text" | "voice";

export interface ImageRef {
  id: string;
  media_type: string;
}

/** A web page a search found for the question a reply answers. */
export interface WebLink {
  title: string;
  url: string;
  snippet: string;
}

export interface WebResults {
  query: string;
  links: WebLink[];
  /** Topics to explore next. */
  related: string[];
}

// ---- agents: a lead assistant plans, helpers use tools --------------------------
// Mirrors AgentTrace & co. in services/chat/app/schemas.py.

export type AgentStatus = "waiting" | "running" | "done" | "failed" | "skipped";

export interface ToolTrace {
  tool: string;
  /** Short previews of the arguments and the result. */
  input: string;
  output: string;
  status: AgentStatus;
  /** The draft the tool stored for review, if any. */
  action_id?: string | null;
}

export interface AgentStepTrace {
  agent: string;
  title: string;
  task: string;
  status: AgentStatus;
  summary: string;
  tools: ToolTrace[];
}

export interface AgentTrace {
  steps: AgentStepTrace[];
  /** Why it stopped early, if it did. */
  note: string;
}

export interface AgentInfo {
  name: string;
  title: string;
  description: string;
  tools: string[];
}

export type AgentMode = "off" | "toggle" | "always";

export interface Message {
  id: string;
  session_id: string;
  role: Role;
  content: string;
  source: Source;
  created_at: string;
  images?: ImageRef[];
  files?: FileRef[];
  /** Links and related topics from a web search, on assistant messages. */
  web?: WebResults | null;
  /** How the helpers (agents) worked on this reply. */
  agent?: AgentTrace | null;
  /** Client-only: the web search running for this reply (its query). */
  searching?: string;
  /** Client-only: message is still streaming or not yet persisted. */
  pending?: boolean;
  /** Client-only: data: URLs of images attached to a message that is not yet persisted. */
  previews?: string[];
  error?: string;
}

export interface ImageUpload {
  media_type: "image/jpeg" | "image/png";
  /** base64 */
  data: string;
}

export interface Session {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface SessionDetail extends Session {
  messages: Message[];
  actions: Action[];
}

// ---- task automation --------------------------------------------------------

export type ActionKind = "email" | "event" | "document" | "workflow" | "code" | "file_edit";
/** "drafting" is client-only: the model is still writing the draft. */
export type ActionStatus = "drafting" | "pending" | "running" | "done" | "rejected";

export interface EmailPayload {
  to: string[];
  cc: string[];
  subject: string;
  body: string;
}
export interface EventPayload {
  title: string;
  /** Local time, YYYY-MM-DDTHH:MM */
  start: string;
  end: string;
  location: string;
  notes: string;
}
export interface DocumentPayload {
  title: string;
  content: string;
}
export interface WorkflowPayload {
  workflow: string;
  input: string;
}
export interface CodePayload {
  /** Only "python" runs. */
  language: string;
  code: string;
  stdin: string;
  purpose: string;
}
export interface FileEdit {
  /** Exact text replaced; empty when creating the file. */
  old: string;
  new: string;
}
export interface FileEditPayload {
  path: string;
  create: boolean;
  edits: FileEdit[];
  /** Set by the server: the file version it was drafted against, and a diff to review. */
  base_sha256: string;
  diff: string;
}
export type ActionPayload =
  | EmailPayload
  | EventPayload
  | DocumentPayload
  | WorkflowPayload
  | CodePayload
  | FileEditPayload;

/** Something the assistant drafted. It runs only after the user approves it. */
export interface Action {
  id: string;
  session_id: string;
  kind: ActionKind;
  status: ActionStatus;
  instruction: string;
  payload: Record<string, unknown>;
  result: {
    message: string;
    /** Opened right after approval: a compose window with the email filled in. */
    open_url?: string;
    /** The same email in each place the user might write mail: gmail | outlook | mail_app. */
    compose_urls?: Record<string, string>;
    drafts_url?: string;
    download_url?: string;
    event_id?: string;
    /** Code runs: what the program printed. */
    stdout?: string;
    stderr?: string;
    exit_code?: number | null;
    timed_out?: boolean;
    truncated?: boolean;
    /** File edits: the file that changed. */
    path?: string;
  } | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface CalendarEvent {
  id: string;
  action_id: string | null;
  title: string;
  start: string;
  end: string;
  location: string;
  notes: string;
}

export interface AutomationInfo {
  email_delivery: "smtp" | "drafts" | "gmail" | "outlook" | "mail_app";
  workflows: string[];
  /** Where phone review reminders go, e.g. "SMS to +84••••••567"; empty = off. */
  notify_channels: string[];
  notify_remind_minutes: number;
  /** A code sandbox is set up, so approved Python can run. */
  code_runs: boolean;
  /** A project folder is set up for the code helper. */
  project: boolean;
}

export type ProviderName = "ollama" | "anthropic" | "claude_cli" | "echo";

export interface ChatConfig {
  provider: ProviderName;
  model: string;
  system_prompt: string;
  temperature: number;
  available_providers: ProviderName[];
  provider_status: Record<string, string>;
  /** The web search engine ("searxng", "brave"); "" = web search is off on the server. */
  web_search: string;
  /** AGENT_MODE: whether the Agents switch is shown ("toggle") or helpers always work. */
  agent_mode: AgentMode;
}

export interface ServiceHealth {
  ok: boolean;
  detail: string;
}

export type VoiceMode = "ptt" | "handsfree" | "wake";

export type VoiceState =
  | "off"
  | "connecting"
  | "wake-listening"
  | "listening"
  | "recording"
  | "transcribing"
  | "thinking"
  | "speaking";

export interface VoicePrefs {
  mode: VoiceMode;
  speakReplies: boolean;
  bargeIn: boolean;
  wakeModel: string;
  wakeThreshold: number;
  followUpSeconds: number;
  /** The language to talk in ("Vietnamese"); empty = STT_LANGUAGE, and the model follows the user. */
  language?: string;
  /** Look questions up on the web before answering. */
  search?: boolean;
}

/** Messages sent by the voice service over /ws/voice. */
export type VoiceServerMessage =
  | { type: "ready"; session_id: string | null; mode: string; tts_engine: string; sample_rate: number }
  | { type: "state"; state: string }
  | { type: "vad"; speaking: boolean }
  | { type: "session"; session_id: string }
  | { type: "transcript"; text: string; language?: string; stt_seconds?: number }
  | { type: "assistant_start"; model?: string }
  | { type: "token"; text: string }
  | { type: "assistant_done"; text: string; message_id?: string; llm_seconds?: number }
  | { type: "speak"; text: string; lang?: string }
  | { type: "tts_done" }
  | { type: "turn_done" }
  | { type: "interrupted" }
  | { type: "error"; message: string };

export type WakeServerMessage =
  | { type: "ready"; model: string; threshold: number }
  | { type: "wake"; model: string; score: number }
  | { type: "score"; score: number }
  | { type: "error"; message: string };

// ---- notifications ------------------------------------------------------------

export type NotificationCategory = "sign_in" | "credentials" | "review" | "task" | "photo_limit" | "token_limit";

export interface AppNotification {
  id: string;
  category: NotificationCategory;
  level: "info" | "warning" | "error";
  title: string;
  body: string;
  /** The chat it's about, if any. */
  session_id: string | null;
  created_at: string;
  read: boolean;
}

/** As streamed live: whether this category pops up in the browser. */
export interface LiveNotification extends AppNotification {
  popup: boolean;
}

export interface ChannelPrefs {
  web: boolean;
  phone: boolean;
}

export interface NotificationSettings {
  categories: (ChannelPrefs & { id: NotificationCategory; label: string; description: string })[];
  /** e.g. ["ntfy push"]; empty = no phone channel set up. */
  phone_channels: string[];
}

export interface AuthStatus {
  /** Reached over the network (HTTPS port), not from the computer running the assistant. */
  remote: boolean;
  /** APP_PASSCODE is set, so other devices can sign in. */
  configured: boolean;
  signed_in: boolean;
  /** The address other devices open (APP_URL), or "". */
  app_url: string;
}

/** A document attached to a message. */
export interface FileRef {
  id: string;
  name: string;
  media_type: string;
  size: number;
}

/** A document uploaded and read by the server, waiting to be sent. */
export interface UploadedFile extends FileRef {
  /** Characters of text read from it. */
  chars: number;
  /** PDF pages, slides or sheets. */
  pages: number | null;
}
