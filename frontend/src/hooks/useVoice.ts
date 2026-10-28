import { useCallback, useEffect, useRef, useState } from "react";
import { VoiceController } from "../audio/voiceController";
import type { VoicePrefs, VoiceServerMessage, VoiceState } from "../lib/types";

const PREFS_KEY = "assistant.voicePrefs";

export const DEFAULT_PREFS: VoicePrefs = {
  mode: "handsfree",
  speakReplies: true,
  bargeIn: false,
  wakeModel: "hey_jarvis",
  wakeThreshold: 0.5,
  followUpSeconds: 8,
};

export function useVoicePrefs() {
  const [prefs, setPrefs] = useState<VoicePrefs>(() => {
    try {
      return { ...DEFAULT_PREFS, ...JSON.parse(localStorage.getItem(PREFS_KEY) ?? "{}") };
    } catch {
      return DEFAULT_PREFS;
    }
  });
  const update = useCallback((changes: Partial<VoicePrefs>) => {
    setPrefs((p) => {
      const next = { ...p, ...changes };
      try {
        localStorage.setItem(PREFS_KEY, JSON.stringify(next));
      } catch {
        /* storage unavailable: prefs just won't persist */
      }
      return next;
    });
  }, []);
  return [prefs, update] as const;
}

export function useVoice(
  sessionId: string | null,
  prefs: VoicePrefs,
  onMessage: (msg: VoiceServerMessage) => void,
) {
  const [state, setState] = useState<VoiceState>("off");
  const [level, setLevel] = useState(0);
  const [wakeScore, setWakeScore] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  const ctrlRef = useRef<VoiceController | null>(null);
  if (!ctrlRef.current) {
    ctrlRef.current = new VoiceController(prefs, sessionId, {
      onState: (s) => {
        setState(s);
        if (s !== "connecting") setError(null);
      },
      onLevel: setLevel,
      onWakeScore: setWakeScore,
      onMessage: (msg) => {
        if (msg.type === "error") setError(msg.message);
        onMessageRef.current(msg);
      },
      onError: setError,
    });
  }
  const ctrl = ctrlRef.current;

  useEffect(() => ctrl.setSession(sessionId), [ctrl, sessionId]);
  useEffect(() => void ctrl.setPrefs(prefs), [ctrl, prefs]);
  useEffect(() => () => void ctrl.disable(), [ctrl]);

  const toggle = useCallback(async () => {
    setError(null);
    if (ctrl.enabled) await ctrl.disable();
    else await ctrl.enable();
  }, [ctrl]);

  return {
    state,
    level,
    wakeScore,
    error,
    enabled: state !== "off",
    toggle,
    pttDown: () => ctrl.pttDown(),
    pttUp: () => ctrl.pttUp(),
    interrupt: () => ctrl.interrupt(),
    wakeNow: () => ctrl.wakeNow(),
  };
}
