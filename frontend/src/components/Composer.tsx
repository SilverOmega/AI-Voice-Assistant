import { useEffect, useRef, useState } from "react";
import { LiveDictation } from "../audio/dictation";
import { api } from "../lib/api";
import { liveText } from "../lib/dictationText";
import { ATTACH_ACCEPT, MAX_FILES, MAX_FILE_MB, describeFile, isImage } from "../lib/files";
import { MAX_IMAGES, imageFiles, prepareImage, type PreparedImage } from "../lib/images";
import { requestNotifyPermission } from "../lib/notify";
import type { ActionKind, AutomationInfo, UploadedFile } from "../lib/types";
import { KIND_LABELS } from "./ActionCard";
import { ModelPicker } from "./ModelPicker";
import {
  BoltIcon,
  FileIcon,
  GlobeIcon,
  ImageIcon,
  LinkIcon,
  MicIcon,
  PaperclipIcon,
  PawIcon,
  StopIcon,
  TeamIcon,
  XIcon,
} from "./icons";

interface Props {
  streaming: boolean;
  /** A task is being drafted. */
  drafting: boolean;
  automation: AutomationInfo | null;
  onSend: (text: string, images: PreparedImage[], files: UploadedFile[]) => void;
  onTask: (kind: ActionKind, instruction: string) => void;
  onStop: () => void;
  onError: (message: string) => void;
  /** More pictures than a message can carry; `attempted` counts the ones already attached. */
  onPhotoLimit: (attempted: number) => void;
  /** Code of the language dictation listens for ("vi"); null = the server's default. */
  dictationLanguage?: string | null;
  /** Web search for questions: on or off on this device, and whether the server has it. */
  webSearch?: { on: boolean; available: boolean | null; toggle: () => void };
  /** Helpers (agents): on or off on this device; the switch shows only with AGENT_MODE=toggle. */
  agents?: { on: boolean; available: boolean; toggle: () => void };
}

/** A document in the message box: uploading (no `file` yet) or read by the server. */
interface Attachment {
  key: number;
  name: string;
  file?: UploadedFile;
}

/** Phone and tablet keyboards: Enter starts a new line, the send button sends. */
const touchKeyboard = () => window.matchMedia?.("(pointer: coarse)").matches ?? false;

const TASKS: { kind: ActionKind; label: string; placeholder: string }[] = [
  { kind: "email", label: "Draft an email", placeholder: "Who is it to, and what should it say?" },
  { kind: "event", label: "Add to my schedule", placeholder: "What, when and where? e.g. Dentist tomorrow at 3pm" },
  { kind: "document", label: "Compile a document", placeholder: "What should it cover? e.g. Summarize this chat as meeting notes" },
  { kind: "workflow", label: "Run a workflow", placeholder: "What should the workflow do?" },
  { kind: "code", label: "Run Python code", placeholder: "What should the program do? e.g. Count the primes below 1000" },
];

let nextKey = 0;

