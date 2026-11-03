"""Nodos de control de flujo: route, aprobación manual, scripts y subplanes."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from gestplan.nodes.base import EventSpec, NodeType, SideEffect, event
from gestplan.nodes.common import EVENT_NAME_PATTERN, TInt
from gestplan.nodes.registry import register
from gestplan.plans.conditions import Condition, iter_predicates
from gestplan.plans.issues import Issue, error
from gestplan.plans.refs import check_path


class RouteCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: str = Field(pattern=EVENT_NAME_PATTERN, description="Evento que se emite si se cumple")
    when: Condition


@register
class Route(NodeType):
    type = "route"
    family = "flow"
    side_effect = "none"
    description = (
        "Ramifica según condiciones sobre el contexto (inputs, salidas de nodos). Los casos se evalúan "
        "en orden; gana el primero que se cumple; si ninguno, se emite 'default'. Cada evento emitido "
        "debe tener arista. Los grupos con nombre de las regex quedan en outputs.captures."
    )
    example = {
        "cases": [
            {
                "event": "bugfix",
                "when": {"path": "inputs.summary", "op": "matches", "value": "^\\[BUG\\]"},
            }
        ],
        "default": "other",
    }

    class ConfigModel(BaseModel):
        model_config = ConfigDict(extra="forbid")

        cases: list[RouteCase] = Field(min_length=1)
        default: str = Field(pattern=EVENT_NAME_PATTERN)

        @model_validator(mode="after")
        def _unique_events(self) -> Route.ConfigModel:
            names = [case.event for case in self.cases] + [self.default]
            duplicated = sorted({name for name in names if names.count(name) > 1})
            if duplicated:
                raise ValueError(f"eventos repetidos: {', '.join(duplicated)}")
            return self

    class OutputsModel(BaseModel):
        matched_event: str
        captures: dict[str, str]

    @classmethod
    def event_specs(cls, config: dict[str, Any], ctx: Any = None) -> list[EventSpec]:
        parsed = cls.parse_config(config)
        if not parsed:
            return []
        specs = [event(case.event, "Se cumple su condición") for case in parsed.cases]
        specs.append(event(parsed.default, "Ninguna condición se cumple"))
        return specs

    @classmethod
    def check(cls, node_id: str, spec: Any, ctx: Any) -> list[Issue]:
        parsed = cls.parse_config(spec.config)
        if not parsed:
            return []
        issues: list[Issue] = []
        for index, case in enumerate(parsed.cases):
            where = f"config.cases[{index}].when"
            for predicate in iter_predicates(case.when):
                issues.extend(check_path(predicate.path, node_id, ctx, where))
                if predicate.op == "matches":
                    try:
                        re.compile(predicate.value)
                    except re.error as exc:
                        issues.append(
                            error(
                                "invalid_regex",
                                f"expresión regular no válida '{predicate.value}': {exc}",
                                node=node_id,
                                path=where,
                            )
                        )
        connected = {evt for src, evt, _ in ctx.graph.edges if src == node_id}
        for spec_event in cls.event_specs(spec.config):
            if spec_event.name not in connected:
                issues.append(
                    error(
                        "route_event_unconnected",
                        f"el evento '{spec_event.name}' del route no tiene arista de salida",
                        node=node_id,
                        hint=f"añade una arista desde '{node_id}.{spec_event.name}'",
                    )
                )
        return issues


@register
class ManualApproval(NodeType):
    type = "manual_approval"
    family = "flow"
    side_effect = "none"
    description = (
        "Pausa el run hasta que una persona apruebe o rechace. Muestra la vista previa de los nodos "
        "que cubre. Todo nodo con efecto externo debe tener un manual_approval que lo cubra en todos "
        "los caminos que llegan a él."
    )
    events = (
        event("approved", "Aprobado"),
        event("rejected", "Rechazado", failure=True),
        event("expired", "Caducó sin decisión", failure=True),
    )
    example = {"approves": ["push", "pr"], "message": "Revisa el diff antes de publicar"}

    class ConfigModel(BaseModel):
        model_config = ConfigDict(extra="forbid")

        approves: list[str] = Field(min_length=1, description="Ids de los nodos que esta aprobación cubre")
        message: str = ""
        expire_after_s: int | None = Field(default=None, ge=1)

    class OutputsModel(BaseModel):
        decided_by: str
        comment: str

    @classmethod
    def check(cls, node_id: str, spec: Any, ctx: Any) -> list[Issue]:
        parsed = cls.parse_config(spec.config)
        if not parsed:
            return []
        issues: list[Issue] = []
        for target in parsed.approves:
            if target not in ctx.plan.nodes:
                issues.append(
                    error(
                        "approval_target_unknown",
                        f"'approves' menciona el nodo '{target}', que no existe",
                        node=node_id,
                        path="config.approves",
                    )
                )
            elif target == node_id:
                issues.append(
                    error(
                        "approval_target_unknown",
                        "una aprobación no puede cubrirse a sí misma",
                        node=node_id,
                        path="config.approves",
                    )
                )
        return issues


class ScriptMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = ""
    side_effect: SideEffect = "external"
    events: list[str] = Field(default_factory=lambda: ["ok", "failed"], min_length=1)
    failure_events: list[str] = Field(default_factory=lambda: ["failed"])


def load_script_meta(path: Path) -> ScriptMeta:
    """Lee la constante META de un script sin ejecutarlo (análisis estático)."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8-sig"))
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "META" for target in statement.targets
        ):
            return ScriptMeta.model_validate(ast.literal_eval(statement.value))
    raise ValueError("el script no define META")


