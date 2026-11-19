/** Number formats for the Rates panel, the way they're written in Vietnam: 25.940 and 136,2. */

function group(int: string): string {
  return int.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
}

/** 25940 -> "25.940"; small rates keep two decimals (166.1 -> "166,10"); null -> "–". */
export function formatVnd(n: number | null): string {
  if (n === null || !Number.isFinite(n)) return "–";
  const [int, frac] = Math.abs(n).toFixed(Math.abs(n) < 1000 ? 2 : 0).split(".");
  return `${n < 0 ? "-" : ""}${group(int)}${frac ? `,${frac}` : ""}`;
}

/** VND per lượng in millions (triệu): 136_200_000 -> "136,2 tr", 133_250_000 -> "133,25 tr". */
export function formatMillions(n: number | null): string {
  if (n === null || !Number.isFinite(n)) return "–";
  const [int, frac] = (n / 1_000_000).toFixed(2).split(".");
  const trimmed = frac.replace(/0+$/, "");
  return `${group(int)}${trimmed ? `,${trimmed}` : ""} tr`;
}

/** 3738.6 -> "3.738,60". */
export function formatUsd(n: number): string {
  const [int, frac] = n.toFixed(2).split(".");
  return `${group(int)},${frac}`;
}

/** When a source last changed its prices: "13:37" today, "18/11 13:37" on another day. */
export function formatUpdated(iso: string | null, now = new Date()): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (v: number) => String(v).padStart(2, "0");
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  return d.toDateString() === now.toDateString() ? time : `${pad(d.getDate())}/${pad(d.getMonth() + 1)} ${time}`;
}
