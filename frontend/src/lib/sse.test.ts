import { describe, expect, it } from "vitest";
import { SseParser } from "./sse";

describe("SseParser", () => {
  it("parses events split across arbitrary chunk boundaries", () => {
    const stream =
      'event: start\ndata: {"a":1}\n\nevent: token\ndata: {"text":"Hel"}\n\n' +
      'event: token\ndata: {"text":"lo"}\n\nevent: done\ndata: {}\n\n';
    for (const size of [1, 3, 7, stream.length]) {
      const parser = new SseParser();
      const events = [];
      for (let i = 0; i < stream.length; i += size) events.push(...parser.feed(stream.slice(i, i + size)));
      expect(events.map((e) => e.event)).toEqual(["start", "token", "token", "done"]);
      expect(events[1].data).toBe('{"text":"Hel"}');
    }
  });

  it("handles CRLF, comments, default event names and multi-line data", () => {
    const parser = new SseParser();
    const events = parser.feed(": keep-alive\r\n\r\ndata: line1\r\ndata: line2\r\n\r\n");
    expect(events).toEqual([{ event: "message", data: "line1\nline2" }]);
  });

  it("waits for the blank line before emitting", () => {
    const parser = new SseParser();
    expect(parser.feed("event: token\ndata: {}\n")).toEqual([]);
    expect(parser.feed("\n")).toHaveLength(1);
  });
});
