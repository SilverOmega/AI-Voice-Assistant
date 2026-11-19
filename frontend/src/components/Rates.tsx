import { useRates } from "../hooks/useRates";
import { formatMillions, formatUpdated, formatUsd, formatVnd } from "../lib/rates";
import type { RatesSection } from "../lib/types";
import { ChevronDownIcon, RefreshIcon } from "./icons";

const HEAD = "pb-0.5 text-[11px] font-medium text-slate-500";
const NUM = "whitespace-nowrap py-0.5 pl-2 text-right tabular-nums";

/** The drawer's Rates panel: gold prices and exchange rates against VND, fetched when opened. */
export function Rates({ visible }: { visible: boolean }) {
  const { open, toggle, rates, loading, error, refresh } = useRates(visible);
  const sections = rates ? [rates.gold, rates.world, rates.fx].filter((s) => s !== null) : [];
  const off = rates !== null && sections.length === 0;

  return (
    <section className="border-t border-slate-200 px-2 py-2 dark:border-slate-800" aria-label="Exchange rates and gold prices">
      <div className="flex items-center">
        <button
          onClick={toggle}
          aria-expanded={open}
          aria-controls="rates-panel"
          className="flex flex-1 items-center gap-1 rounded px-3 pb-1 text-left text-xs font-semibold uppercase tracking-wide text-slate-500 hover:text-slate-900 dark:hover:text-white"
        >
          Rates
          <ChevronDownIcon width={12} height={12} className={`transition-transform ${open ? "rotate-180" : ""}`} />
        </button>
        {open && !off && (
          <button
            onClick={() => void refresh()}
            disabled={loading}
            className="mr-1 rounded p-1 pointer-coarse:p-2 text-slate-500 hover:text-slate-900 disabled:opacity-50 dark:hover:text-white"
            aria-label="Get the latest rates"
            title="Get the latest rates"
          >
            <RefreshIcon width={14} height={14} className={loading ? "animate-spin" : ""} />
          </button>
        )}
      </div>
      {open && (
        <div id="rates-panel" className="max-h-72 space-y-2 overflow-y-auto px-3 text-sm" aria-busy={loading}>
          {error && <p className="text-xs text-amber-600 dark:text-amber-400">{error}</p>}
          {!rates && !error && <p className="text-xs text-slate-500">Loading…</p>}
          {off && <p className="text-xs text-slate-500">Rates are turned off on this server.</p>}

          {rates?.gold && (
            <div>
              <table className="w-full">
                <caption className="sr-only">Gold prices per lượng at {rates.gold.source}</caption>
                <thead>
                  <tr>
                    <th scope="col" className={`${HEAD} text-left`}>Gold (per lượng)</th>
                    <th scope="col" className={`${HEAD} text-right`}>Buy</th>
                    <th scope="col" className={`${HEAD} text-right`}>Sell</th>
                  </tr>
                </thead>
                <tbody>
                  {rates.gold.prices.map((p) => (
                    <tr key={p.code}>
                      <th scope="row" className="max-w-0 truncate py-0.5 text-left font-normal" title={p.name}>
                        {p.name}
                      </th>
                      <td className={NUM}>{formatMillions(p.buy)}</td>
                      <td className={NUM}>{formatMillions(p.sell)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <Problem section={rates.gold} />
            </div>
          )}

          {rates?.world && (
            <div>
              {rates.world.price && (
                <p className="flex items-baseline justify-between gap-2">
                  <span>World gold</span>
                  <span className="text-right tabular-nums">
                    {formatUsd(rates.world.price.usd_per_ounce)} USD/oz
                    {rates.world.price.vnd_per_luong !== null && (
                      <span className="block text-xs text-slate-500" title="At the bank's dollar rate, before the Vietnamese premium">
                        ≈ {formatMillions(rates.world.price.vnd_per_luong)} per lượng
                      </span>
                    )}
                  </span>
                </p>
              )}
              <Problem section={rates.world} />
            </div>
          )}

          {rates?.fx && (
            <div>
              <table className="w-full">
                <caption className="sr-only">Exchange rates in đồng at {rates.fx.source}</caption>
                <thead>
                  <tr>
                    <th scope="col" className={`${HEAD} text-left`}>Currency (VND)</th>
                    <th scope="col" className={`${HEAD} text-right`}>Buy</th>
                    <th scope="col" className={`${HEAD} text-right`}>Sell</th>
                  </tr>
                </thead>
                <tbody>
                  {rates.fx.rates.map((r) => (
                    <tr key={r.code}>
                      <th scope="row" className="py-0.5 text-left font-medium" title={r.name}>
                        {r.code}
                      </th>
                      <td className={NUM}>{formatVnd(r.buy)}</td>
                      <td className={NUM}>{formatVnd(r.sell)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <Problem section={rates.fx} />
            </div>
          )}

          {sections.length > 0 && (
            <p className="text-[11px] text-slate-500">
              {sections
                .filter((s) => s.updated_at)
                .map((s) => `${s.source} ${formatUpdated(s.updated_at)}`)
                .join(" · ")}
              <span className="block">Indicative prices: check with the bank or shop before trading.</span>
            </p>
          )}
        </div>
      )}
    </section>
  );
}

function Problem({ section }: { section: RatesSection }) {
  if (!section.error) return null;
  return (
    <p className="text-xs text-amber-600 dark:text-amber-400">
      {section.error}
      {section.stale && " These are the last prices it sent."}
    </p>
  );
}
