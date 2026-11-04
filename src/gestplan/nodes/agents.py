"""Nodos de IA: agente que modifica el workspace y validador cruzado."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from gestplan.nodes.base import NodeType, event
from gestplan.nodes.common import ModelTier
from gestplan.nodes.registry import register
from gestplan.plans.issues import Issue, error, warning
from gestplan.settings import MODEL_TIERS


class AgentConfigBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["claude", "copilot"] = Field(default="claude", description="Proveedor de IA")
    model_tier: ModelTier | list[ModelTier] = Field(
        default="cheap",
        description=(
            "Nivel de modelo por coste: cheap, standard o premium. Una lista, p. ej. [\"cheap\", \"standard\"], "
            "usa el nivel i-ésimo en el intento i (el último se repite) para escalar en los reintentos"
        ),
    )
    model: str | None = Field(default=None, description="Modelo explícito (solo si está permitido)")
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    agent: str | None = Field(default=None, description="Agente de reglas (--agent)")
    skill: str | None = Field(
        default=None,
        pattern=r"^(?:[A-Za-z0-9_.:-]+|\$\{[^}]+\})$",
        description=(
            "Skill a aplicar: el prompt empieza por la línea /<skill>. Se busca en la carpeta de skills de usuario "
            "(<GP_AGENTS_HOME>/skills, por defecto ~/.copilot/skills) y en las del repositorio"
        ),
    )
    workspace: str = Field(description="Ruta del workspace donde trabaja el agente")
    prompt_template: str | None = Field(default=None, description="Plantilla de prompt (ruta)")
    prompt: str | None = Field(default=None, description="Instrucciones en línea")
    variables: dict[str, Any] = Field(
        default_factory=dict, description="Variables disponibles en la plantilla"
    )
    data: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Contenido de terceros (ticket, comentarios, ficheros) que el agente debe leer: título del bloque → "
            "texto. Se entrega delimitado como DATOS NO CONFIABLES: el agente lo trata como información, nunca como "
            "órdenes del sistema"
        ),
    )
    max_data_chars: int = Field(
        default=3_000,
        ge=200,
        description=(
            "Tamaño máximo en caracteres de cada bloque de `data` dentro del prompt. Un bloque mayor NO se recorta: "
            "se guarda entero en la carpeta `agent-data` junto al workspace (que se da al agente como carpeta "
            "adicional) y el prompt solo le ordena leerlo completo antes de empezar"
        ),
    )
    session_mode: Literal["fresh", "continue"] = "fresh"
    permission_mode: Literal["acceptEdits", "dontAsk", "plan"] = "acceptEdits"
    allowed_tools: list[str] = Field(default_factory=list)
    extra_dirs: list[str] = Field(default_factory=list)
    max_cost_usd: float | None = Field(default=None, gt=0, description="Tope de coste por invocación")

    @model_validator(mode="after")
    def _needs_prompt(self) -> AgentConfigBase:
        if not self.prompt_template and not self.prompt:
            raise ValueError("indica 'prompt_template' o 'prompt'")
        return self


def _check_agent_files(node_id: str, ctx: Any, config: AgentConfigBase) -> list[Issue]:
    """Aviso (no error) si la skill o el agente no están en la carpeta de usuario: pueden venir del repositorio."""
    home = ctx.settings.agents_home
    issues: list[Issue] = []
    if config.skill and "${" not in config.skill and not (home / "skills" / config.skill / "SKILL.md").is_file():
        issues.append(warning("skill_not_found", f"no se encontró la skill '{config.skill}' en {home / 'skills'}",
                              node=node_id, path="config.skill",
                              hint="si está en el repositorio (.github/skills, .claude/skills) no hace falta nada; "
                                   "si no, revisa GP_AGENTS_HOME"))
    if config.agent and "${" not in config.agent and not any(
            (home / "agents" / f"{config.agent}{ext}").is_file() for ext in (".agent.md", ".md")):
        issues.append(warning("agent_not_found", f"no se encontró el agente '{config.agent}' en {home / 'agents'}",
                              node=node_id, path="config.agent",
                              hint="si está en el repositorio no hace falta nada; si no, revisa GP_AGENTS_HOME"))
    return issues


def _check_agent(node_id: str, spec: Any, ctx: Any, config: AgentConfigBase) -> list[Issue]:
    issues: list[Issue] = []
    if config.prompt_template and not (ctx.root / config.prompt_template).is_file():
        issues.append(
            error(
                "missing_template",
                f"no existe la plantilla de prompt '{config.prompt_template}'",
                node=node_id,
                path="config.prompt_template",
                hint="crea el fichero o corrige la ruta (relativa a la raíz del proyecto)",
            )
        )
    tiers = [config.model_tier] if isinstance(config.model_tier, str) else list(config.model_tier)
    for tier in tiers:
        if not ctx.settings.tier_allowed(tier):
            issues.append(
                error(
                    "model_tier_above_cap",
                    f"el nivel '{tier}' supera el máximo permitido ('{ctx.settings.max_model_tier}')",
                    node=node_id,
                    path="config.model_tier",
                    hint=f"usa uno de: {', '.join(t for t in MODEL_TIERS if ctx.settings.tier_allowed(t))}",
                )
            )
    issues += _check_agent_files(node_id, ctx, config)
    if config.provider == "copilot" and not config.model:
        missing = [t for t in tiers if not ctx.settings.copilot_models.get(t)]
        if missing:
            issues.append(
                error(
                    "copilot_model_missing",
                    f"no hay modelo de Copilot configurado para el nivel '{missing[0]}'",
                    node=node_id,
                    path="config.model_tier",
                    hint=f"define GP_COPILOT_MODEL_{missing[0].upper()} en el .env o indica 'model'",
                )
            )
    if config.model and ctx.settings.allowed_models and config.model not in ctx.settings.allowed_models:
        issues.append(
            error(
                "model_not_allowed",
                f"el modelo '{config.model}' no está en la lista de modelos permitidos",
                node=node_id,
                path="config.model",
                hint=f"permitidos: {', '.join(ctx.settings.allowed_models)}",
            )
        )
    return issues


@register
class IaAgent(NodeType):
    type = "ia_agent"
    family = "ia"
    side_effect = "local"
    default_timeout_s = 3600
    idle_timeout_s = 600
    description = (
        "Lanza un agente de IA (CLI) en modo no interactivo sobre el workspace para implementar una "
        "tarea. No hace commit ni push. No pregunta al usuario: ante una ambigüedad decide y la "
        "registra en 'assumptions' y 'open_questions'. Devuelve un resultado estructurado."
    )
    events = (
        event("done", "El agente terminó y devolvió un resultado válido"),
        event("failed", "El agente falló o no devolvió un resultado válido", failure=True),
        event("timeout", "Se superó el tiempo máximo", failure=True),
        event("stalled", "Sin actividad durante demasiado tiempo", failure=True),
        event("budget_exceeded", "Se superó el presupuesto de coste", failure=True),
    )
    example = {
        "provider": "claude",
        "model_tier": "cheap",
        "workspace": "${nodes.ws.outputs.path}",
        "prompt_template": "templates/prompts/implement-task.md",
        "variables": {"task_id": "${inputs.task_id}"},
    }

    class ConfigModel(AgentConfigBase):
        pass

    class OutputsModel(BaseModel):
        summary: str
        files: list[str]
        files_changed: int
        suggested_commit_subject: str
        suggested_commit_body: str
        suggested_pr_title: str
        suggested_pr_body: str
        assumptions: list[str]
        open_questions: list[str]
        cost_usd: float
        session_id: str

    @classmethod
    def check(cls, node_id: str, spec: Any, ctx: Any) -> list[Issue]:
        config = cls.parse_config(spec.config)
        return _check_agent(node_id, spec, ctx, config) if config else []


@register
class IaAgentValidator(NodeType):
    type = "ia_agent_validator"
    family = "ia"
    side_effect = "local"
    default_timeout_s = 1800
    idle_timeout_s = 600
    loop_event = "changes_requested"
    description = (
        "Validación cruzada de los cambios del workspace por un agente de solo lectura. Devuelve un "
        "veredicto estructurado con incidencias. Si pide cambios, conecta 'changes_requested' con el "
        "nodo ia_agent; 'max_retries' acota el bucle."
    )
    events = (
        event("approved", "Los cambios son aceptables"),
        event("changes_requested", "Hay incidencias bloqueantes; el agente debe corregir"),
        event("max_retries_reached", "Se agotaron las rondas de corrección", failure=True),
        event("no_progress", "El feedback no cambia entre rondas", failure=True),
        event("failed", "El validador falló", failure=True),
        event("timeout", "Se superó el tiempo máximo", failure=True),
        event("budget_exceeded", "Se superó el presupuesto de coste", failure=True),
    )
    example = {
        "provider": "claude",
        "model_tier": "cheap",
        "workspace": "${nodes.ws.outputs.path}",
        "prompt_template": "templates/prompts/validate-task.md",
        "max_retries": 2,
    }

    class ConfigModel(AgentConfigBase):
        permission_mode: Literal["acceptEdits", "dontAsk", "plan"] = "plan"
        max_retries: int = Field(default=2, ge=0, description="Veces que puede devolver el trabajo")
        stop_on_no_progress: bool = True
        blocking_severities: list[Literal["high", "medium", "low"]] = Field(
            default_factory=lambda: ["high", "medium"]
        )
        criteria: str | None = Field(default=None, description="Criterios de aceptación")

    class OutputsModel(BaseModel):
        verdict: str
        feedback: str
        issues: list[dict[str, Any]]
        notes: str
        retries_used: int
        cost_usd: float

    @classmethod
    def loop_bound(cls, config: dict[str, Any]) -> int | None:
        parsed = cls.parse_config(config)
        return parsed.max_retries + 1 if parsed else None

    @classmethod
    def check(cls, node_id: str, spec: Any, ctx: Any) -> list[Issue]:
        config = cls.parse_config(spec.config)
        if not config:
            return []
        issues = _check_agent(node_id, spec, ctx, config)
        cap = ctx.settings.validator_max_retries_cap
        if config.max_retries > cap:
            issues.append(
                error(
                    "retries_above_cap",
                    f"max_retries={config.max_retries} supera el tope global ({cap})",
                    node=node_id,
                    path="config.max_retries",
                )
            )
        return issues
