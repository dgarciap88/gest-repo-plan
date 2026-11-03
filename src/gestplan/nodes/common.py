"""Tipos auxiliares para las configuraciones de nodo."""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import StringConstraints

# Un valor que en realidad es una referencia ${...} (se resuelve al ejecutar)
TemplateStr = Annotated[str, StringConstraints(pattern=r"^\$\{[^}]+\}$")]
TInt = Union[int, TemplateStr]  # noqa: UP007
TBool = Union[bool, TemplateStr]  # noqa: UP007
TStrList = Union[list[str], TemplateStr]  # noqa: UP007

ModelTier = Literal["cheap", "standard", "premium"]
EVENT_NAME_PATTERN = r"^[a-z][a-z0-9_]*$"
