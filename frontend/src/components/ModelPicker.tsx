import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api, CHAT_BASE, type ModelList } from "../lib/api";
import { announceConfig, CONFIG_EVENT, modelLabel, PROVIDER_LABELS } from "../lib/models";
import type { ChatConfig, ProviderName } from "../lib/types";
import { ChipIcon } from "./icons";

interface Props {
  /** A reply is being written: switching waits until it's done. */
  disabled?: boolean;
  onError: (message: string) => void;
}

type Lists = Partial<Record<ProviderName, ModelList>>;

/** Ollama treats "llama3.2" and "llama3.2:latest" as the same model. */
const sameModel = (a: string, b: string) => a.replace(/:latest$/, "") === b.replace(/:latest$/, "");

/**
 * Next to the microphone: shows the model in use and switches provider and model in one click.
 * It never leaves the assistant on a model that can't answer: Claude asks for its API key first,
 * and a switch to anything that turns out not to be ready goes back to the previous model.
 */
export function ModelPicker({ disabled = false, onError }: Props) {
  const [config, setConfig] = useState<ChatConfig | null>(null);
  const [open, setOpen] = useState(false);
  const [lists, setLists] = useState<Lists>({});
  const [loading, setLoading] = useState(false);
  const [switching, setSwitching] = useState<string | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.getConfig().then(setConfig, () => setConfig(null));
    const onChange = (e: Event) => setConfig((e as CustomEvent<ChatConfig>).detail);
    window.addEventListener(CONFIG_EVENT, onChange);
    return () => window.removeEventListener(CONFIG_EVENT, onChange);
  }, []);

  // Close on a click or tap anywhere else.
  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, [open]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const current = await api.getConfig();
      setConfig(current);
      const entries = await Promise.all(
        current.available_providers.map(async (p) => {
          if (p === "echo") return [p, { provider: p, default: "echo", models: ["echo"] }] as const;
          try {
            return [p, await api.listModels(p)] as const;
          } catch {
            return [p, { provider: p, default: "", models: [] }] as const;
          }
        }),
      );
      setLists(Object.fromEntries(entries));
    } catch (err) {
      onError(`Couldn't load the models: ${(err as Error).message}`);
      setOpen(false);
    } finally {
      setLoading(false);
    }
  }, [onError]);

  const toggle = () => {
    if (!open) void load();
    setOpen((o) => !o);
  };

  const choose = async (provider: ProviderName, model: string) => {
    if (!config) return;
    if (provider === config.provider && (provider === "echo" || sameModel(model, config.model))) {
      setOpen(false);
      return;
    }
    const previous = { provider: config.provider, model: config.model };
    const label = modelLabel(provider, model);
    setSwitching(`${provider}/${model}`);
    try {
      const updated = await api.updateConfig({ provider, model: provider === "echo" ? "" : model });
      // Check it can answer before keeping it (model not pulled, key rejected...).
      const health = await api.health(CHAT_BASE);
      if (health.body && health.body.provider_ok === false) {
        const restored = await api.updateConfig(previous);
        setConfig(restored);
        announceConfig(restored);
        onError(
          `${label} isn't ready (${health.body.detail}), so the assistant is still using ` +
            `${modelLabel(restored.provider, restored.model)}.`,
        );
        return;
      }
      setConfig(updated);
      announceConfig(updated);
      setOpen(false);
    } catch (err) {
      onError(`Couldn't switch to ${label}: ${(err as Error).message}`);
    } finally {
      setSwitching(null);
    }
  };

  const current = config
    ? modelLabel(config.provider, config.model) + (config.provider === "claude_cli" ? " · plan" : "")
    : "Model";

  return (
    <div ref={boxRef} className="relative" onKeyDown={(e) => e.key === "Escape" && setOpen(false)}>
      <button
        onClick={toggle}
        disabled={disabled || !config}
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label={`Language model: ${current}`}
        title={disabled ? "Switch the model when the reply is done" : `Language model: ${current}. Click to change.`}
        className="flex max-w-[11rem] items-center gap-1 rounded-xl p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-600 disabled:opacity-40 dark:hover:bg-slate-800 dark:hover:text-slate-300"
      >
        <ChipIcon className="shrink-0" />
        <span className="hidden truncate text-xs font-medium sm:inline">{current}</span>
      </button>
      {open && (
        <div
          role="menu"
          aria-label="Choose a language model"
          className="absolute bottom-full right-0 z-10 mb-2 max-h-[min(26rem,65vh)] w-72 overflow-y-auto rounded-xl border border-slate-200 bg-white p-1 shadow-lg sm:left-0 sm:right-auto dark:border-slate-700 dark:bg-slate-800"
        >
          {loading && Object.keys(lists).length === 0 ? (
            <p className="flex items-center gap-2 px-3 py-2 text-sm text-slate-500">
              <span className="block h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
              Loading models…
            </p>
          ) : (
            (config?.available_providers ?? []).map((provider) => {
              const list = lists[provider];
              const models = list?.models ?? [];
              const needsKey = provider === "anthropic" && list?.key_source === null;
              const notReady = provider === "claude_cli" && list?.ready === false;
              return (
                <section key={provider} className="py-1">
                  <h3 className="px-3 pb-1 pt-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                    {PROVIDER_LABELS[provider] ?? provider}
                  </h3>
                  {needsKey && <ClaudeKeyForm canAdd={list?.can_add_key ?? false} onAdded={load} />}
                  {notReady && <BridgeNotReady detail={list?.detail ?? ""} />}
                  {needsKey ? null : models.length === 0 ? (
                    <p className="px-3 py-1.5 text-xs text-slate-500">
                      {provider === "ollama" ? (
                        <>
                          No models found. Pull one with <code>ollama pull llama3.2</code>.
                        </>
                      ) : (
                        "Not available."
                      )}
                    </p>
                  ) : (
                    models.map((model) => {
                      const active =
                        config?.provider === provider && (provider === "echo" || sameModel(config.model, model));
                      const busy = switching === `${provider}/${model}`;
                      return (
                        <button
                          key={model}
                          role="menuitemradio"
                          aria-checked={active}
                          disabled={switching !== null || needsKey || notReady}
                          title={needsKey ? "Add a Claude API key first" : notReady ? "Set up Claude Code first" : model}
                          onClick={() => void choose(provider, model)}
                          className={`flex w-full items-center gap-2 rounded-lg px-3 py-1.5 text-left text-sm hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent dark:hover:bg-slate-700 ${
                            active ? "font-medium text-indigo-600 dark:text-indigo-300" : ""
                          }`}
                        >
                          <span className="w-4 shrink-0 text-center">
                            {busy ? (
                              <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
                            ) : active ? (
                              "✓"
                            ) : (
                              ""
                            )}
                          </span>
                          <span className="min-w-0 truncate">{modelLabel(provider, model)}</span>
                        </button>
                      );
                    })
                  )}
                  {provider === "anthropic" && list?.key_source === "app" && list.can_add_key && (
                    <RemoveClaudeKey inUse={config?.provider === "anthropic"} onRemoved={load} onError={onError} />
                  )}
                </section>
              );
            })
          )}
          <p className="border-t border-slate-100 px-3 pb-1 pt-2 text-[11px] text-slate-500 dark:border-slate-700">
            Applies to every chat on every device. Claude sends your messages to Anthropic; Ollama keeps them here.
            Claude with your subscription uses your plan's limits.
          </p>
        </div>
      )}
    </div>
  );
}

