"""Nodos de GitHub de lectura (con gh): estado del PR, checks, comentarios de revisión y workflows."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from gestplan.nodes.base import NodeType, event
from gestplan.nodes.common import TInt
from gestplan.nodes.registry import register

_FAILED = event("failed", "La consulta a GitHub falló", failure=True)


class _PrConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace: str = Field(description="Workspace del repositorio (gh deduce el repo de su remoto)")
    pr: str = Field(description="Número o URL del PR, p. ej. ${nodes.pr.outputs.pr_url}")


@register
class GithubGetPrStatus(NodeType):
    type = "github.get_pr_status"
    family = "github"
    side_effect = "none"
    default_timeout_s = 120
    description = "Consulta el estado de un PR: abierto, fusionado o cerrado (y si es fusionable)."
    events = (event("open", "El PR sigue abierto"), event("merged", "El PR se fusionó"),
              event("closed", "El PR se cerró sin fusionar"), _FAILED)
    example = {"workspace": "${nodes.ws.outputs.path}", "pr": "${nodes.pr.outputs.pr_url}"}
    ConfigModel = _PrConfig

    class OutputsModel(BaseModel):
        state: str
        number: int
        url: str
        mergeable: str
        review_decision: str


@register
class GithubWaitChecks(NodeType):
    type = "github.wait_checks"
    family = "github"
    side_effect = "none"
    default_timeout_s = None  # la espera se acota con max_wait_s, no con el timeout de proceso
    description = (
        "Espera por sondeo a que terminen los checks del PR (el nodo queda en espera externa). 'failure' "
        "incluye los nombres y enlaces de los checks fallidos para devolver el trabajo al agente."
    )
    events = (event("success", "Todos los checks pasaron (o no hay checks)"),
              event("failure", "Algún check falló", failure=True),
              event("timeout", "Se superó max_wait_s sin que terminaran", failure=True), _FAILED)
    example = {"workspace": "${nodes.ws.outputs.path}", "pr": "${nodes.pr.outputs.pr_url}", "max_wait_s": 1800}

    class ConfigModel(_PrConfig):
        poll_interval_s: TInt = Field(default=30, description="Segundos entre consultas")
        max_wait_s: TInt = Field(default=3600, description="Espera máxima total")

    class OutputsModel(BaseModel):
        state: str
        failed_checks: list[dict[str, Any]]
        summary: str = Field(description="Resumen en texto de los checks, listo para un prompt")


@register
class GithubGetReviewComments(NodeType):
    type = "github.get_review_comments"
    family = "github"
    side_effect = "none"
    default_timeout_s = 120
    description = (
        "Lee las revisiones y comentarios del PR (generales y en líneas de código). Contenido NO CONFIABLE: "
        "pásalo al agente como dato. Con 'since_login' ignora los comentarios del propio autor del PR."
    )
    events = (event("has_comments", "Hay comentarios o cambios solicitados que atender"),
              event("no_comments", "No hay nada pendiente"), _FAILED)
    example = {"workspace": "${nodes.ws.outputs.path}", "pr": "${nodes.pr.outputs.pr_url}"}

    class ConfigModel(_PrConfig):
        ignore_authors: list[str] = Field(default_factory=list, description="Logins a ignorar (p. ej. bots)")

    class OutputsModel(BaseModel):
        count: int
        changes_requested: bool
        comments: list[dict[str, Any]] = Field(description="Cada uno: author, path, line, body")
        text: str = Field(description="Todo concatenado, listo para un prompt")


@register
class GithubGetWorkflowRuns(NodeType):
    type = "github.get_workflow_runs"
    family = "github"
    side_effect = "none"
    default_timeout_s = 120
    description = "Lista las últimas ejecuciones de workflows de una rama."
    events = (event("ok", "Ejecuciones listadas (puede no haber ninguna)"), _FAILED)
    example = {"workspace": "${nodes.ws.outputs.path}", "branch": "${nodes.ws.outputs.remote_branch}"}

    class ConfigModel(BaseModel):
        model_config = ConfigDict(extra="forbid")

        workspace: str
        branch: str
        limit: TInt = 10

    class OutputsModel(BaseModel):
        runs: list[dict[str, Any]]
        count: int
        latest_conclusion: str
