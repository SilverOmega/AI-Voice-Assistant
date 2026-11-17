import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";

const SEARCH_KEY = "assistant.webSearch";

/**
 * Whether questions are looked up on the web (links and related topics under replies).
 * On by default; the choice is kept per device. `available` is false when the server has
 * no search engine (WEB_SEARCH=off), null until it has answered.
 */
export function useWebSearch() {
  const [available, setAvailable] = useState<boolean | null>(null);
  const [on, setOn] = useState(() => {
    try {
      return localStorage.getItem(SEARCH_KEY) !== "off";
    } catch {
      return true;
    }
  });

  useEffect(() => {
    api.getConfig().then(
      (config) => setAvailable(Boolean(config.web_search)),
      () => setAvailable(null),
    );
  }, []);

  const toggle = useCallback(() => {
    setOn((was) => {
      try {
        localStorage.setItem(SEARCH_KEY, was ? "off" : "on");
      } catch {
        /* storage unavailable: the choice just won't persist */
      }
      return !was;
    });
  }, []);

  return { on, available, enabled: on && available !== false, toggle };
}
