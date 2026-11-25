/**
 * The message box while dictating: what was in it before, then the finished phrases, then
 * the phrase still being heard (which keeps changing until it is finished).
 */
export function liveText(before: string, finals: readonly string[], partial: string): string {
  const spoken = [...finals, partial]
    .map((t) => t.trim())
    .filter(Boolean)
    .join(" ");
  if (!spoken) return before;
  if (!before || /\s$/.test(before)) return before + spoken;
  return `${before} ${spoken}`;
}
