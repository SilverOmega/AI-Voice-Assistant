import { describe, expect, it } from "vitest";
import { activeStep, applyAgent, applyPlan, applyTool, toolLabel, traceSummary } from "./agentTrace";

const plan = {
  steps: [
    { agent: "researcher", title: "Researcher", task: "Find it" },
    { agent: "coder", title: "Coder", task: "Write it" },
  ],
};

describe("agent trace", () => {
  it("builds the trace from stream events", () => {
    let t = applyPlan(null, plan);
    expect(t.steps.map((s) => s.status)).toEqual(["waiting", "waiting"]);
    t = applyAgent(t, { step: 0, agent: "researcher", title: "Researcher", task: "Find it", status: "running", summary: "" });
    expect(activeStep(t)?.agent).toBe("researcher");
    t = applyTool(t, { step: 0, index: 0, agent: "researcher", tool: "web_search", input: "{}", output: "", status: "running" });
    t = applyTool(t, { step: 0, index: 0, agent: "researcher", tool: "web_search", input: "{}", output: "3 links", status: "done" });
    t = applyAgent(t, { step: 0, agent: "researcher", title: "Researcher", task: "Find it", status: "done", summary: "Found it." });
    expect(t.steps[0]).toMatchObject({ status: "done", summary: "Found it." });
    expect(t.steps[0].tools).toEqual([
      { tool: "web_search", input: "{}", output: "3 links", status: "done", action_id: undefined },
    ]);
    expect(activeStep(t)).toBeUndefined();
    expect(traceSummary(t)).toBe("Researcher, Coder · 1 tool use");
  });

  it("does not change the trace it was given", () => {
    const t = applyPlan(null, plan);
    applyAgent(t, { step: 1, agent: "coder", title: "Coder", task: "Write it", status: "running", summary: "" });
    expect(t.steps[1].status).toBe("waiting");
  });

  it("copes with events for steps it hasn't seen", () => {
    const t = applyTool(undefined, { step: 1, index: 0, agent: "coder", tool: "read_project_file", input: "", output: "", status: "running" });
    expect(t.steps).toHaveLength(2);
    expect(t.steps[1].tools[0].tool).toBe("read_project_file");
  });

  it("labels tools in plain words", () => {
    expect(toolLabel("propose_code_run")).toBe("Proposed a code run");
    expect(toolLabel("some_new_tool")).toBe("some new tool");
  });
});
