"""Nodos de git: commit, push y creación de PR."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from gestplan.nodes.base import NodeType, artifact, event
from gestplan.nodes.common import TBool, TStrList
from gestplan.nodes.registry import register


@register
class GitCommit(NodeType):
    type = "git.commit"
    family = "git"
    side_effect = "local"
    default_timeout_s = 300
    description = (
        "Hace stage y commit de los cambios del workspace. El asunto debe ser descriptivo (política de "
        "mensajes). Nunca añade trailers de coautoría de IA."
    )
    events = (
        event("ok", "Commit creado"),
        event("nothing_to_commit", "No había cambios"),
        event("invalid_message", "El mensaje incumple la política de mensajes", failure=True),
        event("hook_failed", "Un hook de git rechazó el commit", failure=True),
        event("failed", "El commit falló", failure=True),
    )
    example = {
        "workspace": "${nodes.ws.outputs.path}",
        "subject": "${nodes.dev.outputs.suggested_commit_subject}",
        "body": "${nodes.dev.outputs.suggested_commit_body}",
    }

    class ConfigModel(BaseModel):
        model_config = ConfigDict(extra="forbid")

        workspace: str
        subject: str = Field(description="Asunto del commit (imperativo, con la clave del ticket)")
        body: str = Field(default="", description="Cuerpo: por qué y qué enfoque")
        paths: TStrList = Field(default_factory=lambda: ["."], description="Rutas a incluir")

    class OutputsModel(BaseModel):
        commit_sha: str
        subject: str


@register
class GitPush(NodeType):
    type = "git.push"
    family = "git"
    side_effect = "external"
    default_timeout_s = 300
    description = (
        "Publica la rama de trabajo actualizando la rama remota original (refspec explícito, sin "
        "--force). EFECTO EXTERNO: requiere un manual_approval que lo cubra."
    )
    events = (
        event("ok", "Push realizado"),
        event("rejected", "El remoto rechazó el push (avanzó o hay reglas de rama)", failure=True),
        event("failed", "El push falló", failure=True),
        event("timeout", "Se superó el tiempo máximo", failure=True),
    )
    artifacts = (artifact("branch", "Rama publicada en el remoto (enlace a GitHub si el remoto lo es)"),)
    example = {
        "workspace": "${nodes.ws.outputs.path}",
        "remote_branch": "${nodes.ws.outputs.remote_branch}",
    }

    class ConfigModel(BaseModel):
        model_config = ConfigDict(extra="forbid")

        workspace: str
        remote_branch: str = Field(description="Rama remota a actualizar")
        remote: str = "origin"

    class OutputsModel(BaseModel):
        pushed_sha: str
        remote_branch: str


@register
class GitCreatePr(NodeType):
    type = "git.create_pr"
    family = "git"
    side_effect = "external"
    editable_fields = ("title", "body")
    default_timeout_s = 120
    description = (
        "Crea un Pull Request con gh (o reutiliza el que ya exista para la rama). EFECTO EXTERNO: "
        "requiere un manual_approval que lo cubra."
    )
    events = (
        event("ok", "PR creado o ya existente"),
        event("failed", "No se pudo crear el PR", failure=True),
    )
    artifacts = (artifact("pr", "Pull request abierto (o el que ya existía para esa rama)"),)
    example = {
        "workspace": "${nodes.ws.outputs.path}",
        "base_branch": "${inputs.base}",
        "head_branch": "${nodes.ws.outputs.remote_branch}",
        "title": "${nodes.dev.outputs.suggested_pr_title}",
        "body": "${nodes.dev.outputs.suggested_pr_body}",
    }

    class ConfigModel(BaseModel):
        model_config = ConfigDict(extra="forbid")

        workspace: str
        base_branch: str
        head_branch: str
        title: str
        body: str
        draft: TBool = False
        reviewers: TStrList = Field(default_factory=list)
        labels: TStrList = Field(default_factory=list)

    class OutputsModel(BaseModel):
        pr_number: int
        pr_url: str
