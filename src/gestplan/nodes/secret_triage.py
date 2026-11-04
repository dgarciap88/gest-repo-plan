"""Triaje con IA de los hallazgos del escaneo de secretos (NICE_TO_HAVE 1.2, segunda pasada)."""

from __future__ import annotations

from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from gestplan.nodes.base import NodeType, event
from gestplan.nodes.common import ModelTier, TemplateStr
from gestplan.nodes.registry import register


@register
class SecretTriage(NodeType):
    type = "secret_triage"
    family = "security"
    side_effect = "none"
    default_timeout_s = 600
    idle_timeout_s = 300
    description = (
        "Segunda pasada del escaneo de secretos: un agente de IA decide, para cada hallazgo de `secret_scan`, si es un "
        "secreto real o un falso positivo (ejemplos, tests, hashes, ids). El agente solo recibe fragmentos de unas "
        "pocas líneas con los valores sospechosos YA ENMASCARADOS, nunca el fichero ni el secreto. Si no responde algo "
        "claro sobre un hallazgo, se considera secreto. Conéctalo a `secret_scan.secrets_detected`."
    )
    events = (
        event("all_false_positive", "Todos los hallazgos son falsos positivos: se puede continuar"),
        event("confirmed", "Al menos un hallazgo es un secreto real", failure=True),
        event("failed", "El agente no pudo hacer el triaje", failure=True),
        event("timeout", "Se superó el tiempo máximo", failure=True),
        event("budget_exceeded", "Se superó el presupuesto de coste", failure=True),
    )
    example = {"workspace": "${nodes.ws.outputs.path}", "findings": "${nodes.scan.outputs.findings}",
               "model_tier": "cheap"}

    class ConfigModel(BaseModel):
        model_config = ConfigDict(extra="forbid")

        provider: Literal["claude", "copilot"] = Field(default="claude", description="Proveedor de IA")
        model_tier: ModelTier = Field(default="cheap", description="Nivel de modelo por coste")
        model: str | None = Field(default=None, description="Modelo explícito (solo si está permitido)")
        workspace: str = Field(description="Workspace donde están los ficheros señalados")
        findings: Union[list[dict[str, Any]], TemplateStr] = Field(  # noqa: UP007
            description="Hallazgos de secret_scan: normalmente ${nodes.<scan>.outputs.findings}"
        )
        context_lines: int = Field(default=2, ge=0, le=6, description="Líneas de contexto a cada lado de la línea")
        max_cost_usd: float | None = Field(default=None, gt=0, description="Tope de coste de la invocación")

    class OutputsModel(BaseModel):
        confirmed: list[dict[str, Any]] = Field(description="Hallazgos que son secretos reales, con el motivo")
        dismissed: list[dict[str, Any]] = Field(description="Falsos positivos descartados, con su motivo")
        summary: str = Field(description="Resultado del triaje en texto, listo para un comentario o una aprobación")
