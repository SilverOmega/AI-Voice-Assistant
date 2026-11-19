import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { Rates } from "../lib/types";

const OPEN_KEY = "assistant.ratesOpen";

/**
 * Exchange rates and gold prices for the drawer's Rates panel. Nothing is fetched in the
 * background: they're loaded when the panel is open and the drawer is showing (the server keeps
 * them for a few minutes, so reopening is cheap), and again when the refresh button is pressed.
 * Whether the panel is open is kept per device.
 */
export function useRates(visible: boolean) {
  const [open, setOpen] = useState(() => {
    try {
      return localStorage.getItem(OPEN_KEY) === "1";
    } catch {
      return false;
    }
  });
  const [rates, setRates] = useState<Rates | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const latest = useRef(0);

  const load = useCallback(async (refresh = false) => {
    const id = ++latest.current;
    setLoading(true);
    try {
      const next = await api.getRates(refresh);
      if (id !== latest.current) return;
      setRates(next);
      setError(null);
    } catch {
      if (id === latest.current) setError("Couldn't load the rates. Check that the chat service is running.");
    } finally {
      if (id === latest.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (visible && open) void load();
  }, [visible, open, load]);

  const toggle = useCallback(() => {
    setOpen((was) => {
      try {
        localStorage.setItem(OPEN_KEY, was ? "0" : "1");
      } catch {
        /* storage unavailable: the choice just won't persist */
      }
      return !was;
    });
  }, []);

  return { open, toggle, rates, loading, error, refresh: () => load(true) };
}
