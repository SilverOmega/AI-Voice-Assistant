import type { AgentStatus, AgentStepTrace, AgentTrace, ToolTrace } from "./types";

/** `plan` event: the helpers the lead assistant picked, in order. */
export interface PlanEvent {
  steps: { agent: string; title: string; task: string }[];
}

/** `agent` event: one helper started or finished. */
export interface AgentEvent {
  step: number;
  agent: string;
  title: string;
  task: string;
  status: AgentStatus;
  summary: string;
}

/** `tool` event: a helper started or finished using a tool. */
export interface ToolEvent extends ToolTrace {
  step: number;
  index: number;
  agent: string;
}

const empty = (): AgentTrace => ({ steps: [], note: "" });

export function applyPlan(_trace: AgentTrace | null | undefined, event: PlanEvent): AgentTrace {
  return {
    note: "",
    steps: event.steps.map((s) => ({ ...s, status: "waiting", summary: "", tools: [] })),
  };
}

function withStep(
  trace: AgentTrace | null | undefined,
  index: number,
  fallback: Omit<AgentStepTrace, "status" | "summary" | "tools">,
  fn: (step: AgentStepTrace) => AgentStepTrace,
): AgentTrace {
  const base = trace ?? empty();
  const steps = base.steps.slice();
  while (steps.length <= index) {
    steps.push({ ...fallback, status: "waiting", summary: "", tools: [] });
  }
  steps[index] = fn(steps[index]);
  return { ...base, steps };
}

export function applyAgent(trace: AgentTrace | null | undefined, e: AgentEvent): AgentTrace {
  return withStep(trace, e.step, e, (step) => ({
    ...step,
    status: e.status,
    summary: e.summary || step.summary,
  }));
}

export function applyTool(trace: AgentTrace | null | undefined, e: ToolEvent): AgentTrace {
  const fallback = { agent: e.agent, title: e.agent, task: "" };
  return withStep(trace, e.step, fallback, (step) => {
    const tools = step.tools.slice();
    const { tool, input, output, status, action_id } = e;
    tools[e.index] = { tool, input, output, status, action_id };
    return { ...step, tools: tools.map((t) => t ?? { tool: "", input: "", output: "", status: "waiting" }) };
  });
}

/** "Researcher, Coder · 4 tool uses" */
export function traceSummary(trace: AgentTrace): string {
  const names = [...new Set(trace.steps.map((s) => s.title))];
  const uses = trace.steps.reduce((n, s) => n + s.tools.length, 0);
  const tools = uses ? ` · ${uses} tool use${uses === 1 ? "" : "s"}` : "";
  return `${names.join(", ")}${tools}`;
}

/** The step a still-running trace is on, for a live status line. */
export function activeStep(trace: AgentTrace): AgentStepTrace | undefined {
  return trace.steps.find((s) => s.status === "running");
}

const TOOL_LABELS: Record<string, string> = {
  web_search: "Searched the web",
  fetch_page: "Read a web page",
  list_documents: "Listed documents",
  read_document: "Read a document",
  translate: "Translated",
  list_events: "Checked the schedule",
  list_actions: "Checked earlier tasks",
  get_action_result: "Checked a task's result",
  propose_email: "Drafted an email",
  propose_event: "Drafted an event",
  propose_document: "Drafted a document",
  propose_workflow: "Drafted a workflow run",
  list_project_files: "Listed project files",
  read_project_file: "Read a project file",
  search_project: "Searched the project",
  propose_file_edit: "Proposed a file change",
  propose_code_run: "Proposed a code run",
};

export function toolLabel(name: string): string {
  return TOOL_LABELS[name] ?? name.replace(/_/g, " ");
}
