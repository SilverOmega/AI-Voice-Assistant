import httpx
import pytest

from app.github import CodeHostError, GitHubClient, format_commit, repo_name
from app.search import about_code

REPO = "NVIDIA-BioNeMo/BioNeMo-Inference-Runtime"
SHA = "6a5acc1217b2f0e568787d94aaf0a0942b1613da"
PARENT = "e289332aa1f0e568787d94aaf0a0942b1613dbb"


def repo_json(full_name: str = REPO) -> dict:
    return {
        "full_name": full_name,
        "description": "BioNeMo Inference Runtime",
        "default_branch": "main",
        "stargazers_count": 42,
        "pushed_at": "2026-11-18T15:36:56Z",
    }


def commit_json() -> dict:
    return {
        "sha": SHA,
        "commit": {
            "message": "chore: refresh CuTeDSL CUBIN packs\n\nRefresh the CUBIN corpus after this change on main:\n\n    perf(transition): run the transition MLPs on Vera Rubin (SM107)",
            "author": {"name": "cubin-bot", "date": "2026-11-18T15:36:56Z"},
            "committer": {"name": "cubin-bot", "date": "2026-11-18T15:36:56Z"},
        },
        "author": None,
        "parents": [{"sha": PARENT}],
        "stats": {"additions": 5143, "deletions": 1020, "total": 6163},
        "files": [
            {"filename": "cpp/kernels/cutedsl_transition_mlp/cubins/index.json", "status": "modified",
             "additions": 40, "deletions": 14, "changes": 54, "patch": "@@ -1 +1 @@\n" + "-x\n+y\n" * 300},
            {"filename": "cpp/kernels/cutedsl_transition_mlp/cubins/packs/transition_mlp_sm107a.tar.xz",
             "status": "added", "additions": 3, "deletions": 0, "changes": 3,
             "patch": "@@ -0,0 +1,3 @@\n+version https://git-lfs.github.com/spec/v1\n+oid sha256:84b7\n+size 1234"},
            {"filename": "python/bioir/launch.py", "status": "modified", "additions": 1, "deletions": 1,
             "changes": 2, "patch": "@@ -1 +1 @@\n-ARCHS = (100,)\n+ARCHS = (100, 107)"},
            {"filename": "data/shards.json", "status": "modified", "additions": 900, "deletions": 900,
             "changes": 1800, "patch": "@@ big"},
        ],
    }


