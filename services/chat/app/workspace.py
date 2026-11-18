"""The project folder the code helper works in (PROJECT_DIR).

Reading is free; writing happens only from an approved `file_edit` action. Every path is
relative to the folder and must stay inside it: absolute paths, `..`, and links that lead out
are refused, and so are secrets (.env, keys) and tool folders (.git, node_modules).
"""

import difflib
import fnmatch
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".tox", "dist", "build", ".next", ".idea", ".vscode",
}
SECRET_NAMES = [
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.keystore", "id_rsa*", "id_dsa*",
    "id_ecdsa*", "id_ed25519*", ".npmrc", ".pypirc", ".netrc", "credentials*", "*.sqlite", "*.db",
]
MAX_FILE_BYTES = 512 * 1024
MAX_LIST = 300
MAX_READ_LINES = 400
MAX_MATCHES = 60


class WorkspaceError(Exception):
    """A request the project folder can't serve; the message is fit to show the user."""


@dataclass(frozen=True)
class Edit:
    old: str
    new: str


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Workspace:
    def __init__(self, root: str):
        self.root = Path(root).resolve()

    @property
    def available(self) -> bool:
        return self.root.is_dir()

    # ---- paths -------------------------------------------------------------------

    def resolve(self, rel: str) -> Path:
        """The real path of `rel` inside the folder, or WorkspaceError."""
        text = (rel or "").strip().replace("\\", "/")
        if not text:
            raise WorkspaceError("Give a file path inside the project folder.")
        pure = PurePosixPath(text)
        if pure.is_absolute() or ":" in pure.parts[0] or ".." in pure.parts:
            raise WorkspaceError(f"'{rel}' is outside the project folder. Use a path like src/app.py.")
        for part in pure.parts[:-1]:
            if part in SKIP_DIRS:
                raise WorkspaceError(f"'{rel}' is in {part}, which the assistant doesn't touch.")
        if _is_secret(pure.name):
            raise WorkspaceError(f"'{pure.name}' may hold secrets, so the assistant doesn't open it.")
        path = (self.root / Path(*pure.parts)).resolve()
        if path != self.root and self.root not in path.parents:
            raise WorkspaceError(f"'{rel}' leads outside the project folder.")
        return path

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    # ---- reading -----------------------------------------------------------------

    def list_files(self, pattern: str = "") -> list[str]:
        pattern = (pattern or "").strip().replace("\\", "/") or "*"
        out: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            base = Path(dirpath)
            for name in sorted(filenames):
                if _is_secret(name):
                    continue
                path = base / name
                if path.is_symlink() and self.root not in path.resolve().parents:
                    continue
                rel = self.relative(path)
                if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(name, pattern):
                    out.append(rel)
                    if len(out) >= MAX_LIST:
                        return out
        return out

    def read_text(self, rel: str) -> tuple[Path, str, bytes]:
        path = self.resolve(rel)
        if not path.is_file():
            raise WorkspaceError(f"There is no file '{rel}' in the project folder.")
        data = path.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            raise WorkspaceError(f"'{rel}' is larger than {MAX_FILE_BYTES // 1024} KB.")
        if b"\0" in data[:8192]:
            raise WorkspaceError(f"'{rel}' is not a text file.")
        try:
            return path, data.decode("utf-8"), data
        except UnicodeDecodeError:
            raise WorkspaceError(f"'{rel}' is not a UTF-8 text file.") from None

    def read(self, rel: str, start_line: int = 1, end_line: int | None = None) -> str:
        """Lines of a file, numbered, so the model can quote them exactly."""
        _, text, _ = self.read_text(rel)
        lines = text.splitlines()
        start = max(1, int(start_line or 1))
        end = min(len(lines), int(end_line) if end_line else start + MAX_READ_LINES - 1)
        end = min(end, start + MAX_READ_LINES - 1)
        body = "\n".join(f"{n:>5}  {lines[n - 1]}" for n in range(start, end + 1))
        more = f"\n(lines {start}-{end} of {len(lines)})" if end < len(lines) or start > 1 else ""
        return (body or "(empty file)") + more

    def search(self, text: str) -> list[str]:
        needle = (text or "").strip()
        if not needle:
            raise WorkspaceError("Say what text to look for.")
        folded = needle.casefold()
        hits: list[str] = []
        for rel in self.list_files():
            try:
                _, content, _ = self.read_text(rel)
            except WorkspaceError:
                continue
            for n, line in enumerate(content.splitlines(), 1):
                if folded in line.casefold():
                    hits.append(f"{rel}:{n}: {line.strip()[:200]}")
                    if len(hits) >= MAX_MATCHES:
                        return hits
        return hits

    # ---- edits (approved only) ------------------------------------------------------

    def prepare_edit(self, rel: str, edits: list[Edit], create: bool) -> dict[str, Any]:
        """Check an edit against the file as it is now and describe it for review.
        Nothing is written."""
        path = self.resolve(rel)
        if create:
            if path.exists():
                raise WorkspaceError(f"'{rel}' already exists. Edit it instead of creating it.")
            before, base = "", ""
        else:
            path, before, data = self.read_text(rel)
            base = sha256(data)
        after = _apply(before, edits, create, rel)
        name = self.relative(path)
        diff = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile="/dev/null" if create else f"a/{name}",
                tofile=f"b/{name}",
            )
        )
        return {
            "path": name,
            "create": create,
            "edits": [{"old": e.old, "new": e.new} for e in edits],
            "base_sha256": base,
            "diff": diff,
        }

    def apply_edit(self, rel: str, edits: list[Edit], create: bool, base_sha256: str) -> str:
        """Write an approved edit, if the file hasn't changed since it was drafted."""
        path = self.resolve(rel)
        if create:
            if path.exists():
                raise WorkspaceError(f"'{rel}' was created since this edit was drafted. Ask again.")
            before = ""
        else:
            path, before, data = self.read_text(rel)
            if base_sha256 and sha256(data) != base_sha256:
                raise WorkspaceError(
                    f"'{rel}' changed since this edit was drafted, so it wasn't applied. Ask again."
                )
        after = _apply(before, edits, create, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, after)
        verb = "Created" if create else "Updated"
        return f"{verb} {self.relative(path)}."


def _is_secret(name: str) -> bool:
    lowered = name.lower()
    return any(fnmatch.fnmatch(lowered, pattern) for pattern in SECRET_NAMES)


def _apply(text: str, edits: list[Edit], create: bool, rel: str) -> str:
    if not edits:
        raise WorkspaceError("The edit has no changes.")
    if create:
        return "".join(e.new for e in edits)
    crlf = "\r\n" in text
    for edit in edits:
        if crlf and "\r\n" not in edit.old:  # the model sees lines, not line endings
            edit = Edit(edit.old.replace("\n", "\r\n"), edit.new.replace("\n", "\r\n"))
        if not edit.old:
            raise WorkspaceError(f"Each change to '{rel}' needs the exact text it replaces.")
        count = text.count(edit.old)
        if count != 1:
            where = "isn't in" if count == 0 else f"appears {count} times in"
            raise WorkspaceError(
                f"The text to replace {where} '{rel}'; it must appear exactly once. Ask again."
            )
        text = text.replace(edit.old, edit.new, 1)
    return text


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        if path.exists():
            os.chmod(tmp, path.stat().st_mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def edits_from(payload: dict[str, Any]) -> list[Edit]:
    return [Edit(str(e.get("old", "")), str(e.get("new", ""))) for e in payload.get("edits") or []]
