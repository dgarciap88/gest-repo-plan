"""Nodos de Confluence Cloud: páginas a partir de una plantilla de bloques JSON."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from gestplan.nodes.base import NodeType, event
from gestplan.nodes.common import TemplateStr
from gestplan.nodes.registry import register


class _Block(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Heading(_Block):
    type: Literal["heading"]
    level: int = Field(default=2, ge=1, le=6)
    text: str


class Paragraph(_Block):
    type: Literal["paragraph"]
    text: str


class ListBlock(_Block):
    type: Literal["list"]
    items: Union[list[str], TemplateStr]  # noqa: UP007
    ordered: bool = False


class TableBlock(_Block):
    type: Literal["table"]
    columns: list[str] = Field(min_length=1)
    rows: Union[list[list[Any]], TemplateStr]  # noqa: UP007


class CodeBlock(_Block):
    type: Literal["code"]
    text: str
    language: str = ""


Block = Annotated[Union[Heading, Paragraph, ListBlock, TableBlock, CodeBlock], Field(discriminator="type")]  # noqa: UP007


@register
class ConfluenceCreatePage(NodeType):
    type = "confluence.create_page"
    family = "confluence"
    side_effect = "external"
    default_timeout_s = 180
    description = (
        "Crea una página de Confluence desde bloques (heading, paragraph, list, table, code) renderizados a "
        "storage format. Idempotente: si ya existe una página con ese título creada por este run, no repite. "
        "EFECTO EXTERNO: requiere un manual_approval que lo cubra."
    )
    events = (
        event("ok", "Página creada"),
        event("already_done", "Este run ya había creado la página"),
        event("title_exists", "Existe otra página con ese título en el espacio", failure=True),
        event("failed", "La llamada a Confluence falló", failure=True),
    )
    example = {
        "space": "DOC",
        "title": "Informe ${inputs.jira_id}",
        "blocks": [{"type": "heading", "level": 1, "text": "Resumen"},
                   {"type": "paragraph", "text": "${nodes.dev.outputs.summary}"}],
    }

    class ConfigModel(BaseModel):
        model_config = ConfigDict(extra="forbid")

        space: str = Field(description="Clave del espacio")
        title: str
        blocks: Union[list[Block], TemplateStr] = Field(description="Contenido de la página")  # noqa: UP007
        parent_id: str = Field(default="", description="Id de la página padre (opcional)")

    class OutputsModel(BaseModel):
        page_id: str
        url: str
        title: str


@register
class ConfluenceUpdatePage(NodeType):
    type = "confluence.update_page"
    family = "confluence"
    side_effect = "external"
    default_timeout_s = 180
    description = (
        "Reemplaza el contenido de una página de Confluence existente con los bloques indicados (nueva versión). "
        "Idempotente: si la página ya lleva el marcador de este run y nodo, no vuelve a actualizarla. EFECTO "
        "EXTERNO: requiere un manual_approval que lo cubra."
    )
    events = (
        event("ok", "Página actualizada"),
        event("already_done", "Este run ya había actualizado la página"),
        event("not_found", "La página no existe o no hay permiso", failure=True),
        event("failed", "La llamada a Confluence falló", failure=True),
    )
    example = {
        "page_id": "458753",
        "blocks": [{"type": "heading", "level": 1, "text": "Informe actualizado"},
                   {"type": "paragraph", "text": "${nodes.dev.outputs.summary}"}],
    }

    class ConfigModel(BaseModel):
        model_config = ConfigDict(extra="forbid")

        page_id: str = Field(description="Id de la página a actualizar")
        title: str = Field(default="", description="Título nuevo; vacío conserva el actual")
        blocks: Union[list[Block], TemplateStr] = Field(description="Contenido nuevo de la página")  # noqa: UP007

    class OutputsModel(BaseModel):
        page_id: str = Field(description="Id de la página")
        url: str = Field(description="Enlace a la página")
        title: str = Field(description="Título tras la actualización")
        version: int = Field(description="Número de versión resultante")
