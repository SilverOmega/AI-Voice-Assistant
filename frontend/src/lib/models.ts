import type { ChatConfig, ProviderName } from "./types";

export const PROVIDER_LABELS: Record<ProviderName, string> = {
  ollama: "On this computer (Ollama)",
  anthropic: "Claude (API key)",
  claude_cli: "Claude (your subscription)",
  echo: "Test mode (no model)",
};

/** A model name as people read it: "llama3.2", "Claude Sonnet 5.5", "Echo". */
export function modelLabel(provider: ProviderName, model: string): string {
  if (provider === "echo") return "Echo";
  const name = model.replace(/:latest$/, "");
  const claude = /^claude-([a-z]+)-(\d+)-(\d+)/.exec(name);
  if (claude) return `Claude ${claude[1][0].toUpperCase()}${claude[1].slice(1)} ${claude[2]}.${claude[3]}`;
  return name;
}

/** Fired when the provider or model changes, so every control showing it stays in step. */
export const CONFIG_EVENT = "assistant:config";

export function announceConfig(config: ChatConfig): void {
  window.dispatchEvent(new CustomEvent<ChatConfig>(CONFIG_EVENT, { detail: config }));
}
