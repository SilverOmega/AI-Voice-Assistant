import { useEffect, useState } from "react";
import { api, CHAT_BASE, VOICE_BASE, WAKEWORD_BASE } from "../lib/api";

export interface HealthState {
  chat: { ok: boolean; detail: string } | null;
  voice: { ok: boolean; detail: string } | null;
  wakeword: { ok: boolean; detail: string } | null;
}

function describe(service: string, body: Record<string, unknown> | null): string {
  if (!body) return "unreachable";
  if (service === "chat") return `${body.provider} · ${body.model} — ${body.detail}`;
  if (service === "voice") {
    const stt = body.stt as { ready: boolean; loading: boolean; error: string | null; detail: { model: string } };
    const tts = body.tts as { ready: boolean; error: string | null; engine: string };
    const sttText = stt.error ? `STT error: ${stt.error}` : stt.ready ? `STT ${stt.detail.model} ready` : "STT loading…";
    const ttsText = tts.error ? `TTS error: ${tts.error}` : `TTS ${tts.engine}${tts.ready ? "" : " loading…"}`;
    return `${sttText} · ${ttsText}${body.chat_service ? "" : " · chat service unreachable"}`;
  }
  return body.error ? String(body.error) : "ready";
}

/** Polls the /health endpoint of each backend service. */
export function useHealth(intervalMs = 15_000): HealthState {
  const [health, setHealth] = useState<HealthState>({ chat: null, voice: null, wakeword: null });

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      const [chat, voice, wakeword] = await Promise.all(
        [CHAT_BASE, VOICE_BASE, WAKEWORD_BASE].map((b) => api.health(b)),
      );
      if (cancelled) return;
      setHealth({
        chat: { ok: chat.ok, detail: describe("chat", chat.body) },
        voice: { ok: voice.ok, detail: describe("voice", voice.body) },
        wakeword: { ok: wakeword.ok, detail: describe("wakeword", wakeword.body) },
      });
    };
    void poll();
    const timer = window.setInterval(poll, intervalMs);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [intervalMs]);

  return health;
}
