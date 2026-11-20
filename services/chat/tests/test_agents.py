import asyncio
import json
from collections.abc import AsyncIterator

import httpx
import pytest

from app.config import Settings
from app.llm import LLMError, LLMProvider, ProviderRegistry
from app.llm.base import AgentStep, AgentTurn, ToolCall
from app.main import create_app

from .test_api import new_session, parse_sse
from .test_github import REPO, FakeGitHub
from .test_github import make_client as make_github
from .test_search import FakeSearch

NOW = "2026-11-17T09:30"


class ScriptedAgentProvider(LLMProvider):
    """Plans with `plan`, takes tool steps from `steps` in order, then streams `reply`."""

    name = "ollama"
    default_model = "fake-model"

    def __init__(self, plan: dict | None = None, steps: list[AgentStep] | None = None, reply: str = "Here is your answer."):
        self.plan = plan if plan is not None else {"steps": []}
        self.steps = list(steps or [])
        self.reply = reply
        self.plans: list = []
        self.step_calls: list[tuple[list[AgentTurn], object, list]] = []
        self.streams: list = []
        self.step_delay = 0.0

    async def complete_json(self, turns, params, schema):
        self.plans.append((turns, params, schema))
        if isinstance(self.plan, Exception):
            raise self.plan
        return self.plan

    async def run_tools_step(self, turns, params, tools):
        self.step_calls.append((list(turns), params, tools))
        if self.step_delay:
            await asyncio.sleep(self.step_delay)
        return self.steps.pop(0) if self.steps else AgentStep("Report: done.")

    async def stream(self, turns, params) -> AsyncIterator[str]:
        self.streams.append((turns, params))
        for i, word in enumerate(self.reply.split(" ")):
            yield word if i == 0 else " " + word

    async def check(self, model):
        return True, "ok"


def call(name: str, **args) -> AgentStep:
    return AgentStep("", (ToolCall(f"id-{name}", name, args),))


@pytest.fixture
def provider():
    return ScriptedAgentProvider()


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (root / ".env").write_text("SECRET=1\n", encoding="utf-8")
    return root


@pytest.fixture
def settings_kw():
    return {}


@pytest.fixture
async def env(provider, project, tmp_path, settings_kw):
    """(client, app, sandbox requests)"""
    settings = Settings(
        **{
            "database_path": str(tmp_path / "test.db"),
            "llm_provider": "ollama",
            "web_search": "off",
            "agent_mode": "toggle",
            "project_dir": str(project),
            "sandbox_url": "http://sandbox.test",
            "sandbox_token": "tok",
            **settings_kw,
        }
    )
    app = create_app(settings, ProviderRegistry({"ollama": provider}))
    runs: list[httpx.Request] = []

    def sandbox(request: httpx.Request) -> httpx.Response:
        runs.append(request)
        return httpx.Response(
            200,
            json={"stdout": "3\n", "stderr": "", "exit_code": 0, "duration_ms": 41, "timed_out": False, "truncated": False},
        )

    async with app.router.lifespan_context(app):
        app.state.chat.search = FakeSearch()
        github = make_github(FakeGitHub())
        app.state.chat.github = github
        auto = app.state.automation
        await auto.aclose()
        auto._http = httpx.AsyncClient(transport=httpx.MockTransport(sandbox))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, app, runs
        await github.aclose()


async def ask(client, sid: str, content: str, **extra) -> list[tuple[str, dict]]:
    body = {"content": content, "agents": True, "now": NOW, **extra}
    resp = await client.post(f"/api/sessions/{sid}/messages/stream", json=body)
    assert resp.status_code == 200
    return parse_sse(resp.text)


