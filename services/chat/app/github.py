"""Public code on GitHub, for the code researcher helper.

It only reads, and only from fixed hosts: the GitHub API, and the PyPI and npm registries
(used to find the repository behind a package name). Nothing the model writes is ever used as
a host, so there is no way to point it at this computer or the local network.

Without GITHUB_TOKEN, GitHub allows about 60 lookups an hour from one address; a token (no
scopes needed for public repositories) raises that to 5,000.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)

API = "https://api.github.com"
PYPI = "https://pypi.org/pypi"
NPM = "https://registry.npmjs.org"

MAX_COMMIT_CHARS = 8_000  # what the model sees of one commit
MAX_MESSAGE_CHARS = 3_000
MAX_PATCH_CHARS = 1_500  # of one file's diff
MAX_FILES_LISTED = 60
MAX_FILE_LIST_CHARS = 3_500  # the rest of a commit's budget goes to diffs
MAX_FILE_CHARS = 10_000  # of one file read
MAX_FILE_BYTES = 2_000_000
MAX_ENTRIES = 200  # of one folder listing

_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")
_REF = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")  # a branch, tag or commit hash
_PACKAGE = re.compile(r"^(?:@[a-z0-9][\w.-]*/)?[A-Za-z0-9][\w.-]{0,213}$")
_GITHUB_LINK = re.compile(r"github\.com[/:]([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9._-]{1,100})", re.I)
# github.com/<these>/... are GitHub's own pages, not repositories.
_NOT_OWNERS = frozenset({"sponsors", "orgs", "features", "marketplace", "topics", "apps", "settings", "about"})
# Files whose diffs say nothing to a reader: lockfiles, build output, minified code, data shards.
_GENERATED = re.compile(
    r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|uv\.lock|Cargo\.lock|go\.sum|"
    r"composer\.lock|Gemfile\.lock|Pipfile\.lock)$"
    r"|\.(min\.js|min\.css|map|lock|svg|ipynb|pb|onnx|bin|tar|gz|xz|zip|whl)$"
    r"|(^|/)(dist|build|vendor|node_modules|__snapshots__)/",
    re.I,
)
_BIG_DATA_LINES = 200  # a .json/.csv/.yaml change larger than this is summarised, not shown


class CodeHostError(Exception):
    """A lookup failed; the message is plain enough to show to the user."""


@dataclass(frozen=True)
class Repo:
    full_name: str
    description: str
    default_branch: str
    stars: int
    pushed_at: str
    found_via: str  # link | name | PyPI | npm | search

    def line(self) -> str:
        about = f": {self.description}" if self.description else ""
        return (
            f"- {self.full_name} (found via {self.found_via}){about}. Default branch "
            f"{self.default_branch}, {self.stars} stars, last push {self.pushed_at[:10] or 'unknown'}."
        )


# ---- checking what the model asks for -------------------------------------------------------


def repo_name(value: str) -> str:
    """`owner/name` from a name or a GitHub link, or CodeHostError."""
    value = value.strip()
    if m := _GITHUB_LINK.search(value):
        return _join(m.group(1), m.group(2))
    value = value.strip("/")
    if value.endswith(".git"):
        value = value[:-4]
    if not _REPO.match(value):
        raise CodeHostError("Give the repository as owner/name, e.g. python/cpython.")
    return value


def _join(owner: str, name: str) -> str:
    name = name[:-4] if name.lower().endswith(".git") else name
    return f"{owner}/{name}"


def _ref(value: str | None) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    if not _REF.match(value) or ".." in value:
        raise CodeHostError("That doesn't look like a branch, tag or commit.")
    return value


def _path(value: str | None) -> str:
    value = (value or "").strip().strip("/")
    parts = value.split("/") if value else []
    if any(p in ("", ".", "..") for p in parts) or any(ord(c) < 32 for c in value):
        raise CodeHostError("Give a path inside the repository, e.g. src/main.py.")
    return value


def github_repo_in(url: object) -> str | None:
    """`owner/name` if `url` points at a GitHub repository."""
    if not isinstance(url, str):
        return None
    m = _GITHUB_LINK.search(url)
    if not m or m.group(1).lower() in _NOT_OWNERS:
        return None
    return _join(m.group(1), m.group(2))


# ---- formatting what GitHub returns ---------------------------------------------------------


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _summarised(file: dict[str, Any]) -> str | None:
    """Why a file's diff isn't shown, or None to show it."""
    name = str(file.get("filename", ""))
    patch = file.get("patch")
    changes = int(file.get("changes") or 0)
    if patch is None:
        return "binary or too large to show"
    if "version https://git-lfs" in patch:
        return "stored in Git LFS"
    if _GENERATED.search(name):
        return "generated or packed file"
    if re.search(r"\.(json|csv|tsv|ya?ml|txt)$", name, re.I) and changes > _BIG_DATA_LINES:
        return "large data file"
    return None


def format_commit(repo: str, data: dict[str, Any], limit: int = MAX_COMMIT_CHARS) -> str:
    commit = data.get("commit") or {}
    author = commit.get("author") or {}
    committer = commit.get("committer") or {}
    login = (data.get("author") or {}).get("login") or ""
    sha = str(data.get("sha", ""))
    parents = [str(p.get("sha", ""))[:7] for p in data.get("parents") or [] if isinstance(p, dict)]
    stats = data.get("stats") or {}
    files = [f for f in data.get("files") or [] if isinstance(f, dict)]
    who = author.get("name") or login or "unknown"
    if login and login != who:
        who += f" (@{login})"
    head = [
        f"Commit {sha[:7]} in {repo} ({sha})",
        f"Author: {who}, {author.get('date', '')}",
    ]
    if committer.get("name") and committer.get("name") != author.get("name"):
        head.append(f"Committed by: {committer['name']}, {committer.get('date', '')}")
    head.append(
        f"Parents: {', '.join(parents) or 'none'}" + (" (a merge commit)" if len(parents) > 1 else "")
    )
    head.append(
        f"Changes: {len(files)} files, +{stats.get('additions', 0)} −{stats.get('deletions', 0)}"
    )
    message = _clip(str(commit.get("message", "")).strip(), MAX_MESSAGE_CHARS)
    lines = ["\n".join(head), "Message:\n" + "\n".join("    " + m for m in message.splitlines())]

    listed: list[str] = []
    size = 0
    for f in files[:MAX_FILES_LISTED]:
        row = (
            f"{str(f.get('status', 'modified'))[:1].upper()} {f.get('filename')}"
            + (" (renamed)" if f.get("previous_filename") else "")
            + f" (+{f.get('additions', 0)} −{f.get('deletions', 0)})"
            + (f" [{why}]" if (why := _summarised(f)) else "")
        )
        if size + len(row) > MAX_FILE_LIST_CHARS:
            break
        listed.append(row)
        size += len(row) + 1
    if len(listed) < len(files):
        listed.append(f"… and {len(files) - len(listed)} more files")
    if listed:
        lines.append("Files:\n" + "\n".join(listed))

    text = "\n\n".join(lines)
    diffs: list[str] = []
    left = limit - len(text) - 100
    skipped = 0
    for f in files:
        if _summarised(f):
            continue
        if left < 300:
            skipped += 1
            continue
        part = f"--- {f.get('filename')}\n" + _clip(str(f["patch"]), min(MAX_PATCH_CHARS, left - 50))
        diffs.append(part)
        left -= len(part) + 2
    if diffs:
        text += "\n\nDiffs (clipped):\n" + "\n\n".join(diffs)
    if skipped:
        text += f"\n\n({skipped} more diffs not shown; read those files with read_repo_file.)"
    return _clip(text, limit)


# ---- the client --------------------------------------------------------------------------


class GitHubClient:
    name = "github"

    def __init__(self, token: str = "", client: httpx.AsyncClient | None = None):
        self.token = token.strip()
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=5.0),
            headers={"User-Agent": "voice-assistant/1.0"},
            # Only for renamed repositories and normalised package names, on the same hosts.
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---- requests --------------------------------------------------------------

    async def _get(self, url: str, params: dict | None = None, headers: dict | None = None) -> httpx.Response:
        try:
            return await self._client.get(url, params=params, headers=headers)
        except httpx.TimeoutException:
            raise CodeHostError("GitHub took too long to answer.") from None
        except httpx.HTTPError as exc:
            raise CodeHostError(f"Couldn't reach GitHub ({exc.__class__.__name__}).") from None

    async def _api(self, path: str, params: dict | None = None, raw: bool = False) -> httpx.Response:
        headers = {
            "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        resp = await self._get(f"{API}{path}", {k: v for k, v in (params or {}).items() if v}, headers)
        code = resp.status_code
        if code == 401:
            raise CodeHostError("GitHub refused GITHUB_TOKEN. Check it, or leave it empty.")
        if code == 429 or (code == 403 and resp.headers.get("x-ratelimit-remaining") == "0"):
            raise CodeHostError(
                "GitHub's limit for lookups is used up for now. Try again later"
                + ("." if self.token else ", or set GITHUB_TOKEN to raise the limit.")
            )
        if code == 404:
            raise CodeHostError(
                "GitHub found nothing there: the repository, branch, commit or file doesn't exist or isn't public."
            )
        if code == 409:
            raise CodeHostError("That repository is empty.")
        if code == 422:
            raise CodeHostError("GitHub couldn't do that lookup. Check the branch, commit or path.")
        if code >= 400:
            raise CodeHostError(f"GitHub answered with an error ({code}).")
        return resp

    async def _json(self, path: str, params: dict | None = None) -> Any:
        resp = await self._api(path, params)
        try:
            return resp.json()
        except ValueError:
            raise CodeHostError("GitHub sent something that isn't JSON.") from None

    # ---- finding a repository --------------------------------------------------

    async def repo(self, full_name: str, found_via: str = "name") -> Repo:
        data = await self._json(f"/repos/{repo_name(full_name)}")
        return _repo(data, found_via)

    async def find(self, name: str, count: int = 5) -> list[Repo]:
        """Repositories matching a project or package name, a GitHub link, or owner/name."""
        name = " ".join(name.split())[:200]
        if not name:
            raise CodeHostError("Give a project or package name.")
        candidates: list[tuple[str, str]] = []
        if link := github_repo_in(name):
            candidates.append((link, "link"))
        elif _REPO.match(name):
            candidates.append((name, "name"))
        elif _PACKAGE.match(name):
            pypi, npm = await asyncio.gather(self._pypi(name), self._npm(name))
            candidates += [(r, how) for r, how in ((pypi, "PyPI"), (npm, "npm")) if r]

        found: list[Repo] = []
        seen: set[str] = set()
        failure: CodeHostError | None = None
        for full, how in candidates:
            if full.casefold() in seen:
                continue
            try:
                repo = await self.repo(full, how)
            except CodeHostError as exc:
                failure = exc
                continue
            seen.add(repo.full_name.casefold())
            found.append(repo)

        # GitHub's search allows only a few lookups a minute: use it when nothing matched directly.
        if not found:
            query = name.split("/")[-1] if _REPO.match(name) else name
            data = await self._json("/search/repositories", {"q": query, "per_page": count})
            for item in (data.get("items") or []) if isinstance(data, dict) else []:
                if isinstance(item, dict) and item.get("full_name"):
                    found.append(_repo(item, "search"))
        if not found and failure:
            raise failure
        return found[:count]

    async def _pypi(self, name: str) -> str | None:
        """The GitHub repository a PyPI package links to, if any."""
        if name.startswith("@"):
            return None
        try:
            resp = await self._get(f"{PYPI}/{quote(name)}/json", headers={"Accept": "application/json"})
            data = resp.json() if resp.status_code == 200 else {}
        except (CodeHostError, ValueError):
            return None
        info = data.get("info") if isinstance(data, dict) else None
        if not isinstance(info, dict):
            return None
        urls = info.get("project_urls") or {}
        preferred = ("source", "source code", "repository", "code", "github", "homepage")
        ordered = sorted(urls.items(), key=lambda kv: next(
            (i for i, p in enumerate(preferred) if str(kv[0]).casefold() == p), len(preferred)
        )) if isinstance(urls, dict) else []
        for url in [u for _, u in ordered] + [info.get("home_page"), info.get("download_url")]:
            if repo := github_repo_in(url):
                return repo
        return None

    async def _npm(self, name: str) -> str | None:
        """The GitHub repository an npm package links to, if any."""
        try:
            resp = await self._get(f"{NPM}/{quote(name.lower(), safe='@')}", headers={"Accept": "application/json"})
            data = resp.json() if resp.status_code == 200 else {}
        except (CodeHostError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        repository = data.get("repository")
        url = repository.get("url") if isinstance(repository, dict) else repository
        return github_repo_in(url) or github_repo_in(data.get("homepage"))

    # ---- reading a repository --------------------------------------------------

    async def commits(
        self, repo: str, ref: str | None = None, path: str | None = None, count: int = 10
    ) -> list[dict[str, Any]]:
        full = repo_name(repo)
        params = {"sha": _ref(ref), "path": _path(path), "per_page": max(1, min(count, 30))}
        data = await self._json(f"/repos/{full}/commits", params)
        out = []
        for item in data if isinstance(data, list) else []:
            commit = item.get("commit") or {}
            author = commit.get("author") or {}
            out.append(
                {
                    "sha": str(item.get("sha", "")),
                    "date": str(author.get("date", "")),
                    "author": author.get("name") or (item.get("author") or {}).get("login") or "unknown",
                    "subject": str(commit.get("message", "")).strip().split("\n", 1)[0],
                    "merge": len(item.get("parents") or []) > 1,
                }
            )
        return out

    async def commit(self, repo: str, sha: str) -> str:
        full = repo_name(repo)
        ref = _ref(sha)
        if ref is None:
            raise CodeHostError("Give the commit hash (or a branch name for its newest commit).")
        data = await self._json(f"/repos/{full}/commits/{quote(ref, safe='/')}")
        return format_commit(full, data if isinstance(data, dict) else {})

    async def list_files(self, repo: str, path: str = "", ref: str | None = None) -> str:
        full, where = repo_name(repo), _path(path)
        data = await self._json(f"/repos/{full}/contents/{quote(where)}", {"ref": _ref(ref)})
        if isinstance(data, dict):
            return f"{where} is a file ({data.get('size', 0)} bytes). Read it with read_repo_file."
        entries = sorted(
            (e for e in data if isinstance(e, dict)), key=lambda e: (e.get("type") != "dir", str(e.get("name")))
        )
        lines = [
            f"{e.get('path')}/" if e.get("type") == "dir" else f"{e.get('path')} ({e.get('size', 0)} bytes)"
            for e in entries[:MAX_ENTRIES]
        ]
        if len(entries) > MAX_ENTRIES:
            lines.append(f"… and {len(entries) - MAX_ENTRIES} more")
        return "\n".join(lines) or "The folder is empty."

    async def read_file(
        self, repo: str, path: str, ref: str | None = None, start: int = 1, end: int | None = None
    ) -> str:
        full, where = repo_name(repo), _path(path)
        if not where:
            raise CodeHostError("Give the path of a file, e.g. README.md.")
        resp = await self._api(f"/repos/{full}/contents/{quote(where)}", {"ref": _ref(ref)}, raw=True)
        data = resp.content
        if len(data) > MAX_FILE_BYTES:
            raise CodeHostError("That file is too large to read here.")
        if b"\x00" in data[:8_000]:
            raise CodeHostError("That is a binary file, not text.")
        if "json" in resp.headers.get("content-type", "") and data.lstrip().startswith(b"["):
            raise CodeHostError(f"{where} is a folder. List it with list_repo_files.")
        lines = data.decode("utf-8", errors="replace").splitlines()
        start = max(1, start)
        last = min(len(lines), end or len(lines))
        out, size, shown = [], 0, start - 1
        for n in range(start, last + 1):
            row = f"{n:>5}  {lines[n - 1]}"
            if size + len(row) > MAX_FILE_CHARS:
                break
            out.append(row)
            size += len(row) + 1
            shown = n
        text = "\n".join(out) or "(no lines in that range)"
        if shown < last:
            text += f"\n(… {last - shown} more lines; read on with start_line {shown + 1})"
        return f"{where} in {full} ({len(lines)} lines; line numbers are not part of the file):\n{text}"


def _repo(data: Any, found_via: str) -> Repo:
    data = data if isinstance(data, dict) else {}
    return Repo(
        full_name=str(data.get("full_name", "")),
        description=_clip(" ".join(str(data.get("description") or "").split()), 200),
        default_branch=str(data.get("default_branch") or "main"),
        stars=int(data.get("stargazers_count") or 0),
        pushed_at=str(data.get("pushed_at") or ""),
        found_via=found_via,
    )
