"""Exchange rates against VND and gold prices, fetched when someone looks at them.

Three public sources, each optional (an empty URL in the settings turns it off):
- Vietcombank's rate table (XML): cash buy, transfer and sell rates in VND.
- PNJ's gold prices (JSON): SJC bars, ring gold and jewellery gold, in VND per lượng.
- gold-api.com (JSON): the world spot price in USD per troy ounce.

Results are kept in memory for RATES_CACHE_MINUTES. Each source succeeds or fails on its own:
a source that fails keeps showing its last prices, marked stale, with a plain-language reason.
"""

import asyncio
import logging
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .config import Settings
from .schemas import (
    CurrencyRate,
    FxSection,
    GoldPrice,
    GoldSection,
    RatesOut,
    WorldGold,
    WorldGoldSection,
)

log = logging.getLogger(__name__)

VIETNAM = timezone(timedelta(hours=7))
OUNCES_PER_LUONG = 37.5 / 31.1034768  # 1 lượng = 37.5 g; 1 troy ounce = 31.1034768 g
PNJ_UNIT = 10_000  # PNJ lists prices in 10,000 VND per lượng (13620 = 136,200,000 VND)
# The PNJ products worth showing: SJC bars, PNJ ring gold 999.9, jewellery 24K and 18K.
GOLD_CODES = ("SJC", "N24K", "24K", "75")
RETRY_SECONDS = 60.0  # after a failed fetch, try again sooner than the normal cache time


class RatesError(Exception):
    """A source answered with something that isn't the price list we expect."""


# ---- parsers ---------------------------------------------------------------------------


def _number(value: str | None) -> float | None:
    """'25,530.00' -> 25530.0; '-' or empty -> None (the bank doesn't offer that rate)."""
    text = (value or "").strip().replace(",", "")
    if not text or text == "-":
        return None
    try:
        return float(text)
    except ValueError:
        raise RatesError(f"not a number: {value!r}") from None


def parse_vietcombank(content: bytes | str) -> tuple[list[CurrencyRate], str | None]:
    """Vietcombank's ExrateList XML -> every currency in it, and when the bank last updated it."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise RatesError(f"bad XML: {exc}") from None
    if root.tag != "ExrateList":
        raise RatesError(f"unexpected root {root.tag!r}")
    rates = []
    for item in root.iter("Exrate"):
        code = (item.get("CurrencyCode") or "").strip().upper()
        if not code:
            continue
        rates.append(
            CurrencyRate(
                code=code,
                name=" ".join((item.get("CurrencyName") or code).split()),
                buy=_number(item.get("Buy")),
                transfer=_number(item.get("Transfer")),
                sell=_number(item.get("Sell")),
            )
        )
    if not rates:
        raise RatesError("no rates in the list")
    updated = None
    stamp = (root.findtext("DateTime") or "").strip()
    if stamp:
        try:
            updated = datetime.strptime(stamp, "%m/%d/%Y %I:%M:%S %p").replace(tzinfo=VIETNAM).isoformat()
        except ValueError:
            log.info("Vietcombank sent a date we can't read: %r", stamp)
    return rates, updated


def pick_currencies(rates: list[CurrencyRate], codes: list[str]) -> list[CurrencyRate]:
    """The configured currencies, in the configured order; unknown codes are skipped."""
    by_code = {r.code: r for r in rates}
    return [by_code[c] for c in codes if c in by_code]


def parse_pnj(data: Any) -> tuple[list[GoldPrice], str | None]:
    """PNJ's gold price JSON -> the products in GOLD_CODES, in VND per lượng."""
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise RatesError("no price list")
    by_code = {}
    for item in data["data"]:
        if not isinstance(item, dict):
            continue
        code = str(item.get("masp", "")).strip()
        buy, sell = item.get("giamua"), item.get("giaban")
        if code not in GOLD_CODES or not isinstance(buy, (int, float)) or not isinstance(sell, (int, float)):
            continue
        name = " ".join(str(item.get("tensp") or code).split())
        by_code[code] = GoldPrice(code=code, name=name, buy=buy * PNJ_UNIT, sell=sell * PNJ_UNIT)
    if not by_code:
        raise RatesError("none of the expected gold products")
    updated = None
    stamp = str(data.get("updateDate") or "").strip()
    if stamp:
        try:
            updated = datetime.strptime(stamp, "%d/%m/%Y %H:%M:%S").replace(tzinfo=VIETNAM).isoformat()
        except ValueError:
            log.info("PNJ sent a date we can't read: %r", stamp)
    return [by_code[c] for c in GOLD_CODES if c in by_code], updated