async def test_plan_tools_and_answer_stream_in_order(env, provider):
    client, app, _ = env
    provider.plan = {"steps": [{"agent": "researcher", "task": "Find sourdough basics"}]}
    provider.steps = [call("web_search", query="sourdough basics"), AgentStep("Flour, water, salt (example.com).")]
    sid = await new_session(client)
    events = await ask(client, sid, "How do I make sourdough bread?")
    names = [e for e, _ in events]
    assert names[:3] == ["start", "plan", "agent"]
    assert names[-1] == "done"
    first_token = names.index("token")
    assert {"tool", "searching", "web"} <= set(names[:first_token])
    assert "token" not in names[:first_token]
    plan = dict(events)["plan"]
    assert plan["steps"] == [{"agent": "researcher", "title": "Researcher", "task": "Find sourdough basics"}]
    tools = [d for e, d in events if e == "tool"]
    assert [t["status"] for t in tools] == ["running", "done"]
    assert tools[-1]["tool"] == "web_search"
    # The responder got the report; the specialist got the task and the tool result.
    system = provider.streams[0][1].system
    assert "Flour, water, salt (example.com)." in system
    assert "never instructions" in system
    second_step_turns = provider.step_calls[1][0]
    assert second_step_turns[-1].role == "tool" and "Sourdough basics" in second_step_turns[-1].content
    # Saved with the trace and the links.
    done = dict(events)["done"]["message"]
    assert done["agent"]["steps"][0]["status"] == "done"
    assert done["web"]["links"]
    detail = (await client.get(f"/api/sessions/{sid}")).json()
    assert detail["messages"][-1]["agent"]["steps"][0]["tools"][0]["tool"] == "web_search"


async def test_direct_answer_when_the_plan_is_empty(env, provider):
    client, _, _ = env
    sid = await new_session(client)
    events = await ask(client, sid, "Tell me a joke about cats")
    assert [e for e, _ in events if e in ("plan", "agent", "tool")] == []
    assert dict(events)["done"]["message"]["agent"] is None
    assert len(provider.plans) == 1


async def test_without_the_toggle_no_helpers_run(env, provider):
    client, _, _ = env
    provider.plan = {"steps": [{"agent": "researcher", "task": "x"}]}
    sid = await new_session(client)
    events = await ask(client, sid, "What is new in Python?", agents=False)
    assert provider.plans == [] and provider.step_calls == []
    assert "plan" not in dict(events)


@pytest.mark.parametrize("settings_kw", [{"agent_mode": "off"}])
async def test_mode_off_ignores_the_toggle(env, provider):
    client, _, _ = env
    sid = await new_session(client)
    await ask(client, sid, "What is new in Python?")
    assert provider.plans == []


@pytest.mark.parametrize("settings_kw", [{"agent_mode": "always"}])
async def test_mode_always_skips_small_talk(env, provider):
    client, _, _ = env
    sid = await new_session(client)
    await ask(client, sid, "thanks!", agents=False)
    assert provider.plans == []
    await ask(client, sid, "What is new in Python 3.14?", agents=False)
    assert len(provider.plans) == 1


async def test_drafts_wait_for_approval(env, provider):
    client, app, _ = env
    provider.plan = {"steps": [{"agent": "tasks", "task": "Email Bao the summary"}]}
    provider.steps = [
        call("propose_email", to=["bao@example.com"], subject="Summary", body="Here it is."),
        AgentStep("Drafted an email to bao@example.com for review."),
    ]
    sid = await new_session(client)
    events = await ask(client, sid, "Email bao@example.com a summary")
    action = dict(events)["action"]["action"]
    assert action["kind"] == "email" and action["status"] == "pending"
    assert action["payload"]["to"] == ["bao@example.com"]
    tool_done = [d for e, d in events if e == "tool"][-1]
    assert tool_done["action_id"] == action["id"]
    assert "review card" in provider.streams[0][1].system
    stored = await app.state.chat.repo.get_action(action["id"])
    assert stored.status == "pending" and stored.result is None
    detail = (await client.get(f"/api/sessions/{sid}")).json()
    assert [a["id"] for a in detail["actions"]] == [action["id"]]