def _script_path(ctx: Any, script: str) -> Path:
    return ctx.root / ctx.settings.scripts_dir / f"{script}.py"


@register
class PythonScript(NodeType):
    type = "python_script"
    family = "flow"
    side_effect = "external"  # valor conservador; el real sale del META del script
    dynamic_outputs = True
    default_timeout_s = 600
    description = (
        "Ejecuta un script Python registrado en scripts/ (subproceso, entrada y salida JSON). El script "
        "declara en META su efecto ('none', 'local' o 'external'), sus eventos y su descripción. Sin "
        "'side_effect' se asume 'external'."
    )
    example = {"script": "resumen_run", "inputs": {"run": "${run.id}"}}

    class ConfigModel(BaseModel):
        model_config = ConfigDict(extra="forbid")

        script: str = Field(pattern=r"^[a-z][a-z0-9_]*$", description="Nombre del script (sin .py)")
        inputs: dict[str, Any] = Field(default_factory=dict)

    class OutputsModel(BaseModel):
        outputs: dict[str, Any]

    @classmethod
    def _meta(cls, config: dict[str, Any], ctx: Any) -> ScriptMeta | None:
        parsed = cls.parse_config(config)
        if ctx is None or not parsed:
            return None
        path = _script_path(ctx, parsed.script)
        if not path.is_file():
            return None
        try:
            return load_script_meta(path)
        except (ValueError, SyntaxError, ValidationError):
            return None

    @classmethod
    def event_specs(cls, config: dict[str, Any], ctx: Any = None) -> list[EventSpec]:
        meta = cls._meta(config, ctx)
        if meta is None:
            return [event("ok", "Terminó bien"), event("failed", "Falló", failure=True)]
        return [
            event(name, "Evento del script", failure=name in meta.failure_events)
            for name in meta.events
        ]

    @classmethod
    def effective_side_effect(cls, config: dict[str, Any], ctx: Any = None) -> SideEffect:
        meta = cls._meta(config, ctx)
        return meta.side_effect if meta else "external"

    @classmethod
    def check(cls, node_id: str, spec: Any, ctx: Any) -> list[Issue]:
        parsed = cls.parse_config(spec.config)
        if not parsed:
            return []
        path = _script_path(ctx, parsed.script)
        if not path.is_file():
            return [
                error(
                    "missing_script",
                    f"no existe el script '{parsed.script}' ({path})",
                    node=node_id,
                    path="config.script",
                    hint="crea el script en scripts/ con una constante META",
                )
            ]
        try:
            load_script_meta(path)
        except (ValueError, SyntaxError, ValidationError) as exc:
            return [
                error(
                    "invalid_script_meta",
                    f"META del script '{parsed.script}' no válido: {exc}",
                    node=node_id,
                    path="config.script",
                )
            ]
        return []


