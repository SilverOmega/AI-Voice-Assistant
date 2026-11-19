import { useEffect, useState, type ReactNode } from "react";
import { useRates } from "../hooks/useRates";
import {
  convert,
  formatMillions,
  formatPercent,
  formatUpdated,
  formatUsd,
  formatVnd,
  spread,
  type ConvertDirection,
} from "../lib/rates";
import type { CurrencyRate, RatesSection } from "../lib/types";
import { RefreshIcon, XIcon } from "./icons";

const HEAD = "pb-1 text-xs font-medium text-slate-500";
const NUM = "whitespace-nowrap py-1 pl-3 text-right tabular-nums";
const CARD = "rounded-xl border border-slate-200 p-4 dark:border-slate-800";

interface Props {
  open: boolean;
  onClose: () => void;
}

/** Exchange rates against VND, gold prices and a converter, opened from the header's chart button. */
export function RatesDashboard({ open, onClose }: Props) {
  const { rates, loading, error, refresh } = useRates(open);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const sections = rates ? [rates.gold, rates.world, rates.fx].filter((s) => s !== null) : [];
  const off = rates !== null && sections.length === 0;
  const usd = rates?.fx?.rates.find((r) => r.code === "USD") ?? null;
  const sjc = rates?.gold?.prices.find((p) => p.code === "SJC") ?? null;
  const world = rates?.world?.price ?? null;

  return (
    <div className="fixed inset-0 z-40 flex items-stretch justify-center bg-black/40 md:items-center md:p-6" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Exchange rates and gold prices"
        className="flex h-full w-full flex-col bg-white shadow-xl md:h-auto md:max-h-full md:max-w-4xl md:rounded-2xl dark:bg-slate-900"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-slate-200 px-5 pt-[max(1rem,env(safe-area-inset-top))] pb-4 dark:border-slate-800">
          <h2 className="flex-1 font-semibold">Exchange rates &amp; gold</h2>
          {!off && (
            <button
              onClick={() => void refresh()}
              disabled={loading}
              className="rounded p-2 text-slate-500 hover:bg-slate-100 hover:text-slate-900 disabled:opacity-50 dark:hover:bg-slate-800 dark:hover:text-white"
              aria-label="Get the latest rates"
              title="Get the latest rates"
            >
              <RefreshIcon width={18} height={18} className={loading ? "animate-spin" : ""} />
            </button>
          )}
          <button
            onClick={onClose}
            autoFocus
            aria-label="Close rates"
            className="-mr-1 rounded p-2 hover:bg-slate-100 dark:hover:bg-slate-800"
          >
            <XIcon />
          </button>
        </div>

        <div
          className="flex-1 space-y-5 overflow-y-auto overscroll-contain px-5 pt-5 pb-[max(1.25rem,env(safe-area-inset-bottom))] text-sm"
          aria-busy={loading}
        >
          {error && <p className="text-amber-600 dark:text-amber-400">{error}</p>}
          {!rates && !error && <p className="text-slate-500">Loading…</p>}
          {off && <p className="text-slate-500">Rates are turned off on this server.</p>}

          {(usd || sjc || world) && (
            <div className="grid gap-3 sm:grid-cols-3">
              {usd && (
                <SummaryCard label="US dollar (sell)" value={`${formatVnd(usd.sell)} ₫`} note={`Buy ${formatVnd(usd.buy)} ₫`} />
              )}
              {sjc && (
                <SummaryCard
                  label="SJC gold bar (sell)"
                  value={formatMillions(sjc.sell)}
                  note={`Buy ${formatMillions(sjc.buy)} · per lượng`}
                />
              )}
              {world && (
                <SummaryCard
                  label="World gold"
                  value={`${formatUsd(world.usd_per_ounce)} USD/oz`}
                  note={
                    world.vnd_per_luong !== null ? `≈ ${formatMillions(world.vnd_per_luong)} per lượng at the bank's rate` : undefined
                  }
                />
              )}
            </div>
          )}

          {rates?.fx && rates.fx.rates.length > 0 && <Converter rates={rates.fx.rates} source={rates.fx.source} />}

          <div className="grid gap-5 md:grid-cols-2">
            {(rates?.gold || rates?.world) && (
              <div className="space-y-3">
                {rates?.gold && (
                  <div className={CARD}>
                    <table className="w-full">
                      <caption className="pb-2 text-left font-semibold">
                        Gold at {rates.gold.source} <span className="font-normal text-slate-500">(per lượng)</span>
                      </caption>
                      <thead>
                        <tr>
                          <th scope="col" className={`${HEAD} text-left`}>Product</th>
                          <th scope="col" className={`${HEAD} text-right`}>Buy</th>
                          <th scope="col" className={`${HEAD} text-right`}>Sell</th>
                          <th scope="col" className={`${HEAD} text-right`} title="Sell minus buy">Spread</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rates.gold.prices.map((p) => {
                          const s = spread(p.buy, p.sell);
                          return (
                            <tr key={p.code} className="border-t border-slate-100 dark:border-slate-800">
                              <th scope="row" className="py-1 text-left font-normal">{p.name}</th>
                              <td className={NUM}>{formatMillions(p.buy)}</td>
                              <td className={NUM}>{formatMillions(p.sell)}</td>
                              <td className={`${NUM} text-slate-500`}>{s ? formatMillions(s.amount) : "–"}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                    <Problem section={rates.gold} />
                  </div>
                )}
                {rates?.world && <Problem section={rates.world} />}
              </div>
            )}

            {rates?.fx && (
              <div className={CARD}>
                <table className="w-full">
                  <caption className="pb-2 text-left font-semibold">
                    Currencies at {rates.fx.source} <span className="font-normal text-slate-500">(in đồng)</span>
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col" className={`${HEAD} text-left`}>Currency</th>
                      <th scope="col" className={`${HEAD} text-right`}>Buy</th>
                      <th scope="col" className={`${HEAD} text-right`} title="Buying by bank transfer">Transfer</th>
                      <th scope="col" className={`${HEAD} text-right`}>Sell</th>
                      <th scope="col" className={`${HEAD} text-right`} title="Sell minus buy, as a share of buy">Spread</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rates.fx.rates.map((r) => {
                      const s = spread(r.buy ?? r.transfer, r.sell);
                      return (
                        <tr key={r.code} className="border-t border-slate-100 dark:border-slate-800">
                          <th scope="row" className="py-1 text-left font-medium" title={r.name}>
                            {r.code}
                            <span className="block text-xs font-normal capitalize text-slate-500">{r.name.toLowerCase()}</span>
                          </th>
                          <td className={NUM}>{formatVnd(r.buy)}</td>
                          <td className={NUM}>{formatVnd(r.transfer)}</td>
                          <td className={NUM}>{formatVnd(r.sell)}</td>
                          <td className={`${NUM} text-slate-500`}>{s ? formatPercent(s.percent) : "–"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                <p className="pt-2 text-xs text-slate-500">A dash means the bank doesn't buy that currency in cash.</p>
                <Problem section={rates.fx} />
              </div>
            )}
          </div>

          {sections.length > 0 && (
            <p className="text-xs text-slate-500">
              {sections
                .filter((s) => s.updated_at)
                .map((s) => `${s.source} updated ${formatUpdated(s.updated_at)}`)
                .join(" · ")}
              <span className="block">Indicative prices: check with the bank or shop before trading.</span>
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

function SummaryCard({ label, value, note }: { label: string; value: string; note?: ReactNode }) {
  return (
    <div className={CARD}>
      <p className="text-xs font-medium text-slate-500">{label}</p>
      <p className="mt-1 text-xl font-semibold tabular-nums">{value}</p>
      {note && <p className="mt-0.5 text-xs text-slate-500 tabular-nums">{note}</p>}
    </div>
  );
}

const USED_LABEL = { buy: "cash buy", transfer: "transfer", sell: "sell" } as const;

function Converter({ rates, source }: { rates: CurrencyRate[]; source: string }) {
  const [code, setCode] = useState(() => (rates.some((r) => r.code === "USD") ? "USD" : rates[0].code));
  const [direction, setDirection] = useState<ConvertDirection>("toVnd");
  const [amount, setAmount] = useState("100");

  const rate = rates.find((r) => r.code === code) ?? rates[0];
  const value = amount.trim() === "" ? Number.NaN : Number(amount);
  const result = Number.isFinite(value) ? convert(value, rate, direction) : null;
  const from = direction === "toVnd" ? rate.code : "VND";
  const to = direction === "toVnd" ? "VND" : rate.code;
  const pill = (active: boolean) =>
    `rounded-md px-3 py-1.5 text-sm font-medium ${
      active ? "bg-white shadow-sm dark:bg-slate-700" : "text-slate-500 hover:text-slate-900 dark:hover:text-white"
    }`;

  return (
    <section className={CARD} aria-label="Currency converter">
      <h3 className="pb-3 font-semibold">Converter</h3>
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex rounded-lg bg-slate-100 p-0.5 dark:bg-slate-800" role="group" aria-label="Direction">
          <button className={pill(direction === "toVnd")} aria-pressed={direction === "toVnd"} onClick={() => setDirection("toVnd")}>
            {rate.code} → VND
          </button>
          <button className={pill(direction === "fromVnd")} aria-pressed={direction === "fromVnd"} onClick={() => setDirection("fromVnd")}>
            VND → {rate.code}
          </button>
        </div>
        <label className="flex items-center gap-2">
          <span className="sr-only">Amount in {from}</span>
          <input
            type="number"
            inputMode="decimal"
            min={0}
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            className="w-36 rounded-lg border border-slate-300 bg-transparent px-3 py-1.5 tabular-nums dark:border-slate-700"
          />
          <span className="text-slate-500">{from}</span>
        </label>
        <label className="flex items-center gap-2">
          <span className="sr-only">Currency</span>
          <select
            value={rate.code}
            onChange={(e) => setCode(e.target.value)}
            className="rounded-lg border border-slate-300 bg-transparent px-2 py-1.5 dark:border-slate-700 dark:bg-slate-900"
          >
            {rates.map((r) => (
              <option key={r.code} value={r.code}>
                {r.code}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className="mt-3 text-lg font-semibold tabular-nums" aria-live="polite">
        {result ? (
          <>≈ {direction === "toVnd" ? `${formatVnd(Math.round(result.value))} ₫` : `${formatUsd(result.value)} ${to}`}</>
        ) : (
          <span className="text-base font-normal text-slate-500">
            {Number.isFinite(value) && value >= 0 ? `${source} has no rate for this right now.` : "Type an amount."}
          </span>
        )}
      </p>
      {result && (
        <p className="text-xs text-slate-500">
          At {source}'s {USED_LABEL[result.used]} rate of {formatVnd(result.rate)} ₫ per {rate.code}.
        </p>
      )}
    </section>
  );
}

function Problem({ section }: { section: RatesSection }) {
  if (!section.error) return null;
  return (
    <p className="pt-2 text-xs text-amber-600 dark:text-amber-400">
      {section.error}
      {section.stale && " These are the last prices it sent."}
    </p>
  );
}
