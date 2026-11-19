import httpx
import pytest

from app.config import Settings
from app.llm import ProviderRegistry
from app.llm.echo import EchoProvider
from app.main import create_app
from app.rates import (
    OUNCES_PER_LUONG,
    RatesError,
    RatesService,
    parse_pnj,
    parse_vietcombank,
    parse_world_gold,
    pick_currencies,
)

from .test_auth import PASSCODE, REMOTE

FX_URL = "https://fx.test/rates.xml"
GOLD_URL = "https://gold.test/prices"
WORLD_URL = "https://world.test/xau"

VCB_XML = """<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<ExrateList>
  <DateTime>11/19/2026 3:04:16 PM</DateTime>
  <Exrate CurrencyCode="AUD" CurrencyName="AUSTRALIAN DOLLAR   " Buy="16,793.14" Transfer="16,962.77" Sell="17,506.58" />
  <Exrate CurrencyCode="EUR" CurrencyName="EURO                " Buy="27,511.85" Transfer="27,789.75" Sell="28,963.23" />
  <Exrate CurrencyCode="INR" CurrencyName="INDIAN RUPEE        " Buy="-" Transfer="274.32" Sell="286.13" />
  <Exrate CurrencyCode="USD" CurrencyName="US DOLLAR           " Buy="25,530.00" Transfer="25,560.00" Sell="25,940.00" />
  <Source>Joint Stock Commercial Bank for Foreign Trade of Vietnam - Vietcombank</Source>
</ExrateList>"""

PNJ_JSON = {
    "chinhanh": "hochiminh",
    "updateDate": "19/11/2026 13:37:33",
    "data": [
        {"masp": "SJC", "tensp": "Vàng miếng SJC 999.9", "giaban": 13620, "giamua": 13320},
        {"masp": "N24K", "tensp": "Nhẫn Trơn PNJ 999.9", "giaban": 13300, "giamua": 12880},
        {"masp": "KB", "tensp": "Vàng Kim Bảo 999.9", "giaban": 13300, "giamua": 12880},
        {"masp": "75", "tensp": "Vàng 750 (18K)", "giaban": 9994, "giamua": 9374},
        {"masp": "24K", "tensp": "Vàng nữ trang 999.9", "giaban": 13325, "giamua": 12746},
    ],
}

WORLD_JSON = {"currency": "USD", "name": "Gold", "price": 3735.57, "symbol": "XAU", "updatedAt": "2026-11-19T08:12:32Z"}


# ---- parsers ---------------------------------------------------------------------------


def test_vietcombank_rates():
    rates, updated = parse_vietcombank(VCB_XML.encode())
    assert updated == "2026-11-19T15:04:16+07:00"
    usd = next(r for r in rates if r.code == "USD")
    assert (usd.name, usd.buy, usd.transfer, usd.sell) == ("US DOLLAR", 25530.0, 25560.0, 25940.0)
    inr = next(r for r in rates if r.code == "INR")
    assert inr.buy is None and inr.sell == 286.13  # the bank doesn't buy rupees in cash
    picked = pick_currencies(rates, ["USD", "XYZ", "EUR"])
    assert [r.code for r in picked] == ["USD", "EUR"]


@pytest.mark.parametrize(
    "content",
    [b"<html>Just a moment...</html>", b"not xml at all", b"<ExrateList></ExrateList>",
     b'<ExrateList><Exrate CurrencyCode="USD" Sell="abc"/></ExrateList>'],
)
def test_vietcombank_refuses_what_isnt_a_rate_table(content):
    with pytest.raises(RatesError):
        parse_vietcombank(content)


def test_pnj_gold_prices_in_vnd_per_luong():
    prices, updated = parse_pnj(PNJ_JSON)
    assert updated == "2026-11-19T13:37:33+07:00"
    assert [p.code for p in prices] == ["SJC", "N24K", "24K", "75"]
    sjc = prices[0]
    assert (sjc.name, sjc.buy, sjc.sell) == ("Vàng miếng SJC 999.9", 133_200_000, 136_200_000)


@pytest.mark.parametrize(
    "data", [None, [], {"data": "x"}, {"data": [{"masp": "KB", "giaban": 1, "giamua": 1}]},
             {"data": [{"masp": "SJC", "giaban": "13620", "giamua": None}]}],
)
def test_pnj_refuses_what_isnt_a_price_list(data):
    with pytest.raises(RatesError):
        parse_pnj(data)


def test_world_gold():
    assert parse_world_gold(WORLD_JSON) == (3735.57, "2026-11-19T08:12:32Z")
    for bad in (None, {}, {"price": "3735"}, {"price": 0}, {"price": True}):
        with pytest.raises(RatesError):
            parse_world_gold(bad)


# ---- service ---------------------------------------------------------------------------


