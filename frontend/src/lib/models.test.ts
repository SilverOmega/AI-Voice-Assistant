import { describe, expect, it } from "vitest";
import { modelLabel } from "./models";

describe("modelLabel", () => {
  it("reads like a name", () => {
    expect(modelLabel("anthropic", "claude-sonnet-5-5")).toBe("Claude Sonnet 5.5");
    expect(modelLabel("anthropic", "claude-opus-5-5")).toBe("Claude Opus 5.5");
    expect(modelLabel("ollama", "llama3.2:latest")).toBe("llama3.2");
    expect(modelLabel("ollama", "qwen2.5:7b")).toBe("qwen2.5:7b");
    expect(modelLabel("echo", "")).toBe("Echo");
  });
});
