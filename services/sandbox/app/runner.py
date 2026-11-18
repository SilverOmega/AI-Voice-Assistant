"""Runs one Python program at a time in a fresh folder, with limits.

The container is the main boundary (no network, read-only filesystem, no capabilities, a
non-root user). Inside it each run also gets operating-system limits on CPU time, memory,
file size, open files and processes, a wall-clock timeout that kills its whole process
group, and a cap on the output kept.
"""

import asyncio
import os
import signal
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from typing import Protocol

from .config import Settings

READ_CHUNK = 4096
FILE_LIMIT_BYTES = 16 * 1024 * 1024
MAX_PROCESSES = 64  # counts every process and thread of the user, the server included
MAX_OPEN_FILES = 64


@dataclass
class RunResult:
    stdout: str
    stderr: str
    exit_code: int | None
    duration_ms: int
    timed_out: bool = False
    truncated: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


class Runner(Protocol):
    async def run(self, language: str, code: str, stdin: str) -> RunResult: ...


class SandboxUnavailable(Exception):
    pass


def limits_supported() -> bool:
    return sys.platform != "win32"


class SubprocessRunner:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._one_at_a_time = asyncio.Semaphore(1)

    async def run(self, language: str, code: str, stdin: str) -> RunResult:
        if language != "python":
            raise SandboxUnavailable(f"Only Python can be run, not {language}.")
        if not limits_supported() and not self.settings.sandbox_allow_unlimited:
            raise SandboxUnavailable("Code runs need Linux or macOS; use the Docker sandbox.")
        async with self._one_at_a_time:
            with tempfile.TemporaryDirectory(prefix="run-") as folder:
                script = os.path.join(folder, "main.py")
                with open(script, "w", encoding="utf-8") as fh:
                    fh.write(code)
                return await self._run(script, folder, stdin)

    async def _run(self, script: str, folder: str, stdin: str) -> RunResult:
        s = self.settings
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": folder, "TMPDIR": folder,
               "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1", "LANG": "C.UTF-8"}
        if sys.platform == "win32":  # Python can't start on Windows without these
            env.update({k: os.environ[k] for k in ("SYSTEMROOT", "WINDIR") if k in os.environ})
        posix = limits_supported()
        started = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            s.sandbox_python, "-I", script,
            cwd=folder,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=posix,
            preexec_fn=self._limits if posix else None,
        )
        truncated = False

        def kill() -> None:
            try:
                if posix:
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
            except (ProcessLookupError, PermissionError):
                pass

        async def read(stream: asyncio.StreamReader) -> bytes:
            nonlocal truncated
            out = bytearray()
            while chunk := await stream.read(READ_CHUNK):
                room = s.sandbox_max_output - len(out)
                out += chunk[:room]
                if len(chunk) > room:
                    truncated = True
                    kill()  # stop a program that floods its output
                    break
            return bytes(out)

        async def feed() -> None:
            assert proc.stdin is not None
            try:
                if stdin:
                    proc.stdin.write(stdin.encode("utf-8"))
                    await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, RuntimeError):
                pass  # it ended without reading its input (uvloop raises RuntimeError for that)
            finally:
                try:
                    proc.stdin.close()
                except RuntimeError:
                    pass

        assert proc.stdout is not None and proc.stderr is not None
        work = asyncio.gather(read(proc.stdout), read(proc.stderr), feed(), proc.wait())
        timed_out = False
        try:
            stdout, stderr, _, _ = await asyncio.wait_for(work, s.sandbox_timeout_seconds)
        except TimeoutError:
            timed_out = True
            kill()
            await proc.wait()
            stdout, stderr = b"", b"Stopped: the program took too long."
        finally:
            kill()  # anything it left running in the background
        return RunResult(
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            exit_code=proc.returncode,
            duration_ms=int((time.monotonic() - started) * 1000),
            timed_out=timed_out,
            truncated=truncated,
        )

    def _limits(self) -> None:  # pragma: no cover - runs in the child process on POSIX
        import resource

        s = self.settings
        cpu = int(s.sandbox_timeout_seconds) + 1
        memory = s.sandbox_memory_mb * 1024 * 1024
        for limit, value in (
            (resource.RLIMIT_CPU, cpu),
            (resource.RLIMIT_AS, memory),
            (resource.RLIMIT_FSIZE, FILE_LIMIT_BYTES),
            (resource.RLIMIT_NOFILE, MAX_OPEN_FILES),
            (resource.RLIMIT_NPROC, MAX_PROCESSES),
            (resource.RLIMIT_CORE, 0),
        ):
            try:
                resource.setrlimit(limit, (value, value))
            except (ValueError, OSError):
                pass
