"""Nodos locales de apoyo: ejecución de comandos permitidos en el workspace."""

from __future__ import annotations

from pathlib import PurePath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from gestplan.nodes.base import NodeType, event
from gestplan.nodes.registry import register
from gestplan.plans.issues import Issue, error


@register
class RunCommand(NodeType):
    type = "run_command"
    family = "local"
    side_effect = "local"
    default_timeout_s = 900
    idle_timeout_s = 300
    description = (
        "Ejecuta un comando (lista de argumentos, sin shell) en el workspace; solo ejecutables de la "
        "lista de permitidos (tests, lint, build). 'failed' significa que el comando terminó con error, "
        "p. ej. tests en rojo: se suele enrutar de vuelta al agente."
    )
    events = (
        event("ok", "El comando terminó con código 0"),
        event("failed", "El comando terminó con código distinto de 0", failure=True),
        event("timeout", "Se superó el tiempo máximo", failure=True),
    )
    example = {"workspace": "${nodes.ws.outputs.path}", "command": ["python", "-m", "pytest", "-q"]}

    class ConfigModel(BaseModel):
        model_config = ConfigDict(extra="forbid")

        workspace: str
        command: list[str] = Field(min_length=1, description="Ejecutable y argumentos")

    class OutputsModel(BaseModel):
        exit_code: int
        passed: bool
        output_tail: str

    @classmethod
    def check(cls, node_id: str, spec: Any, ctx: Any) -> list[Issue]:
        config = cls.parse_config(spec.config)
        if not config:
            return []
        executable = PurePath(config.command[0]).name.lower().removesuffix(".exe")
        if executable not in ctx.settings.command_allowlist:
            return [
                error(
                    "command_not_allowed",
                    f"el ejecutable '{executable}' no está en la lista de permitidos",
                    node=node_id,
                    path="config.command",
                    hint=f"permitidos: {', '.join(ctx.settings.command_allowlist)}",
                )
            ]
        return []
