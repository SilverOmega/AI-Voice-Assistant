import { useCallback, useEffect, useId, useRef, useState, type CSSProperties, type RefObject } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "../lib/api";
import { findLanguage, LANGUAGES, languageLabel, MAX_LANGUAGE_CHARS, validLanguageName } from "../lib/languages";
import { MAX_TRANSLATE_CHARS, TranslationStore } from "../lib/translate";
import { CopyIcon, TranslateIcon, XIcon } from "./icons";

/** Shared by every message and selection on the page. */
export const translations = new TranslationStore((text, target) =>
  api.translate(text, target).then((r) => r.translation),
);

type State =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "done"; text: string }
  | { status: "error"; error: string };

/** The translation of `text` into `target`, fetched while `active`. */
function useTranslation(text: string, target: string, active: boolean): [State, () => void] {
  const [state, setState] = useState<State>({ status: "idle" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!active || !text.trim() || !target) return;
    const hit = translations.cached(text, target);
    if (hit !== undefined) {
      setState({ status: "done", text: hit });
      return;
    }
    let current = true;
    setState({ status: "loading" });
    translations.translate(text, target).then(
      (result) => current && setState({ status: "done", text: result }),
      (err) => current && setState({ status: "error", error: (err as Error).message }),
    );
    return () => {
      current = false;
    };
  }, [text, target, active, attempt]);
  return [state, useCallback(() => setAttempt((n) => n + 1), [])];
}

// ---- language picker -------------------------------------------------------------

const OTHER = "__other__";

interface PickerProps {
  value: string;
  onChange: (language: string) => void;
  /** Label for the "" choice (no particular language); omit to require a language. */
  autoLabel?: string;
  className?: string;
  "aria-label"?: string;
}

/** The listed languages, or any other typed in by name. */
export function LanguagePicker({ value, onChange, autoLabel, className = "", ...rest }: PickerProps) {
  const listed = value ? findLanguage(value) : undefined;
  const [other, setOther] = useState(Boolean(value) && !listed);
  const [typed, setTyped] = useState(listed ? "" : value);
  const inputId = useId();
  const typedOk = validLanguageName(typed);

  return (
    <div className="space-y-1.5">
      <select
        className={className}
        aria-label={rest["aria-label"]}
        value={other ? OTHER : (listed?.name ?? "")}
        onChange={(e) => {
          if (e.target.value === OTHER) {
            setOther(true);
            if (validLanguageName(typed)) onChange(typed.trim().replace(/\s+/g, " "));
            return;
          }
          setOther(false);
          onChange(e.target.value);
        }}
      >
        {autoLabel !== undefined ? <option value="">{autoLabel}</option> : !listed && !other && <option value="" />}
        {LANGUAGES.map((l) => (
          <option key={l.name} value={l.name}>
            {languageLabel(l.name)}
          </option>
        ))}
        <option value={OTHER}>Other language…</option>
      </select>
      {other && (
        <>
          <input
            id={inputId}
            className={className}
            value={typed}
            maxLength={MAX_LANGUAGE_CHARS}
            placeholder="Type a language, e.g. Swiss German"
            aria-label="Language name"
            autoFocus
            onChange={(e) => {
              setTyped(e.target.value);
              if (validLanguageName(e.target.value)) onChange(e.target.value.trim().replace(/\s+/g, " "));
            }}
          />
          {typed && !typedOk && (
            <span className="block text-xs text-red-600 dark:text-red-400">
              Use letters only, like “Swiss German” or “Tagalog”.
            </span>
          )}
        </>
      )}
    </div>
  );
}

// ---- one message ---------------------------------------------------------------------

interface MessageTranslationProps {
  text: string;
  target: string;
  /** Assistant replies are Markdown. */
  markdown: boolean;
  /** The bubble's colors: the user's are blue with white text. */
  tone: "user" | "assistant";
  open: boolean;
}

