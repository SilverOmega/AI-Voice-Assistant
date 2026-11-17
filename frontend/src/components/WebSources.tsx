import { useState } from "react";
import type { WebResults } from "../lib/types";
import { ExternalIcon, GlobeIcon } from "./icons";

/** Links shown before "Show more". */
const FIRST_LINKS = 3;

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** Only http(s) links become clickable (the server checks too). */
function safeHref(url: string): string | undefined {
  try {
    const { protocol } = new URL(url);
    return protocol === "http:" || protocol === "https:" ? url : undefined;
  } catch {
    return undefined;
  }
}

interface Props {
  web?: WebResults | null;
  /** The query of a search still running. */
  searching?: string;
  /** Ask about a related topic; undefined while a reply is being written. */
  onAsk?: (topic: string) => void;
}

/** Under an assistant reply: the web pages its answer drew on, and related topics to ask about next. */
export function WebSources({ web, searching, onAsk }: Props) {
  const [expanded, setExpanded] = useState(false);
  if (searching) {
    return (
      <p className="mt-1.5 flex items-center gap-1.5 px-1 text-xs text-slate-500 dark:text-slate-400" role="status">
        <GlobeIcon width={13} height={13} className="animate-pulse" />
        <span className="truncate">Searching the web for “{searching}”…</span>
      </p>
    );
  }
  if (!web || (web.links.length === 0 && web.related.length === 0)) return null;
  const shown = expanded ? web.links : web.links.slice(0, FIRST_LINKS);
  const hidden = web.links.length - shown.length;

  return (
    <div className="mt-1.5 w-full max-w-[85%] space-y-2">
      {web.links.length > 0 && (
        <section aria-label="Websites to read more">
          <h3 className="mb-1 flex items-center gap-1.5 px-1 text-xs font-medium text-slate-500 dark:text-slate-400">
            <GlobeIcon width={13} height={13} /> Read more on the web
          </h3>
          <ul className="grid gap-1.5 sm:grid-cols-2">
            {shown.map((link) => (
              <li key={link.url}>
                <a
                  href={safeHref(link.url)}
                  target="_blank"
                  rel="noopener noreferrer"
                  title={link.url}
                  className="group/link block h-full rounded-xl border border-slate-200 bg-white px-3 py-2 hover:border-sky-300 hover:bg-sky-50 dark:border-slate-700 dark:bg-slate-900 dark:hover:border-sky-700 dark:hover:bg-slate-800"
                >
                  <span className="flex items-center gap-1 text-[11px] text-slate-500 dark:text-slate-400">
                    <span className="truncate">{hostOf(link.url)}</span>
                    <ExternalIcon width={10} height={10} className="shrink-0 opacity-0 group-hover/link:opacity-100" />
                  </span>
                  <span className="line-clamp-2 text-sm font-medium text-sky-700 dark:text-sky-300">{link.title}</span>
                  {link.snippet && (
                    <span className="mt-0.5 line-clamp-2 text-xs text-slate-600 dark:text-slate-400">{link.snippet}</span>
                  )}
                </a>
              </li>
            ))}
          </ul>
          {web.links.length > FIRST_LINKS && (
            <button
              onClick={() => setExpanded((e) => !e)}
              className="mt-1 rounded px-1 text-xs text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200"
            >
              {expanded ? "Show fewer" : `Show ${hidden} more`}
            </button>
          )}
        </section>
      )}
      {web.related.length > 0 && (
        <section aria-label="Related topics">
          <h3 className="mb-1 px-1 text-xs font-medium text-slate-500 dark:text-slate-400">Related topics</h3>
          <div className="flex flex-wrap gap-1.5">
            {web.related.map((topic) => (
              <button
                key={topic}
                onClick={() => onAsk?.(topic)}
                disabled={!onAsk}
                title={onAsk ? `Ask about “${topic}”` : "Wait for the reply to finish"}
                className="rounded-full border border-sky-200 bg-sky-50 px-2.5 py-1 text-left text-xs text-sky-800 hover:bg-sky-100 disabled:opacity-50 dark:border-sky-900 dark:bg-sky-950/50 dark:text-sky-200 dark:hover:bg-sky-900/60"
              >
                {topic}
              </button>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
