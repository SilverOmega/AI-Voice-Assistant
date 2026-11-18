"""The helpers the lead assistant can hand work to."""

from dataclasses import dataclass

from .tools import Toolbox


@dataclass(frozen=True)
class AgentSpec:
    name: str
    title: str
    description: str  # for the planner: when to use this helper
    role: str  # for the helper itself
    tools: tuple[str, ...]


SPECIALISTS: tuple[AgentSpec, ...] = (
    AgentSpec(
        "researcher",
        "Researcher",
        "Looks things up on the web and reads web pages. Use for current events, facts to check, "
        "prices, or anything the conversation and documents don't cover.",
        "Find reliable, current information for the task. Search, then read the most useful "
        "pages when the snippets aren't enough. Report the facts with their sources (site names).",
        ("web_search", "fetch_page"),
    ),
    AgentSpec(
        "documents",
        "Document reader",
        "Reads the documents the user attached in this chat and finds, quotes or summarizes what "
        "is in them; can also translate passages.",
        "Read the attached documents to do the task. Quote the parts that matter and say which "
        "document they come from.",
        ("list_documents", "read_document", "translate"),
    ),
    AgentSpec(
        "tasks",
        "Task planner",
        "Drafts emails, calendar events, saved documents and workflow runs for the user to "
        "approve, and checks the schedule and earlier tasks.",
        "Prepare the drafts the task calls for. Use only email addresses the user gave. Resolve "
        "dates such as 'tomorrow' from the current time. Every draft waits for the user's approval.",
        (
            "list_events", "list_actions", "propose_email", "propose_event",
            "propose_document", "propose_workflow",
        ),
    ),
    AgentSpec(
        "coder",
        "Coder",
        "Writes, explains, reviews and fixes code. Can read the user's project folder and propose "
        "changes to its files, and propose running Python in a sandbox to test it.",
        "Do the programming task well: correct, idiomatic, complete code. Read the relevant project "
        "files before proposing changes, and keep each change small and exact. To check that code "
        "works, propose a sandbox run. Put code you write in Markdown code blocks in your report.",
        (
            "list_project_files", "read_project_file", "search_project", "propose_file_edit",
            "propose_code_run", "list_actions", "get_action_result",
        ),
    ),
)

BY_NAME = {s.name: s for s in SPECIALISTS}


def available_tools(spec: AgentSpec, box: Toolbox) -> list[str]:
    return [t for t in spec.tools if box.available(t)]


def available_agents(box: Toolbox) -> list[AgentSpec]:
    """Helpers that have something to work with. The coder can always write code; the
    researcher needs web search or at least page reading, which is always there."""
    return [s for s in SPECIALISTS if s.name == "coder" or available_tools(s, box)]