def _plan_file(ctx: Any, name: str) -> Path | None:
    for folder in (ctx.root / "plans", ctx.root / "plans" / "examples"):
        candidate = folder / f"{name}.json"
        if candidate.is_file():
            return candidate
    return None


@register
class LaunchPlans(NodeType):
    type = "launch_plans"
    family = "flow"
    side_effect = "local"
    default_timeout_s = 300
    description = (
        "Lanza uno o varios runs de otro plan (uno por cada elemento de 'items', con los inputs de ese "
        "elemento) y devuelve sus ids. Cada run hijo tiene sus propios aprobadores. Cancelar el padre "
        "cancela los hijos."
    )
    events = (
        event("ok", "Runs lanzados"),
        event("failed", "No se pudieron lanzar", failure=True),
    )
    example = {"plan": "implement-task", "items": [{"task_id": "TASK-001"}], "max_parallel": 2}

    class ConfigModel(BaseModel):
        model_config = ConfigDict(extra="forbid")

        plan: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$", description="Nombre del plan hijo")
        items: list[dict[str, Any]] | str = Field(
            description="Lista de inputs (uno por run hijo) o una referencia ${...} a una lista"
        )
        max_parallel: TInt = Field(default=2, description="Hijos simultáneos como máximo")

    class OutputsModel(BaseModel):
        run_ids: list[str]
        count: int

    @classmethod
    def check(cls, node_id: str, spec: Any, ctx: Any) -> list[Issue]:
        parsed = cls.parse_config(spec.config)
        if not parsed:
            return []
        if _plan_file(ctx, parsed.plan) is None:
            return [
                error(
                    "missing_plan",
                    f"no existe el plan hijo '{parsed.plan}'",
                    node=node_id,
                    path="config.plan",
                    hint="debe existir plans/<nombre>.json",
                )
            ]
        return []


@register
class WaitPlan(NodeType):
    type = "wait_plan"
    family = "flow"
    side_effect = "none"
    default_timeout_s = 4 * 3600
    description = "Espera a que terminen los runs lanzados por un nodo launch_plans anterior."
    events = (
        event("all_ok", "Todos los runs hijos terminaron bien"),
        event("some_failed", "Alguno falló", failure=True),
        event("cancelled", "Se cancelaron", failure=True),
        event("timeout", "Se superó el tiempo máximo", failure=True),
    )
    example = {"launched_by": "launch"}

    class ConfigModel(BaseModel):
        model_config = ConfigDict(extra="forbid")

        launched_by: str = Field(description="Id del nodo launch_plans cuyos runs se esperan")

    class OutputsModel(BaseModel):
        results: list[dict[str, Any]]
        failed_count: int

    @classmethod
    def check(cls, node_id: str, spec: Any, ctx: Any) -> list[Issue]:
        parsed = cls.parse_config(spec.config)
        if not parsed:
            return []
        target = ctx.plan.nodes.get(parsed.launched_by)
        if target is None or target.type != LaunchPlans.type:
            return [
                error(
                    "wait_target_invalid",
                    f"'{parsed.launched_by}' no es un nodo launch_plans del plan",
                    node=node_id,
                    path="config.launched_by",
                )
            ]
        if parsed.launched_by not in ctx.graph.ancestors(node_id):
            return [
                error(
                    "wait_target_invalid",
                    f"'{parsed.launched_by}' no se ejecuta antes que '{node_id}'",
                    node=node_id,
                    path="config.launched_by",
                )
            ]
        return []
