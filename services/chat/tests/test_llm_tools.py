"""Tool calling in the provider layer: the JSON fallback, Ollama's native tools, Claude's blocks."""

import json
from types import SimpleNamespace

import httpx

from app.llm.anthropic_provider import agent_messages, step_from_response
from app.llm.base import (
    FINISH,
    AgentStep,
    AgentTurn,
    GenerationParams,
    LLMProvider,
    ToolCall,
    ToolSpec,
    parse_fallback_choice,
    render_agent_turns,
)
from app.llm.ollama import OllamaProvider

PARAMS = GenerationParams(model="m", system="Be useful.", temperature=0.2, max_tokens=256)
TOOLS = [ToolSpec("web_search", "Search the web.", {"type": "object", "properties": {"query": {"type": "string"}}})]


class JsonOnly(LLMProvider):
    """A model without native tools: answers complete_json with a fixed object."""

    name = "json"
    default_model = "m"

    def __init__(self, answer: dict):
        self.answer = answer
        self.seen: list = []

    async def stream(self, turns, params):
        yield ""

    async def complete_json(self, turns, params, schema):
        self.seen.append((turns, params, schema))
        return self.answer

    async def check(self, model):
        return True, "ok"


async def test_fallback_picks_a_tool():
    provider = JsonOnly({"tool": "web_search", "arguments": {"query": "bread"}, "answer": ""})
    step = await provider.run_tools_step([AgentTurn("user", "find bread")], PARAMS, TOOLS)
    assert [(c.name, c.arguments) for c in step.tool_calls] == [("web_search", {"query": "bread"})]
    turns, params, schema = provider.seen[0]
    assert schema["properties"]["tool"]["enum"] == ["web_search", FINISH]
    assert "web_search: Search the web." in params.system and params.system.startswith("Be useful.")


async def test_fallback_finish_and_unknown_tools_end_the_loop():
    assert parse_fallback_choice({"tool": FINISH, "arguments": {}, "answer": " Done. "}, {"web_search"}) == AgentStep("Done.")
    assert not parse_fallback_choice({"tool": "rm_rf", "arguments": {}, "answer": "x"}, {"web_search"}).tool_calls
    odd = parse_fallback_choice({"tool": "web_search", "arguments": "bread"}, {"web_search"})
    assert odd.tool_calls[0].arguments == {}


def test_fallback_writes_tool_turns_as_text():
    turns = [
        AgentTurn("user", "find bread"),
        AgentTurn("assistant", "", (ToolCall("1", "web_search", {"query": "bread"}),)),
        AgentTurn("tool", "Result A", tool_call_id="1", tool_name="web_search"),
    ]
    chat = render_agent_turns(turns)
    assert [t.role for t in chat] == ["user", "assistant", "user"]
    assert 'web_search with {"query": "bread"}' in chat[1].content
    assert chat[2].content == '<tool_result name="web_search">\nResult A\n</tool_result>'


def ollama(handler) -> OllamaProvider:
    return OllamaProvider("http://ollama.test", "m", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_ollama_native_tools():
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "tools"]})
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={
            "message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "web_search", "arguments": {"query": "bread"}}},
                {"function": {"name": "web_search", "arguments": '{"query": "rye"}'}},
            ]},
            "done": True,
        })

    turns = [
        AgentTurn("user", "find bread"),
        AgentTurn("assistant", "", (ToolCall("a", "web_search", {"query": "x"}),)),
        AgentTurn("tool", "nothing", tool_call_id="a", tool_name="web_search"),
    ]
    step = await ollama(handler).run_tools_step(turns, PARAMS, TOOLS)
    assert [c.arguments["query"] for c in step.tool_calls] == ["bread", "rye"]
    assert len({c.id for c in step.tool_calls}) == 2
    body = bodies[0]
    assert body["stream"] is False
    assert body["tools"][0]["function"]["name"] == "web_search"
    assert body["messages"][0] == {"role": "system", "content": "Be useful."}
    assert body["messages"][2]["tool_calls"][0]["function"] == {"name": "web_search", "arguments": {"query": "x"}}
    assert body["messages"][3] == {"role": "tool", "content": "nothing", "tool_name": "web_search"}


async def test_ollama_without_tool_support_uses_json():
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion"]})
        bodies.append(json.loads(request.content))
        answer = {"tool": FINISH, "arguments": {}, "answer": "All done."}
        return httpx.Response(200, json={"message": {"content": json.dumps(answer)}, "done": True})

    step = await ollama(handler).run_tools_step([AgentTurn("user", "hi")], PARAMS, TOOLS)
    assert step == AgentStep("All done.")
    assert "tools" not in bodies[0] and bodies[0]["format"]["properties"]["tool"]["enum"] == ["web_search", FINISH]


def test_claude_messages_echo_blocks_and_group_results():
    blocks = [{"type": "thinking", "thinking": "…", "signature": "sig"}, {"type": "tool_use", "id": "t1", "name": "web_search", "input": {}}]
    turns = [
        AgentTurn("user", "find bread"),
        AgentTurn("assistant", "", (ToolCall("t1", "web_search", {}), ToolCall("t2", "web_search", {})), provider_data=blocks),
        AgentTurn("tool", "A", tool_call_id="t1", tool_name="web_search"),
        AgentTurn("tool", "", tool_call_id="t2", tool_name="web_search"),
        AgentTurn("user", "Write your report now."),
    ]
    messages = agent_messages(turns)
    assert messages[1] == {"role": "assistant", "content": blocks}
    assert [b["type"] for b in messages[2]["content"]] == ["tool_result", "tool_result", "text"]
    assert messages[2]["content"][1]["content"] == "(empty)"
    assert len(messages) == 3


def test_claude_messages_without_provider_data():
    turns = [AgentTurn("user", "q"), AgentTurn("assistant", "Let me look.", (ToolCall("t1", "web_search", {"query": "q"}),))]
    content = agent_messages(turns)[1]["content"]
    assert content == [
        {"type": "text", "text": "Let me look."},
        {"type": "tool_use", "id": "t1", "name": "web_search", "input": {"query": "q"}},
    ]


def test_claude_response_to_step():
    response = SimpleNamespace(
        stop_reason="tool_use",
        content=[
            SimpleNamespace(type="thinking"),
            SimpleNamespace(type="text", text="Searching."),
            SimpleNamespace(type="tool_use", id="t9", name="web_search", input={"query": "bread"}),
        ],
    )
    step = step_from_response(response)
    assert step.text == "Searching."
    assert step.tool_calls == (ToolCall("t9", "web_search", {"query": "bread"}),)
    assert step.provider_data == response.content
    refused = step_from_response(SimpleNamespace(stop_reason="refusal", content=[]))
    assert "can't help" in refused.text and not refused.tool_calls
    assert step_from_response(SimpleNamespace(stop_reason="max_tokens", content=[])).truncated