class FakeGitHub:
    """api.github.com, pypi.org and registry.npmjs.org, enough for the code researcher."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.rate_limited = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        if host == "pypi.org":
            if path == "/pypi/bionemo-ir/json":
                return httpx.Response(200, json={"info": {
                    "home_page": "",
                    "project_urls": {"Homepage": "https://www.nvidia.com", "Source": f"https://github.com/{REPO}.git"},
                }})
            return httpx.Response(404, json={"message": "Not Found"})
        if host == "registry.npmjs.org":
            if path == "/left-pad":
                return httpx.Response(200, json={"repository": {"type": "git", "url": "git+https://github.com/left-pad/left-pad.git"}})
            return httpx.Response(404, json={"error": "Not found"})
        assert host == "api.github.com"
        if self.rate_limited:
            return httpx.Response(403, headers={"x-ratelimit-remaining": "0"}, json={"message": "API rate limit exceeded"})
        if path == f"/repos/{REPO}":
            return httpx.Response(200, json=repo_json())
        if path == "/repos/left-pad/left-pad":
            return httpx.Response(200, json=repo_json("left-pad/left-pad"))
        if path == f"/repos/{REPO}/commits":
            return httpx.Response(200, json=[
                {"sha": SHA, "parents": [{"sha": PARENT}],
                 "commit": {"message": "chore: refresh CuTeDSL CUBIN packs\n\nmore", "author": {"name": "cubin-bot", "date": "2026-11-18T15:36:56Z"}}},
                {"sha": PARENT, "parents": [{"sha": "a"}, {"sha": "b"}],
                 "commit": {"message": "perf(transition): run the transition MLPs on Vera Rubin (SM107)", "author": {"name": "Dana", "date": "2026-11-18T14:00:00Z"}}},
            ])
        if path in (f"/repos/{REPO}/commits/6a5acc1", f"/repos/{REPO}/commits/{SHA}"):
            return httpx.Response(200, json=commit_json())
        if path == f"/repos/{REPO}/contents/":
            return httpx.Response(200, json=[
                {"name": "README.md", "path": "README.md", "type": "file", "size": 120},
                {"name": "cpp", "path": "cpp", "type": "dir", "size": 0},
            ])
        if path == f"/repos/{REPO}/contents/README.md":
            return httpx.Response(200, text="# BioIR\n\nFast inference.\n", headers={"content-type": "text/plain"})
        if path == "/search/repositories":
            q = request.url.params.get("q")
            items = [repo_json("someone/bionemo-tools")] if "bionemo" in q else []
            return httpx.Response(200, json={"items": items})
        return httpx.Response(404, json={"message": "Not Found"})


def make_client(fake: FakeGitHub, token: str = "") -> GitHubClient:
    return GitHubClient(token, client=httpx.AsyncClient(transport=httpx.MockTransport(fake), follow_redirects=True))


@pytest.fixture
async def gh():
    fake = FakeGitHub()
    client = make_client(fake)
    yield client, fake
    await client.aclose()


async def test_find_through_a_pypi_package(gh):
    client, fake = gh
    repos = await client.find("bionemo-ir")
    assert [(r.full_name, r.found_via) for r in repos] == [(REPO, "PyPI")]
    assert repos[0].default_branch == "main"
    assert "found via PyPI" in repos[0].line()
    # A direct match means GitHub's rate-limited search isn't used.
    assert not any(r.url.path == "/search/repositories" for r in fake.requests)


async def test_find_through_npm_a_link_and_owner_name(gh):
    client, _ = gh
    assert [r.found_via for r in await client.find("left-pad")] == ["npm"]
    assert (await client.find(f"https://github.com/{REPO}/tree/main"))[0].found_via == "link"
    assert (await client.find(REPO))[0].full_name == REPO


async def test_find_falls_back_to_search(gh):
    client, fake = gh
    repos = await client.find("bionemo tools")
    assert [(r.full_name, r.found_via) for r in repos] == [("someone/bionemo-tools", "search")]
    assert await client.find("nothing like it") == []


async def test_commits_and_one_commit(gh):
    client, _ = gh
    commits = await client.commits(REPO, count=5)
    assert commits[0]["sha"] == SHA and commits[0]["author"] == "cubin-bot"
    assert commits[0]["subject"] == "chore: refresh CuTeDSL CUBIN packs"
    assert commits[1]["merge"] is True
    text = await client.commit(REPO, "6a5acc1")
    assert f"Commit 6a5acc1 in {REPO}" in text
    assert "Parents: e289332" in text
    assert "perf(transition): run the transition MLPs on Vera Rubin" in text
    assert "+ARCHS = (100, 107)" in text  # a real code diff is shown


def test_format_commit_leaves_out_generated_and_data_diffs():
    text = format_commit(REPO, commit_json())
    assert "transition_mlp_sm107a.tar.xz (+3 −0) [stored in Git LFS]" in text
    assert "data/shards.json (+900 −900) [large data file]" in text
    assert "oid sha256" not in text and "@@ big" not in text
    assert len(text) <= 8_000
    # A huge patch is clipped, not dropped.
    assert "--- cpp/kernels/cutedsl_transition_mlp/cubins/index.json" in text


def test_format_commit_stays_within_its_budget():
    data = commit_json()
    data["files"] = [
        {"filename": f"src/m{i}.py", "status": "modified", "additions": 50, "deletions": 50, "changes": 100,
         "patch": "@@\n" + "+line of code\n" * 200}
        for i in range(40)
    ]
    text = format_commit(REPO, data)
    assert len(text) <= 8_000
    assert "more diffs not shown" in text


def test_many_files_leave_room_for_the_diffs():
    data = commit_json()
    noise = [
        {"filename": f"cpp/kernels/k/cubins/records/{i}_{'f' * 64}.json", "status": "added",
         "additions": 300, "deletions": 0, "changes": 300, "patch": "@@ data"}
        for i in range(120)
    ]
    data["files"] = noise + data["files"]
    text = format_commit(REPO, data)
    assert len(text) <= 8_000
    assert "more files" in text
    assert "+ARCHS = (100, 107)" in text


async def test_files_and_reading_one(gh):
    client, _ = gh
    listing = await client.list_files(REPO)
    assert listing.splitlines() == ["cpp/", "README.md (120 bytes)"]
    text = await client.read_file(REPO, "README.md", start=3)
    assert "    3  Fast inference." in text and "# BioIR" not in text


async def test_bad_input_is_refused_before_any_request(gh):
    client, fake = gh
    for call in (
        client.commits("not a repo"),
        client.commit(REPO, "../../etc"),
        client.read_file(REPO, "../secret"),
        client.list_files("evil.com/x/y"),
    ):
        with pytest.raises(CodeHostError):
            await call
    assert fake.requests == []
    assert repo_name("git@github.com:psf/requests.git") == "psf/requests"


async def test_plain_errors(gh):
    client, fake = gh
    with pytest.raises(CodeHostError, match="doesn't exist or isn't public"):
        await client.commit("nobody/nothing", "abc1234")
    fake.rate_limited = True
    with pytest.raises(CodeHostError, match="GITHUB_TOKEN"):
        await client.commits(REPO)


async def test_token_goes_to_github_only():
    fake = FakeGitHub()
    client = make_client(fake, token="ghp_secret")
    await client.find("bionemo-ir")
    await client.aclose()
    for request in fake.requests:
        expected = "Bearer ghp_secret" if request.url.host == "api.github.com" else None
        assert request.headers.get("authorization") == expected
    without = FakeGitHub()
    client = make_client(without)
    await client.commits(REPO)
    await client.aclose()
    assert "authorization" not in without.requests[0].headers


@pytest.mark.parametrize(
    "text",
    [
        "explain the latest commit of bionemo-ir",
        "What changed in https://github.com/psf/requests/pull/1",
        "show me the changelog for fastapi",
        "which repo has the openWakeWord models?",
        "summarize the commit history of numpy",
        "is there a pypi package for this?",
    ],
)
def test_about_code(text):
    assert about_code(text)


@pytest.mark.parametrize(
    "text",
    ["hello", "I'm committed to running a marathon", "Should I commit to this job?", "How do I bake bread?"],
)
def test_not_about_code(text):
    assert not about_code(text)
