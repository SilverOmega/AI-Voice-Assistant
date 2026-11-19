import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { Rates } from "../lib/types";

/**
 * Exchange rates and gold prices for the Rates dashboard. Nothing is fetched in the background:
 * they're loaded each time the dashboard opens (the server keeps them for a few minutes, so
 * reopening is cheap), and again when the refresh button is pressed.
 */
export function useRates(active: boolean) {
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
    if (active) void load();
  }, [active, load]);

  return { rates, loading, error, refresh: () => load(true) };
}
