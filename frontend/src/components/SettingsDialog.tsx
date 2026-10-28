import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { ChatConfig, ProviderName, VoicePrefs } from "../lib/types";
import { XIcon } from "./icons";

interface Props {
  open: boolean;
  onClose: () => void;
  prefs: VoicePrefs;
  onPrefs: (changes: Partial<VoicePrefs>) => void;
}

const PROVIDER_LABELS: Record<ProviderName, string> = {
  ollama: "Ollama (local)",
  anthropic: "Claude (Anthropic API)",
  echo: "Echo (offline test)",
};

export function SettingsDialog({ open, onClose, prefs, onPrefs }: Props) {
  const [config, setConfig] = useState<ChatConfig | null>(null);
  const [draft, setDraft] = useState({ provider: "ollama" as ProviderName, model: "", system_prompt: "", temperature: 0.7 });
  const [models, setModels] = useState<string[]>([]);
  const [wakeModels, setWakeModels] = useState<string[]>([]);
  const [status, setStatus] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    setStatus(null);
    api
      .getConfig()
      .then((c) => {
        setConfig(c);
        setDraft({ provider: c.provider, model: c.model, system_prompt: c.system_prompt, temperature: c.temperature });
      })
      .catch((e) => setStatus(`Could not load chat settings: ${e.message}`));
    api.listModels().then((m) => setModels(m.models)).catch(() => setModels([]));
    api.wakeModels().then((m) => setWakeModels(m.models)).catch(() => setWakeModels([]));
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const save = async () => {
    setSaving(true);
    setStatus(null);
    try {
      const providerChanged = draft.provider !== config?.provider;
      const updated = await api.updateConfig({
        provider: draft.provider,
        // Switching provider with an untouched model name -> let the server pick its default.
        ...(providerChanged && draft.model === config?.model ? {} : { model: draft.model }),
        system_prompt: draft.system_prompt,
        temperature: draft.temperature,
      });
      setConfig(updated);
      setDraft((d) => ({ ...d, model: updated.model }));
      setStatus(`Saved. ${updated.provider_status[updated.provider] ?? ""}`);
      api.listModels().then((m) => setModels(m.models)).catch(() => setModels([]));
    } catch (e) {
      setStatus(`Save failed: ${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  const field = "mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-800";

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/40" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Settings"
        className="flex h-full w-full max-w-md flex-col bg-white shadow-xl dark:bg-slate-900"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4 dark:border-slate-800">
          <h2 className="font-semibold">Settings</h2>
          <button onClick={onClose} aria-label="Close settings" className="rounded p-1 hover:bg-slate-100 dark:hover:bg-slate-800">
            <XIcon />
          </button>
        </div>

        <div className="flex-1 space-y-6 overflow-y-auto px-5 py-5">
          <section className="space-y-3">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Language model</h3>
            <label className="block text-sm">
              Provider
              <select
                className={field}
                value={draft.provider}
                onChange={(e) => setDraft({ ...draft, provider: e.target.value as ProviderName })}
              >
                {(config?.available_providers ?? ["ollama", "anthropic", "echo"]).map((p) => (
                  <option key={p} value={p}>
                    {PROVIDER_LABELS[p]}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-sm">
              Model
              <input
                className={field}
                list="model-options"
                value={draft.model}
                placeholder="provider default"
                onChange={(e) => setDraft({ ...draft, model: e.target.value })}
              />
              <datalist id="model-options">
                {models.map((m) => (
                  <option key={m} value={m} />
                ))}
              </datalist>
              {draft.provider === "ollama" && (
                <span className="mt-1 block text-xs text-slate-500">
                  Pulled models appear as suggestions. Pull new ones with <code>ollama pull &lt;name&gt;</code>.
                </span>
              )}
            </label>
            <label className="block text-sm">
              System prompt
              <textarea
                className={`${field} h-32 resize-y`}
                value={draft.system_prompt}
                onChange={(e) => setDraft({ ...draft, system_prompt: e.target.value })}
              />
            </label>
            {draft.provider !== "anthropic" && (
              <label className="block text-sm">
                Temperature: {draft.temperature.toFixed(1)}
                <input
                  type="range"
                  min={0}
                  max={2}
                  step={0.1}
                  value={draft.temperature}
                  onChange={(e) => setDraft({ ...draft, temperature: Number(e.target.value) })}
                  className="mt-1 w-full accent-indigo-600"
                />
              </label>
            )}
            <div className="flex items-center gap-3">
              <button
                onClick={save}
                disabled={saving}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
              >
                {saving ? "Saving…" : "Save model settings"}
              </button>
              {status && <p className="text-xs text-slate-600 dark:text-slate-400">{status}</p>}
            </div>
          </section>

          <section className="space-y-3">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Voice</h3>
            <Toggle
              label="Speak replies aloud"
              checked={prefs.speakReplies}
              onChange={(v) => onPrefs({ speakReplies: v })}
            />
            <Toggle
              label="Allow interrupting the assistant by talking (barge-in)"
              hint="Best with headphones; with speakers the assistant may hear itself."
              checked={prefs.bargeIn}
              onChange={(v) => onPrefs({ bargeIn: v })}
            />
          </section>

          <section className="space-y-3">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Wake word</h3>
            <label className="block text-sm">
              Wake phrase
              <select className={field} value={prefs.wakeModel} onChange={(e) => onPrefs({ wakeModel: e.target.value })}>
                {(wakeModels.length ? wakeModels : [prefs.wakeModel]).map((m) => (
                  <option key={m} value={m}>
                    {m.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-sm">
              Sensitivity threshold: {prefs.wakeThreshold.toFixed(2)}
              <input
                type="range"
                min={0.2}
                max={0.9}
                step={0.05}
                value={prefs.wakeThreshold}
                onChange={(e) => onPrefs({ wakeThreshold: Number(e.target.value) })}
                className="mt-1 w-full accent-indigo-600"
              />
              <span className="block text-xs text-slate-500">Lower = triggers more easily (more false wakes).</span>
            </label>
            <label className="block text-sm">
              Follow-up window: {prefs.followUpSeconds}s
              <input
                type="range"
                min={3}
                max={30}
                step={1}
                value={prefs.followUpSeconds}
                onChange={(e) => onPrefs({ followUpSeconds: Number(e.target.value) })}
                className="mt-1 w-full accent-indigo-600"
              />
              <span className="block text-xs text-slate-500">
                How long to keep listening for a follow-up before waiting for the wake word again.
              </span>
            </label>
          </section>
        </div>
      </div>
    </div>
  );
}

function Toggle(props: { label: string; hint?: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex cursor-pointer items-start gap-3 text-sm">
      <input
        type="checkbox"
        checked={props.checked}
        onChange={(e) => props.onChange(e.target.checked)}
        className="mt-0.5 accent-indigo-600"
      />
      <span>
        {props.label}
        {props.hint && <span className="block text-xs text-slate-500">{props.hint}</span>}
      </span>
    </label>
  );
}
