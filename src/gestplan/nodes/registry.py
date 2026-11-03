"""Registro de tipos de nodo."""

from __future__ import annotations

import importlib

from gestplan.nodes.base import NodeType

_REGISTRY: dict[str, type[NodeType]] = {}
_BUILTIN_MODULES = (
    "gestplan.nodes.workspace",
    "gestplan.nodes.agents",
    "gestplan.nodes.git",
    "gestplan.nodes.local",
    "gestplan.nodes.flow",
)


def register(cls: type[NodeType]) -> type[NodeType]:
    if cls.type in _REGISTRY and _REGISTRY[cls.type] is not cls:
        raise ValueError(f"tipo de nodo duplicado: {cls.type}")
    _REGISTRY[cls.type] = cls
    return cls


def _ensure_loaded() -> None:
    for module in _BUILTIN_MODULES:
        importlib.import_module(module)


def get_node_type(name: str) -> type[NodeType] | None:
    _ensure_loaded()
    return _REGISTRY.get(name)


def all_node_types() -> list[type[NodeType]]:
    _ensure_loaded()
    return sorted(_REGISTRY.values(), key=lambda cls: (cls.family, cls.type))
