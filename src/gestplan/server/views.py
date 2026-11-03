"""Vistas JSON de los runs, planes y nodos para la API y la UI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gestplan.catalog import build_catalog
from gestplan.engine.engine import Engine
from gestplan.nodes.registry import get_node_type
from gestplan.plans.graph import PlanGraph
from gestplan.plans.loader import load_plan
from gestplan.plans.models import Plan
from gestplan.plans.validator import validate_plan

NODE_W, NODE_H = 300, 120
DONE_STATES = ("succeeded",)
ACTIVE_STATES = ("ready", "running", "waiting_approval", "waiting_external")
READ_LIMIT = 200_000


def compute_layout(plan: Plan, graph: PlanGraph) -> dict[str, dict[str, float]]:
    """Posiciones por niveles (izquierda→derecha). Respeta `layout` del plan si es completo."""
    if plan.layout and set(plan.layout) >= set(plan.nodes):
        return {n: {"x": p["x"], "y": p["y"]} for n, p in plan.layout.items() if n in plan.nodes}
    levels = graph.levels()
    placed = {n for level in levels for n in level}
    leftovers = [n for n in plan.nodes if n not in placed]
    if leftovers:
        levels = [*levels, leftovers]
    layout: dict[str, dict[str, float]] = {}
    for column, level in enumerate(levels):
        for row, node_id in enumerate(level):
            layout[node_id] = {"x": column * NODE_W, "y": (row - (len(level) - 1) / 2) * NODE_H}
    return layout


def graph_view(plan: Plan, root: Path | None = None, settings: Any = None) -> dict[str, Any]:
    from gestplan.plans.context import ValidationContext
    from gestplan.settings import Settings

    graph = PlanGraph(plan)
    back = graph.back_edges()
    # con el contexto se lee el efecto y los eventos reales de los scripts (META), no el valor conservador
    ctx = ValidationContext(plan, Path(root), settings or Settings(root=Path(root))) if root else None
    nodes: dict[str, Any] = {}
    for node_id, spec in plan.nodes.items():
        node_type = get_node_type(spec.type)
        nodes[node_id] = {
            "type": spec.type,
            "family": node_type.family if node_type else "?",
            "side_effect": node_type.effective_side_effect(spec.config, ctx) if node_type else "none",
            "description": spec.description or (node_type.description.split(". ")[0] if node_type else ""),
            "events": [e.name for e in node_type.event_specs(spec.config, ctx)] if node_type else [],
            "max_attempts": spec.max_attempts,
        }
    edges = [{"from": s, "event": e, "to": d, "back": (s, d) in back} for s, e, d in graph.edges]
    return {"nodes": nodes, "edges": edges, "layout": compute_layout(plan, graph)}


def _node_states(engine: Engine, run_id: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for node_id, state in engine.store.all_nodes(run_id).items():
        attempts = engine.store.attempts(run_id, node_id)
        last = attempts[-1] if attempts else None
        result[node_id] = {
            "status": state["status"], "attempts": state["attempts"], "last_event": state["last_event"],
            "unhandled": bool(state["unhandled"]), "updated_at": state["updated_at"],
            "last_error": last["error"] if last else None,
            "cost_usd": round(sum(a["cost_usd"] for a in attempts), 6),
        }
    return result


def run_summary(engine: Engine, run: dict) -> dict[str, Any]:
    store = engine.store
    nodes = store.all_nodes(run["id"])
    started, finished = run["started_at"], run["finished_at"]
    return {
        "id": run["id"], "plan_name": run["plan_name"], "status": run["status"], "inputs": run["inputs"],
        "created_at": run["created_at"], "started_at": started, "finished_at": finished,
        "error": run["error"], "parent_run_id": run["parent_run_id"], "parent_node_id": run["parent_node_id"],
        "cost_usd": round(store.total_cost(run["id"]), 6),
        "progress": {
            "succeeded": sum(1 for n in nodes.values() if n["status"] in DONE_STATES),
            "total": len(nodes),
            "active": [nid for nid, n in nodes.items() if n["status"] in ACTIVE_STATES],
        },
        "children_count": len(store.children_of(run["id"])),
        "pending_approvals": sum(1 for a in store.list_approvals("pending") if a["run_id"] == run["id"]),
    }


def run_detail(engine: Engine, run_id: str) -> dict[str, Any] | None:
    run = engine.store.get_run(run_id)
    if run is None:
        return None
    plan = Plan.model_validate(run["plan_snapshot"])
    return {
        "run": run_summary(engine, run),
        "plan": {"name": plan.name, "description": plan.description,
                 "settings": plan.settings.model_dump(),
                 **graph_view(plan, engine.settings.root, engine.settings)},
        "nodes": _node_states(engine, run_id),
        "children": [run_summary(engine, c) for c in engine.store.children_of(run_id)],
        "approvals": [a for a in engine.store.list_approvals(None) if a["run_id"] == run_id],
    }


def node_detail(engine: Engine, run_id: str, node_id: str) -> dict[str, Any] | None:
    if engine.store.get_run(run_id) is None or node_id not in engine.store.all_nodes(run_id):
        return None
    store = engine.store
    invocations = []
    for inv in store.invocations(run_id, node_id):
        invocations.append({k: v for k, v in inv.items() if k not in ("result_raw",)}
                           | {"result_preview": (inv.get("result_raw") or "")[:1500]})
    return {"attempts": store.attempts(run_id, node_id), "invocations": invocations,
            "executions": store.executions(run_id, node_id)}


def read_text(path: str | Path | None, limit: int = READ_LIMIT) -> str:
    if not path:
        return ""
    file = Path(path)
    if not file.is_file():
        return ""
    data = file.read_bytes()
    text = data[-limit:].decode("utf-8", errors="replace")
    return ("[... recortado ...]\n" + text) if len(data) > limit else text


def attempt_files(engine: Engine, run_id: str, node_id: str, attempt: int) -> list[dict[str, Any]]:
    folder = engine.attempt_dir(run_id, node_id, attempt)
    if not folder.is_dir():
        return []
    return [{"name": p.name, "size": p.stat().st_size} for p in sorted(folder.iterdir()) if p.is_file()]


def plan_list(root: Path) -> list[dict[str, Any]]:
    from gestplan.settings import Settings

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for folder in (root / "plans", root / "plans" / "examples"):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            if path.stem in seen:
                continue
            seen.add(path.stem)
            plan, issues = load_plan(path)
            entry: dict[str, Any] = {"name": path.stem, "path": str(path.relative_to(root)),
                                     "example": folder.name == "examples"}
            if plan is None:
                entry |= {"valid": False, "description": "", "node_count": 0, "required_inputs": [],
                          "errors": len(issues), "warnings": 0}
            else:
                result = validate_plan(plan, root, Settings(root=root))
                entry |= {"valid": result.ok, "description": plan.description, "node_count": len(plan.nodes),
                          "required_inputs": [n for n, s in plan.inputs.items() if s.required and s.default is None],
                          "errors": len(result.errors), "warnings": len(result.warnings)}
            items.append(entry)
    return items


def plan_detail(root: Path, name: str, settings: Any) -> dict[str, Any] | None:
    from gestplan.plans.loader import find_plan_file

    path = find_plan_file(root, name)
    if path is None:
        return None
    plan, issues = load_plan(path)
    if plan is None:
        return {"name": name, "valid": False, "plan": None, "issues": [i.__dict__ for i in issues]}
    result = validate_plan(plan, root, settings)
    return {"name": name, "valid": result.ok, "plan": plan.model_dump(by_alias=True, mode="json"),
            "issues": [i.__dict__ for i in result.issues],
            "graph": graph_view(plan, root, settings),
            "inputs": {n: s.model_dump() for n, s in plan.inputs.items()}}


def catalog_view() -> dict[str, Any]:
    return build_catalog()
