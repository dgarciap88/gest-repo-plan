"""`gestplan doctor`: comprobaciones del entorno antes de lanzar un run."""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# (argv) -> (código de salida, salida combinada). Inyectable para los tests.
Runner = Callable[[list[str]], tuple[int, str]]

Status = Literal["ok", "warn", "fail"]

_CLAUDE_FALLBACK = Path.home() / ".local" / "bin" / "claude.exe"


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str
    hint: str | None = None

    def format(self) -> str:
        mark = {"ok": "OK   ", "warn": "AVISO", "fail": "FALLO"}[self.status]
        text = f"[{mark}] {self.name}: {self.detail}"
        return text + (f"\n         -> {self.hint}" if self.hint else "")


def default_runner(argv: list[str]) -> tuple[int, str]:
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)
    return done.returncode, (done.stdout + done.stderr).strip()


def run_doctor(run: Runner = default_runner, which: Callable[[str], str | None] = shutil.which) -> list[Check]:
    checks: list[Check] = []

    version = sys.version_info
    checks.append(
        Check("python", "ok", f"{version.major}.{version.minor}.{version.micro}")
        if version >= (3, 11)
        else Check("python", "fail", f"{version.major}.{version.minor}", "se necesita Python 3.11 o superior")
    )

    git = which("git")
    if not git:
        checks.append(Check("git", "fail", "no se encuentra git en el PATH"))
    else:
        code, out = run([git, "--version"])
        checks.append(Check("git", "ok" if code == 0 else "fail", out))
        code, email = run([git, "config", "user.email"])
        if code != 0 or not email:
            checks.append(Check("git user.email", "fail", "no configurado",
                                "git config --global user.email <correo>"))
        else:
            hint = None
            status: Status = "ok"
            if "noreply" not in email:
                status = "warn"
                hint = ("si tu cuenta de GitHub bloquea correos privados (GH007), el push se rechazará; "
                        "usa el correo noreply de GitHub en los repos de trabajo")
            checks.append(Check("git user.email", status, email, hint))

    gh = which("gh")
    if not gh:
        checks.append(Check("gh", "fail", "no se encuentra gh en el PATH", "instala GitHub CLI"))
    else:
        code, out = run([gh, "auth", "status"])
        first = out.splitlines()[0] if out else ""
        checks.append(
            Check("gh auth", "ok", first or "autenticado")
            if code == 0
            else Check("gh auth", "fail", "sin sesión activa", "ejecuta: gh auth login")
        )

    claude = which("claude") or (str(_CLAUDE_FALLBACK) if _CLAUDE_FALLBACK.is_file() else None)
    if not claude:
        checks.append(Check("claude", "fail", "no se encuentra el CLI de Claude Code",
                            "instálalo y añade su carpeta (no el .exe) al PATH"))
    else:
        code, out = run([claude, "--version"])
        checks.append(
            Check("claude", "ok", f"{out} ({claude})")
            if code == 0
            else Check("claude", "fail", out or "no responde a --version")
        )

    copilot = which("copilot")
    checks.append(
        Check("copilot", "ok", copilot)
        if copilot
        else Check("copilot", "warn", "no encontrado (opcional hasta que exista su adaptador)")
    )
    return checks
