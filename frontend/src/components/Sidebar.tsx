import { useState } from "react";
import type { HealthState } from "../hooks/useHealth";
import type { Session } from "../lib/types";
import { GearIcon, PencilIcon, PlusIcon, TrashIcon, XIcon } from "./icons";

interface Props {
  sessions: Session[];
  activeId: string | null;
  health: HealthState;
  open: boolean;
  onClose: () => void;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onSettings: () => void;
}

export function Sidebar(props: Props) {
  const { sessions, activeId, health, open } = props;
  return (
    <>
      {open && <div className="fixed inset-0 z-20 bg-black/40 md:hidden" onClick={props.onClose} />}
      <aside
        className={`fixed inset-y-0 left-0 z-30 flex w-72 flex-col border-r border-slate-200 bg-slate-50 transition-transform dark:border-slate-800 dark:bg-slate-900 md:static md:translate-x-0 ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex items-center justify-between px-4 py-4">
          <h1 className="text-base font-semibold tracking-tight">Local Assistant</h1>
          <button className="rounded p-1 md:hidden" onClick={props.onClose} aria-label="Close sidebar">
            <XIcon />
          </button>
        </div>
        <div className="px-3">
          <button
            onClick={props.onNew}
            className="flex w-full items-center gap-2 rounded-lg border border-slate-300 px-3 py-2 text-sm font-medium hover:bg-white dark:border-slate-700 dark:hover:bg-slate-800"
          >
            <PlusIcon width={16} height={16} /> New chat
          </button>
        </div>
        <nav className="mt-3 flex-1 space-y-0.5 overflow-y-auto px-2 pb-4" aria-label="Conversations">
          {sessions.length === 0 && (
            <p className="px-3 py-2 text-sm text-slate-500">No conversations yet.</p>
          )}
          {sessions.map((s) => (
            <SessionRow
              key={s.id}
              session={s}
              active={s.id === activeId}
              onSelect={() => props.onSelect(s.id)}
              onRename={(t) => props.onRename(s.id, t)}
              onDelete={() => props.onDelete(s.id)}
            />
          ))}
        </nav>
        <div className="border-t border-slate-200 p-3 dark:border-slate-800">
          <div className="mb-2 space-y-1">
            <StatusDot label="Chat" status={health.chat} />
            <StatusDot label="Voice" status={health.voice} />
            <StatusDot label="Wake word" status={health.wakeword} />
          </div>
          <button
            onClick={props.onSettings}
            className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm hover:bg-slate-200 dark:hover:bg-slate-800"
          >
            <GearIcon width={16} height={16} /> Settings
          </button>
        </div>
      </aside>
    </>
  );
}

function StatusDot({ label, status }: { label: string; status: { ok: boolean; detail: string } | null }) {
  const color = status === null ? "bg-slate-400" : status.ok ? "bg-emerald-500" : "bg-amber-500";
  return (
    <div className="flex items-center gap-2 px-3 text-xs text-slate-600 dark:text-slate-400" title={status?.detail}>
      <span className={`h-2 w-2 shrink-0 rounded-full ${color}`} />
      <span className="shrink-0">{label}</span>
      <span className="truncate opacity-70">{status?.detail ?? "checking…"}</span>
    </div>
  );
}

function SessionRow(props: {
  session: Session;
  active: boolean;
  onSelect: () => void;
  onRename: (title: string) => Promise<void>;
  onDelete: () => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(props.session.title);

  const commit = async () => {
    setEditing(false);
    const next = title.trim();
    if (next && next !== props.session.title) await props.onRename(next);
    else setTitle(props.session.title);
  };

  if (editing) {
    return (
      <input
        autoFocus
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") void commit();
          if (e.key === "Escape") {
            setTitle(props.session.title);
            setEditing(false);
          }
        }}
        className="w-full rounded-lg border border-indigo-400 bg-white px-3 py-2 text-sm outline-none dark:bg-slate-800"
      />
    );
  }

  return (
    <div
      className={`group flex items-center rounded-lg text-sm ${
        props.active ? "bg-indigo-100 dark:bg-indigo-500/20" : "hover:bg-slate-200/70 dark:hover:bg-slate-800"
      }`}
    >
      <button onClick={props.onSelect} className="min-w-0 flex-1 truncate px-3 py-2 text-left">
        {props.session.title}
      </button>
      <div className="flex shrink-0 pr-1 opacity-0 group-hover:opacity-100 focus-within:opacity-100">
        <button
          className="rounded p-1 text-slate-500 hover:text-slate-900 dark:hover:text-white"
          onClick={() => {
            setTitle(props.session.title);
            setEditing(true);
          }}
          aria-label="Rename conversation"
        >
          <PencilIcon width={14} height={14} />
        </button>
        <button
          className="rounded p-1 text-slate-500 hover:text-red-600"
          onClick={() => {
            if (confirm(`Delete “${props.session.title}”?`)) void props.onDelete();
          }}
          aria-label="Delete conversation"
        >
          <TrashIcon width={14} height={14} />
        </button>
      </div>
    </div>
  );
}
