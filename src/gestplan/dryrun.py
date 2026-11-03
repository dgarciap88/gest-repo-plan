"""Simulación del grafo sin ejecutar nada: orden, ramas y puntos de aprobación."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gestplan.nodes.flow import ManualApproval
from gestplan.nodes.registry import get_node_type
from gestplan.plans.context import ValidationContext
from gestplan.plans.inputs import resolve_inputs
from gestplan.plans.issues import Issue
from gestplan.plans.models import Plan
from gestplan.plans.validator import validate_plan
from gestplan.settings import Settings


@dataclass
class DryRunNode:
    id: str
    type: str
    level: int
    side_effect: str
    routes: dict[str, list[str]]
    approved_by: list[str] = field(default_factory=list)
    loop_back: list[str] = field(default_factory=list)


@dataclass
class DryRunReport:
    plan: str
    inputs: dict[str, Any]
    nodes: list[DryRunNode] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    def render(self) -> str:
        lines = [f"Plan: {self.plan}"]
        lines.append("Inputs: " + (", ".join(f"{k}={v}" for k, v in self.inputs.items()) or "(ninguno)"))
        for issue in self.issues:
            lines.append(issue.format())
        if not self.nodes:
            return "\n".join(lines)
        lines.append("")
        lines.append("Orden de ejecución (los nodos de un mismo nivel pueden ir en paralelo):")
        for node in self.nodes:
            marker = {"none": " ", "local": "·", "external": "!"}[node.side_effect]
            lines.append(f"  [{node.level}] {marker} {node.id} ({node.type})")
            for event_name, targets in sorted(node.routes.items()):
                lines.append(f"        {event_name} -> {', '.join(targets)}")
            if node.approved_by:
                lines.append(f"        cubierto por: {', '.join(node.approved_by)}")
            if node.loop_back:
                lines.append(f"        vuelve a: {', '.join(node.loop_back)}")
        lines.append("")
        lines.append("Leyenda: ! efecto externo · efecto local")
        return "\n".join(lines)


def dry_run(
    plan: Plan,
    provided_inputs: dict[str, Any],
    root: Path | str = ".",
    settings: Settings | None = None,
) -> DryRunReport:
    settings = settings or Settings.from_env()
    values, input_issues = resolve_inputs(plan, provided_inputs)
    report = DryRunReport(plan=plan.name, inputs=values, issues=list(input_issues))
    if not report.ok:
        return report  # sin inputs válidos no se puede simular
    result = validate_plan(plan, root, settings)
    report.issues.extend(result.issues)
    if not result.ok:
        return report

    ctx = ValidationContext(plan, Path(root), settings)
    graph = ctx.graph
    back = graph.back_edges()
    dominators = graph.dominators()
    for level, node_ids in enumerate(graph.levels(), start=1):
        for node_id in node_ids:
            spec = plan.nodes[node_id]
            node_type = get_node_type(spec.type)
            assert node_type is not None  # el validador ya lo ha comprobado
            routes: dict[str, list[str]] = {}
            for src, event_name, dst in graph.edges:
                if src == node_id:
                    routes.setdefault(event_name, []).append(dst)
            approvers = [
                other
                for other in dominators.get(node_id, set()) - {node_id}
                if get_node_type(plan.nodes[other].type) is ManualApproval
                and node_id in plan.nodes[other].config.get("approves", [])
            ]
            report.nodes.append(
                DryRunNode(
                    id=node_id,
                    type=spec.type,
                    level=level,
                    side_effect=node_type.effective_side_effect(spec.config, ctx),
                    routes=routes,
                    approved_by=sorted(approvers),
                    loop_back=sorted(dst for src, dst in back if src == node_id),
                )
            )
    return report
