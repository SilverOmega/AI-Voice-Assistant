import { describe, expect, it, vi } from "vitest";
import { dispatchStreamEvent, type StreamHandlers } from "./api";

describe("chat stream events", () => {
  it("routes agent events to their handlers and ignores unknown ones", () => {
    const handlers: StreamHandlers = {
      onToken: vi.fn(),
      onPlan: vi.fn(),
      onAgent: vi.fn(),
      onTool: vi.fn(),
      onAction: vi.fn(),
    };
    dispatchStreamEvent("plan", { steps: [] }, handlers);
    dispatchStreamEvent("agent", { step: 0, status: "running" }, handlers);
    dispatchStreamEvent("tool", { step: 0, index: 0, tool: "web_search" }, handlers);
    dispatchStreamEvent("action", { action: { id: "a1" } }, handlers);
    dispatchStreamEvent("something_new", { x: 1 }, handlers);
    dispatchStreamEvent("token", { text: "Hi" }, handlers);
    expect(handlers.onPlan).toHaveBeenCalledWith({ steps: [] });
    expect(handlers.onAgent).toHaveBeenCalledWith({ step: 0, status: "running" });
    expect(handlers.onTool).toHaveBeenCalledWith({ step: 0, index: 0, tool: "web_search" });
    expect(handlers.onAction).toHaveBeenCalledWith({ id: "a1" });
    expect(handlers.onToken).toHaveBeenCalledTimes(1);
    expect(handlers.onToken).toHaveBeenCalledWith("Hi");
  });
});
