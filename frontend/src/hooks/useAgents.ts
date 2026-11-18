import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import type { AgentMode } from "../lib/types";

const AGENTS_KEY = "assistant.agents";

/**
 * Whether the helpers (agents) work on messages before the assistant replies. Off by default
 * (they take longer); the choice is kept per device. `mode` is the server's AGENT_MODE:
 * the switch is only shown with "toggle"; with "always" helpers work on every message.
 */
export function useAgents() {
  const [mode, setMode] = useState<AgentMode | null>(null);
  const [on, setOn] = useState(() => {
    try {
      return localStorage.getItem(AGENTS_KEY) === "on";
    } catch {
      return false;
    }
  });

  useEffect(() => {
    api.getConfig().then(
      (config) => setMode(config.agent_mode ?? "off"),
      () => setMode(null),
    );
  }, []);

  const toggle = useCallback(() => {
    setOn((was) => {
      try {
        localStorage.setItem(AGENTS_KEY, was ? "off" : "on");
      } catch {
        /* storage unavailable: the choice just won't persist */
      }
      return !was;
    });
  }, []);

  return { on, mode, available: mode === "toggle", enabled: on && mode === "toggle", toggle };
}

export type AgentsState = ReturnType<typeof useAgents>;