/** A message's translation, shown inside its bubble under the original. */
export function MessageTranslation({ text, target, markdown, tone, open }: MessageTranslationProps) {
  const [state, retry] = useTranslation(text, target, open);
  if (!open) return null;
  const muted = tone === "user" ? "text-sky-100" : "text-slate-500 dark:text-slate-400";
  return (
    <div
      lang={findLanguage(target)?.code}
      className={`mt-2 border-t pt-2 ${tone === "user" ? "border-white/30" : "border-slate-300 dark:border-slate-600"}`}
    >
      <div className={`mb-1 flex items-center gap-1 text-[11px] ${muted}`}>
        <TranslateIcon width={11} height={11} /> {languageLabel(target)}
      </div>
      {state.status === "loading" || state.status === "idle" ? (
        <p className={`animate-pulse text-sm ${muted}`}>Translating…</p>
      ) : state.status === "error" ? (
        <p className="text-sm">
          <span className={tone === "user" ? "text-white" : "text-red-600 dark:text-red-400"}>
            Couldn't translate: {state.error}
          </span>{" "}
          <button onClick={retry} className="underline">
            Try again
          </button>
        </p>
      ) : markdown ? (
        <div className="markdown break-words">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{state.text}</ReactMarkdown>
        </div>
      ) : (
        <p className="whitespace-pre-wrap break-words">{state.text}</p>
      )}
    </div>
  );
}

// ---- selected text -----------------------------------------------------------------

interface Anchor {
  x: number;
  top: number;
  bottom: number;
}

interface SelectionTranslatorProps {
  /** Selections inside this element can be translated. */
  containerRef: RefObject<HTMLElement | null>;
  target: string;
  onTarget: (language: string) => void;
}

const PANEL_WIDTH = 352; // 22rem

function selectionIn(container: HTMLElement | null): { text: string; anchor: Anchor } | null {
  const sel = window.getSelection();
  if (!container || !sel || sel.isCollapsed || sel.rangeCount === 0) return null;
  const range = sel.getRangeAt(0);
  if (!container.contains(range.commonAncestorContainer)) return null;
  // Not text typed into a field (the panel's own language box), only the conversation.
  const node = range.commonAncestorContainer;
  const el = node instanceof Element ? node : node.parentElement;
  if (el?.closest("input, textarea, select, [data-translate-panel]")) return null;
  const text = sel.toString().trim();
  if (!text) return null;
  const rect = range.getBoundingClientRect();
  return { text: text.slice(0, MAX_TRANSLATE_CHARS), anchor: { x: rect.left + rect.width / 2, top: rect.top, bottom: rect.bottom } };
}

/**
 * Select any part of the conversation and a "Translate" button appears next to it; it opens
 * the translation, with a language menu to read it in any other language.
 */
export function SelectionTranslator({ containerRef, target, onTarget }: SelectionTranslatorProps) {
  const [chip, setChip] = useState<{ text: string; anchor: Anchor } | null>(null);
  const [panel, setPanel] = useState<{ text: string; anchor: Anchor } | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  // Wait for the selection to settle: it changes on every step of a drag or handle move.
  useEffect(() => {
    let timer = 0;
    const update = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        const found = selectionIn(containerRef.current);
        setChip(found);
        if (found) setPanel((p) => (p && p.text !== found.text ? null : p));
      }, 250);
    };
    document.addEventListener("selectionchange", update);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener("selectionchange", update);
    };
  }, [containerRef]);

  // The chip points at where the selection was; once the text scrolls away, so does the chip.
  useEffect(() => {
    const el = containerRef.current;
    if (!el || !chip) return;
    const hide = () => setChip(null);
    el.addEventListener("scroll", hide, { passive: true });
    return () => el.removeEventListener("scroll", hide);
  }, [containerRef, chip]);

  useEffect(() => {
    if (!panel) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setPanel(null);
    const onDown = (e: PointerEvent) => {
      if (!panelRef.current?.contains(e.target as Node)) setPanel(null);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onDown);
    };
  }, [panel]);

  return (
    <>
      {chip && !panel && (
        <button
          type="button"
          style={place(chip.anchor, 120, false)}
          // Keep the selection: a normal click would clear it before the panel opens.
          onPointerDown={(e) => e.preventDefault()}
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => {
            setPanel(chip);
            setChip(null);
          }}
          className="fixed z-30 flex items-center gap-1.5 rounded-full bg-slate-900 px-3 py-1.5 text-xs font-medium text-white shadow-lg hover:bg-slate-700 pointer-coarse:px-4 pointer-coarse:py-2 pointer-coarse:text-sm dark:bg-white dark:text-slate-900 dark:hover:bg-slate-200"
        >
          <TranslateIcon width={14} height={14} /> Translate
        </button>
      )}
      {panel && (
        <TranslationPanel
          ref={panelRef}
          text={panel.text}
          anchor={panel.anchor}
          target={target}
          onTarget={onTarget}
          onClose={() => setPanel(null)}
        />
      )}
    </>
  );
}

