import { useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "../lib/api";
import type { Action, AutomationInfo, Message } from "../lib/types";
import { ActionCard, KIND_LABELS } from "./ActionCard";
import { MicIcon } from "./icons";

interface Props {
  messages: Message[];
  actions: Action[];
  automation: AutomationInfo | null;
  onApprove: (id: string, payload: Record<string, unknown>) => Promise<unknown>;
  onReject: (id: string) => Promise<void>;
  emptyHint: string;
}

type Item = { at: string; message: Message } | { at: string; action: Action };

export function MessageList({ messages, actions, automation, onApprove, onReject, emptyHint }: Props) {
  const endRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const last = messages[messages.length - 1];
  const items: Item[] = [
    ...messages.map((message) => ({ at: message.created_at, message })),
    ...actions.map((action) => ({ at: action.created_at, action })),
  ].sort((a, b) => a.at.localeCompare(b.at));
  const lastAction = actions[actions.length - 1];

  // Follow the stream, but don't yank the view if the user scrolled up to read.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 160;
    if (nearBottom || last?.role === "user") endRef.current?.scrollIntoView({ block: "end" });
  }, [messages.length, last?.content, last?.role, actions.length, lastAction?.status]);

  if (items.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center p-8 text-center text-slate-500">
        <div>
          <p className="text-lg font-medium text-slate-700 dark:text-slate-300">How can I help?</p>
          <p className="mt-1 text-sm">{emptyHint}</p>
        </div>
      </div>
    );
  }

  return (
    <div ref={containerRef} className="flex-1 overflow-y-auto" aria-live="polite">
      <div className="mx-auto max-w-3xl space-y-5 px-4 py-6">
        {items.map((item) =>
          "message" in item ? (
            <MessageBubble key={item.message.id} message={item.message} />
          ) : (
            <div key={item.action.id} className="space-y-2">
              <TaskRequest action={item.action} />
              <ActionCard
                key={`${item.action.id}:${item.action.status}`}
                action={item.action}
                info={automation}
                onApprove={onApprove}
                onReject={onReject}
              />
            </div>
          ),
        )}
        <div ref={endRef} />
      </div>
    </div>
  );
}

function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";
  const thinking = message.pending && !message.content && !message.error;
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded-2xl px-4 py-2.5 ${
          isUser
            ? "bg-indigo-600 text-white"
            : "bg-slate-100 text-slate-900 dark:bg-slate-800 dark:text-slate-100"
        }`}
      >
        {message.source === "voice" && (
          <div className={`mb-1 flex items-center gap-1 text-[11px] ${isUser ? "text-indigo-200" : "text-slate-500"}`}>
            <MicIcon width={11} height={11} /> voice
          </div>
        )}
        <MessageImages message={message} />
        {thinking ? (
          <TypingDots />
        ) : isUser ? (
          message.content && <p className="whitespace-pre-wrap break-words">{message.content}</p>
        ) : (
          <div className="markdown break-words">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
            {message.pending && <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-current align-middle" />}
          </div>
        )}
        {message.error && (
          <p className="mt-2 rounded-md bg-red-100 px-2 py-1 text-sm text-red-700 dark:bg-red-900/40 dark:text-red-300">
            {message.error}
          </p>
        )}
      </div>
    </div>
  );
}

/** What the user asked for, shown like their message. */
function TaskRequest({ action }: { action: Action }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] rounded-2xl bg-indigo-600 px-4 py-2.5 text-white">
        <div className="mb-1 text-[11px] text-indigo-200">Task · {KIND_LABELS[action.kind].title.toLowerCase()}</div>
        <p className="whitespace-pre-wrap break-words">{action.instruction}</p>
      </div>
    </div>
  );
}

function MessageImages({ message }: { message: Message }) {
  const urls = message.previews ?? (message.images ?? []).map((img) => api.imageUrl(img.id));
  if (urls.length === 0) return null;
  return (
    <div className={`flex flex-wrap gap-2 ${message.content ? "mb-2" : ""} ${urls.length === 1 ? "" : "max-w-sm"}`}>
      {urls.map((url, i) => (
        <a key={url} href={url} target="_blank" rel="noreferrer" title="Open full size">
          <img
            src={url}
            alt={`Attached picture ${i + 1}`}
            loading="lazy"
            className={`rounded-lg object-cover ${urls.length === 1 ? "max-h-64 max-w-full" : "h-32 w-32"}`}
          />
        </a>
      ))}
    </div>
  );
}

function TypingDots() {
  return (
    <span className="flex gap-1 py-2" aria-label="Assistant is thinking">
      {[0, 150, 300].map((d) => (
        <span key={d} className="h-2 w-2 animate-bounce rounded-full bg-slate-400" style={{ animationDelay: `${d}ms` }} />
      ))}
    </span>
  );
}
