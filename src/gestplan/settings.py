"""Ajustes globales del motor: topes, listas de permitidos y rutas."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from gestplan.envfile import load_env_file

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


def _env_float(name: str, default: float) -> float:
    try:
        return max(float(os.environ.get(name, "").strip()), 0.0)
    except ValueError:
        return default


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
    copilot_path: str | None = None
    # Carpeta donde viven los agentes y skills de usuario (<home>/.copilot/agents y /skills)
    agents_home: Path = field(default_factory=lambda: Path.home() / ".copilot")
    copilot_models: dict[str, str] = field(default_factory=dict)
    # Atlassian Cloud (las credenciales viven en el .env, nunca en los planes)
    jira_base_url: str = ""
    jira_email: str = ""
    jira_token: str = ""
    confluence_base_url: str = ""
    confluence_email: str = ""
    confluence_token: str = ""
    # Seguridad de la API local
    api_token: str = ""
    git_user_name: str = ""
    git_user_email: str = ""
    allowed_hosts: tuple[str, ...] = ()
    # Presupuestos en USD (0 = sin límite): por run (con sus hijos) y por día natural
    run_budget_usd: float = 0.0
    daily_budget_usd: float = 0.0

    @classmethod
    def from_env(cls, root: Path | None = None) -> Settings:
        root = root or Path(os.environ.get("GP_ROOT", "."))
        load_env_file(root / ".env")
        env = os.environ.get
        tier = os.environ.get("GP_MAX_MODEL_TIER", "standard")
        jira_base = env("JIRA_BASE_URL", "").strip().rstrip("/")
        jira_email, jira_token = env("JIRA_EMAIL", "").strip(), env("JIRA_API_TOKEN", "").strip()
        copilot_models = {t: env(f"GP_COPILOT_MODEL_{t.upper()}", "").strip() for t in MODEL_TIERS}
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
            root=root,
            data_dir=Path(os.environ.get("GP_DATA_DIR", "data")),
            workspaces_dir=Path(workspaces) if workspaces else None,
            max_concurrent_nodes=_env_int("GP_MAX_CONCURRENT_NODES", cls.max_concurrent_nodes),
            max_agents_parallel=_env_int("GP_MAX_AGENTS_PARALLEL", cls.max_agents_parallel),
            env_allowlist=_env_list("GP_ENV_ALLOWLIST", ()),
            claude_path=os.environ.get("GP_CLAUDE_PATH") or None,
            claude_setting_sources=os.environ.get("GP_CLAUDE_SETTING_SOURCES", "project"),
            copilot_path=env("GP_COPILOT_PATH") or None,
            agents_home=Path(env("GP_AGENTS_HOME", "").strip() or Path.home() / ".copilot"),
            copilot_models={t: m for t, m in copilot_models.items() if m},
            jira_base_url=jira_base,
            jira_email=jira_email,
            jira_token=jira_token,
            confluence_base_url=env("CONFLUENCE_BASE_URL", "").strip().rstrip("/")
            or (f"{jira_base}/wiki" if jira_base else ""),
            confluence_email=env("CONFLUENCE_EMAIL", "").strip() or jira_email,
            confluence_token=env("CONFLUENCE_API_TOKEN", "").strip() or jira_token,
            api_token=env("GP_API_TOKEN", "").strip(),
            git_user_name=env("GP_GIT_USER_NAME", "").strip(),
            git_user_email=env("GP_GIT_USER_EMAIL", "").strip(),
            allowed_hosts=_env_list("GP_ALLOWED_HOSTS", ()),
            run_budget_usd=_env_float("GP_RUN_BUDGET_USD", 0.0),
            daily_budget_usd=_env_float("GP_DAILY_BUDGET_USD", 0.0),
        )

    def tier_allowed(self, tier: str) -> bool:
        return MODEL_TIERS.index(tier) <= MODEL_TIERS.index(self.max_model_tier)

    @property
    def workspaces_root(self) -> Path:
        return self.workspaces_dir or (self.data_dir / "workspaces")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "gestplan.db"

    @property
    def secret_values(self) -> tuple[str, ...]:
        return tuple(v for v in (self.jira_token, self.confluence_token, self.api_token) if v)
