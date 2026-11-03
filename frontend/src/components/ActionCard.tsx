import { useState, type ReactNode } from "react";
import { api } from "../lib/api";
import type { Action, ActionKind, AutomationInfo } from "../lib/types";
import { BoltIcon, CalendarIcon, DownloadIcon, FileIcon, MailIcon } from "./icons";

export const KIND_LABELS: Record<ActionKind, { title: string; icon: typeof MailIcon }> = {
  email: { title: "Email", icon: MailIcon },
  event: { title: "Calendar event", icon: CalendarIcon },
  document: { title: "Document", icon: FileIcon },
  workflow: { title: "Workflow", icon: BoltIcon },
};

interface Props {
  action: Action;
  info: AutomationInfo | null;
  onApprove: (id: string, payload: Record<string, unknown>) => Promise<unknown>;
  onReject: (id: string) => Promise<void>;
}

type Fields = Record<string, string>;

/** Editable form values: address lists become comma-separated text. */
function toFields(payload: Record<string, unknown>): Fields {
  return Object.fromEntries(
    Object.entries(payload).map(([k, v]) => [k, Array.isArray(v) ? v.join(", ") : String(v ?? "")]),
  );
}

function toPayload(kind: ActionKind, fields: Fields): Record<string, unknown> {
  if (kind !== "email") return fields;
  const list = (s = "") => s.split(/[,;\s]+/).filter(Boolean);
  return { ...fields, to: list(fields.to), cc: list(fields.cc) };
}

const COMPOSE_LABELS: Record<string, string> = { gmail: "Gmail", outlook: "Outlook", mail_app: "mail app" };

function approveLabel(kind: ActionKind, info: AutomationInfo | null): string {
  if (kind === "email") {
    const where = info?.email_delivery ?? "gmail";
    if (where === "smtp") return "Approve & send";
    if (where === "drafts") return "Approve & save to Drafts";
    return `Approve & open in ${COMPOSE_LABELS[where]}`;
  }
  if (kind === "event") return "Approve & add to schedule";
  if (kind === "document") return "Approve & save";
  return "Approve & run";
}

