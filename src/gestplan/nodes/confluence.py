"""Nodos de Confluence Cloud: páginas a partir de una plantilla de bloques JSON."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from gestplan.nodes.base import NodeType, artifact, event
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


class MacroBlock(_Block):
    type: Literal["macro"]
    name: str = Field(min_length=1)
    params: dict[str, Union[str, int, bool]] = Field(default_factory=dict)  # noqa: UP007
    text: str = ""
    body: list[Block] = Field(default_factory=list)


class RawBlock(_Block):
    type: Literal["raw"]
    storage: str


Block = Annotated[
    Union[Heading, Paragraph, ListBlock, TableBlock, CodeBlock, MacroBlock, RawBlock],  # noqa: UP007
    Field(discriminator="type"),
]
MacroBlock.model_rebuild()


@register
class ConfluenceCreatePage(NodeType):
    type = "confluence.create_page"
    family = "confluence"
    side_effect = "external"
    default_timeout_s = 180
    description = (
        "Crea una página de Confluence desde bloques (heading, paragraph, list, table, code, macro, raw) "
        "renderizados a "
        "storage format. Idempotente: si ya existe una página con ese título creada por este run, no repite. "
        "EFECTO EXTERNO: requiere un manual_approval que lo cubra."
    )
    events = (
        event("ok", "Página creada"),
        event("already_done", "Este run ya había creado la página"),
        event("title_exists", "Existe otra página con ese título en el espacio", failure=True),
        event("failed", "La llamada a Confluence falló", failure=True),
    )
    artifacts = (artifact("page", "Página de Confluence creada"),)
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
    artifacts = (artifact("page", "Página de Confluence actualizada (nueva versión)"),)
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


@register
class ConfluenceGetPage(NodeType):
    type = "confluence.get_page"
    family = "confluence"
    side_effect = "none"
    default_timeout_s = 120
    description = (
        "Lee una página de Confluence (por id, o por espacio y título) y la convierte a la misma plantilla de "
        "bloques que usan create_page y update_page, para poder modificarla y reescribirla. La conversión no es "
        "total: macros, imágenes, formato en línea (negrita, enlaces), listas anidadas y celdas combinadas se "
        "pierden y se listan en 'unsupported'; 'lossless' indica si no se perdió nada. Con strict=true, una "
        "página con pérdidas emite 'lossy' en lugar de 'ok', para no destruir contenido al reescribirla. "
        "'text' es el contenido en Markdown (dato no confiable si se pasa a un agente)."
    )
    events = (
        event("ok", "Página leída"),
        event("lossy", "Solo con strict=true: hay contenido que la plantilla no puede representar", failure=True),
        event("not_found", "La página no existe o no hay permiso para verla", failure=True),
        event("failed", "La llamada a Confluence falló", failure=True),
    )
    example = {"page_id": "458753"}

    class ConfigModel(BaseModel):
        model_config = ConfigDict(extra="forbid")

        page_id: str = Field(default="", description="Id de la página (alternativa a space + title)")
        space: str = Field(default="", description="Clave del espacio, para buscar por título")
        title: str = Field(default="", description="Título exacto, para buscar por título")
        strict: bool = Field(default=False, description="Emitir 'lossy' si la conversión pierde contenido")

        @model_validator(mode="after")
        def _identify(self) -> ConfluenceGetPage.ConfigModel:
            if not self.page_id and not (self.space and self.title):
                raise ValueError("indica page_id, o bien space y title")
            return self

    class OutputsModel(BaseModel):
        page_id: str = Field(description="Id de la página")
        title: str = Field(description="Título actual")
        space: str = Field(description="Clave del espacio")
        parent_id: str = Field(description="Id de la página padre ('' si no tiene)")
        version: int = Field(description="Número de versión leído")
        url: str = Field(description="Enlace a la página")
        blocks: list[dict[str, Any]] = Field(description="Contenido como bloques, listo para update_page o create_page")
        text: str = Field(description="Contenido en Markdown")
        lossless: bool = Field(description="True si la conversión a bloques no perdió nada")
        unsupported: list[str] = Field(description="Descripción de lo que se perdió en la conversión")