async def test_tool_budget_then_wrap_up(env, provider):
    client, app, _ = env
    app.state.chat.settings.agent_max_tool_calls = 2
    provider.plan = {"steps": [{"agent": "researcher", "task": "dig"}]}
    provider.steps = [call("web_search", query=f"q{i}") for i in range(5)]
    sid = await new_session(client)
    events = await ask(client, sid, "Research everything about bread")
    assert len(app.state.chat.search.queries) == 2
    # Two runs, then the wrap-up request, whose tool calls are ignored.
    assert len(provider.step_calls) == 3
    assert provider.step_calls[-1][0][-1].content.startswith("You've used all your tool calls")
    step = dict(events)["done"]["message"]["agent"]["steps"][0]
    assert step["status"] == "done"


async def test_tool_errors_go_back_to_the_model(env, provider):
    client, _, _ = env
    provider.plan = {"steps": [{"agent": "coder", "task": "read it"}]}
    provider.steps = [call("read_project_file", path="../outside.txt"), call("nope"), AgentStep("Couldn't read it.")]
    sid = await new_session(client)
    events = await ask(client, sid, "Read the file outside the project")
    tools = [d for e, d in events if e == "tool" and d["status"] != "running"]
    assert [t["status"] for t in tools] == ["failed", "failed"]
    assert "outside the project folder" in tools[0]["output"]
    assert dict(events)["done"]["message"]["content"]


async def test_failed_plan_answers_directly(env, provider):
    client, _, _ = env
    provider.plan = LLMError("The model did not return a usable draft.")
    sid = await new_session(client)
    events = await ask(client, sid, "What is the weather like on Mars?")
    assert "plan" not in dict(events)
    assert dict(events)["done"]["message"]["content"] == "Here is your answer."


async def test_time_limit_answers_with_what_it_has(env, provider):
    client, app, _ = env
    app.state.chat.settings.agent_timeout_seconds = 0.2
    provider.step_delay = 0.5
    provider.plan = {"steps": [{"agent": "researcher", "task": "a"}, {"agent": "coder", "task": "b"}]}
    sid = await new_session(client)
    events = await ask(client, sid, "Do a lot of research about bread")
    steps = dict(events)["done"]["message"]["agent"]["steps"]
    assert [s["status"] for s in steps] == ["failed", "skipped"]
    assert "ran out of time" in provider.streams[0][1].system


async def test_voice_hears_only_the_final_answer(env, provider):
    client, _, _ = env
    provider.plan = {"steps": [{"agent": "researcher", "task": "look"}]}
    provider.steps = [AgentStep("Found it.")]
    sid = await new_session(client)
    events = await ask(client, sid, "What's the news on the Mars rover?", source="voice")
    tokens = "".join(d["text"] for e, d in events if e == "token")
    assert tokens == "Here is your answer."
    assert "read aloud" in provider.streams[0][1].system


async def test_agents_endpoint_and_config(env):
    client, _, _ = env
    body = (await client.get("/api/agents")).json()
    assert body["mode"] == "toggle"
    coder = next(a for a in body["agents"] if a["name"] == "coder")
    assert {"read_project_file", "propose_file_edit", "propose_code_run"} <= set(coder["tools"])
    assert (await client.get("/api/config")).json()["agent_mode"] == "toggle"
    info = (await client.get("/api/automation")).json()
    assert info["code_runs"] and info["project"]


# ---- code repositories ---------------------------------------------------------------------


async def test_code_researcher_reads_the_latest_commit_and_its_parent(env, provider):
    client, _, _ = env
    provider.plan = {"steps": [{"agent": "code_researcher", "task": "Explain the latest commit of bionemo-ir"}]}
    provider.steps = [
        AgentStep("", (ToolCall("id-find", "find_repository", {"name": "bionemo-ir"}),)),
        call("list_commits", repo=REPO, count=3),
        call("show_commit", repo=REPO, sha="6a5acc1"),
        AgentStep("6a5acc1 by cubin-bot refreshes CUBIN packs after e289332 (SM107 support)."),
    ]
    sid = await new_session(client)
    events = await ask(client, sid, "explain the latest commit of bionemo-ir")
    done = [d for e, d in events if e == "tool" and d["status"] != "running"]
    assert [(t["tool"], t["status"]) for t in done] == [
        ("find_repository", "done"), ("list_commits", "done"), ("show_commit", "done"),
    ]
    assert REPO in done[0]["output"]
    # The helper saw the commit list and the commit itself.
    turns = provider.step_calls[-1][0]
    tool_texts = [t.content for t in turns if t.role == "tool"]
    assert "6a5acc1 2026-11-18 cubin-bot: chore: refresh CuTeDSL CUBIN packs" in tool_texts[1]
    assert "Parents: e289332" in tool_texts[2]
    tools = {t.name for t in provider.step_calls[0][2]}
    assert {"find_repository", "list_commits", "show_commit", "read_repo_file"} <= tools
    assert "e289332 (SM107 support)" in provider.streams[0][1].system


