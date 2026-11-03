"""Carga de planes desde JSON, convirtiendo los fallos en `Issue` legibles."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from gestplan.plans.issues import Issue, error
from gestplan.plans.models import Plan


def load_plan(path: Path) -> tuple[Plan | None, list[Issue]]:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None, [error("file_not_found", f"no existe el fichero {path}")]
    except OSError as exc:
        return None, [error("file_unreadable", f"no se pudo leer {path}: {exc}")]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, [
            error("json_syntax", f"JSON no válido en línea {exc.lineno}, columna {exc.colno}: {exc.msg}")
        ]
    return parse_plan(data)


def find_plan_file(root: Path, name: str) -> Path | None:
    """Busca un plan por nombre en plans/ y plans/examples/."""
    for folder in (Path(root) / "plans", Path(root) / "plans" / "examples"):
        candidate = folder / f"{name}.json"
        if candidate.is_file():
            return candidate
    return None


def parse_plan(data: Any) -> tuple[Plan | None, list[Issue]]:
    if not isinstance(data, dict):
        return None, [error("schema", "la raíz del plan debe ser un objeto JSON")]
    try:
        return Plan.model_validate(data), []
    except ValidationError as exc:
        return None, issues_from_validation_error(exc)


def issues_from_validation_error(exc: ValidationError, prefix: str = "", node: str | None = None,
                                 code: str = "schema") -> list[Issue]:
    issues: list[Issue] = []
    for item in exc.errors():
        location = ".".join(str(part) for part in item["loc"])
        path = ".".join(part for part in (prefix, location) if part)
        issues.append(error(code, item["msg"], node=node, path=path or None))
    return issues
