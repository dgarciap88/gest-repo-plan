"""Referencias ${...} dentro de los valores de un plan."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from gestplan.plans.paths import get_path

REF_RE = re.compile(r"\$\{([^}]*)\}")
WHOLE_RE = re.compile(r"^\$\{([^}]*)\}$")

NAMESPACES = ("inputs", "nodes", "run", "env")
RUN_KEYS = ("id", "plan", "created_at")


class UnresolvedReference(KeyError):
    """Una referencia ${...} no existe en el contexto."""


@dataclass(frozen=True)
class Ref:
    raw: str
    parts: tuple[str, ...]

    @property
    def namespace(self) -> str:
        return self.parts[0] if self.parts else ""


def parse_ref(raw: str) -> Ref:
    cleaned = raw.strip()
    return Ref(raw=cleaned, parts=tuple(part.strip() for part in cleaned.split(".")))


def find_refs(value: Any, path: str = "") -> list[tuple[str, Ref]]:
    """Devuelve (ubicación, referencia) para cada ${...} de un valor anidado."""
    found: list[tuple[str, Ref]] = []
    if isinstance(value, str):
        found.extend((path, parse_ref(m.group(1))) for m in REF_RE.finditer(value))
    elif isinstance(value, dict):
        for key, item in value.items():
            found.extend(find_refs(item, f"{path}.{key}" if path else str(key)))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found.extend(find_refs(item, f"{path}[{index}]"))
    return found


def walk_strings(value: Any, path: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from walk_strings(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from walk_strings(item, f"{path}[{index}]")


def resolve_template(value: Any, context: dict[str, Any], *, empty_missing_nodes: bool = False) -> Any:
    """Resuelve las referencias de un valor. Si el valor es solo una referencia,
    conserva el tipo del dato; si es texto mezclado, interpola con str().

    Con `empty_missing_nodes`, una referencia `nodes.*` que aún no existe (p. ej. el feedback del
    validador en la primera ronda de un bucle) se resuelve como cadena vacía.
    """
    if isinstance(value, str):
        whole = WHOLE_RE.match(value)
        if whole:
            return _lookup(whole.group(1), context, empty_missing_nodes)
        return REF_RE.sub(lambda m: str(_lookup(m.group(1), context, empty_missing_nodes)), value)
    if isinstance(value, dict):
        return {k: resolve_template(v, context, empty_missing_nodes=empty_missing_nodes)
                for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_template(v, context, empty_missing_nodes=empty_missing_nodes) for v in value]
    return value


def _lookup(raw: str, context: dict[str, Any], empty_missing_nodes: bool = False) -> Any:
    path = raw.strip()
    found, result = get_path(context, path)
    if not found:
        parts = path.split(".")
        # solo es "vacío" si el nodo aún no ha producido salidas (p. ej. primera ronda de un bucle);
        # un campo que falta en las salidas de un nodo que sí se ejecutó es un error
        if empty_missing_nodes and len(parts) >= 2 and parts[0] == "nodes" \
                and parts[1] not in context.get("nodes", {}):
            return ""
        raise UnresolvedReference(path)
    return result
