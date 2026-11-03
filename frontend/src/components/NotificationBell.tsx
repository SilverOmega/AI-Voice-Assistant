import { useEffect, useRef, useState } from "react";
import { timeAgo } from "../lib/notify";
import type { AppNotification } from "../lib/types";
import { BellIcon } from "./icons";

interface Props {
  items: AppNotification[];
  unread: number;
  onMarkRead: (ids?: string[]) => void;
  onClear: () => void;
  onOpenChat: (sessionId: string) => void;
}

export const LEVEL_DOT: Record<AppNotification["level"], string> = {
  info: "bg-indigo-500",
  warning: "bg-amber-500",
  error: "bg-red-500",
};

/** The header bell: unread count, and the latest notifications from every device and service. */
export function NotificationBell({ items, unread, onMarkRead, onClear, onOpenChat }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => ref.current?.contains(e.target as Node) || setOpen(false);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const select = (n: AppNotification) => {
    if (!n.read) onMarkRead([n.id]);
    if (n.session_id) {
      onOpenChat(n.session_id);
      setOpen(false);
    }
  };

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}
        aria-expanded={open}
        className="relative rounded-lg p-1.5 text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
      >
        <BellIcon width={20} height={20} />
        {unread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 min-w-[1.1rem] rounded-full bg-red-600 px-1 text-center text-[10px] font-semibold leading-[1.1rem] text-white">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>

      {open && (
        <div className="fixed inset-x-2 top-14 z-50 flex max-h-[70vh] flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xl sm:absolute sm:inset-x-auto sm:right-0 sm:top-10 sm:w-96 dark:border-slate-700 dark:bg-slate-900">
          <div className="flex items-center gap-2 border-b border-slate-200 px-3 py-2 dark:border-slate-700">
            <h3 className="flex-1 text-sm font-semibold">Notifications</h3>
            <button
              onClick={() => onMarkRead()}
              disabled={unread === 0}
              className="rounded px-2 py-1 text-xs text-indigo-600 hover:bg-slate-100 disabled:opacity-40 dark:text-indigo-400 dark:hover:bg-slate-800"
            >
              Mark all read
            </button>
            <button
              onClick={onClear}
              disabled={items.length === 0}
              className="rounded px-2 py-1 text-xs text-slate-500 hover:bg-slate-100 disabled:opacity-40 dark:hover:bg-slate-800"
            >
              Clear
            </button>
          </div>
          {items.length === 0 ? (
            <p className="px-4 py-8 text-center text-sm text-slate-500">Nothing yet.</p>
          ) : (
            <ul className="overflow-y-auto">
              {items.map((n) => (
                <li key={n.id}>
                  <button
                    onClick={() => select(n)}
                    className={`flex w-full gap-3 border-b border-slate-100 px-3 py-2.5 text-left hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/60 ${
                      n.read ? "" : "bg-indigo-50/60 dark:bg-indigo-500/10"
                    }`}
                  >
                    <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${LEVEL_DOT[n.level]}`} />
                    <span className="min-w-0 flex-1">
                      <span className={`block text-sm ${n.read ? "" : "font-semibold"}`}>{n.title}</span>
                      <span className="block text-sm text-slate-600 dark:text-slate-400">{n.body}</span>
                      <span className="mt-0.5 block text-xs text-slate-400">
                        {timeAgo(n.created_at)}
                        {n.session_id && " · open chat"}
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
