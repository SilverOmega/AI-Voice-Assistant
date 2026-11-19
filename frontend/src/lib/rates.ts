/** Number formats and sums for the Rates dashboard, written the Vietnamese way: 25.940 and 136,2. */

import type { CurrencyRate } from "./types";

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

/** Sell minus buy, and that as a share of the buy price; null when either side is missing. */
export function spread(buy: number | null, sell: number | null): { amount: number; percent: number } | null {
  if (buy === null || sell === null || !Number.isFinite(buy) || !Number.isFinite(sell) || buy <= 0) return null;
  const amount = sell - buy;
  return { amount, percent: (amount / buy) * 100 };
}

/** 1.234 -> "1,23%". */
export function formatPercent(n: number): string {
  return `${n.toFixed(2).replace(".", ",")}%`;
}

export type ConvertDirection = "toVnd" | "fromVnd";

/**
 * What an amount comes to at the bank. Selling foreign money for đồng uses the bank's buy rate
 * (cash, or transfer when it doesn't buy that currency in cash); buying it with đồng uses the
 * sell rate. null when the rate is missing or the amount isn't a usable number.
 */
export function convert(
  amount: number,
  rate: CurrencyRate,
  direction: ConvertDirection,
): { value: number; used: "buy" | "transfer" | "sell"; rate: number } | null {
  if (!Number.isFinite(amount) || amount < 0) return null;
  if (direction === "toVnd") {
    if (rate.buy !== null && rate.buy > 0) return { value: amount * rate.buy, used: "buy", rate: rate.buy };
    if (rate.transfer !== null && rate.transfer > 0)
      return { value: amount * rate.transfer, used: "transfer", rate: rate.transfer };
    return null;
  }
  if (rate.sell === null || rate.sell <= 0) return null;
  return { value: amount / rate.sell, used: "sell", rate: rate.sell };
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
