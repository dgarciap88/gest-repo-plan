"""Acceso a valores del contexto por ruta con puntos (a.b.0.c)."""

from __future__ import annotations

from typing import Any


def get_path(context: Any, path: str) -> tuple[bool, Any]:
    current = context
    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                return False, None
            current = current[part]
        elif isinstance(current, (list, tuple)):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return False, None
        else:
            return False, None
    return True, current
