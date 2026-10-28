import { useRef, useState } from "react";
import { ClipRecorder } from "../audio/recorder";
import { api } from "../lib/api";
import { MAX_IMAGES, imageFiles, prepareImage, type PreparedImage } from "../lib/images";
import { ImageIcon, MicIcon, SendIcon, StopIcon, XIcon } from "./icons";

interface Props {
  streaming: boolean;
  onSend: (text: string, images: PreparedImage[]) => void;
  onStop: () => void;
  onError: (message: string) => void;
}

/**
 * Text input with MediaRecorder dictation (speech is transcribed into the box, not sent)
 * and picture attachments (button, paste, or drag and drop).
 */
export function Composer({ streaming, onSend, onStop, onError }: Props) {
  const [text, setText] = useState("");
  const [images, setImages] = useState<PreparedImage[]>([]);
  const [preparing, setPreparing] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [dictation, setDictation] = useState<"idle" | "recording" | "transcribing">("idle");
  const recorder = useRef(new ClipRecorder());
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const canSend = (text.trim() !== "" || images.length > 0) && preparing === 0;
  const imagesFull = images.length + preparing >= MAX_IMAGES;

  const submit = () => {
    if (!canSend || streaming) return;
    onSend(text, images);
    setText("");
    setImages([]);
  };

  const addImages = async (files: File[]) => {
    const picked = imageFiles(files);
    if (picked.length === 0) return;
    const room = MAX_IMAGES - images.length - preparing;
    if (picked.length > room) onError(`You can attach up to ${MAX_IMAGES} pictures per message.`);
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

  const toggleDictation = async () => {
    if (dictation === "recording") {
      setDictation("transcribing");
      try {
        const clip = await recorder.current.stop();
        const { text: heard } = await api.transcribe(clip);
        if (heard) setText((t) => (t ? `${t} ${heard}` : heard));
        else onError("No speech was recognized.");
      } catch (err) {
        onError(`Dictation failed: ${(err as Error).message}`);
      } finally {
        setDictation("idle");
        inputRef.current?.focus();
      }
      return;
    }
    try {
      await recorder.current.start();
      setDictation("recording");
    } catch (err) {
      onError(`Could not start recording: ${(err as Error).message}`);
    }
  };

  return (
    <div className="mx-auto w-full max-w-3xl px-4 pb-4">
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
          void addImages([...e.dataTransfer.files]);
        }}
        className={`flex flex-col gap-2 rounded-2xl border bg-white p-2 shadow-sm focus-within:border-indigo-400 dark:bg-slate-900 ${
          dragging ? "border-indigo-400 ring-2 ring-indigo-300/50" : "border-slate-300 dark:border-slate-700"
        }`}
      >
        {(images.length > 0 || preparing > 0) && (
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
                  className="absolute -right-1.5 -top-1.5 rounded-full bg-slate-800 p-0.5 text-white shadow hover:bg-slate-700 dark:bg-slate-200 dark:text-slate-900"
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
          </div>
        )}
        <div className="flex items-end gap-2">
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            multiple
            hidden
            onChange={(e) => {
              void addImages([...(e.target.files ?? [])]);
              e.target.value = ""; // allow picking the same file again
            }}
          />
          <button
            onClick={() => fileRef.current?.click()}
            disabled={imagesFull}
            title={imagesFull ? `Up to ${MAX_IMAGES} pictures per message` : "Attach pictures (or paste / drop them here)"}
            aria-label="Attach pictures"
            className="rounded-xl p-2.5 text-slate-500 hover:bg-slate-100 disabled:opacity-40 dark:hover:bg-slate-800"
          >
            <ImageIcon />
          </button>
          <textarea
            ref={inputRef}
            rows={1}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                submit();
              }
            }}
            onPaste={(e) => {
              const pasted = imageFiles(e.clipboardData.files);
              if (pasted.length === 0) return; // plain text paste
              e.preventDefault();
              void addImages(pasted);
            }}
            placeholder={
              dictation === "recording"
                ? "Listening… click the mic again to finish"
                : images.length > 0
                  ? "Ask about the picture…"
                  : "Message the assistant…"
            }
            className="max-h-48 min-h-[2.5rem] flex-1 resize-none bg-transparent px-2 py-2 outline-none [field-sizing:content]"
            aria-label="Message"
          />
          <button
            onClick={toggleDictation}
            disabled={dictation === "transcribing"}
            title={dictation === "recording" ? "Stop dictation" : "Dictate (speech to text)"}
            aria-label="Dictate"
            className={`relative rounded-xl p-2.5 ${
              dictation === "recording"
                ? "pulse-ring text-red-500"
                : "text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
            } disabled:opacity-50`}
          >
            <span className="relative">
              {dictation === "transcribing" ? <Spinner /> : <MicIcon />}
            </span>
          </button>
          {streaming ? (
            <button onClick={onStop} aria-label="Stop generating" className="rounded-xl bg-slate-800 p-2.5 text-white dark:bg-slate-200 dark:text-slate-900">
              <StopIcon />
            </button>
          ) : (
            <button
              onClick={submit}
              disabled={!canSend}
              aria-label="Send"
              className="rounded-xl bg-indigo-600 p-2.5 text-white hover:bg-indigo-500 disabled:opacity-40"
            >
              <SendIcon />
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
