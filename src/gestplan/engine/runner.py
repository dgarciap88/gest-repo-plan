"""Ejecución de procesos externos: reales (con matado del árbol) y falsos para los tests."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class ProcResult:
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    stalled: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.stalled

    @property
    def combined(self) -> str:
        return (self.stdout + ("\n" if self.stdout and self.stderr else "") + self.stderr).strip()


class ProcessRunner(Protocol):
    async def run(
        self,
        argv: list[str],
        *,
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
        stdin_text: str | None = None,
        timeout_s: float | None = None,
        idle_timeout_s: float | None = None,
        log_path: Path | None = None,
    ) -> ProcResult: ...


_SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{16,}"),
    re.compile(r"(?i)((?:token|password|secret|api[_-]?key)\s*[=:]\s*)\S{8,}"),
)


def redact(text: str) -> str:
    """Oculta valores con forma de credencial antes de persistir o mostrar texto."""
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            text = pattern.sub(lambda m: m.group(1) + "***", text)
        else:
            text = pattern.sub("***", text)
    return text


def resolve_executable(argv: list[str]) -> list[str]:
    """Resuelve el ejecutable por PATH (necesario en Windows para .cmd/.exe)."""
    found = shutil.which(argv[0])
    return [found, *argv[1:]] if found else argv


def kill_process_tree(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
    else:
        try:
            os.killpg(pid, 9)
        except OSError:
            pass


class RealRunner:
    """Lanza procesos sin shell. Mata el árbol completo al vencer un timeout o cancelar."""

    async def run(
        self,
        argv: list[str],
        *,
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
        stdin_text: str | None = None,
        timeout_s: float | None = None,
        idle_timeout_s: float | None = None,
        log_path: Path | None = None,
    ) -> ProcResult:
        full_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})}
        try:
            proc = await asyncio.create_subprocess_exec(
                *resolve_executable(argv),
                cwd=str(cwd) if cwd else None,
                env=full_env,
                stdin=asyncio.subprocess.PIPE if stdin_text is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (OSError, NotImplementedError) as exc:
            return ProcResult(exit_code=127, stderr=f"no se pudo lanzar {argv[0]}: {exc}")

        out: list[bytes] = []
        err: list[bytes] = []
        last_activity = time.monotonic()
        log_file = open(log_path, "ab") if log_path else None  # noqa: SIM115

        async def pump(stream: asyncio.StreamReader, sink: list[bytes]) -> None:
            nonlocal last_activity
            while chunk := await stream.read(4096):
                sink.append(chunk)
                last_activity = time.monotonic()
                if log_file:
                    log_file.write(chunk)
                    log_file.flush()

        async def feed() -> None:
            if stdin_text is not None and proc.stdin:
                try:
                    proc.stdin.write(stdin_text.encode("utf-8"))
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    proc.stdin.close()

        timed_out = stalled = False
        tasks = [
            asyncio.ensure_future(pump(proc.stdout, out)),  # type: ignore[arg-type]
            asyncio.ensure_future(pump(proc.stderr, err)),  # type: ignore[arg-type]
            asyncio.ensure_future(feed()),
        ]
        started = time.monotonic()
        try:
            while True:
                try:
                    await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=0.2)
                    break
                except TimeoutError:
                    now = time.monotonic()
                    if timeout_s and now - started > timeout_s:
                        timed_out = True
                    elif idle_timeout_s and now - last_activity > idle_timeout_s:
                        stalled = True
                    if timed_out or stalled:
                        kill_process_tree(proc.pid)
                        await proc.wait()
                        break
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            kill_process_tree(proc.pid)
            for task in tasks:
                task.cancel()
            raise
        finally:
            if log_file:
                log_file.close()
        return ProcResult(
            exit_code=proc.returncode,
            stdout=b"".join(out).decode("utf-8", errors="replace"),
            stderr=b"".join(err).decode("utf-8", errors="replace"),
            timed_out=timed_out,
            stalled=stalled,
        )


Handler = Callable[[list[str], "Path | str | None", "str | None"], "ProcResult | None | Awaitable"]


class FakeRunner:
    """Runner de pruebas: cada manejador recibe (argv, cwd, stdin) y devuelve un ProcResult o None."""

    def __init__(self, *handlers: Handler) -> None:
        self.handlers = list(handlers)
        self.calls: list[dict] = []

    async def run(
        self,
        argv: list[str],
        *,
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
        stdin_text: str | None = None,
        timeout_s: float | None = None,
        idle_timeout_s: float | None = None,
        log_path: Path | None = None,
    ) -> ProcResult:
        self.calls.append({"argv": argv, "cwd": cwd, "stdin": stdin_text, "env": env})
        for handler in self.handlers:
            result = handler(argv, cwd, stdin_text)
            if asyncio.iscoroutine(result):
                result = await result
            if result is not None:
                if log_path:
                    Path(log_path).write_text(result.stdout + result.stderr, encoding="utf-8")
                return result  # type: ignore[return-value]
        return ProcResult(exit_code=127, stderr=f"FakeRunner sin manejador para {argv}")


class RoutingRunner:
    """Reparte según el ejecutable (argv[0]) entre runners; el resto va al runner por defecto."""

    def __init__(self, routes: dict[str, ProcessRunner], default: ProcessRunner | None = None) -> None:
        self.routes = routes
        self.default = default or RealRunner()

    async def run(self, argv: list[str], **kwargs) -> ProcResult:  # type: ignore[override]
        name = Path(argv[0]).name.lower().removesuffix(".exe").removesuffix(".cmd")
        runner = self.routes.get(name, self.default)
        return await runner.run(argv, **kwargs)
