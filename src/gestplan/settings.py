"""Ajustes globales del motor: topes, listas de permitidos y rutas."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

MODEL_TIERS = ("cheap", "standard", "premium")

DEFAULT_COMMAND_ALLOWLIST = (
    "python",
    "pytest",
    "ruff",
    "mypy",
    "npm",
    "npx",
    "pnpm",
    "node",
    "dotnet",
    "mvn",
    "gradle",
)

DEFAULT_CLAUDE_MODELS = {
    "cheap": "claude-haiku-5-5",
    "standard": "claude-sonnet-5-5",
    "premium": "claude-opus-5-5",
}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw.isdigit() else default


def _env_list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(name, "").strip()
    return tuple(item.strip() for item in raw.split(",") if item.strip()) if raw else default


@dataclass(frozen=True)
class Settings:
    """Topes que un plan no puede superar (DESIGN.md 4.5 y 10.1) y rutas de trabajo."""

    timeout_cap_s: int = 4 * 3600
    max_model_tier: str = "standard"
    validator_max_retries_cap: int = 10
    max_attempts_cap: int = 10
    allowed_models: tuple[str, ...] = ()
    command_allowlist: tuple[str, ...] = DEFAULT_COMMAND_ALLOWLIST
    scripts_dir: Path = Path("scripts")
    # Ejecución
    root: Path = Path(".")
    data_dir: Path = Path("data")
    workspaces_dir: Path | None = None
    max_concurrent_nodes: int = 4
    max_agents_parallel: int = 2
    poll_interval_s: float = 0.2
    env_allowlist: tuple[str, ...] = ()
    # IA
    claude_path: str | None = None
    claude_setting_sources: str = "project"
    models: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_CLAUDE_MODELS))

    @classmethod
    def from_env(cls) -> Settings:
        tier = os.environ.get("GP_MAX_MODEL_TIER", "standard")
        workspaces = os.environ.get("GP_WORKSPACES_DIR", "").strip()
        return cls(
            timeout_cap_s=_env_int("GP_TIMEOUT_CAP_S", cls.timeout_cap_s),
            max_model_tier=tier if tier in MODEL_TIERS else "standard",
            validator_max_retries_cap=_env_int(
                "GP_VALIDATOR_MAX_RETRIES_CAP", cls.validator_max_retries_cap
            ),
            max_attempts_cap=_env_int("GP_MAX_ATTEMPTS_CAP", cls.max_attempts_cap),
            allowed_models=_env_list("GP_ALLOWED_MODELS", ()),
            command_allowlist=_env_list("GP_COMMAND_ALLOWLIST", DEFAULT_COMMAND_ALLOWLIST),
            root=Path(os.environ.get("GP_ROOT", ".")),
            data_dir=Path(os.environ.get("GP_DATA_DIR", "data")),
            workspaces_dir=Path(workspaces) if workspaces else None,
            max_concurrent_nodes=_env_int("GP_MAX_CONCURRENT_NODES", cls.max_concurrent_nodes),
            max_agents_parallel=_env_int("GP_MAX_AGENTS_PARALLEL", cls.max_agents_parallel),
            env_allowlist=_env_list("GP_ENV_ALLOWLIST", ()),
            claude_path=os.environ.get("GP_CLAUDE_PATH") or None,
            claude_setting_sources=os.environ.get("GP_CLAUDE_SETTING_SOURCES", "project"),
        )

    def tier_allowed(self, tier: str) -> bool:
        return MODEL_TIERS.index(tier) <= MODEL_TIERS.index(self.max_model_tier)

    @property
    def workspaces_root(self) -> Path:
        return self.workspaces_dir or (self.data_dir / "workspaces")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "gestplan.db"