async def test_code_questions_start_the_helpers_without_the_switch(env, provider):
    client, _, _ = env
    sid = await new_session(client)
    # The planner answers directly, but a code question still gets the code researcher.
    events = await ask(client, sid, "explain the latest commit of bionemo-ir", agents=False)
    plan = dict(events)["plan"]["steps"]
    assert [s["agent"] for s in plan] == ["code_researcher"]
    assert "explain the latest commit of bionemo-ir" in plan[0]["task"]
    # Not for other messages, and not for voice.
    provider.plans.clear()
    await ask(client, sid, "I'm committed to learning Spanish", agents=False)
    await ask(client, sid, "What's in the latest commit of numpy?", agents=False, source="voice")
    assert provider.plans == []


@pytest.mark.parametrize("settings_kw", [{"github_lookup": False}])
async def test_github_lookup_off(env, provider):
    client, app, _ = env
    app.state.chat.github = None
    sid = await new_session(client)
    await ask(client, sid, "explain the latest commit of bionemo-ir", agents=False)
    assert provider.plans == []
    body = (await client.get("/api/agents")).json()
    assert "code_researcher" not in [a["name"] for a in body["agents"]]


async def test_github_errors_go_back_to_the_model(env, provider):
    client, _, _ = env
    provider.plan = {"steps": [{"agent": "code_researcher", "task": "x"}]}
    provider.steps = [call("show_commit", repo="nobody/nothing", sha="abc1234"), AgentStep("Not found.")]
    sid = await new_session(client)
    events = await ask(client, sid, "What did commit abc1234 in nobody/nothing change?")
    tool = [d for e, d in events if e == "tool" and d["status"] != "running"][0]
    assert tool["status"] == "failed" and "isn't public" in tool["output"]


# ---- coding ------------------------------------------------------------------------------