def parse_world_gold(data: Any) -> tuple[float, str | None]:
    """gold-api.com's XAU JSON -> USD per troy ounce, and when it was last traded."""
    price = data.get("price") if isinstance(data, dict) else None
    if not isinstance(price, (int, float)) or isinstance(price, bool) or price <= 0:
        raise RatesError("no gold price")
    updated = data.get("updatedAt")
    return float(price), updated if isinstance(updated, str) and updated else None


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        raise RatesError("not JSON") from None


# ---- service ---------------------------------------------------------------------------


@dataclass
class _Source:
    name: str  # shown to the user: "Vietcombank"
    url: str
    parse: Callable[[httpx.Response], tuple[Any, str | None]]
    data: Any = None  # the last prices that came through
    updated_at: str | None = None
    error: str | None = None


class RatesService:
    def __init__(
        self,
        settings: Settings,
        http: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.currencies = [c.strip().upper() for c in settings.rates_currencies.split(",") if c.strip()]
        self.cache_seconds = settings.rates_cache_minutes * 60.0
        self._http = http or httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=3.0),
            headers={"User-Agent": "voice-assistant/1.0", "Accept": "application/json, application/xml;q=0.9, */*;q=0.5"},
            follow_redirects=False,
        )
        self._clock = clock
        self._lock = asyncio.Lock()
        self._out: RatesOut | None = None
        self._expires = 0.0
        self.fx = _Source("Vietcombank", settings.rates_fx_url.strip(), lambda r: parse_vietcombank(r.content))
        self.gold = _Source("PNJ", settings.rates_gold_url.strip(), lambda r: parse_pnj(_json(r)))
        self.world = _Source("gold-api.com", settings.rates_world_gold_url.strip(), lambda r: parse_world_gold(_json(r)))

    @property
    def sources(self) -> list[_Source]:
        return [s for s in (self.fx, self.gold, self.world) if s.url]

    async def get(self, refresh: bool = False) -> RatesOut:
        """The latest prices; asks the sources again if `refresh` or the cached ones are old."""
        async with self._lock:
            if self._out is not None and not refresh and self._clock() < self._expires:
                return self._out
            await asyncio.gather(*(self._fetch(s) for s in self.sources))
            self._out = self._build()
            failed = any(s.error for s in self.sources)
            self._expires = self._clock() + (min(RETRY_SECONDS, self.cache_seconds) if failed else self.cache_seconds)
            return self._out

    async def _fetch(self, source: _Source) -> None:
        try:
            response = await self._http.get(source.url)
            response.raise_for_status()
            data, updated = source.parse(response)
        except httpx.HTTPError as exc:
            log.info("Fetching prices from %s failed: %r", source.name, exc)
            source.error = f"Couldn't reach {source.name} right now."
        except RatesError as exc:
            log.warning("%s sent prices we can't read: %s", source.name, exc)
            source.error = f"{source.name} sent prices this app couldn't read."
        except Exception:
            log.exception("Fetching prices from %s failed", source.name)
            source.error = f"Couldn't get prices from {source.name} right now."
        else:
            source.data, source.updated_at, source.error = data, updated, None

    def _build(self) -> RatesOut:
        all_rates: list[CurrencyRate] = self.fx.data or []
        fx = gold = world = None
        if self.fx.url:
            fx = FxSection(**self._meta(self.fx), rates=pick_currencies(all_rates, self.currencies))
        if self.gold.url:
            gold = GoldSection(**self._meta(self.gold), prices=self.gold.data or [])
        if self.world.url:
            price = None
            if self.world.data is not None:
                usd = next((r for r in all_rates if r.code == "USD"), None)
                usd_rate = usd and (usd.sell or usd.transfer)
                price = WorldGold(
                    usd_per_ounce=self.world.data,
                    vnd_per_luong=round(self.world.data * OUNCES_PER_LUONG * usd_rate) if usd_rate else None,
                )
            world = WorldGoldSection(**self._meta(self.world), price=price)
        return RatesOut(fx=fx, gold=gold, world=world, fetched_at=datetime.now(timezone.utc).isoformat())

    @staticmethod
    def _meta(source: _Source) -> dict[str, Any]:
        return {
            "source": source.name,
            "updated_at": source.updated_at,
            "stale": source.error is not None and source.data is not None,
            "error": source.error,
        }

    async def aclose(self) -> None:
        await self._http.aclose()