class Sources:
    """Fake price sources: counts requests; `fail[url]` is a status code or an exception."""

    def __init__(self):
        self.calls: list[str] = []
        self.fail: dict[str, object] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)
        failure = self.fail.get(url)
        if isinstance(failure, Exception):
            raise failure
        if isinstance(failure, int):
            return httpx.Response(failure, text="nope")
        if url == FX_URL:
            return httpx.Response(200, content=VCB_XML.encode(), headers={"content-type": "text/xml"})
        if url == GOLD_URL:
            return httpx.Response(200, json=PNJ_JSON)
        if url == WORLD_URL:
            return httpx.Response(200, json=WORLD_JSON)
        return httpx.Response(404)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def rates_settings(**kw) -> Settings:
    base = dict(
        rates_fx_url=FX_URL, rates_gold_url=GOLD_URL, rates_world_gold_url=WORLD_URL,
        rates_currencies="usd, EUR,JPY", rates_cache_minutes=10,
    )
    return Settings(**{**base, **kw})


@pytest.fixture
def sources():
    return Sources()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
async def service(sources, clock):
    svc = RatesService(rates_settings(), http=httpx.AsyncClient(transport=httpx.MockTransport(sources)), clock=clock)
    yield svc
    await svc.aclose()


async def test_all_prices_together(service):
    out = await service.get()
    assert [r.code for r in out.fx.rates] == ["USD", "EUR"]  # JPY isn't in the table
    assert out.fx.source == "Vietcombank" and not out.fx.stale and out.fx.error is None
    assert out.gold.prices[0].sell == 136_200_000
    assert out.world.price.usd_per_ounce == 3735.57
    assert out.world.price.vnd_per_luong == round(3735.57 * OUNCES_PER_LUONG * 25940)
    assert 116_000_000 < out.world.price.vnd_per_luong < 118_000_000


async def test_prices_are_kept_for_a_while(service, sources, clock):
    await service.get()
    assert len(sources.calls) == 3
    clock.now += 9 * 60
    await service.get()
    assert len(sources.calls) == 3
    await service.get(refresh=True)
    assert len(sources.calls) == 6
    clock.now += 11 * 60
    await service.get()
    assert len(sources.calls) == 9


async def test_one_source_down_leaves_the_others(service, sources):
    sources.fail[GOLD_URL] = httpx.ConnectError("no route")
    sources.fail[WORLD_URL] = 503
    out = await service.get()
    assert [r.code for r in out.fx.rates] == ["USD", "EUR"] and out.fx.error is None
    assert out.gold.prices == [] and not out.gold.stale
    assert out.gold.error == "Couldn't reach PNJ right now."
    assert out.world.price is None and out.world.error == "Couldn't reach gold-api.com right now."


async def test_a_failed_refresh_keeps_the_last_prices(service, sources, clock):
    await service.get()
    sources.fail[FX_URL] = 500
    out = await service.get(refresh=True)
    assert out.fx.stale and out.fx.error == "Couldn't reach Vietcombank right now."
    assert [r.code for r in out.fx.rates] == ["USD", "EUR"]
    assert out.world.price.vnd_per_luong is not None  # still converted at the last USD rate
    # A failure is retried after a minute rather than the whole cache time.
    del sources.fail[FX_URL]
    clock.now += 61
    out = await service.get()
    assert not out.fx.stale and out.fx.error is None


async def test_a_page_that_isnt_prices(service, sources):
    def cloudflare(request):
        if str(request.url) == GOLD_URL:
            return httpx.Response(200, text="<html>Just a moment...</html>")
        return sources(request)

    await service._http.aclose()
    service._http = httpx.AsyncClient(transport=httpx.MockTransport(cloudflare))
    out = await service.get()
    assert out.gold.error == "PNJ sent prices this app couldn't read."
    assert out.fx.error is None


async def test_empty_urls_turn_parts_off(sources):
    svc = RatesService(
        rates_settings(rates_gold_url="", rates_fx_url=""),
        http=httpx.AsyncClient(transport=httpx.MockTransport(sources)),
    )
    out = await svc.get()
    await svc.aclose()
    assert out.fx is None and out.gold is None
    assert sources.calls == [WORLD_URL]
    assert out.world.price.vnd_per_luong is None  # no USD rate to convert with


# ---- route -----------------------------------------------------------------------------


@pytest.fixture
async def app(tmp_path, sources):
    settings = Settings(database_path=str(tmp_path / "test.db"), llm_provider="echo", app_passcode=PASSCODE)
    app = create_app(settings, ProviderRegistry({"echo": EchoProvider()}))
    async with app.router.lifespan_context(app):
        await app.state.rates.aclose()
        app.state.rates = RatesService(rates_settings(), http=httpx.AsyncClient(transport=httpx.MockTransport(sources)))
        yield app


async def test_rates_route(app, sources):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
        body = (await client.get("/api/rates")).json()
        assert body["fx"]["rates"][0]["code"] == "USD"
        assert body["gold"]["prices"][0]["code"] == "SJC"
        assert body["world"]["price"]["usd_per_ounce"] == 3735.57
        await client.get("/api/rates")
        assert len(sources.calls) == 3
        await client.get("/api/rates?refresh=true")
        assert len(sources.calls) == 6
        # Other devices have to sign in first.
        assert (await client.get("/api/rates", headers=REMOTE)).status_code == 401