async def test_file_edit_is_drafted_then_written_on_approval(env, provider, project):
    client, _, _ = env
    provider.plan = {"steps": [{"agent": "coder", "task": "Fix add()"}]}
    provider.steps = [
        call("read_project_file", path="app.py"),
        call("propose_file_edit", path="app.py", edits=[{"old": "return a - b", "new": "return a + b"}]),
        AgentStep("Proposed fixing add() to use +."),
    ]
    sid = await new_session(client)
    events = await ask(client, sid, "Fix the bug in add()")
    read = [d for e, d in events if e == "tool" and d["tool"] == "read_project_file"][-1]
    assert read["status"] == "done"
    action = dict(events)["action"]["action"]
    assert action["kind"] == "file_edit"
    assert "-    return a - b" in action["payload"]["diff"] and "+    return a + b" in action["payload"]["diff"]
    assert "a - b" in (project / "app.py").read_text()  # nothing written yet
    resp = await client.post(f"/api/actions/{action['id']}/approve", json={"payload": action["payload"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "done"
    assert (project / "app.py").read_text() == "def add(a, b):\n    return a + b\n"


async def test_file_edit_refused_when_the_file_changed(env, provider, project):
    client, _, _ = env
    provider.plan = {"steps": [{"agent": "coder", "task": "Fix add()"}]}
    provider.steps = [
        call("propose_file_edit", path="app.py", edits=[{"old": "return a - b", "new": "return a + b"}]),
        AgentStep("Proposed."),
    ]
    sid = await new_session(client)
    action = dict(await ask(client, sid, "Fix add()"))["action"]["action"]
    (project / "app.py").write_text("def add(a, b):\n    return a - b  # edited\n", encoding="utf-8")
    resp = await client.post(f"/api/actions/{action['id']}/approve", json={"payload": action["payload"]})
    body = resp.json()
    assert body["status"] == "pending"
    assert "changed since this edit was drafted" in body["error"]
    assert "# edited" in (project / "app.py").read_text()


async def test_secrets_stay_hidden_from_the_coder(env, provider):
    client, _, _ = env
    provider.plan = {"steps": [{"agent": "coder", "task": "list files"}]}
    provider.steps = [call("list_project_files"), call("read_project_file", path=".env"), AgentStep("Listed.")]
    sid = await new_session(client)
    events = await ask(client, sid, "What files are in my project?")
    done = [d for e, d in events if e == "tool" and d["status"] != "running"]
    assert done[0]["output"] == "app.py"
    assert done[1]["status"] == "failed" and "secrets" in done[1]["output"]


async def test_code_run_waits_for_approval_then_runs_in_the_sandbox(env, provider):
    client, app, runs = env
    provider.plan = {"steps": [{"agent": "coder", "task": "Test add"}]}
    provider.steps = [call("propose_code_run", code="print(1 + 2)", purpose="Check addition"), AgentStep("Proposed a run.")]
    sid = await new_session(client)
    action = dict(await ask(client, sid, "Write and test an add function"))["action"]["action"]
    assert action["kind"] == "code" and runs == []
    payload = {**action["payload"], "code": "print(1 + 2)  # reviewed"}
    resp = await client.post(f"/api/actions/{action['id']}/approve", json={"payload": payload})
    result = resp.json()
    assert result["status"] == "done"
    assert result["result"]["stdout"] == "3\n" and result["result"]["exit_code"] == 0
    assert len(runs) == 1
    assert runs[0].headers["authorization"] == "Bearer tok"
    assert json.loads(runs[0].content) == {"language": "python", "code": "print(1 + 2)  # reviewed", "stdin": ""}
    # The coder can read the output on the next turn.
    provider.plan = {"steps": [{"agent": "coder", "task": "check output"}]}
    provider.steps = [call("get_action_result", action_id=action["id"]), AgentStep("It printed 3.")]
    events = await ask(client, sid, "Did the test pass?")
    out = [d for e, d in events if e == "tool" and d["status"] == "done"][-1]["output"]
    assert "stdout: 3" in out


async def test_code_block_run_button_skips_the_model(env, provider):
    client, _, runs = env
    sid = await new_session(client)
    resp = await client.post(
        f"/api/sessions/{sid}/actions",
        json={"kind": "code", "instruction": "Run this code", "now": NOW, "payload": {"code": "print('hi')"}},
    )
    assert resp.status_code == 201, resp.text
    action = resp.json()
    assert action["payload"]["code"] == "print('hi')" and action["payload"]["language"] == "python"
    assert provider.plans == [] and runs == []


async def test_only_python_runs(env):
    client, _, _ = env
    sid = await new_session(client)
    action = (await client.post(
        f"/api/sessions/{sid}/actions",
        json={"kind": "code", "instruction": "x", "now": NOW, "payload": {"code": "ls", "language": "bash"}},
    )).json()
    resp = await client.post(f"/api/actions/{action['id']}/approve", json={"payload": action["payload"]})
    assert resp.status_code == 422
    assert "Only Python" in resp.json()["detail"]


@pytest.mark.parametrize("settings_kw", [{"sandbox_url": "", "project_dir": ""}])
async def test_coding_tools_hidden_when_not_set_up(env):
    client, _, _ = env
    body = (await client.get("/api/agents")).json()
    coder = next(a for a in body["agents"] if a["name"] == "coder")
    assert coder["tools"] == ["list_actions", "get_action_result"]
    sid = await new_session(client)
    resp = await client.post(
        f"/api/sessions/{sid}/actions", json={"kind": "code", "instruction": "x", "now": NOW, "payload": {"code": "1"}}
    )
    assert resp.status_code == 400
