"""Adaptador del CLI de Claude Code (ver DESIGN.md, "Adaptador de Claude")."""

from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gestplan.settings import Settings

# En Windows Claude Code ejecuta comandos con la herramienta PowerShell; en el resto, con Bash.
_SHELLS = ("Bash", "PowerShell")
DEV_TOOLS = ("Read", "Edit", "Write", "Glob", "Grep") + tuple(
    f"{shell}({command})" for shell in _SHELLS
    for command in ("python *", "py *", "pytest *", "git status*", "git diff*", "git log*")
)
VALIDATOR_TOOLS = ("Read", "Grep", "Glob") + tuple(
    f"{shell}({command})" for shell in _SHELLS for command in ("git status*", "git diff*", "git log*")
)
_NAMESPACE = uuid.UUID("6f1c1d2e-0b0a-4b7e-9f34-2f5b8f0c9a11")


def claude_executable(settings: Settings) -> str:
    if settings.claude_path:
        return settings.claude_path
    found = shutil.which("claude")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "claude.exe"
    return str(fallback) if fallback.is_file() else "claude"


def resolve_model(settings: Settings, *, tier: str | list[str], model: str | None, attempt: int) -> str:
    """Modelo explícito, o el del nivel (con escalado por intento si se da una lista de niveles)."""
    if model:
        return model
    tiers = [tier] if isinstance(tier, str) else list(tier)
    chosen = tiers[min(max(attempt - 1, 0), len(tiers) - 1)]
    return settings.models[chosen]


def session_uuid(run_id: str, node_id: str, attempt: int | None = None) -> str:
    """UUID determinista por run y nodo (y por intento si la sesión no se continúa)."""
    name = f"{run_id}:{node_id}" + (f":{attempt}" if attempt is not None else "")
    return str(uuid.uuid5(_NAMESPACE, name))


@dataclass
class ClaudeOptions:
    model: str
    permission_mode: str
    allowed_tools: tuple[str, ...]
    session_id: str
    resume: bool = False
    effort: str | None = None
    agent: str | None = None
    extra_dirs: list[str] = field(default_factory=list)
    max_cost_usd: float | None = None
    setting_sources: str = "project"


def build_argv(exe: str, opts: ClaudeOptions) -> list[str]:
    argv = [exe, "-p", "--output-format", "json", "--model", opts.model,
            "--permission-mode", opts.permission_mode, "--permission-prompts", "none"]
    if opts.setting_sources:
        argv += ["--setting-sources", opts.setting_sources]
    if opts.allowed_tools:
        argv += ["--allowedTools", ",".join(opts.allowed_tools)]
    argv += ["--resume" if opts.resume else "--session-id", opts.session_id]
    if opts.effort:
        argv += ["--effort", opts.effort]
    if opts.agent:
        argv += ["--agent", opts.agent]
    if opts.max_cost_usd:
        argv += ["--max-budget-usd", str(opts.max_cost_usd)]
    for directory in opts.extra_dirs:
        argv += ["--add-dir", directory]
    return argv


def parse_cli_output(stdout: str) -> dict[str, Any] | None:
    """Lee el JSON único que imprime `claude -p --output-format json`."""
    text = stdout.strip()
    if not text:
        return None
    for candidate in (text, text.splitlines()[-1]):
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Extrae el objeto JSON de la respuesta del modelo (tolera bloques ```json)."""
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, re.DOTALL)
    candidates = [fenced.group(1)] if fenced else []
    candidates.append(cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        candidates.append(cleaned[start:end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None
