import { useEffect } from "react";
import type { VoiceMode, VoicePrefs, VoiceState } from "../lib/types";
import { MicIcon, StopIcon } from "./icons";

interface Props {
  prefs: VoicePrefs;
  onPrefs: (changes: Partial<VoicePrefs>) => void;
  state: VoiceState;
  level: number;
  wakeScore: number;
  error: string | null;
  enabled: boolean;
  onToggle: () => void;
  onPttDown: () => void;
  onPttUp: () => void;
  onInterrupt: () => void;
  onWakeNow: () => void;
}

const MODES: { id: VoiceMode; label: string; hint: string }[] = [
  { id: "ptt", label: "Push to talk", hint: "Hold the button (or Space) while speaking" },
  { id: "handsfree", label: "Hands-free", hint: "Just talk; pauses end your turn" },
  { id: "wake", label: "Wake word", hint: "Say the wake phrase to start" },
];

const STATE_TEXT: Record<VoiceState, string> = {
  off: "Voice off",
  connecting: "Connecting…",
  "wake-listening": "Waiting for wake word",
  listening: "Listening",
  recording: "Hearing you…",
  transcribing: "Transcribing…",
  thinking: "Thinking…",
  speaking: "Speaking",
};

const ORB_COLOR: Record<VoiceState, string> = {
  off: "bg-slate-200 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
  connecting: "bg-slate-300 text-slate-700 dark:bg-slate-700 dark:text-slate-200",
  "wake-listening": "bg-violet-600 text-white",
  listening: "bg-emerald-600 text-white",
  recording: "bg-red-500 text-white",
  transcribing: "bg-amber-500 text-white",
  thinking: "bg-amber-500 text-white",
  speaking: "bg-indigo-600 text-white",
};

function wakeLabel(model: string) {
  return model.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function VoiceBar(props: Props) {
  const { prefs, state, enabled } = props;
  const ptt = prefs.mode === "ptt";

  // Space bar = push-to-talk when voice is on and focus is not in a text field.
  useEffect(() => {
    if (!enabled || !ptt) return;
    const isTyping = (e: KeyboardEvent) =>
      e.target instanceof HTMLElement && e.target.closest("input, textarea, select, [contenteditable]");
    const down = (e: KeyboardEvent) => {
      if (e.code !== "Space" || e.repeat || isTyping(e)) return;
      e.preventDefault();
      props.onPttDown();
    };
    const up = (e: KeyboardEvent) => {
      if (e.code !== "Space" || isTyping(e)) return;
      e.preventDefault();
      props.onPttUp();
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
    };
  }, [enabled, ptt, props.onPttDown, props.onPttUp]);

  const pttHandlers =
    enabled && ptt
      ? {
          onPointerDown: (e: React.PointerEvent) => {
            (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
            props.onPttDown();
          },
          onPointerUp: props.onPttUp,
          onPointerCancel: props.onPttUp,
        }
      : {};

  const busy = state === "speaking" || state === "thinking" || state === "transcribing";
  const statusText =
    state === "wake-listening" ? `Say “${wakeLabel(prefs.wakeModel)}”` : STATE_TEXT[state];

  return (
    <div className="mx-auto w-full max-w-3xl px-4 pb-2">
      <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-slate-200 bg-slate-50 px-3 py-2 dark:border-slate-800 dark:bg-slate-900/60">
        <button
          onClick={enabled && ptt ? undefined : props.onToggle}
          {...pttHandlers}
          aria-label={enabled ? (ptt ? "Hold to talk" : "Turn voice off") : "Turn voice on"}
          title={enabled ? (ptt ? "Hold to talk" : "Turn voice off") : "Turn voice on"}
          className={`relative flex h-12 w-12 shrink-0 touch-none select-none items-center justify-center rounded-full transition-colors ${ORB_COLOR[state]} ${
            state === "recording" ? "pulse-ring" : ""
          }`}
          style={
            state === "listening" || state === "recording"
              ? { boxShadow: `0 0 0 ${Math.min(14, props.level * 160)}px rgb(16 185 129 / 0.25)` }
              : undefined
          }
        >
          <span className="relative">
            <MicIcon />
          </span>
        </button>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 text-sm font-medium">
            <span>{statusText}</span>
            {state === "wake-listening" && (
              <span className="h-1.5 w-16 overflow-hidden rounded bg-slate-200 dark:bg-slate-700" title="Wake-word confidence">
                <span
                  className="block h-full bg-violet-500 transition-[width]"
                  style={{ width: `${Math.round(props.wakeScore * 100)}%` }}
                />
              </span>
            )}
          </div>
          <p className="truncate text-xs text-slate-500">
            {props.error ? (
              <span className="text-amber-600 dark:text-amber-400">{props.error}</span>
            ) : enabled ? (
              MODES.find((m) => m.id === prefs.mode)?.hint
            ) : (
              "Click the mic to start a voice conversation"
            )}
          </p>
        </div>

        <div className="flex items-center gap-2">
          {enabled && busy && (
            <button
              onClick={props.onInterrupt}
              className="flex items-center gap-1 rounded-lg border border-slate-300 px-2 py-1 text-xs hover:bg-white dark:border-slate-700 dark:hover:bg-slate-800"
            >
              <StopIcon width={12} height={12} /> Stop
            </button>
          )}
          {enabled && state === "wake-listening" && (
            <button
              onClick={props.onWakeNow}
              className="rounded-lg border border-slate-300 px-2 py-1 text-xs hover:bg-white dark:border-slate-700 dark:hover:bg-slate-800"
            >
              Talk now
            </button>
          )}
          {enabled && ptt && (
            <button
              onClick={props.onToggle}
              className="rounded-lg border border-slate-300 px-2 py-1 text-xs hover:bg-white dark:border-slate-700 dark:hover:bg-slate-800"
            >
              Voice off
            </button>
          )}
          <div role="radiogroup" aria-label="Voice mode" className="flex rounded-lg bg-slate-200 p-0.5 text-xs dark:bg-slate-800">
            {MODES.map((m) => (
              <button
                key={m.id}
                role="radio"
                aria-checked={prefs.mode === m.id}
                onClick={() => props.onPrefs({ mode: m.id })}
                title={m.hint}
                className={`rounded-md px-2 py-1 ${
                  prefs.mode === m.id ? "bg-white shadow-sm dark:bg-slate-600" : "text-slate-600 dark:text-slate-400"
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>
          <label className="flex cursor-pointer items-center gap-1 text-xs text-slate-600 dark:text-slate-400">
            <input
              type="checkbox"
              checked={prefs.speakReplies}
              onChange={(e) => props.onPrefs({ speakReplies: e.target.checked })}
              className="accent-indigo-600"
            />
            Speak replies
          </label>
        </div>
      </div>
    </div>
  );
}
