import { describe, expect, it } from "vitest";
import { liveText } from "./dictationText";

describe("liveText", () => {
  it("adds the finished phrases and the one being heard", () => {
    expect(liveText("", ["Hello there."], "how are")).toBe("Hello there. how are");
  });

  it("keeps what was typed before, with one space", () => {
    expect(liveText("Note:", [" first "], "")).toBe("Note: first");
    expect(liveText("Line one\n", ["second"], "")).toBe("Line one\nsecond");
    expect(liveText("ends with space ", [], "word")).toBe("ends with space word");
  });

  it("leaves the box alone until something is heard", () => {
    expect(liveText("typed", [], "")).toBe("typed");
    expect(liveText("typed", ["", " "], "  ")).toBe("typed");
    expect(liveText("", [], "")).toBe("");
  });
});
