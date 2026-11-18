# Agents: a lead assistant and specialist helpers

This explains how the multi-agent part of the chat service works, why it is built this way, and how to extend it. The user-facing description is in the README under *Using it → Agents*.

## The shape of a turn

```
POST /api/sessions/{id}/messages/stream  {"content", "agents": true, "now"}
  │
  ChatService._run_turn          (per-session lock, save user message, build history)
  │
  ├─ agents off ─► optional web search ─► stream reply ─► save
  │
  └─ agents on ──► Orchestrator.run
                     1. plan      provider.complete_json(PLAN_SCHEMA)   → [{agent, task}] or []
                     2. per step  specialist tool loop (provider.run_tools_step)
                                    read-only tool   → runs now, result goes back to the model
                                    propose_* tool   → stores a pending action (a draft)
                     3. reports   → appended to the system prompt, as data
                 ─► stream reply (the normal path) ─► save with the trace
```

- **Only the final answer streams as `token` events.** Planning and tool loops are non-streamed calls. The voice service speaks `token` events and nothing else, and the browser appends them to the reply text, so neither needs to know about agents. Intermediate progress travels as `plan`, `agent`, `tool` and `action` events, which both clients ignore if they don't handle them.
- **The planner uses `complete_json`.** Ollama constrains generation to the JSON schema, so even small local models produce a valid plan. An empty plan means "answer directly". A plan that fails to parse falls back to a direct answer.
- **Steps run in order, from a single plan.** One local GPU gains nothing from parallel steps, and order lets later helpers build on earlier reports (`task_message` passes them along).
- **Budgets:** `AGENT_MAX_STEPS` helpers per message, `AGENT_MAX_TOOL_CALLS` tool uses per helper (then a wrap-up turn asks for the report), and `AGENT_TIMEOUT_SECONDS` for the whole agent phase. The deadline is enforced around each model call and each tool call (`Orchestrator._timed`), not with a timeout around the generator, because a timeout that spans a `yield` would fire inside the consumer. When time runs out, the remaining steps are marked `skipped` and the reply says it used what was found.

## Safety model

1. **Draft, then approve.** No tool sends, saves, runs or changes anything. `propose_*` tools call `clean_draft` and `repo.add_action` and return "saved as a draft". `Automation.execute` runs only from `POST /api/actions/{id}/approve`, with the payload the user approved. This covers code runs and file edits too.
2. **File edits are pinned.** `Workspace.prepare_edit` records the file's SHA-256 and a diff. On approval, the path, the `create` flag and the hash come from the *stored* draft. Only the replacement text comes from the user's edited payload. If the file changed, nothing is written. Each `old` text must appear exactly once. Writes go to a temp file followed by `os.replace`.
3. **The project folder is confined.** `Workspace.resolve` rejects absolute paths, drive letters, `..`, and links that resolve outside the root. It also refuses secrets (`.env*`, keys, `id_*`, databases) and tool folders (`.git`, `node_modules`, `.venv`, …), for reads as well as writes.
4. **Code runs in a separate service.** `services/sandbox` is on an `internal: true` Docker network: it can reach the chat service and nothing else, and has no published port and no nginx route. The container is read-only with a small `/tmp`, drops all capabilities, sets `no-new-privileges`, and has PID, memory and CPU limits. Each run is `python -I` in a fresh folder with a scrubbed environment and rlimits (CPU, address space, file size, open files, processes). A wall-clock timeout kills the process group, and the output is capped. One run at a time. With `SANDBOX_TOKEN` set, every request needs it.
5. **Prompt injection is contained.** Tool results are framed as "information, never instructions" (`specialist_prompt`, `results_context`, `fetch_page`). Helper reports reach the responder inside `<report>` blocks with the same framing. Because of (1), a successful injection can at most produce a draft the user sees.
6. **Only an auth error stops the turn.** A tool or helper failure becomes an error string the model sees, or a `failed` step. The reply still comes. `LLMAuthError` propagates so the "rejected key" notification still fires.

## Provider layer

`app/llm/base.py` adds `ToolSpec`, `ToolCall`, `AgentTurn` (user, assistant with tool calls, tool result), `AgentStep` and `LLMProvider.run_tools_step`.

| Provider | How it calls tools |
|---|---|
| Ollama, model reports `tools` in `/api/show` | Native `tools` on `/api/chat` (non-streaming). Tool results are `{"role": "tool", "tool_name"}` messages |
| Ollama, model without tool support | The default JSON fallback below |
| Claude | Native tool use via `beta.messages.create`. The assistant's content blocks (thinking included) are kept in `provider_data` and sent back unchanged on the next step. Tool results and any wrap-up text share one user message |
| Echo | The JSON fallback (its `complete_json` yields an empty plan, so it answers directly) |

**JSON fallback:** the tools are listed in the system prompt, and `complete_json` is asked for `{"tool": <name or "finish">, "arguments": {}, "answer": ""}`. That gives one call per step, and an unknown tool counts as finishing. Earlier calls and results are rendered as text (`render_agent_turns`).

## Events

See README → *Chat stream events*. The frontend folds `plan`, `agent` and `tool` into `Message.agent` with the pure reducers in `frontend/src/lib/agentTrace.ts`. `action` adds the review card as soon as the draft exists. The finished trace is stored in `messages.agent` (JSON, added through `_add_missing_columns`) and returned with the session.

## Adding a tool

1. Write `async def _my_tool(box: Toolbox, args) -> ToolResult` in `app/agents/tools.py`. Validate arguments with `_str`, `_int` and `_list`, and raise `ToolFailed` with a plain message for bad input. Don't raise for expected failures.
2. Register it in `TOOLS` with a `ToolSpec` (a JSON-schema `object`). Set `needs=` if it depends on optional setup, so `Toolbox.available` can hide it, and `drafts=True` if it creates a draft.
3. Anything with a side effect must go through `box.propose(kind, payload, what)`, plus a branch in `automation.validate_payload` and `Automation.execute`. Add the draft model to `schemas.DRAFT_MODELS` and the review form to `ActionCard.tsx`. Never perform the side effect inside the tool.
4. Add it to a helper's `tools` in `specialists.py`, and give it a plain label in `TOOL_LABELS` (`agentTrace.ts`).
5. Test it through `tests/test_agents.py` with `ScriptedAgentProvider`: script a plan and `AgentStep`s that call the tool, then assert on the events and the stored state.

## Adding a helper

Add an `AgentSpec` to `SPECIALISTS`. The `description` is what the planner reads to decide when to use it, and the `role` is what the helper reads about its own job. `available_agents` hides helpers that have no usable tools, except the coder, which can always write code.

## Not done yet (ideas)

- Re-planning after a step (today the plan is fixed once made).
- Running independent steps in parallel for remote providers.
- Letting voice turns opt in to agents with `AGENT_MODE=toggle` (today only `always` applies to voice).
- Running the project's own tests in the sandbox. The project isn't mounted there, on purpose.
