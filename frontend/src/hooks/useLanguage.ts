import { useCallback, useState } from "react";
import { browserLanguage } from "../lib/languages";

export interface LanguagePrefs {
  /** The language the assistant replies (and talks) in; "" = the same language as the user. */
  reply: string;
  /** The language "Translate" turns messages into, for whoever reads them on this device. */
  translateTo: string;
}

const LANGUAGE_KEY = "assistant.language";

/** Kept per device: two people following the same chat can each read it in their own language. */
export function useLanguagePrefs() {
  const [prefs, setPrefs] = useState<LanguagePrefs>(() => {
    const defaults: LanguagePrefs = { reply: "", translateTo: browserLanguage() };
    try {
      const saved = JSON.parse(localStorage.getItem(LANGUAGE_KEY) ?? "{}");
      return {
        reply: typeof saved.reply === "string" ? saved.reply : defaults.reply,
        translateTo: typeof saved.translateTo === "string" && saved.translateTo ? saved.translateTo : defaults.translateTo,
      };
    } catch {
      return defaults;
    }
  });
  const update = useCallback((changes: Partial<LanguagePrefs>) => {
    setPrefs((p) => {
      const next = { ...p, ...changes };
      try {
        localStorage.setItem(LANGUAGE_KEY, JSON.stringify(next));
      } catch {
        /* storage unavailable: the choice just won't persist */
      }
      return next;
    });
  }, []);
  return [prefs, update] as const;
}