/** Below the selection, or above it when it's low on the screen; always inside the window. */
function place(anchor: Anchor, width: number, panel: boolean): CSSProperties {
  const w = Math.min(width, window.innerWidth - 16);
  const left = Math.max(8, Math.min(anchor.x - w / 2, window.innerWidth - w - 8));
  const below = anchor.bottom < window.innerHeight * (panel ? 0.55 : 0.85);
  return below
    ? { left, top: anchor.bottom + 8, width: panel ? w : undefined }
    : { left, bottom: window.innerHeight - anchor.top + 8, width: panel ? w : undefined };
}

interface PanelProps {
  ref: RefObject<HTMLDivElement | null>;
  text: string;
  anchor: Anchor;
  target: string;
  onTarget: (language: string) => void;
  onClose: () => void;
}

function TranslationPanel({ ref, text, anchor, target, onTarget, onClose }: PanelProps) {
  const [state, retry] = useTranslation(text, target, true);
  const [copied, setCopied] = useState(false);
  const field =
    "w-full rounded-md border border-slate-300 bg-white px-2 py-1 text-sm dark:border-slate-600 dark:bg-slate-800";

  const copy = async () => {
    if (state.status !== "done") return;
    try {
      await navigator.clipboard.writeText(state.text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked: the text can still be selected by hand */
    }
  };

  return (
    <div
      ref={ref}
      data-translate-panel
      role="dialog"
      aria-label="Translation"
      style={{ ...place(anchor, PANEL_WIDTH, true), maxHeight: "min(24rem, 60vh)" }}
      className="fixed z-30 flex flex-col overflow-hidden rounded-xl bg-white text-slate-900 shadow-2xl ring-1 ring-slate-900/10 dark:bg-slate-900 dark:text-slate-100 dark:ring-white/15"
    >
      <div className="flex items-start gap-2 border-b border-slate-200 p-2 dark:border-slate-700">
        <TranslateIcon width={16} height={16} className="mt-1.5 shrink-0 text-sky-500" />
        <div className="min-w-0 flex-1">
          <LanguagePicker value={target} onChange={onTarget} className={field} aria-label="Translate into" />
        </div>
        <button onClick={onClose} aria-label="Close translation" className="rounded p-1.5 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800">
          <XIcon width={14} height={14} />
        </button>
      </div>
      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto overscroll-contain p-3 text-sm">
        <p className="line-clamp-3 text-xs text-slate-500 dark:text-slate-400">{text}</p>
        {state.status === "done" ? (
          <p lang={findLanguage(target)?.code} className="whitespace-pre-wrap break-words">
            {state.text}
          </p>
        ) : state.status === "error" ? (
          <p className="text-red-600 dark:text-red-400">
            Couldn't translate: {state.error}{" "}
            <button onClick={retry} className="underline">
              Try again
            </button>
          </p>
        ) : (
          <p className="animate-pulse text-slate-500">Translating…</p>
        )}
      </div>
      {state.status === "done" && (
        <div className="flex justify-end border-t border-slate-200 px-2 py-1.5 dark:border-slate-700">
          <button
            onClick={copy}
            className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            <CopyIcon width={12} height={12} /> {copied ? "Copied" : "Copy"}
          </button>
        </div>
      )}
    </div>
  );
}
