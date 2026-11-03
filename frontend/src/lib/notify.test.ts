import { describe, expect, it } from "vitest";
import { localNow, parseLocal, timeAgo } from "./notify";

describe("local time helpers", () => {
  it("formats local time the way the server and datetime-local inputs expect", () => {
    expect(localNow(new Date(2026, 0, 5, 7, 3))).toBe("2026-01-05T07:03");
  });

  it("round-trips through parseLocal", () => {
    const d = parseLocal("2026-11-02T15:30");
    expect([d.getFullYear(), d.getMonth(), d.getDate(), d.getHours(), d.getMinutes()]).toEqual([2026, 10, 2, 15, 30]);
    expect(localNow(d)).toBe("2026-11-02T15:30");
  });
});

describe("timeAgo", () => {
  const now = Date.parse("2026-11-03T12:00:00Z");
  it("reads naturally", () => {
    expect(timeAgo("2026-11-03T11:59:40Z", now)).toBe("just now");
    expect(timeAgo("2026-11-03T11:55:00Z", now)).toBe("5 min ago");
    expect(timeAgo("2026-11-03T09:00:00Z", now)).toBe("3 h ago");
  });
});
