import os

import pytest

from app.workspace import Edit, Workspace, WorkspaceError


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    (root / "notes.txt").write_bytes(b"one\r\ntwo\r\n")
    (root / ".env").write_text("TOKEN=abc\n")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "lib.js").write_text("ignored")
    (root / "blob.bin").write_bytes(b"\0\1\2")
    return Workspace(str(root))


@pytest.mark.parametrize("path", ["../x.py", "/etc/passwd", "C:/Windows/win.ini", "src/../../x", "", ".env", "node_modules/lib.js", "keys/id_rsa"])
def test_refuses_paths_outside_or_secret(ws, path):
    with pytest.raises(WorkspaceError):
        ws.resolve(path)


def test_refuses_links_that_lead_out(ws, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    link = ws.root / "link.txt"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need extra rights on this system")
    with pytest.raises(WorkspaceError):
        ws.read("link.txt")
    assert "link.txt" not in ws.list_files()


def test_lists_and_reads(ws):
    files = ws.list_files()
    assert files == ["blob.bin", "notes.txt", "src/app.py"]
    assert ws.list_files("*.py") == ["src/app.py"]
    assert ws.read("src/app.py") == "    1  x = 1\n    2  y = 2"
    assert ws.read("src/app.py", 2, 2).startswith("    2  y = 2")
    with pytest.raises(WorkspaceError, match="not a text file"):
        ws.read("blob.bin")
    assert ws.search("Y =") == ["src/app.py:2: y = 2"]


def test_edit_applies_once_and_atomically(ws):
    draft = ws.prepare_edit("src/app.py", [Edit("y = 2", "y = 3")], create=False)
    assert "-y = 2" in draft["diff"] and "+y = 3" in draft["diff"]
    assert (ws.root / "src" / "app.py").read_text() == "x = 1\ny = 2\n"  # not written yet
    assert ws.apply_edit("src/app.py", [Edit("y = 2", "y = 3")], False, draft["base_sha256"]) == "Updated src/app.py."
    assert (ws.root / "src" / "app.py").read_text() == "x = 1\ny = 3\n"
    assert [p.name for p in (ws.root / "src").iterdir()] == ["app.py"]  # no temp files left


def test_edit_needs_text_that_appears_exactly_once(ws):
    with pytest.raises(WorkspaceError, match="isn't in"):
        ws.prepare_edit("src/app.py", [Edit("z = 9", "z = 1")], create=False)
    (ws.root / "src" / "app.py").write_text("a = 1\na = 1\n")
    with pytest.raises(WorkspaceError, match="appears 2 times"):
        ws.prepare_edit("src/app.py", [Edit("a = 1", "a = 2")], create=False)


def test_stale_edit_is_refused(ws):
    draft = ws.prepare_edit("src/app.py", [Edit("x = 1", "x = 5")], create=False)
    (ws.root / "src" / "app.py").write_text("x = 1\ny = 2\nz = 3\n")
    with pytest.raises(WorkspaceError, match="changed since"):
        ws.apply_edit("src/app.py", [Edit("x = 1", "x = 5")], False, draft["base_sha256"])


def test_keeps_windows_line_endings(ws):
    ws.apply_edit("notes.txt", [Edit("one\ntwo", "one\nthree")], False, "")
    assert (ws.root / "notes.txt").read_bytes() == b"one\r\nthree\r\n"


def test_create_file(ws):
    draft = ws.prepare_edit("src/new.py", [Edit("", "print('new')\n")], create=True)
    assert draft["base_sha256"] == "" and "+print('new')" in draft["diff"]
    ws.apply_edit("src/new.py", [Edit("", "print('new')\n")], True, "")
    assert (ws.root / "src" / "new.py").read_text() == "print('new')\n"
    with pytest.raises(WorkspaceError, match="already exists"):
        ws.prepare_edit("src/new.py", [Edit("", "again")], create=True)
