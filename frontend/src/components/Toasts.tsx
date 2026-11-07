import { BellIcon, XIcon } from "./icons";

export interface Toast {
  id: string;
  title: string;
  body: string;
  level?: "info" | "warning" | "error";
  actionLabel?: string;
  onAction?: () => void;
}

const LEVEL_STYLES = {
  info: ["border-indigo-300 dark:border-indigo-500/40", "text-indigo-500"],
  warning: ["border-amber-300 dark:border-amber-500/40", "text-amber-500"],
  error: ["border-red-300 dark:border-red-500/50", "text-red-500"],
};

/** In-app notifications, top right. */
export function Toasts({ toasts, onDismiss }: { toasts: Toast[]; onDismiss: (id: string) => void }) {
  if (toasts.length === 0) return null;
  return (
    <div className="pointer-events-none fixed right-4 top-[max(1rem,env(safe-area-inset-top))] z-50 flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2" aria-live="polite">
      {toasts.slice(0, 4).map((t) => {
        const [border, icon] = LEVEL_STYLES[t.level ?? "warning"];
        return (
          <div
            key={t.id}
            role={t.level === "error" ? "alert" : "status"}
            className={`pointer-events-auto flex gap-3 rounded-xl border bg-white p-3 shadow-lg dark:bg-slate-800 ${border}`}
          >
            <BellIcon width={18} height={18} className={`mt-0.5 shrink-0 ${icon}`} />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium">{t.title}</p>
            <p className="line-clamp-2 text-sm text-slate-600 dark:text-slate-400">{t.body}</p>
            {t.actionLabel && (
              <button
                onClick={() => {
                  t.onAction?.();
                  onDismiss(t.id);
                }}
                className="mt-2 rounded-lg bg-indigo-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-indigo-500"
              >
                {t.actionLabel}
              </button>
            )}
          </div>
          <button onClick={() => onDismiss(t.id)} aria-label="Dismiss" className="self-start rounded p-0.5 pointer-coarse:p-2 text-slate-400 hover:text-slate-700 dark:hover:text-slate-200">
            <XIcon width={14} height={14} />
          </button>
          </div>
        );
      })}
    </div>
  );
}
