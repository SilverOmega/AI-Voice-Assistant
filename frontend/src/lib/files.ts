/** Attaching documents: what can be read, and how to describe an attached file. */

export const MAX_FILES = 5;
export const MAX_FILE_MB = 20;

/** File types the chat service can read (see services/chat/app/documents.py). */
export const DOCUMENT_EXTENSIONS = [
  ".pdf", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp",
  ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".xml", ".html", ".htm", ".yaml", ".yml",
  ".log", ".ini", ".toml", ".sql", ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".h", ".cpp",
  ".cs", ".go", ".rs", ".rb", ".php", ".sh", ".ps1", ".css", ".srt", ".vtt",
];

/**
 * For the file picker. Pictures are offered too (they go to the model as images), so one
 * picker covers the photo library, the camera and the Files app or cloud drives on phones.
 */
export const ATTACH_ACCEPT = ["image/*", ...DOCUMENT_EXTENSIONS].join(",");

const KINDS: [RegExp, string][] = [
  [/\.pdf$/, "PDF"],
  [/\.(docx|odt)$/, "Word"],
  [/\.(xlsx|ods|csv|tsv)$/, "Spreadsheet"],
  [/\.(pptx|odp)$/, "Slides"],
  [/\.(html?)$/, "Web page"],
  [/\.(md|markdown)$/, "Markdown"],
];

/** "PDF", "Word", "Spreadsheet"…, from the file name. */
export function fileKind(name: string): string {
  const lower = name.toLowerCase();
  return KINDS.find(([pattern]) => pattern.test(lower))?.[1] ?? "Text";
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** "PDF · 12 pages · 340 KB" */
export function describeFile(file: { name: string; size: number; pages?: number | null }): string {
  const kind = fileKind(file.name);
  const unit = kind === "Slides" ? "slide" : kind === "Spreadsheet" ? "sheet" : "page";
  const pages = file.pages ? `${file.pages} ${unit}${file.pages === 1 ? "" : "s"}` : null;
  return [kind, pages, formatSize(file.size)].filter(Boolean).join(" · ");
}

export function isImage(file: File): boolean {
  return file.type.startsWith("image/");
}