/** A drafted task. Nothing happens until the user presses Approve. */
export function ActionCard({ action, info, onApprove, onReject }: Props) {
  const [fields, setFields] = useState<Fields>(() => toFields(action.payload));
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const { title, icon: Icon } = KIND_LABELS[action.kind];
  const editable = action.status === "pending" && !busy;

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setProblem(null);
    try {
      await fn();
    } catch (err) {
      setProblem((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const field = (name: string, label: string, opts: { multiline?: boolean; type?: string; rows?: number } = {}) => {
    const common = {
      id: `${action.id}-${name}`,
      value: fields[name] ?? "",
      disabled: !editable,
      onChange: (e: { target: { value: string } }) => setFields((f) => ({ ...f, [name]: e.target.value })),
      className:
        "w-full rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm outline-none focus:border-indigo-400 disabled:bg-slate-50 disabled:text-slate-600 dark:border-slate-700 dark:bg-slate-900 dark:disabled:bg-slate-800/60 dark:disabled:text-slate-300",
    };
    return (
      <label key={name} htmlFor={common.id} className="block">
        <span className="mb-0.5 block text-xs font-medium text-slate-500">{label}</span>
        {opts.multiline ? (
          <textarea {...common} rows={opts.rows ?? 5} className={`${common.className} resize-y`} />
        ) : (
          <input {...common} type={opts.type ?? "text"} />
        )}
      </label>
    );
  };

  let form: ReactNode = null;
  if (action.kind === "email") {
    form = [
      field("to", "To (comma separated)"),
      field("cc", "Cc"),
      field("subject", "Subject"),
      field("body", "Message", { multiline: true, rows: 7 }),
    ];
  } else if (action.kind === "event") {
    form = [
      field("title", "Title"),
      <div key="when" className="grid grid-cols-2 gap-2">
        {field("start", "Starts", { type: "datetime-local" })}
        {field("end", "Ends (empty = 1 hour)", { type: "datetime-local" })}
      </div>,
      field("location", "Location"),
      field("notes", "Notes", { multiline: true, rows: 2 }),
    ];
  } else if (action.kind === "document") {
    form = [field("title", "Title"), field("content", "Content (Markdown)", { multiline: true, rows: 12 })];
  } else {
    form = [
      <label key="workflow" className="block">
        <span className="mb-0.5 block text-xs font-medium text-slate-500">Workflow</span>
        <select
          value={fields.workflow ?? ""}
          disabled={!editable}
          onChange={(e) => setFields((f) => ({ ...f, workflow: e.target.value }))}
          className="w-full rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900"
        >
          {(info?.workflows ?? [fields.workflow]).map((w) => (
            <option key={w} value={w}>
              {w}
            </option>
          ))}
        </select>
      </label>,
      field("input", "Input", { multiline: true, rows: 3 }),
    ];
  }

  return (
    <div className="flex justify-start">
      <section
        aria-label={`${title} for review`}
        className={`w-full max-w-[85%] rounded-2xl border px-4 py-3 ${
          action.status === "pending"
            ? "border-amber-300 bg-amber-50/60 dark:border-amber-500/40 dark:bg-amber-500/5"
            : "border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-800/50"
        }`}
      >
        <header className="mb-2 flex items-center gap-2 text-sm font-medium">
          <Icon width={16} height={16} />
          {title}
          <StatusBadge status={action.status} />
        </header>

        {action.status === "drafting" ? (
          <p className="flex items-center gap-2 py-2 text-sm text-slate-600 dark:text-slate-400">
            <span className="block h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
            Drafting… you'll review it before anything is sent or saved.
          </p>
        ) : action.status === "rejected" ? (
          <p className="text-sm text-slate-500">Rejected. Nothing was sent or saved.</p>
        ) : (
          <div className="space-y-2">{form}</div>
        )}

        {(problem || (action.status === "pending" && action.error)) && (
          <p role="alert" className="mt-2 rounded-md bg-red-100 px-2 py-1 text-sm text-red-700 dark:bg-red-900/40 dark:text-red-300">
            {problem ?? action.error}
          </p>
        )}

        {action.status === "pending" && (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              onClick={() => run(() => onApprove(action.id, toPayload(action.kind, fields)))}
              disabled={busy}
              className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
            >
              {busy ? "Working…" : approveLabel(action.kind, info)}
            </button>
            <button
              onClick={() => run(() => onReject(action.id))}
              disabled={busy}
              className="rounded-lg px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-200 disabled:opacity-50 dark:text-slate-300 dark:hover:bg-slate-700"
            >
              Reject
            </button>
            <span className="text-xs text-slate-500">Nothing happens until you approve.</span>
          </div>
        )}

        {action.status === "done" && action.result && (
          <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
            <span className="text-emerald-700 dark:text-emerald-400">✓ {action.result.message}</span>
            {action.result.download_url && (
              <a
                href={api.chatUrl(action.result.download_url)}
                className="inline-flex items-center gap-1 text-indigo-600 hover:underline dark:text-indigo-400"
              >
                <DownloadIcon width={14} height={14} />
                {action.kind === "event" ? "Add to calendar app (.ics)" : "Download .md"}
              </a>
            )}
            {action.result.drafts_url && (
              <a
                href={action.result.drafts_url}
                target="_blank"
                rel="noreferrer"
                className="text-indigo-600 hover:underline dark:text-indigo-400"
              >
                Open Gmail Drafts
              </a>
            )}
            {Object.entries(action.result.compose_urls ?? (action.result.open_url ? { mail_app: action.result.open_url } : {})).map(
              ([where, url]) => (
                <a
                  key={where}
                  href={url}
                  target={url.startsWith("mailto:") ? undefined : "_blank"}
                  rel="noreferrer"
                  className="text-indigo-600 hover:underline dark:text-indigo-400"
                >
                  Open in {COMPOSE_LABELS[where] ?? where}
                </a>
              ),
            )}
          </div>
        )}
      </section>
    </div>
  );
}

function StatusBadge({ status }: { status: Action["status"] }) {
  const styles: Record<Action["status"], [string, string]> = {
    drafting: ["Drafting", "bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-200"],
    pending: ["Needs your review", "bg-amber-200 text-amber-900 dark:bg-amber-500/30 dark:text-amber-200"],
    running: ["Running", "bg-indigo-100 text-indigo-800 dark:bg-indigo-500/30 dark:text-indigo-200"],
    done: ["Done", "bg-emerald-100 text-emerald-800 dark:bg-emerald-500/20 dark:text-emerald-300"],
    rejected: ["Rejected", "bg-slate-200 text-slate-600 dark:bg-slate-700 dark:text-slate-300"],
  };
  const [label, cls] = styles[status];
  return <span className={`ml-auto rounded-full px-2 py-0.5 text-[11px] font-medium ${cls}`}>{label}</span>;
}
