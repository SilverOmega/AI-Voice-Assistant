import { activeStep, toolLabel, traceSummary } from "../lib/agentTrace";
import type { AgentStatus, AgentTrace as Trace } from "../lib/types";
import { TeamIcon } from "./icons";

const STATUS: Record<AgentStatus, { mark: string; label: string; cls: string }> = {
  waiting: { mark: "○", label: "waiting", cls: "text-slate-400" },
  running: { mark: "◐", label: "working", cls: "text-violet-500 animate-pulse" },
  done: { mark: "✓", label: "done", cls: "text-emerald-600 dark:text-emerald-400" },
  failed: { mark: "!", label: "didn't finish", cls: "text-amber-600 dark:text-amber-400" },
  skipped: { mark: "–", label: "skipped", cls: "text-slate-400" },
};

/** Under an assistant reply: which helpers worked on it and what they did. Live while it streams. */
export function AgentTrace({ trace, pending }: { trace?: Trace | null; pending?: boolean }) {
  if (!trace || trace.steps.length === 0) return null;
  const active = pending ? activeStep(trace) : undefined;
  const lastTool = active?.tools[active.tools.length - 1];

  return (
    <details className="mt-1.5 w-full max-w-[85%] rounded-xl border border-violet-200 bg-violet-50/50 px-3 py-1.5 text-xs dark:border-violet-500/30 dark:bg-violet-500/5" open={pending}>
      <summary className="flex cursor-pointer list-none items-center gap-1.5 text-slate-600 dark:text-slate-300">
        <TeamIcon width={13} height={13} className={active ? "animate-pulse text-violet-500" : "text-violet-500"} />
        {active ? (
          <span className="truncate" role="status">
            {active.title} is working{lastTool?.status === "running" ? `: ${toolLabel(lastTool.tool).toLowerCase()}…` : "…"}
          </span>
        ) : (
          <span className="truncate">Worked with {traceSummary(trace)}</span>
        )}
      </summary>
      <ol className="mt-2 space-y-2 pb-1">
        {trace.steps.map((step, i) => {
          const s = STATUS[step.status];
          return (
            <li key={i}>
              <div className="flex items-baseline gap-1.5">
                <span className={`w-3 shrink-0 text-center font-bold ${s.cls}`} aria-label={s.label}>
                  {s.mark}
                </span>
                <span className="font-medium text-slate-700 dark:text-slate-200">{step.title}</span>
                <span className="min-w-0 truncate text-slate-500" title={step.task}>
                  {step.task}
                </span>
              </div>
              {step.tools.length > 0 && (
                <ul className="ml-4.5 mt-1 space-y-0.5 border-l border-violet-200 pl-2 dark:border-violet-500/30">
                  {step.tools.map((tool, j) => (
                    <li key={j} className="flex items-baseline gap-1.5 text-slate-500 dark:text-slate-400">
                      <span className={STATUS[tool.status].cls}>{STATUS[tool.status].mark}</span>
                      <span className="shrink-0 text-slate-600 dark:text-slate-300">{toolLabel(tool.tool)}</span>
                      {tool.input && tool.input !== "{}" && (
                        <span className="min-w-0 truncate font-mono text-[11px]" title={tool.input}>
                          {tool.input}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
              {step.summary && step.status !== "running" && (
                <p className="ml-4.5 mt-1 line-clamp-3 whitespace-pre-wrap text-slate-500 dark:text-slate-400" title={step.summary}>
                  {step.summary}
                </p>
              )}
            </li>
          );
        })}
      </ol>
      {trace.note && <p className="pb-1 text-amber-700 dark:text-amber-300">{trace.note}</p>}
    </details>
  );
}
