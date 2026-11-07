import { useState } from "react";
import { ApiError, auth } from "../lib/api";
import { deviceId } from "../hooks/useNotifications";

interface Props {
  /** APP_PASSCODE is set; without it other devices can't get in at all. */
  configured: boolean;
  onSignedIn: () => void;
}

/** Shown on phones, tablets and other computers until they sign in. */
export function SignIn({ configured, onSignedIn }: Props) {
  const [passcode, setPasscode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!passcode.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      await auth.login(passcode.trim(), deviceId());
      onSignedIn();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Can't reach the assistant. Is the computer on?");
      setPasscode("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="safe-x flex min-h-full items-center justify-center bg-slate-50 px-4 py-10 dark:bg-slate-950">
      <div className="w-full max-w-sm rounded-2xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <img src="/favicon.svg" alt="" className="mx-auto h-12 w-12" />
        <h1 className="mt-4 text-center text-lg font-semibold">Local Assistant</h1>
        {configured ? (
          <form onSubmit={submit} className="mt-6 space-y-3">
            <label className="block text-sm">
              Passcode
              <input
                type="password"
                name="password"
                autoComplete="current-password"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                autoFocus
                value={passcode}
                onChange={(e) => setPasscode(e.target.value)}
                className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 dark:border-slate-700 dark:bg-slate-800"
              />
            </label>
            {error && (
              <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-900/30 dark:text-red-300">
                {error}
              </p>
            )}
            <button
              type="submit"
              disabled={busy || !passcode.trim()}
              className="w-full rounded-lg bg-indigo-600 px-4 py-2.5 font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
            >
              {busy ? "Signing in…" : "Sign in"}
            </button>
            <p className="text-xs text-slate-500">
              The passcode is <code>APP_PASSCODE</code> in the <code>.env</code> file on the computer running the
              assistant. This device stays signed in for 90 days.
            </p>
          </form>
        ) : (
          <p className="mt-4 text-sm text-slate-600 dark:text-slate-400">
            This assistant only opens on the computer it runs on for now. To use it on phones and tablets, run{" "}
            <code>scripts\lan.ps1</code> on that computer (or set <code>APP_PASSCODE</code> in <code>.env</code>) and
            restart it.
          </p>
        )}
      </div>
    </div>
  );
}
