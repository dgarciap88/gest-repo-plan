"""Ajustes globales del motor: topes y listas de permitidos."""

from __future__ import annotations

import os
from dataclasses import dataclass
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


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw.isdigit() else default


def _env_list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(name, "").strip()
    return tuple(item.strip() for item in raw.split(",") if item.strip()) if raw else default


@dataclass(frozen=True)
class Settings:
    """Topes que un plan no puede superar (ver DESIGN.md 4.5 y 10.1)."""

    timeout_cap_s: int = 4 * 3600
    max_model_tier: str = "standard"
    validator_max_retries_cap: int = 10
    max_attempts_cap: int = 10
    allowed_models: tuple[str, ...] = ()
    command_allowlist: tuple[str, ...] = DEFAULT_COMMAND_ALLOWLIST
    scripts_dir: Path = Path("scripts")

    @classmethod
    def from_env(cls) -> Settings:
        tier = os.environ.get("GP_MAX_MODEL_TIER", "standard")
        return cls(
            timeout_cap_s=_env_int("GP_TIMEOUT_CAP_S", cls.timeout_cap_s),
            max_model_tier=tier if tier in MODEL_TIERS else "standard",
            validator_max_retries_cap=_env_int(
                "GP_VALIDATOR_MAX_RETRIES_CAP", cls.validator_max_retries_cap
            ),
            max_attempts_cap=_env_int("GP_MAX_ATTEMPTS_CAP", cls.max_attempts_cap),
            allowed_models=_env_list("GP_ALLOWED_MODELS", ()),
            command_allowlist=_env_list("GP_COMMAND_ALLOWLIST", DEFAULT_COMMAND_ALLOWLIST),
        )

    def tier_allowed(self, tier: str) -> bool:
        return MODEL_TIERS.index(tier) <= MODEL_TIERS.index(self.max_model_tier)
