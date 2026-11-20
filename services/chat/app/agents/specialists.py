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
    requires: str = ""  # a tool without which this helper is left out


CODE_RESEARCHER = "code_researcher"

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
        CODE_RESEARCHER,
        "Code researcher",
        "Looks inside public code repositories on GitHub: finds a project's repository (also from "
        "a PyPI or npm package name), lists recent commits, shows what a commit changed, and reads "
        "source files. Use for questions about a repository, its commits, releases, changes or code.",
        "Answer from the repository itself, not from memory. First find the repository with "
        "find_repository (if that finds nothing, web_search for the project's GitHub page). For "
        "the latest commit, list the commits on the default branch and show the newest one. If it "
        "is an automated or bookkeeping commit (a bot, a merge, a version bump, a 'chore: refresh' "
        "or regenerated files), also show the commit before it: that is usually the real change "
        "and the two only make sense together. Report the repository, each commit's short hash, "
        "author, date and message, what changed and why, and the key numbers it gives.",
        ("find_repository", "list_commits", "show_commit", "list_repo_files", "read_repo_file",
         "web_search", "fetch_page"),
        requires="find_repository",
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
    return [
        s
        for s in SPECIALISTS
        if (not s.requires or box.available(s.requires)) and (s.name == "coder" or available_tools(s, box))
    ]
