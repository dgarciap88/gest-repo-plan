"""Nodos de seguridad: escaneo determinista de secretos en los cambios del workspace."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from gestplan.nodes.base import NodeType, event
from gestplan.nodes.common import TInt
from gestplan.nodes.registry import register


@register
class SecretScan(NodeType):
    type = "secret_scan"
    family = "security"
    side_effect = "none"
    default_timeout_s = 180
    description = (
        "Busca secretos (claves privadas, tokens, contraseñas asignadas, cadenas de alta entropía) en las "
        "líneas añadidas respecto a 'ref' y en los ficheros nuevos, y señala ficheros como .env o *.pem. Es una "
        "pasada determinista: los hallazgos salen enmascarados y la aprobación manual sigue siendo la barrera "
        "final. Colócalo antes del commit/push; una línea con 'gestplan:allow-secret' se ignora."
    )
    events = (
        event("clean", "No se detectó nada sospechoso"),
        event("secrets_detected", "Hay posibles secretos en los cambios", failure=True),
        event("failed", "No se pudo analizar el workspace", failure=True),
    )
    example = {"workspace": "${nodes.ws.outputs.path}"}

    class ConfigModel(BaseModel):
        model_config = ConfigDict(extra="forbid")

        workspace: str
        ref: str = Field(default="HEAD", description="Referencia con la que comparar (HEAD = cambios sin commitear)")
        max_file_bytes: TInt = Field(default=1_000_000, description="Los ficheros nuevos mayores se omiten")

    class OutputsModel(BaseModel):
        count: int
        findings: list[dict[str, Any]] = Field(description="file, line, kind y sample (enmascarado)")
        summary: str
        skipped: list[str]
