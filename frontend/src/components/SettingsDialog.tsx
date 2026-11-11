import { useEffect, useState } from "react";
import { api, auth as authApi } from "../lib/api";
import { PETS, type PetKind } from "../hooks/usePet";
import type { AuthStatus, AutomationInfo, ChannelPrefs, ChatConfig, NotificationSettings, ProviderName, VoicePrefs } from "../lib/types";
import { XIcon } from "./icons";

interface Props {
  open: boolean;
  onClose: () => void;
  prefs: VoicePrefs;
  onPrefs: (changes: Partial<VoicePrefs>) => void;
  auth: AuthStatus | null;
  onSignedOut: () => void;
  pet: PetKind;
  onPet: (pet: PetKind) => void;
}

const PROVIDER_LABELS: Record<ProviderName, string> = {
  ollama: "Ollama (local)",
  anthropic: "Claude (Anthropic API)",
  echo: "Echo (offline test)",
};

export function SettingsDialog({ open, onClose, prefs, onPrefs, auth, onSignedOut, pet, onPet }: Props) {
  const [config, setConfig] = useState<ChatConfig | null>(null);
  const [draft, setDraft] = useState({ provider: "ollama" as ProviderName, model: "", system_prompt: "", temperature: 0.7 });
  const [models, setModels] = useState<string[]>([]);
  const [wakeModels, setWakeModels] = useState<string[]>([]);
  const [status, setStatus] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [automation, setAutomation] = useState<AutomationInfo | null>(null);
  const [phoneTest, setPhoneTest] = useState<string | null>(null);
  const [notify, setNotify] = useState<NotificationSettings | null>(null);
  const [access, setAccess] = useState<string | null>(null);

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
    api.automationInfo().then(setAutomation, () => setAutomation(null));
    api.notificationSettings().then(setNotify, () => setNotify(null));
    setPhoneTest(null);
    setAccess(null);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const testPhone = async () => {
    setPhoneTest("Sending…");
    try {
      const { errors } = await api.testNotification();
      setPhoneTest(errors.length ? errors.join(" ") : "Sent. Check your phone.");
    } catch (err) {
      setPhoneTest((err as Error).message);
    }
  };

  const setChannel = async (id: string, prefs: ChannelPrefs) => {
    setNotify((n) => n && { ...n, categories: n.categories.map((c) => (c.id === id ? { ...c, ...prefs } : c)) });
    try {
      setNotify(await api.updateNotificationSettings({ [id]: prefs }));
    } catch (err) {
      setStatus(`Could not save notification settings: ${(err as Error).message}`);
    }
  };

  const signOut = async () => {
    await authApi.logout().catch(() => {});
    onSignedOut();
  };

  const signOutOthers = async () => {
    if (!confirm("Sign out every other phone, tablet and computer? They'll need the passcode again.")) return;
    try {
      await authApi.signOutOthers();
      setAccess("Done. Every other device needs the passcode again.");
    } catch (err) {
      setAccess((err as Error).message);
    }
  };

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
        <div className="flex items-center justify-between border-b border-slate-200 px-5 pt-[max(1rem,env(safe-area-inset-top))] pb-4 dark:border-slate-800">
          <h2 className="font-semibold">Settings</h2>
          <button onClick={onClose} aria-label="Close settings" className="-mr-1 rounded p-2 hover:bg-slate-100 dark:hover:bg-slate-800">
            <XIcon />
          </button>
        </div>

        <div className="flex-1 space-y-6 overflow-y-auto overscroll-contain px-5 pt-5 pb-[max(1.25rem,env(safe-area-inset-bottom))]">
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
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Pet</h3>
            <label className="block text-sm">
              Pet
              <select className={field} value={pet} onChange={(e) => onPet(e.target.value as PetKind)}>
                {PETS.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                  </option>
                ))}
              </select>
              <span className="mt-1 block text-xs text-slate-500">
                Sits next to the chat. It falls asleep while the assistant is offline; then point at it (or tap it) to
                hear it snore.
              </span>
            </label>
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

          <section className="space-y-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Notifications</h3>
            <p className="text-xs text-slate-500">
              Every notification is kept under the bell. <strong>Browser</strong> also pops it up in open tabs (and on
              the desktop when the tab is in the background); <strong>Phone</strong> sends it to your phone.
            </p>
            {notify && (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-xs text-slate-500">
                    <th className="pb-1 text-left font-medium">When</th>
                    <th className="w-16 pb-1 font-medium">Browser</th>
                    <th className="w-16 pb-1 font-medium">Phone</th>
                  </tr>
                </thead>
                <tbody>
                  {notify.categories.map((c) => (
                    <tr key={c.id} className="border-t border-slate-100 dark:border-slate-800">
                      <td className="py-1.5 pr-2">
                        {c.label}
                        <span className="block text-xs text-slate-500">{c.description}</span>
                      </td>
                      <td className="text-center">
                        <input
                          type="checkbox"
                          aria-label={`${c.label} in the browser`}
                          checked={c.web}
                          onChange={(e) => void setChannel(c.id, { web: e.target.checked, phone: c.phone })}
                          className="accent-indigo-600"
                        />
                      </td>
                      <td className="text-center">
                        <input
                          type="checkbox"
                          aria-label={`${c.label} on the phone`}
                          checked={c.phone}
                          disabled={notify.phone_channels.length === 0}
                          onChange={(e) => void setChannel(c.id, { web: c.web, phone: e.target.checked })}
                          className="accent-indigo-600 disabled:opacity-40"
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {automation?.notify_channels.length ? (
              <>
                <p className="text-sm">
                  Phone notifications go to: <strong>{automation.notify_channels.join(" and ")}</strong>.
                  {automation.notify_remind_minutes > 0 &&
                    ` A task still waiting for review after ${automation.notify_remind_minutes} minutes gets one more reminder.`}
                </p>
                <p className="text-xs text-slate-500">They only inform you. Approving still happens here, so nothing is sent from your phone.</p>
                <div className="flex items-center gap-3">
                  <button
                    onClick={testPhone}
                    className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800"
                  >
                    Send test notification
                  </button>
                  {phoneTest && <span className="text-sm text-slate-600 dark:text-slate-400">{phoneTest}</span>}
                </div>
              </>
            ) : (
              <p className="text-sm text-slate-600 dark:text-slate-400">
                Phone is off. For free push notifications, install the ntfy app and set <code>NTFY_TOPIC</code> in{" "}
                <code>.env</code>; for text messages, set <code>NOTIFY_PHONE</code> and the Twilio settings. Then restart.
                See the README.
              </p>
            )}
          </section>

          <section className="space-y-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Phones and tablets</h3>
            <DeviceAccess auth={auth} onSignOut={signOut} onSignOutOthers={signOutOthers} />
            {access && <p className="text-sm text-slate-600 dark:text-slate-400">{access}</p>}
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

function DeviceAccess(props: { auth: AuthStatus | null; onSignOut: () => void; onSignOutOthers: () => void }) {
  const { auth } = props;
  const button =
    "rounded-lg border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800";
  if (!auth) return <p className="text-sm text-slate-500">Can't check right now.</p>;
  if (auth.remote) {
    return (
      <>
        <p className="text-sm">This device is signed in with the passcode.</p>
        <div className="flex flex-wrap gap-2">
          <button onClick={props.onSignOut} className={button}>
            Sign out
          </button>
          <button onClick={props.onSignOutOthers} className={button}>
            Sign out all other devices
          </button>
        </div>
      </>
    );
  }
  if (!auth.configured) {
    return (
      <p className="text-sm text-slate-600 dark:text-slate-400">
        Off: the assistant only opens on this computer. To use it on an iPhone, iPad or Android phone, run{" "}
        <code>scripts\lan.ps1</code> (it sets <code>APP_PASSCODE</code> and the address in <code>.env</code>), then
        restart. See the README.
      </p>
    );
  }
  const url = auth.app_url || "https://<this computer's IP>:8443";
  return (
    <>
      <p className="text-sm">
        On a phone, tablet or another computer on the same Wi-Fi, open{" "}
        {auth.app_url ? <strong className="break-all">{url}</strong> : <code>{url}</code>} and enter the passcode
        (<code>APP_PASSCODE</code> in <code>.env</code>).
      </p>
      <ul className="list-disc space-y-1 pl-5 text-xs text-slate-500">
        <li>
          The first visit warns about the certificate. Install <code className="break-all">{url}/ca.crt</code> on the
          device to trust it; the README has the steps for iPhone, iPad and Android.
        </li>
        <li>
          To keep it on the home screen: Share, then <em>Add to Home Screen</em> in Safari; or the menu, then{" "}
          <em>Add to Home screen</em> (or <em>Install app</em>) in Chrome and Samsung Internet.
        </li>
      </ul>
      <button onClick={props.onSignOutOthers} className={button}>
        Sign out all other devices
      </button>
    </>
  );
}
