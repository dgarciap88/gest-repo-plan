"""Comprobación de referencias ${...} y rutas de contexto dentro de un plan."""

from __future__ import annotations

from typing import Any

from gestplan.plans.context import ValidationContext
from gestplan.plans.issues import Issue, error
from gestplan.plans.templates import NAMESPACES, RUN_KEYS, Ref, find_refs, parse_ref


def check_value_refs(
    value: Any, node_id: str | None, ctx: ValidationContext, base_path: str
) -> list[Issue]:
    issues: list[Issue] = []
    for location, ref in find_refs(value):
        full = ".".join(part for part in (base_path, location) if part)
        issues.extend(check_ref(ref, node_id, ctx, full))
    return issues


def check_path(path: str, node_id: str | None, ctx: ValidationContext, where: str) -> list[Issue]:
    """Comprueba una ruta de contexto sin `${}` (la usan las condiciones de `route`)."""
    return check_ref(parse_ref(path), node_id, ctx, where)


def check_ref(ref: Ref, node_id: str | None, ctx: ValidationContext, where: str) -> list[Issue]:
    from gestplan.nodes.registry import get_node_type

    def problem(code: str, message: str, hint: str | None = None) -> list[Issue]:
        return [error(code, message, node=node_id, path=where, hint=hint)]

    if ref.namespace not in NAMESPACES:
        return problem(
            "bad_reference",
            f"'${{{ref.raw}}}': espacio de nombres desconocido '{ref.namespace}'",
            f"usa uno de: {', '.join(NAMESPACES)}",
        )

    if ref.namespace == "inputs":
        if len(ref.parts) < 2 or ref.parts[1] not in ctx.plan.inputs:
            name = ref.parts[1] if len(ref.parts) > 1 else ""
            return problem(
                "undeclared_input",
                f"'${{{ref.raw}}}': el plan no declara el input '{name}'",
                "declara el input en el bloque 'inputs' del plan",
            )
        ctx.used_inputs.add(ref.parts[1])
        return []

    if ref.namespace == "run":
        if len(ref.parts) != 2 or ref.parts[1] not in RUN_KEYS:
            return problem(
                "bad_reference",
                f"'${{{ref.raw}}}': claves de run válidas: {', '.join(RUN_KEYS)}",
            )
        return []

    if ref.namespace == "env":
        if len(ref.parts) != 2:
            return problem("bad_reference", f"'${{{ref.raw}}}': usa env.<NOMBRE>")
        return []

    # nodes.<id>.outputs.<campo>[.más]
    if len(ref.parts) < 4 or ref.parts[2] != "outputs":
        return problem(
            "bad_reference",
            f"'${{{ref.raw}}}': usa el formato nodes.<nodo>.outputs.<campo>",
        )
    target = ref.parts[1]
    if target not in ctx.plan.nodes:
        return problem("unknown_node_ref", f"'${{{ref.raw}}}': no existe el nodo '{target}'")
    if node_id is not None:
        if target == node_id:
            return problem("self_reference", f"'${{{ref.raw}}}': un nodo no puede usar sus propias salidas")
        if target not in ctx.graph.ancestors(node_id):
            return problem(
                "reference_not_upstream",
                f"'${{{ref.raw}}}': '{target}' no se ejecuta antes que '{node_id}'",
                f"conecta '{target}' hacia '{node_id}' con aristas o usa otro nodo",
            )
    node_type = get_node_type(ctx.plan.nodes[target].type)
    if node_type is not None and not node_type.dynamic_outputs:
        fields = node_type.OutputsModel.model_fields
        if ref.parts[3] not in fields:
            return problem(
                "unknown_output",
                f"'${{{ref.raw}}}': el nodo '{target}' ({node_type.type}) no tiene la salida '{ref.parts[3]}'",
                f"salidas disponibles: {', '.join(fields) or '(ninguna)'}",
            )
    return []
