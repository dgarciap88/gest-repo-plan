"""Validador de planes.

Los errores están pensados para que la IA que diseña planes los lea y corrija: cada uno lleva un
código estable, el nodo y la ruta afectados, y una sugerencia cuando se puede dar.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import ValidationError

from gestplan.nodes.base import NodeType
from gestplan.nodes.flow import ManualApproval
from gestplan.nodes.registry import get_node_type
from gestplan.plans.context import ValidationContext
from gestplan.plans.issues import Issue, ValidationResult, error, warning
from gestplan.plans.loader import issues_from_validation_error
from gestplan.plans.models import Plan
from gestplan.plans.refs import check_value_refs
from gestplan.settings import Settings


def validate_plan(
    plan: Plan, root: Path | str = ".", settings: Settings | None = None
) -> ValidationResult:
    ctx = ValidationContext(plan, Path(root), settings or Settings.from_env())
    issues: list[Issue] = []
    types: dict[str, type[NodeType]] = {}

    cap = ctx.settings.run_budget_usd
    if cap and plan.settings.max_cost_usd and plan.settings.max_cost_usd > cap:
        issues.append(error("budget_above_cap",
                            f"settings.max_cost_usd={plan.settings.max_cost_usd} supera el tope global por run ({cap})",
                            path="settings.max_cost_usd"))
    _check_inputs(ctx, issues)
    _check_nodes(ctx, issues, types)
    _check_edges(ctx, issues, types)
    _check_references(ctx, issues)
    _check_node_rules(ctx, issues, types)
    _check_graph(ctx, issues, types)
    _check_approvals(ctx, issues, types)
    _check_unused_inputs(ctx, issues)
    return ValidationResult(issues)


def _check_inputs(ctx: ValidationContext, issues: list[Issue]) -> None:
    for name, spec in ctx.plan.inputs.items():
        if spec.pattern:
            try:
                re.compile(spec.pattern)
            except re.error as exc:
                issues.append(
                    error(
                        "invalid_regex",
                        f"patrón del input '{name}' no válido: {exc}",
                        path=f"inputs.{name}.pattern",
                    )
                )
        if spec.required and spec.default is not None:
            issues.append(
                warning(
                    "input_default_ignored",
                    f"el input '{name}' es obligatorio y tiene valor por defecto: el defecto no se usa",
                    path=f"inputs.{name}",
                )
            )


def _check_nodes(
    ctx: ValidationContext, issues: list[Issue], types: dict[str, type[NodeType]]
) -> None:
    for node_id, spec in ctx.plan.nodes.items():
        node_type = get_node_type(spec.type)
        if node_type is None:
            issues.append(
                error(
                    "unknown_node_type",
                    f"tipo de nodo desconocido '{spec.type}'",
                    node=node_id,
                    path="type",
                    hint="consulta los tipos disponibles con 'gestplan catalog'",
                )
            )
            continue
        types[node_id] = node_type
        if spec.version != node_type.version:
            issues.append(
                error(
                    "unsupported_version",
                    f"'{spec.type}' versión {spec.version} no soportada (actual: {node_type.version})",
                    node=node_id,
                    path="version",
                )
            )
        try:
            node_type.ConfigModel.model_validate(spec.config)
        except ValidationError as exc:
            issues.extend(
                issues_from_validation_error(exc, prefix="config", node=node_id, code="invalid_config")
            )
        if spec.timeout_s and spec.timeout_s > ctx.settings.timeout_cap_s:
            issues.append(
                error(
                    "timeout_above_cap",
                    f"timeout_s={spec.timeout_s} supera el tope global ({ctx.settings.timeout_cap_s}s)",
                    node=node_id,
                    path="timeout_s",
                )
            )
        if spec.max_attempts and spec.max_attempts > ctx.settings.max_attempts_cap:
            issues.append(
                error(
                    "attempts_above_cap",
                    f"max_attempts={spec.max_attempts} supera el tope global "
                    f"({ctx.settings.max_attempts_cap})",
                    node=node_id,
                    path="max_attempts",
                )
            )


def _events_of(ctx: ValidationContext, node_id: str, types: dict[str, type[NodeType]]) -> set[str]:
    node_type = types[node_id]
    return {e.name for e in node_type.event_specs(ctx.plan.nodes[node_id].config, ctx)}


def _check_edges(
    ctx: ValidationContext, issues: list[Issue], types: dict[str, type[NodeType]]
) -> None:
    seen: set[tuple[str, str]] = set()
    for index, edge in enumerate(ctx.plan.edges):
        where = f"edges[{index}]"
        source, event_name = edge.source_node, edge.source_event
        if source not in ctx.plan.nodes:
            issues.append(error("edge_unknown_source", f"el nodo origen '{source}' no existe", path=where))
        elif source in types and event_name not in _events_of(ctx, source, types):
            available = ", ".join(sorted(_events_of(ctx, source, types))) or "(ninguno)"
            issues.append(
                error(
                    "edge_unknown_event",
                    f"el nodo '{source}' ({ctx.plan.nodes[source].type}) no emite el evento '{event_name}'",
                    path=where,
                    hint=f"eventos disponibles: {available}",
                )
            )
        if edge.to not in ctx.plan.nodes:
            issues.append(error("edge_unknown_target", f"el nodo destino '{edge.to}' no existe", path=where))
        if source == edge.to:
            issues.append(error("edge_self_loop", f"'{source}' no puede conectarse consigo mismo", path=where))
        key = (edge.from_, edge.to)
        if key in seen:
            issues.append(warning("duplicate_edge", f"arista repetida {edge.from_} -> {edge.to}", path=where))
        seen.add(key)

    connected = {(src, evt) for src, evt, _ in ctx.graph.edges}
    has_outgoing = {src for src, _evt, _dst in ctx.graph.edges}
    for node_id, node_type in types.items():
        unhandled_failures: list[str] = []
        for spec_event in node_type.event_specs(ctx.plan.nodes[node_id].config, ctx):
            if (node_id, spec_event.name) in connected:
                continue
            if spec_event.failure:
                unhandled_failures.append(spec_event.name)
            elif node_id in has_outgoing:
                issues.append(
                    warning(
                        "unconnected_event",
                        f"el evento '{spec_event.name}' no tiene arista: el camino termina ahí",
                        node=node_id,
                    )
                )
        if unhandled_failures:
            issues.append(
                warning(
                    "unconnected_failure_event",
                    f"eventos de fallo sin arista ({', '.join(unhandled_failures)}): aplicará "
                    f"'{ctx.plan.settings.on_unhandled_failure}'",
                    node=node_id,
                )
            )


def _check_references(ctx: ValidationContext, issues: list[Issue]) -> None:
    for node_id, spec in ctx.plan.nodes.items():
        issues.extend(check_value_refs(spec.config, node_id, ctx, "config"))


def _check_node_rules(
    ctx: ValidationContext, issues: list[Issue], types: dict[str, type[NodeType]]
) -> None:
    for node_id, node_type in types.items():
        issues.extend(node_type.check(node_id, ctx.plan.nodes[node_id], ctx))


def _check_alternative_joins(ctx: ValidationContext, issues: list[Issue]) -> None:
    """Aviso típico en planes escritos a mano o por IA: un nodo con join 'all' que recibe de A y de B, donde B se
    alcanza desde A, casi siempre une dos caminos ALTERNATIVOS; con 'all' esperaría a ambos y nunca arrancaría."""
    graph = ctx.graph
    back = graph.back_edges()
    for node_id, spec in ctx.plan.nodes.items():
        if spec.join != "all":
            continue
        sources = sorted(p for p in graph.pred[node_id] if (p, node_id) not in back)
        pairs = [(a, b) for a in sources for b in sources if a != b and a in graph.ancestors(b)]
        if pairs:  # un aviso por nodo
            first, second = pairs[0]
            issues.append(warning(
                "join_all_alternative_paths",
                f"'{node_id}' recibe de '{first}' y de '{second}', que se alcanza desde '{first}': si son "
                "caminos alternativos, con join 'all' no arrancará nunca",
                node=node_id, path="join",
                hint=f"pon \"join\": \"any\" en '{node_id}' si basta con que llegue uno de los caminos"))


def _check_graph(
    ctx: ValidationContext, issues: list[Issue], types: dict[str, type[NodeType]]
) -> None:
    graph = ctx.graph
    if not graph.roots:
        issues.append(
            error(
                "no_start_node",
                "ningún nodo puede arrancar: todos tienen aristas de entrada",
                hint="al menos un nodo no debe recibir aristas (el nodo inicial)",
            )
        )
    for node_id in sorted(set(graph.nodes) - graph.reachable()):
        issues.append(
            error(
                "unreachable_node",
                f"el nodo '{node_id}' no es alcanzable desde ningún nodo inicial",
                node=node_id,
            )
        )

    _check_alternative_joins(ctx, issues)

    bounded = {node_id for node_id, spec in ctx.plan.nodes.items() if spec.max_attempts}
    for source, event_name, target in graph.edges:
        node_type = types.get(source)
        if node_type and node_type.loop_event == event_name:
            if node_type.loop_bound(ctx.plan.nodes[source].config) is not None:
                bounded.add(target)

    for component in graph.cyclic_components():
        unbounded = set(component) - bounded
        if graph.has_cycle(unbounded):
            names = ", ".join(sorted(unbounded))
            issues.append(
                error(
                    "unbounded_cycle",
                    f"hay un ciclo sin límite entre: {', '.join(component)}",
                    node=sorted(unbounded)[0],
                    hint=(
                        f"añade 'max_attempts' a uno de ({names}) o usa un ia_agent_validator con "
                        "'max_retries' cuyo evento 'changes_requested' vuelva al nodo del bucle"
                    ),
                )
            )


def _check_approvals(
    ctx: ValidationContext, issues: list[Issue], types: dict[str, type[NodeType]]
) -> None:
    """Regla de dominancia: todo efecto externo está dominado por un manual_approval que lo cubre."""
    graph = ctx.graph
    dominators = graph.dominators()
    approvals = {
        node_id: set(ctx.plan.nodes[node_id].config.get("approves", []))
        for node_id, node_type in types.items()
        if node_type is ManualApproval
    }
    for node_id, node_type in types.items():
        spec = ctx.plan.nodes[node_id]
        if node_type.effective_side_effect(spec.config, ctx) != "external":
            continue
        if node_id not in dominators:
            continue  # inalcanzable: ya se ha avisado
        covering = [
            approval
            for approval in dominators[node_id] - {node_id}
            if node_id in approvals.get(approval, set())
        ]
        if not covering:
            issues.append(
                error(
                    "missing_approval",
                    f"'{node_id}' ({spec.type}) tiene efecto externo y no está cubierto por ninguna "
                    "aprobación manual en todos los caminos que llegan a él",
                    node=node_id,
                    hint=(
                        f"añade un nodo manual_approval antes de '{node_id}' con "
                        f"\"approves\": [\"{node_id}\"] y conéctalo en todos los caminos hasta él"
                    ),
                )
            )


def _check_unused_inputs(ctx: ValidationContext, issues: list[Issue]) -> None:
    for name in ctx.plan.inputs:
        if name not in ctx.used_inputs and name not in ctx.plan.settings.display_inputs:
            issues.append(
                warning("unused_input", f"el input '{name}' no se usa en ningún nodo", path=f"inputs.{name}")
            )