function hostOf(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

/**
 * Text input with live dictation (words appear in the box as they are spoken; nothing is sent),
 * pictures and documents (the paperclip, paste, or drag and drop) and documents from a link.
 */
export function Composer({
  streaming,
  drafting,
  automation,
  onSend,
  onTask,
  onStop,
  onError,
  onPhotoLimit,
  dictationLanguage = null,
  webSearch,
  agents,
}: Props) {
  const [text, setText] = useState("");
  const [task, setTask] = useState<ActionKind | null>(null);
  const [menu, setMenu] = useState<"task" | "attach" | null>(null);
  const [images, setImages] = useState<PreparedImage[]>([]);
  const [preparing, setPreparing] = useState(0);
  const [docs, setDocs] = useState<Attachment[]>([]);
  const [linkOpen, setLinkOpen] = useState(false);
  const [link, setLink] = useState("");
  const [dragging, setDragging] = useState(false);
  const [dictation, setDictation] = useState<"idle" | "recording" | "finishing">("idle");
  // Live dictation: the session, and what the box is built from while it runs.
  const live = useRef<LiveDictation | null>(null);
  const heard = useRef({ before: "", finals: [] as string[], partial: "" });
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const taskMenuRef = useRef<HTMLDivElement>(null);
  const attachMenuRef = useRef<HTMLDivElement>(null);

  // Close the open menu on a click or tap anywhere else (its own button toggles it).
  useEffect(() => {
    if (!menu) return;
    const box = menu === "task" ? taskMenuRef : attachMenuRef;
    const away = (e: PointerEvent) => {
      if (!box.current?.contains(e.target as Node)) setMenu(null);
    };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, [menu]);

  // Leaving the page (or the chat) mid-dictation turns the microphone off.
  useEffect(() => () => live.current?.cancel(), []);

  const uploading = docs.some((d) => !d.file);
  const readyDocs = docs.flatMap((d) => (d.file ? [d.file] : []));
  const canSend =
    dictation === "idle" &&
    (task
      ? text.trim() !== "" && !drafting
      : (text.trim() !== "" || images.length > 0 || readyDocs.length > 0) && preparing === 0 && !uploading && !drafting);
  const activeTask = TASKS.find((t) => t.kind === task);

  const submit = () => {
    if (!canSend || streaming) return;
    if (task) {
      onTask(task, text);
      setTask(null);
    } else {
      onSend(text, images, readyDocs);
      setImages([]);
      setDocs([]);
    }
    setText("");
  };

  const addImages = async (files: File[]) => {
    const picked = imageFiles(files);
    if (picked.length === 0) return;
    const room = MAX_IMAGES - images.length - preparing;
    if (picked.length > room) onPhotoLimit(MAX_IMAGES - room + picked.length);
    const accepted = picked.slice(0, Math.max(0, room));
    if (accepted.length === 0) return;
    setPreparing((n) => n + accepted.length);
    await Promise.all(
      accepted.map(async (file) => {
        try {
          const image = await prepareImage(file);
          setImages((list) => [...list, image]);
        } catch (err) {
          onError((err as Error).message);
        } finally {
          setPreparing((n) => n - 1);
        }
      }),
    );
    inputRef.current?.focus();
  };

  /** Shows the document right away, then fills in what the server read (or drops it with the reason). */
  const addDocument = async (name: string, load: () => Promise<UploadedFile>) => {
    const key = nextKey++;
    setDocs((list) => [...list, { key, name }]);
    try {
      const file = await load();
      setDocs((list) => list.map((d) => (d.key === key ? { key, name: file.name, file } : d)));
    } catch (err) {
      setDocs((list) => list.filter((d) => d.key !== key));
      onError((err as Error).message);
    }
  };

  /** Pictures go to the model as images; everything else is read as a document. */
  const addFiles = (files: File[]) => {
    if (files.length === 0) return;
    if (task) {
      return onError("Pictures and documents can't go with a task. Send them as a message first, then start the task.");
    }
    void addImages(files.filter(isImage));
    const documents = files.filter((f) => !isImage(f));
    const room = MAX_FILES - docs.length;
    if (documents.length > room) {
      onError(`A message can carry up to ${MAX_FILES} documents. The extra ones were left out.`);
    }
    for (const file of documents.slice(0, Math.max(0, room))) {
      if (file.size > MAX_FILE_MB * 1024 * 1024) onError(`${file.name} is larger than ${MAX_FILE_MB} MB.`);
      else void addDocument(file.name, () => api.uploadFile(file));
    }
  };

  const importLink = () => {
    const url = link.trim();
    if (!url) return;
    if (docs.length >= MAX_FILES) return onError(`A message can carry up to ${MAX_FILES} documents.`);
    setLink("");
    setLinkOpen(false);
    void addDocument(hostOf(url), () => api.importFile(url));
    inputRef.current?.focus();
  };

  const removeDoc = (doc: Attachment) => {
    setDocs((list) => list.filter((d) => d.key !== doc.key));
    if (doc.file) void api.deleteFile(doc.file.id).catch(() => {});
  };

  const showHeard = () => {
    const h = heard.current;
    setText(liveText(h.before, h.finals, h.partial));
  };

  const endDictation = (session: LiveDictation) => {
    if (live.current !== session) return;
    live.current = null;
    heard.current.partial = "";
    showHeard();
    setDictation("idle");
    inputRef.current?.focus();
  };

  const toggleDictation = async () => {
    if (dictation === "finishing") return;
    const running = live.current;
    if (running) {
      setDictation("finishing");
      await running.stop(); // waits for the last phrase
      if (heard.current.finals.length === 0) onError("No speech was recognized.");
      endDictation(running);
      return;
    }
    heard.current = { before: text, finals: [], partial: "" };
    const session = new LiveDictation({
      onPartial: (t) => {
        heard.current.partial = t;
        showHeard();
      },
      onFinal: (t) => {
        if (t.trim()) heard.current.finals.push(t);
        heard.current.partial = "";
        showHeard();
      },
      onError: (message) => onError(`Dictation: ${message}`),
      onLost: () => {
        onError("Dictation stopped: the connection to the voice service was lost.");
        endDictation(session);
      },
    });
    live.current = session;
    setDictation("recording");
    try {
      await session.start(dictationLanguage);
    } catch (err) {
      session.cancel();
      if (live.current === session) onError(`Live dictation isn't available: ${(err as Error).message}`);
      endDictation(session);
    }
  };

  const menuItem =
    "flex w-full items-start gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm hover:bg-slate-100 disabled:opacity-40 disabled:hover:bg-transparent dark:hover:bg-slate-700";

  return (
    <div className="safe-bottom mx-auto w-full max-w-3xl px-3">
      <div
        onDragOver={(e) => {
          if (!e.dataTransfer.types.includes("Files")) return;
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragging(false);
        }}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          addFiles([...e.dataTransfer.files]);
        }}
        className={`flex flex-col gap-1.5 rounded-2xl border bg-white px-2.5 pt-2.5 pb-2 focus-within:border-sky-300 dark:bg-slate-900 ${
          dragging ? "border-sky-400 ring-2 ring-sky-300/50" : "border-slate-200 dark:border-slate-700"
        }`}
      >
        {activeTask && (
          <div className="flex items-center gap-2 px-1 pt-1 text-xs">
            <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 font-medium text-amber-900 dark:bg-amber-500/20 dark:text-amber-200">
              <BoltIcon width={12} height={12} /> {activeTask.label}
              <button onClick={() => setTask(null)} aria-label="Cancel task" className="ml-0.5 rounded-full hover:text-amber-700">
                <XIcon width={12} height={12} />
              </button>
            </span>
            <span className="text-slate-500">You'll review the draft before anything is sent or saved.</span>
          </div>
        )}
        {(images.length > 0 || preparing > 0 || docs.length > 0) && (
          <div className="flex flex-wrap gap-2 px-1 pt-1">
            {images.map((img) => (
              <div key={img.id} className="group relative">
                <img
                  src={img.previewUrl}
                  alt={img.name || "Attached picture"}
                  className="h-16 w-16 rounded-lg border border-slate-200 object-cover dark:border-slate-700"
                />
                <button
                  onClick={() => setImages((list) => list.filter((i) => i.id !== img.id))}
                  aria-label={`Remove ${img.name || "picture"}`}
                  title="Remove"
                  className="absolute -right-1.5 -top-1.5 rounded-full bg-slate-800 p-0.5 pointer-coarse:p-1.5 text-white shadow hover:bg-slate-700 dark:bg-slate-200 dark:text-slate-900"
                >
                  <XIcon width={12} height={12} />
                </button>
              </div>
            ))}
            {Array.from({ length: preparing }, (_, i) => (
              <div
                key={`preparing-${i}`}
                className="flex h-16 w-16 items-center justify-center rounded-lg border border-dashed border-slate-300 text-slate-400 dark:border-slate-700"
                aria-label="Preparing picture"
              >
                <Spinner />
              </div>
            ))}
            {docs.map((doc) => (
              <div
                key={doc.key}
                className="flex h-16 max-w-[15rem] items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 pl-2.5 pr-1 dark:border-slate-700 dark:bg-slate-800"
              >
                <span className="shrink-0 text-indigo-500">{doc.file ? <FileIcon /> : <Spinner />}</span>
                <span className="min-w-0">
                  <span className="block truncate text-sm" title={doc.name}>
                    {doc.name}
                  </span>
                  <span className="block truncate text-xs text-slate-500">
                    {doc.file ? describeFile(doc.file) : "Reading…"}
                  </span>
                </span>
                <button
                  onClick={() => removeDoc(doc)}
                  aria-label={`Remove ${doc.name}`}
                  title="Remove"
                  className="shrink-0 self-start rounded p-1 pointer-coarse:p-2 text-slate-400 hover:text-slate-700 dark:hover:text-slate-200"
                >
                  <XIcon width={14} height={14} />
                </button>
              </div>
            ))}
          </div>
        )}
        {linkOpen && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              importLink();
            }}
            className="flex items-center gap-2 px-1 pt-1"
          >
            <LinkIcon width={16} height={16} className="shrink-0 text-slate-500" />
            <input
              type="url"
              inputMode="url"
              autoFocus
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              value={link}
              onChange={(e) => setLink(e.target.value)}
              onKeyDown={(e) => e.key === "Escape" && setLinkOpen(false)}
              placeholder="Paste a share link or web address"
              aria-label="Link to a document"
              className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm outline-none focus:border-indigo-400 dark:border-slate-700 dark:bg-slate-800"
            />
            <button
              type="submit"
              disabled={!link.trim()}
              className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-40"
            >
              Add
            </button>
            <button
              type="button"
              onClick={() => setLinkOpen(false)}
              aria-label="Cancel link"
              className="rounded p-1 pointer-coarse:p-2 text-slate-400 hover:text-slate-700 dark:hover:text-slate-200"
            >
              <XIcon width={16} height={16} />
            </button>
          </form>
        )}
          <textarea
            ref={inputRef}
            rows={1}
            value={text}
            readOnly={dictation !== "idle"}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && !touchKeyboard()) {
                e.preventDefault();
                submit();
              }
            }}
            onPaste={(e) => {
              const pasted = [...e.clipboardData.files];
              if (pasted.length === 0) return; // plain text paste
              e.preventDefault();
              addFiles(pasted);
            }}
            placeholder={
              dictation === "recording"
                ? "Listening… press the mic again to finish"
                : activeTask
                  ? activeTask.placeholder
                  : docs.length > 0
                    ? "Ask about the document…"
                    : images.length > 0
                      ? "Ask about the picture…"
                      : touchKeyboard()
                        ? "Message the assistant…"
                        : "Enter to send, Shift+Enter for a new line"
            }
            className="max-h-48 min-h-[3.5rem] w-full resize-none bg-transparent px-1.5 py-1 outline-none placeholder:text-slate-400 [field-sizing:content]"
            aria-label="Message"
          />
        <div className="flex items-center gap-0.5">
          <input
            ref={fileRef}
            type="file"
            accept={ATTACH_ACCEPT}
            multiple
            hidden
            onChange={(e) => {
              addFiles([...(e.target.files ?? [])]);
              e.target.value = ""; // allow picking the same file again
            }}
          />
          <div ref={taskMenuRef} className="relative" onKeyDown={(e) => e.key === "Escape" && setMenu(null)}>
            <button
              onClick={() => setMenu((m) => (m === "task" ? null : "task"))}
              aria-expanded={menu === "task"}
              aria-haspopup="menu"
              title="Tasks: draft an email, schedule, document or workflow"
              aria-label="Tasks"
              className={`rounded-xl p-2 hover:bg-slate-100 dark:hover:bg-slate-800 ${task ? "text-amber-600" : "text-slate-400"}`}
            >
              <BoltIcon />
            </button>
            {menu === "task" && (
              <div
                role="menu"
                className="absolute bottom-full left-0 z-10 mb-2 w-64 rounded-xl border border-slate-200 bg-white p-1 shadow-lg dark:border-slate-700 dark:bg-slate-800"
              >
                {TASKS.map(({ kind, label }) => {
                  const Icon = KIND_LABELS[kind].icon;
                  const unavailable =
                    (kind === "workflow" && !automation?.workflows.length) || (kind === "code" && !automation?.code_runs);
                  const setting = kind === "code" ? "SANDBOX_URL" : "WORKFLOWS";
                  return (
                    <button
                      key={kind}
                      role="menuitem"
                      disabled={unavailable}
                      title={unavailable ? `Add ${setting} to .env to enable (see the README)` : undefined}
                      onClick={() => {
                        setTask(kind);
                        setMenu(null);
                        setImages([]);
                        docs.forEach(removeDoc);
                        setLinkOpen(false);
                        requestNotifyPermission();
                        inputRef.current?.focus();
                      }}
                      className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm hover:bg-slate-100 disabled:opacity-40 disabled:hover:bg-transparent dark:hover:bg-slate-700"
                    >
                      <Icon width={16} height={16} /> {label}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
          <div ref={attachMenuRef} className="relative" onKeyDown={(e) => e.key === "Escape" && setMenu(null)}>
            <button
              onClick={() => setMenu((m) => (m === "attach" ? null : "attach"))}
              disabled={task !== null}
              aria-expanded={menu === "attach"}
              aria-haspopup="menu"
              title="Attach pictures or documents (or paste / drop them here)"
              aria-label="Attach"
              className="rounded-xl p-2 text-slate-400 hover:bg-slate-100 disabled:opacity-40 dark:hover:bg-slate-800"
            >
              <PaperclipIcon />
            </button>
            {menu === "attach" && (
              <div
                role="menu"
                className="absolute bottom-full left-0 z-10 mb-2 w-72 max-w-[calc(100vw-2rem)] rounded-xl border border-slate-200 bg-white p-1 shadow-lg dark:border-slate-700 dark:bg-slate-800"
              >
                <button
                  role="menuitem"
                  onClick={() => {
                    setMenu(null);
                    fileRef.current?.click();
                  }}
                  className={menuItem}
                >
                  <ImageIcon width={16} height={16} className="mt-0.5 shrink-0" />
                  <span>
                    Photos and files
                    <span className="block text-xs text-slate-500 dark:text-slate-400">
                      From this device, or iCloud Drive, Google Drive, OneDrive and Dropbox on phones and synced
                      folders. PDF, Word, Excel, PowerPoint, text. Up to {MAX_FILE_MB} MB.
                    </span>
                  </span>
                </button>
                <button
                  role="menuitem"
                  disabled={docs.length >= MAX_FILES}
                  onClick={() => {
                    setMenu(null);
                    setLinkOpen(true);
                  }}
                  className={menuItem}
                >
                  <LinkIcon width={16} height={16} className="mt-0.5 shrink-0" />
                  <span>
                    From a link
                    <span className="block text-xs text-slate-500 dark:text-slate-400">
                      A Google Drive, Docs, OneDrive or Dropbox file shared with "anyone with the link", or a web
                      page.
                    </span>
                  </span>
                </button>
              </div>
            )}
          </div>
          {webSearch && (
            <button
              onClick={webSearch.toggle}
              disabled={webSearch.available === false || task !== null}
              aria-pressed={webSearch.on && webSearch.available !== false}
              aria-label="Search the web"
              title={
                webSearch.available === false
                  ? "Web search is off on the server (WEB_SEARCH in .env)"
                  : webSearch.on
                    ? "Web search is on: questions are looked up, with links and related topics under replies. Click to turn off."
                    : "Web search is off. Click to look questions up and get links and related topics."
              }
              className={`rounded-xl p-2 disabled:opacity-40 ${
                webSearch.on && webSearch.available !== false
                  ? "bg-sky-50 text-sky-600 hover:bg-sky-100 dark:bg-sky-950/60 dark:text-sky-300 dark:hover:bg-sky-900/60"
                  : "text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800"
              }`}
            >
              <GlobeIcon />
            </button>
          )}
          {agents?.available && (
            <button
              onClick={agents.toggle}
              disabled={task !== null}
              aria-pressed={agents.on}
              aria-label="Agents"
              title={
                agents.on
                  ? "Agents are on: helpers research, read documents, draft tasks and work on code before the reply. Slower. Click to turn off."
                  : "Agents are off. Click to let helpers research, read documents, draft tasks and work on code first."
              }
              className={`rounded-xl p-2 disabled:opacity-40 ${
                agents.on
                  ? "bg-violet-50 text-violet-600 hover:bg-violet-100 dark:bg-violet-950/60 dark:text-violet-300 dark:hover:bg-violet-900/60"
                  : "text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800"
              }`}
            >
              <TeamIcon />
            </button>
          )}
          <button
            onClick={toggleDictation}
            disabled={dictation === "finishing"}
            title={dictation === "recording" ? "Stop dictation" : "Dictate (speech to text)"}
            aria-label="Dictate"
            className={`relative rounded-xl p-2 ${
              dictation === "recording"
                ? "pulse-ring text-red-500"
                : "text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800"
            } disabled:opacity-50`}
          >
            <span className="relative">
              {dictation === "finishing" ? <Spinner /> : <MicIcon />}
            </span>
          </button>
          <ModelPicker disabled={streaming || drafting} onError={onError} />
          {streaming ? (
            <button
              onClick={onStop}
              aria-label="Stop generating"
              className="ml-auto flex h-9 w-9 items-center justify-center rounded-full bg-slate-800 text-white dark:bg-slate-200 dark:text-slate-900"
            >
              <StopIcon width={18} height={18} />
            </button>
          ) : (
            <button
              onClick={submit}
              disabled={!canSend}
              title={uploading ? "Wait until the documents are read" : undefined}
              aria-label={task ? "Draft task" : "Send"}
              className="ml-auto flex h-9 w-9 items-center justify-center rounded-full bg-sky-400 text-white shadow-sm shadow-sky-200 hover:bg-sky-500 disabled:bg-sky-100 disabled:shadow-none dark:shadow-none dark:disabled:bg-sky-950 dark:disabled:text-sky-800"
            >
              <PawIcon width={20} height={20} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function Spinner() {
  return <span className="block h-5 w-5 animate-spin rounded-full border-2 border-current border-t-transparent" />;
}
