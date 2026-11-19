import { describe, expect, it } from "vitest";
import { convert, formatMillions, formatPercent, formatUpdated, formatUsd, formatVnd, spread } from "./rates";
import type { CurrencyRate } from "./types";

const USD: CurrencyRate = { code: "USD", name: "US DOLLAR", buy: 25530, transfer: 25560, sell: 25940 };

describe("converter", () => {
  it("uses the bank's buy rate when you sell foreign money", () => {
    expect(convert(100, USD, "toVnd")).toEqual({ value: 2_553_000, used: "buy", rate: 25530 });
  });

  it("falls back to the transfer rate when the bank doesn't buy cash", () => {
    expect(convert(10, { ...USD, buy: null }, "toVnd")).toEqual({ value: 255_600, used: "transfer", rate: 25560 });
  });

  it("uses the sell rate when you buy foreign money", () => {
    expect(convert(2_594_000, USD, "fromVnd")).toEqual({ value: 100, used: "sell", rate: 25940 });
  });

  it("gives up on missing rates and unusable amounts", () => {
    expect(convert(10, { ...USD, buy: null, transfer: null }, "toVnd")).toBeNull();
    expect(convert(10, { ...USD, sell: null }, "fromVnd")).toBeNull();
    expect(convert(-1, USD, "toVnd")).toBeNull();
    expect(convert(Number.NaN, USD, "fromVnd")).toBeNull();
  });
});

describe("spread", () => {
  it("is sell minus buy, and that as a share of buy", () => {
    const s = spread(25530, 25940);
    expect(s?.amount).toBe(410);
    expect(formatPercent(s!.percent)).toBe("1,61%");
  });

  it("needs both sides", () => {
    expect(spread(null, 25940)).toBeNull();
    expect(spread(25530, null)).toBeNull();
  });
});

describe("rate formats", () => {
  it("writes đồng with dots between thousands", () => {
    expect(formatVnd(25940)).toBe("25.940");
    expect(formatVnd(27511.85)).toBe("27.512");
    expect(formatVnd(166.1)).toBe("166,10");
    expect(formatVnd(null)).toBe("–");
  });

  it("writes gold prices in millions per lượng", () => {
    expect(formatMillions(136_200_000)).toBe("136,2 tr");
    expect(formatMillions(133_250_000)).toBe("133,25 tr");
    expect(formatMillions(99_000_000)).toBe("99 tr");
    expect(formatMillions(null)).toBe("–");
  });

  it("writes dollars", () => {
    expect(formatUsd(3738.600098)).toBe("3.738,60");
  });

  it("shows only the time for today", () => {
    const now = new Date(2026, 10, 19, 16, 0);
    expect(formatUpdated(new Date(2026, 10, 19, 13, 37).toISOString(), now)).toBe("13:37");
    expect(formatUpdated(new Date(2026, 10, 18, 9, 5).toISOString(), now)).toBe("18/11 09:05");
    expect(formatUpdated(null, now)).toBe("");
    expect(formatUpdated("not a date", now)).toBe("");
  });
});
