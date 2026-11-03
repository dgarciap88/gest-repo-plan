"""Nodos de workspace: preparación y limpieza de la copia de trabajo."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from gestplan.nodes.base import NodeType, event
from gestplan.nodes.registry import register


@register
class PrepareWorkspace(NodeType):
    type = "prepare_workspace"
    family = "workspace"
    side_effect = "local"
    default_timeout_s = 900
    idle_timeout_s = 300
    description = (
        "Crea un workspace temporal en disco, clona el repositorio y deja lista una rama de trabajo. "
        "Si la rama pedida ya existe en el remoto, crea una rama local con un sufijo derivado del run "
        "que parte de la remota; el push posterior actualiza la rama remota original."
    )
    events = (
        event("ok", "Workspace listo"),
        event("failed", "No se pudo clonar o preparar la rama", failure=True),
        event("timeout", "Se superó el tiempo máximo", failure=True),
    )
    example = {
        "repo_url": "${inputs.repo_url}",
        "base_branch": "${inputs.base}",
        "new_branch": "feature/${inputs.task_id}",
    }

    class ConfigModel(BaseModel):
        model_config = ConfigDict(extra="forbid")

        repo_url: str = Field(description="URL del repositorio a clonar")
        base_branch: str = Field(default="main", description="Rama de la que partir")
        new_branch: str = Field(description="Nombre deseado de la rama de trabajo")
        on_branch_exists: Literal["track_remote", "fail", "new_branch_from_base"] = Field(
            default="track_remote",
            description="Qué hacer si new_branch ya existe en el remoto",
        )

    class OutputsModel(BaseModel):
        path: str = Field(description="Ruta del workspace")
        local_branch: str
        remote_branch: str = Field(description="Rama remota que actualizará git.push")
        branch_existed: bool
        base_commit: str
        push_ref: str


@register
class CleanupWorkspace(NodeType):
    type = "cleanup_workspace"
    family = "workspace"
    side_effect = "local"
    default_timeout_s = 300
    description = "Borra el workspace temporal (solo si está bajo la carpeta de workspaces del motor)."
    events = (
        event("ok", "Workspace eliminado"),
        event("failed", "No se pudo eliminar", failure=True),
    )
    example = {"workspace": "${nodes.ws.outputs.path}"}

    class ConfigModel(BaseModel):
        model_config = ConfigDict(extra="forbid")

        workspace: str = Field(description="Ruta del workspace a borrar")
