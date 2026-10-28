export type Role = "user" | "assistant" | "system";
export type Source = "text" | "voice";

export interface ImageRef {
  id: string;
  media_type: string;
}

export interface Message {
  id: string;
  session_id: string;
  role: Role;
  content: string;
  source: Source;
  created_at: string;
  images?: ImageRef[];
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
}

export type ProviderName = "ollama" | "anthropic" | "echo";

export interface ChatConfig {
  provider: ProviderName;
  model: string;
  system_prompt: string;
  temperature: number;
  available_providers: ProviderName[];
  provider_status: Record<string, string>;
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
  | { type: "speak"; text: string }
  | { type: "tts_done" }
  | { type: "turn_done" }
  | { type: "interrupted" }
  | { type: "error"; message: string };

export type WakeServerMessage =
  | { type: "ready"; model: string; threshold: number }
  | { type: "wake"; model: string; score: number }
  | { type: "score"; score: number }
  | { type: "error"; message: string };
