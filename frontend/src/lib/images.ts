/** Preparing pictures for upload: downscale in the browser so requests stay small and fast. */

export const MAX_IMAGES = 4;
/** Longest edge sent to the model. Larger images cost more and are downscaled by Claude anyway. */
export const MAX_EDGE = 1568;
const PASS_THROUGH_BYTES = 1.5 * 1024 * 1024;
const JPEG_QUALITY = 0.85;

export interface PreparedImage {
  id: string;
  mediaType: "image/jpeg" | "image/png";
  /** base64 without the data: prefix */
  data: string;
  /** data: URL for previews */
  previewUrl: string;
  name: string;
}

/** Scale (width, height) down to fit within `max` on the longest edge, keeping the aspect ratio. */
export function fitWithin(width: number, height: number, max = MAX_EDGE): { width: number; height: number } {
  const scale = Math.min(1, max / Math.max(width, height));
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

export async function prepareImage(file: File): Promise<PreparedImage> {
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch {
    throw new Error(`${file.name || "This file"} is not an image the browser can open.`);
  }
  const size = fitWithin(bitmap.width, bitmap.height);
  const keepAsIs =
    (file.type === "image/jpeg" || file.type === "image/png") &&
    size.width === bitmap.width &&
    file.size <= PASS_THROUGH_BYTES;

  let blob: Blob = file;
  if (!keepAsIs) {
    const canvas = document.createElement("canvas");
    canvas.width = size.width;
    canvas.height = size.height;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Could not process the image.");
    ctx.fillStyle = "#fff"; // JPEG has no transparency
    ctx.fillRect(0, 0, size.width, size.height);
    ctx.drawImage(bitmap, 0, 0, size.width, size.height);
    blob = await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("Could not process the image."))), "image/jpeg", JPEG_QUALITY),
    );
  }
  bitmap.close();

  const previewUrl = await readAsDataUrl(blob);
  return {
    id: crypto.randomUUID(),
    mediaType: blob.type === "image/png" ? "image/png" : "image/jpeg",
    data: previewUrl.slice(previewUrl.indexOf(",") + 1),
    previewUrl,
    name: file.name,
  };
}

function readAsDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error ?? new Error("Could not read the image."));
    reader.readAsDataURL(blob);
  });
}

/** Image files from a paste or drop, ignoring text and other files. */
export function imageFiles(items: Iterable<File>): File[] {
  return [...items].filter((f) => f.type.startsWith("image/"));
}
