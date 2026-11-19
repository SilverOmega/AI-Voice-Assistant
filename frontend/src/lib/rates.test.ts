import { describe, expect, it } from "vitest";
import { formatMillions, formatUpdated, formatUsd, formatVnd } from "./rates";

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