/** Claude with your subscription needs the bridge running and Claude Code signed in. */
function BridgeNotReady({ detail }: { detail: string }) {
  const text = detail ? detail[0].toUpperCase() + detail.slice(1) : "Claude Code isn't reachable";
  return (
    <p className="mx-2 mb-1 rounded-lg bg-amber-50 px-2 py-1.5 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-200">
      {text}. Then open this menu again.
    </p>
  );
}

/** Claude has no API key yet: paste one here (only on the computer running the assistant). */
function ClaudeKeyForm({ canAdd, onAdded }: { canAdd: boolean; onAdded: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  if (!open) {
    return (
      <p className="px-3 py-1 text-xs text-slate-500">
        Only if you pay for the Claude API.{" "}
        {canAdd ? (
          <button type="button" onClick={() => setOpen(true)} className="font-medium text-indigo-600 hover:underline dark:text-indigo-300">
            Add an API key
          </button>
        ) : (
          "Add the key from the computer running the assistant."
        )}
      </p>
    );
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setProblem(null);
    try {
      await api.setClaudeKey(key.trim());
      setKey("");
      await onAdded();
    } catch (err) {
      setProblem((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={submit} className="mx-2 mb-1 space-y-1.5 rounded-lg bg-amber-50 p-2 dark:bg-amber-500/10">
      <label htmlFor="claude-key" className="block text-xs text-amber-900 dark:text-amber-100">
        Claude needs an API key. Create one at{" "}
        <a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noreferrer" className="underline">
          console.anthropic.com
        </a>{" "}
        and paste it here.
      </label>
      <div className="flex gap-1.5">
        <input
          id="claude-key"
          type="password"
          autoComplete="off"
          spellCheck={false}
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder="sk-ant-…"
          className="min-w-0 flex-1 rounded-md border border-amber-300 bg-white px-2 py-1 font-mono text-xs outline-none focus:border-indigo-400 dark:border-amber-500/40 dark:bg-slate-900"
        />
        <button
          type="submit"
          disabled={saving || key.trim().length < 20}
          className="rounded-md bg-indigo-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
        >
          {saving ? "Checking…" : "Add"}
        </button>
      </div>
      {problem && <p role="alert" className="text-xs text-red-700 dark:text-red-300">{problem}</p>}
      <p className="text-[11px] text-amber-800/80 dark:text-amber-200/70">
        It's checked with Anthropic, then kept on this computer. It's never shown again.
      </p>
    </form>
  );
}

function RemoveClaudeKey({
  inUse,
  onRemoved,
  onError,
}: {
  inUse: boolean;
  onRemoved: () => Promise<void>;
  onError: (message: string) => void;
}) {
  const remove = async () => {
    try {
      await api.removeClaudeKey();
      await onRemoved();
    } catch (err) {
      onError(`Couldn't remove the key: ${(err as Error).message}`);
    }
  };
  return (
    <button
      onClick={() => void remove()}
      disabled={inUse}
      title={inUse ? "Switch to another model before removing the key" : undefined}
      className="mx-3 mt-0.5 text-[11px] text-slate-500 underline-offset-2 hover:underline disabled:no-underline disabled:opacity-50"
    >
      Remove the Claude key added here
    </button>
  );
}
