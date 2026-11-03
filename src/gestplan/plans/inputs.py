"""Resolución y validación de los inputs con los que se lanza un plan."""

from __future__ import annotations

import json
import re
from typing import Any

from gestplan.plans.issues import Issue, error
from gestplan.plans.models import InputSpec, Plan


def resolve_inputs(plan: Plan, provided: dict[str, Any]) -> tuple[dict[str, Any], list[Issue]]:
    """Aplica defaults, convierte tipos (los valores de CLI llegan como texto) y valida."""
    issues: list[Issue] = []
    values: dict[str, Any] = {}

    for name in provided:
        if name not in plan.inputs:
            issues.append(
                error(
                    "unknown_input",
                    f"el plan no declara el input '{name}'",
                    path=f"inputs.{name}",
                    hint=f"inputs declarados: {', '.join(plan.inputs) or '(ninguno)'}",
                )
            )

    for name, spec in plan.inputs.items():
        if name in provided:
            try:
                value = _coerce(provided[name], spec)
            except ValueError as exc:
                issues.append(error("invalid_input", f"input '{name}': {exc}", path=f"inputs.{name}"))
                continue
        elif spec.default is not None:
            value = spec.default
        elif spec.required:
            issues.append(
                error(
                    "missing_input",
                    f"falta el input obligatorio '{name}'",
                    path=f"inputs.{name}",
                    hint=f"añade --input {name}=<valor>",
                )
            )
            continue
        else:
            continue
        if spec.pattern and isinstance(value, str) and not re.search(spec.pattern, value):
            issues.append(
                error(
                    "input_pattern",
                    f"input '{name}': '{value}' no cumple el patrón {spec.pattern}",
                    path=f"inputs.{name}",
                )
            )
            continue
        values[name] = value
    return values, issues


def _coerce(raw: Any, spec: InputSpec) -> Any:
    if not isinstance(raw, str):
        return raw
    kind = spec.type
    if kind == "string":
        return raw
    if kind == "integer":
        return int(raw)
    if kind == "number":
        return float(raw)
    if kind == "boolean":
        lowered = raw.strip().lower()
        if lowered in ("true", "1", "yes", "si", "sí"):
            return True
        if lowered in ("false", "0", "no"):
            return False
        raise ValueError(f"'{raw}' no es un booleano")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"se esperaba JSON válido ({exc.msg})") from exc
    expected = list if kind == "array" else dict
    if not isinstance(parsed, expected):
        raise ValueError(f"se esperaba {kind}")
    return parsed
