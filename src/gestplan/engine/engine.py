"""Motor de ejecución: planifica nodos según el grafo, persiste cada transición y se recupera.

Es cooperativo y de un solo hilo (asyncio): `run_until_quiescent` avanza el run y sus runs hijos
hasta que no queda trabajo en curso (terminado, o esperando aprobaciones/esperas externas).
Ver DESIGN.md secciones 4 y 10.
"""

from __future__ import annotations

import asyncio
import os
import secrets
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from gestplan.db import Store
from gestplan.engine.http import HttpClient, RealHttp
from gestplan.engine.runner import ProcessRunner, RealRunner, register_secrets
from gestplan.executors import default_executors
from gestplan.executors.base import ExecContext, NodeExecutor, Outcome
from gestplan.nodes.registry import get_node_type
from gestplan.plans.graph import PlanGraph
from gestplan.plans.inputs import resolve_inputs
from gestplan.plans.issues import Issue
from gestplan.plans.loader import find_plan_file, load_plan
from gestplan.plans.models import Plan
from gestplan.plans.templates import UnresolvedReference, resolve_template
from gestplan.plans.validator import validate_plan
from gestplan.settings import Settings

TERMINAL_RUN = ("succeeded", "failed", "cancelled")
ACTIVE_NODE = ("ready", "running", "waiting_approval", "waiting_external")
AGENT_TYPES = ("ia_agent", "ia_agent_validator")


class PlanInvalid(Exception):
    def __init__(self, issues: list[Issue]) -> None:
        super().__init__("; ".join(i.message for i in issues if i.severity == "error"))
        self.issues = issues


@dataclass
class RunState:
    plan: Plan
    graph: PlanGraph
    edges_from: dict[tuple[str, str], list[str]]
    forward_in: dict[str, dict[str, set[str]]]
    bounds: dict[str, int]
    tasks: dict[str, asyncio.Task] = field(default_factory=dict)


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


class Engine:
    def __init__(self, store: Store, settings: Settings, runner: ProcessRunner | None = None,
                 executors: dict[str, NodeExecutor] | None = None, http: HttpClient | None = None) -> None:
        self.store = store
        self.settings = settings
        self.runner: ProcessRunner = runner or RealRunner()
        self.http: HttpClient = http or RealHttp()
        self.sleep = asyncio.sleep  # los tests lo sustituyen para no esperar en los reintentos
        register_secrets(settings.secret_values)
        self.executors = executors if executors is not None else default_executors()
        self.cli_versions: dict[str, str | None] = {}
        self._states: dict[str, RunState] = {}
        self._sem = asyncio.Semaphore(settings.max_concurrent_nodes)
        self._agent_sem = asyncio.Semaphore(settings.max_agents_parallel)

    # ------------------------------------------------------------------ creación de runs -----
    def create_run(self, plan: Plan, inputs: dict[str, Any], *, parent_run_id: str | None = None,
                   parent_node_id: str | None = None, parent_index: int | None = None,
                   idempotency_key: str | None = None, start_limit: int = 1000) -> str:
        values, issues = resolve_inputs(plan, inputs)
        if any(i.severity == "error" for i in issues):
            raise PlanInvalid(issues)
        result = validate_plan(plan, self.settings.root, self.settings)
        if not result.ok:
            raise PlanInvalid(result.issues)
        run_id = new_run_id()
        self.store.create_run(run_id, plan.name, plan.model_dump(by_alias=True, mode="json"), values,
                              parent_run_id=parent_run_id, parent_node_id=parent_node_id,
                              parent_index=parent_index, idempotency_key=idempotency_key,
                              start_limit=start_limit)
        self.store.init_nodes(run_id, list(plan.nodes))
        return run_id

    def create_run_from_name(self, name: str, inputs: dict[str, Any], **kwargs: Any) -> str:
        path = find_plan_file(self.settings.root, name)
        if path is None:
            raise PlanInvalid([Issue(code="missing_plan", message=f"no existe el plan '{name}'")])
        plan, issues = load_plan(path)
        if plan is None:
            raise PlanInvalid(issues)
        return self.create_run(plan, inputs, **kwargs)

    # ------------------------------------------------------------------ contexto y utilidades --
    def _state(self, run: dict) -> RunState:
        state = self._states.get(run["id"])
        if state is None:
            plan = Plan.model_validate(run["plan_snapshot"])
            graph = PlanGraph(plan)
            back = graph.back_edges()
            edges_from: dict[tuple[str, str], list[str]] = defaultdict(list)
            forward_in: dict[str, dict[str, set[str]]] = {n: defaultdict(set) for n in plan.nodes}
            for src, event, dst in graph.edges:
                edges_from[(src, event)].append(dst)
                if (src, dst) not in back:
                    forward_in[dst][src].add(event)
            bounds = {n: s.max_attempts for n, s in plan.nodes.items() if s.max_attempts}
            for src, event, dst in graph.edges:
                node_type = get_node_type(plan.nodes[src].type)
                if node_type and node_type.loop_event == event and plan.nodes[dst].max_attempts is None:
                    bound = node_type.loop_bound(plan.nodes[src].config)
                    if bound:
                        bounds[dst] = max(bounds.get(dst, 0), bound)
            state = RunState(plan, graph, dict(edges_from), forward_in, bounds)
            self._states[run["id"]] = state
        return state

    def template_context(self, run: dict, plan: Plan) -> dict[str, Any]:
        nodes: dict[str, Any] = {}
        for node_id in plan.nodes:
            outputs = self.store.latest_outputs(run["id"], node_id)
            if outputs is not None:
                nodes[node_id] = {"outputs": outputs}
        env = {k: os.environ[k] for k in self.settings.env_allowlist if k in os.environ}
        return {"inputs": run["inputs"], "nodes": nodes, "env": env,
                "run": {"id": run["id"], "plan": run["plan_name"], "created_at": run["created_at"]}}

    def node_timeout(self, run: dict, node_id: str) -> float | None:
        plan = self._state(run).plan
        spec = plan.nodes[node_id]
        node_type = get_node_type(spec.type)
        return spec.timeout_s or (node_type.default_timeout_s if node_type else None)

    def node_idle_timeout(self, run: dict, node_id: str) -> float | None:
        node_type = get_node_type(self._state(run).plan.nodes[node_id].type)
        return node_type.idle_timeout_s if node_type else None

    def attempt_dir(self, run_id: str, node_id: str, attempt: int) -> Path:
        # absoluta: los procesos (git -F, gh --body-file) corren con otro cwd y no resolverían una ruta relativa
        return (self.settings.data_dir / "runs" / run_id / node_id / str(attempt)).resolve()

    def _exec_context(self, run: dict, plan: Plan, node_id: str, attempt: int, config: dict) -> ExecContext:
        return ExecContext(self, run, plan, node_id, attempt, config, self.attempt_dir(run["id"], node_id, attempt))

    def prepare_config(self, run: dict, plan: Plan, node_id: str) -> dict[str, Any]:
        """Resuelve las referencias y normaliza con el ConfigModel del nodo (aplica sus valores por defecto).

        Lanza UnresolvedReference o ValidationError si la config no se puede usar.
        """
        spec = plan.nodes[node_id]
        resolved = resolve_template(spec.config, self.template_context(run, plan), empty_missing_nodes=True)
        node_type = get_node_type(spec.type)
        if node_type is None:
            return resolved
        return node_type.ConfigModel.model_validate(resolved).model_dump(mode="python", by_alias=True)

    async def preview_node(self, run: dict, plan: Plan, node_id: str) -> dict[str, Any]:
        executor = self.executors.get(plan.nodes[node_id].type)
        if executor is None:
            return {"kind": "none", "message": "sin vista previa"}
        try:
            config = self.prepare_config(run, plan, node_id)
        except UnresolvedReference as exc:
            return {"kind": "error", "message": f"no se pudo resolver {exc}"}
        except ValidationError as exc:
            return {"kind": "error", "message": f"configuración no válida: {exc.errors()[0]['msg']}"}
        attempt = self.store.get_node(run["id"], node_id)["attempts"] + 1
        try:
            return await executor.preview(self._exec_context(run, plan, node_id, attempt, config)) or {
                "kind": "none", "message": "sin vista previa"}
        except Exception as exc:  # noqa: BLE001 - la vista previa nunca debe romper el run
            return {"kind": "error", "message": f"{type(exc).__name__}: {exc}"}

    # ------------------------------------------------------------------ operación -----------
    def recover(self) -> int:
        """Tras una caída: los nodos que estaban en ejecución quedan interrumpidos (reactivables)."""
        count = 0
        for run in self.store.list_runs(limit=1000):
            if run["status"] in TERMINAL_RUN:
                continue
            for node_id, state in self.store.all_nodes(run["id"]).items():
                if state["status"] == "running":
                    attempt = self.store.open_attempt(run["id"], node_id)
                    if attempt:
                        self.store.finish_attempt(attempt["id"], "interrupted", error="proceso interrumpido")
                    self.store.set_node(run["id"], node_id, "interrupted", reason="recuperación tras caída")
                    count += 1
        return count

    def cancel_run(self, run_id: str, reason: str = "cancelado por el usuario") -> None:
        run = self.store.get_run(run_id)
        if run is None or run["status"] in TERMINAL_RUN:
            return
        self.store.set_run_status(run_id, "cancelled", reason=reason)
        self._cancel_active_nodes(run_id)
        for child in self.store.children_of(run_id):
            self.cancel_run(child["id"], reason="el run padre se canceló")

    def _cancel_active_nodes(self, run_id: str) -> None:
        for node_id, state in self.store.all_nodes(run_id).items():
            if state["status"] in ACTIVE_NODE:
                attempt = self.store.open_attempt(run_id, node_id)
                if attempt:
                    self.store.finish_attempt(attempt["id"], "cancelled", error="cancelado")
                self.store.invalidate_approvals(run_id, node_id)
                self.store.set_node(run_id, node_id, "cancelled", reason="run cancelado")

    def retry_node(self, run_id: str, node_id: str) -> None:
        run = self.store.get_run(run_id)
        if run is None:
            raise ValueError(f"no existe el run {run_id}")
        state = self.store.get_node(run_id, node_id)
        if state["status"] not in ("failed", "cancelled", "interrupted"):
            raise ValueError(f"el nodo '{node_id}' está en estado '{state['status']}': solo se reactivan "
                             "nodos failed, cancelled o interrupted")
        self.store.invalidate_approvals(run_id, node_id)
        self.store.set_node(run_id, node_id, "ready", reason="reactivado", unhandled=0,
                            base_attempts=state["attempts"])
        for other, st in self.store.all_nodes(run_id).items():
            if st["status"] == "skipped":
                self.store.set_node(run_id, other, "pending", reason="reactivación")
        spec = self._state(run).plan.nodes[node_id]
        if spec.type == "wait_plan":  # reactivar la espera reactiva también los hijos cancelados
            launched = self.store.latest_outputs(run_id, spec.config.get("launched_by", "")) or {}
            for child_id in launched.get("run_ids", []):
                child = self.store.get_run(child_id)
                if child and child["status"] == "cancelled":
                    self.reactivate_run(child_id)
        self.store.set_run_status(run_id, "running", reason=f"nodo {node_id} reactivado")
        self._reopen_parent(run)

    def _reopen_parent(self, run: dict) -> None:
        """Reactivar un hijo reabre la espera del padre; si no, el padre (ya terminado) nunca lo volvería a avanzar."""
        parent_id = run.get("parent_run_id")
        parent = self.store.get_run(parent_id) if parent_id else None
        if parent is None:
            return
        plan = self._state(parent).plan
        for node_id, spec in plan.nodes.items():
            waits_for_run = spec.type == "wait_plan" and spec.config.get("launched_by") == run.get("parent_node_id")
            if waits_for_run and self.store.get_node(parent["id"], node_id)["status"] in (
                    "failed", "cancelled", "interrupted"):
                self.retry_node(parent["id"], node_id)  # reabre a su vez al padre del padre
                return

    def reactivate_run(self, run_id: str) -> None:
        """Reactiva los nodos cancelados, interrumpidos o fallidos sin manejar de un run hijo."""
        for node_id, state in self.store.all_nodes(run_id).items():
            if state["status"] in ("cancelled", "interrupted") or (
                    state["status"] == "failed" and state["unhandled"]):
                self.store.set_node(run_id, node_id, "ready", reason="run reactivado", unhandled=0,
                                    base_attempts=state["attempts"])
        for other, st in self.store.all_nodes(run_id).items():
            if st["status"] == "skipped":
                self.store.set_node(run_id, other, "pending", reason="reactivación")
        self.store.set_run_status(run_id, "queued", reason="run reactivado")
        run = self.store.get_run(run_id)
        if run:
            self._reopen_parent(run)

    # ------------------------------------------------------------------ bucle principal -----
    def _family(self, run_id: str) -> list[str]:
        ids = [run_id]
        for child in self.store.children_of(run_id):
            ids.extend(self._family(child["id"]))
        return ids

    async def run_until_quiescent(self, run_id: str) -> str:
        if (self.store.get_run(run_id) or {}).get("status") == "queued":
            self.store.set_run_status(run_id, "running", reason="inicio")
        while True:
            progress, tasks = await self.step(run_id)
            if tasks:
                await asyncio.wait(tasks, timeout=self.settings.poll_interval_s,
                                   return_when=asyncio.FIRST_COMPLETED)
                continue
            if not progress:
                break
        return self.store.get_run(run_id)["status"]  # type: ignore[index]

    async def step(self, run_id: str) -> tuple[bool, list[asyncio.Task]]:
        """Un paso del scheduler para un run y sus hijos. Devuelve (hubo progreso, tareas en curso)."""
        ids = self._family(run_id)
        progress = self._activate_children(ids)
        for rid in ids:
            progress |= await self._tick(rid)
        tasks = [t for rid in ids if rid in self._states for t in self._states[rid].tasks.values()
                 if not t.done()]
        return progress, tasks

    def _root_of(self, run_id: str) -> str:
        run = self.store.get_run(run_id)
        while run and run["parent_run_id"]:
            run = self.store.get_run(run["parent_run_id"])
        return run["id"] if run else run_id

    def active_roots(self) -> list[str]:
        """Runs raíz que necesitan atención: no terminados, o cancelados con tareas aún vivas."""
        roots = [r["id"] for r in self.store.active_roots()]
        for rid, state in self._states.items():
            if any(not t.done() for t in state.tasks.values()):
                roots.append(self._root_of(rid))
        return list(dict.fromkeys(roots))

    def _activate_children(self, ids: list[str]) -> bool:
        changed = False
        groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for rid in ids:
            run = self.store.get_run(rid)
            if run and run["parent_run_id"]:
                groups[(run["parent_run_id"], run["parent_node_id"])].append(run)
        for runs in groups.values():
            active = sum(1 for r in runs if r["status"] in ("running", "waiting"))
            for run in sorted((r for r in runs if r["status"] == "queued"),
                              key=lambda r: (r["parent_index"] or 0, r["id"])):
                if active >= run["start_limit"]:
                    break
                self.store.set_run_status(run["id"], "running", reason="hijo activado")
                active += 1
                changed = True
        return changed

    async def _tick(self, run_id: str) -> bool:
        run = self.store.get_run(run_id)
        if run is None or run["status"] == "queued":
            return False
        state = self._state(run)
        if run["status"] == "cancelled":
            return await self._cancel_tasks(run_id, state)
        if run["status"] in ("succeeded", "failed"):
            return False
        progress = False

        timeout = state.plan.settings.run_timeout_s
        if timeout and run["started_at"]:
            elapsed = (datetime.fromisoformat(self.store.clock()) - datetime.fromisoformat(run["started_at"]))
            if elapsed.total_seconds() > timeout:
                await self._cancel_tasks(run_id, state)
                self._cancel_active_nodes(run_id)
                self.store.set_run_status(run_id, "failed", reason="run_timeout", error="se superó run_timeout_s")
                return True

        for node_id, task in list(state.tasks.items()):
            if not task.done():
                continue
            del state.tasks[node_id]
            progress = True
            try:
                outcome: Outcome | None = task.result()
            except asyncio.CancelledError:
                outcome = None
            self._finish(run, state, node_id, outcome)

        for node_id, node in self.store.all_nodes(run_id).items():
            if node["status"] in ("waiting_approval", "waiting_external") and node_id not in state.tasks:
                outcome = await self._poll(run, state, node_id)
                if outcome is not None:
                    self._finish(run, state, node_id, outcome)
                    progress = True

        progress |= self._reconcile(run_id, state)

        failing = self._failing(run_id, state)
        if not failing:
            for node_id, node in self.store.all_nodes(run_id).items():
                if node["status"] == "ready" and node_id not in state.tasks:
                    self._start_node(run, state, node_id)
                    progress = True
        progress |= self._finalize(run_id, state, failing)
        return progress

    def _failing(self, run_id: str, state: RunState) -> bool:
        if state.plan.settings.on_unhandled_failure != "fail_run":
            return False
        return any(n["unhandled"] for n in self.store.all_nodes(run_id).values())

    async def _cancel_tasks(self, run_id: str, state: RunState) -> bool:
        tasks = list(state.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        state.tasks.clear()
        return bool(tasks)

    # ------------------------------------------------------------------ transiciones --------
    def _reconcile(self, run_id: str, state: RunState) -> bool:
        changed = False
        nodes = self.store.all_nodes(run_id)
        for node_id, node in nodes.items():
            if node["status"] not in ("pending", "skipped") or node["attempts"] > 0:
                continue
            groups = state.forward_in.get(node_id) or {}
            if not groups:
                target = "ready" if node["status"] == "pending" else None
            else:
                fired = dead = 0
                for source, events in groups.items():
                    src = nodes[source]
                    emitted = src["status"] in ("succeeded", "failed") and src["last_event"] in events
                    if emitted:
                        fired += 1
                    elif src["status"] in ("succeeded", "failed", "skipped"):
                        dead += 1
                join = state.plan.nodes[node_id].join
                if join == "any":
                    target = "ready" if fired else ("skipped" if dead == len(groups) else "pending")
                else:
                    target = "ready" if fired == len(groups) else ("skipped" if dead else "pending")
            if target and target != node["status"]:
                self.store.set_node(run_id, node_id, target, reason="reconciliación")
                changed = True
        return changed

    def _start_node(self, run: dict, state: RunState, node_id: str) -> None:
        run_id = run["id"]
        node = self.store.get_node(run_id, node_id)
        spec = state.plan.nodes[node_id]
        attempt = node["attempts"] + 1
        bound = state.bounds.get(node_id, self.settings.max_attempts_cap)
        attempt_id = self.store.start_attempt(run_id, node_id, attempt, None)
        self.store.set_node(run_id, node_id, "running", attempts=attempt, unhandled=0, reason="inicio")

        if attempt - node["base_attempts"] > bound:
            self._finish(run, state, node_id, Outcome(
                "max_attempts", error=f"se superó el máximo de {bound} ejecuciones del nodo"))
            return
        executor = self.executors.get(spec.type)
        if executor is None:
            self._finish(run, state, node_id, Outcome("failed", error=f"sin ejecutor para '{spec.type}'"))
            return
        try:
            config = self.prepare_config(run, state.plan, node_id)
        except UnresolvedReference as exc:
            self._finish(run, state, node_id, Outcome("failed", error=f"referencia sin resolver: ${{{exc}}}"))
            return
        except ValidationError as exc:
            detail = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:3])
            self._finish(run, state, node_id, Outcome("failed", error=f"configuración no válida: {detail}"))
            return
        self.store._update("node_attempts", "id=?", (attempt_id,), resolved_config=config)
        ctx = self._exec_context(run, state.plan, node_id, attempt, config)
        timeout = self.node_timeout(run, node_id)
        state.tasks[node_id] = asyncio.ensure_future(
            self._node_task(executor, ctx, spec.type, timeout))

    async def _node_task(self, executor: NodeExecutor, ctx: ExecContext, node_type: str,
                         timeout: float | None) -> Outcome:
        declared = {e.name for e in (get_node_type(node_type).events if get_node_type(node_type) else ())}
        async with self._sem:
            try:
                if node_type in AGENT_TYPES:
                    async with self._agent_sem:
                        return await self._with_timeout(executor.run(ctx), timeout)
                return await self._with_timeout(executor.run(ctx), timeout)
            except TimeoutError:
                return Outcome("timeout" if "timeout" in declared else "failed",
                               error=f"se superó el tiempo máximo ({timeout}s)")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - un fallo del ejecutor no debe tumbar el motor
                ctx.log(f"excepción en el ejecutor: {type(exc).__name__}: {exc}")
                return Outcome("failed", error=f"{type(exc).__name__}: {exc}")

    @staticmethod
    async def _with_timeout(coro, timeout: float | None):
        return await (asyncio.wait_for(coro, timeout) if timeout else coro)

    async def _poll(self, run: dict, state: RunState, node_id: str) -> Outcome | None:
        executor = self.executors.get(state.plan.nodes[node_id].type)
        attempt = self.store.open_attempt(run["id"], node_id)
        if executor is None or attempt is None:
            return Outcome("failed", error="no se puede reanudar la espera del nodo")
        ctx = self._exec_context(run, state.plan, node_id, attempt["attempt"], attempt["resolved_config"] or {})
        try:
            return await executor.poll(ctx)
        except Exception as exc:  # noqa: BLE001
            return Outcome("failed", error=f"{type(exc).__name__}: {exc}")

    def _finish(self, run: dict, state: RunState, node_id: str, outcome: Outcome | None) -> None:
        run_id = run["id"]
        attempt = self.store.open_attempt(run_id, node_id)
        if outcome is None:
            if attempt:
                self.store.finish_attempt(attempt["id"], "cancelled", error="cancelado")
            self.store.set_node(run_id, node_id, "cancelled", reason="tarea cancelada")
            return
        if outcome.waiting:
            if attempt:
                self.store.set_attempt_status(attempt["id"], "waiting")
            self.store.set_node(run_id, node_id, f"waiting_{outcome.waiting}", reason="en espera")
            return

        spec = state.plan.nodes[node_id]
        node_type = get_node_type(spec.type)
        failure = self._is_failure(node_type, spec.type, outcome.event)
        status = "failed" if failure else "succeeded"
        if attempt:
            self.store.finish_attempt(attempt["id"], status, event=outcome.event, outputs=outcome.outputs or {},
                                      error=outcome.error, cost_usd=outcome.cost_usd)
        targets = state.edges_from.get((node_id, outcome.event), [])
        unhandled = int(failure and not targets)
        self.store.set_node(run_id, node_id, status, event=outcome.event, reason=outcome.error,
                            unhandled=unhandled)
        for target in targets:
            self._trigger(run_id, state, target)

    @staticmethod
    def _is_failure(node_type: Any, type_name: str, event: str) -> bool:
        if type_name == "route":
            return False
        if type_name == "python_script":
            return event == "failed"
        if node_type is not None:
            for spec in node_type.events:
                if spec.name == event:
                    return spec.failure
        return True  # un evento no declarado es un fallo

    def _trigger(self, run_id: str, state: RunState, target: str) -> None:
        """Un nodo que ya se ejecutó vuelve a ejecutarse (bucle); los nuevos los decide `_reconcile`."""
        node = self.store.get_node(run_id, target)
        if node["attempts"] == 0 or node["status"] in ("ready", "running"):
            return
        attempt = self.store.open_attempt(run_id, target)
        if attempt:
            self.store.finish_attempt(attempt["id"], "superseded", error="sustituido por una nueva ejecución")
        self.store.invalidate_approvals(run_id, target)
        self.store.set_node(run_id, target, "ready", reason="reactivado por un bucle", unhandled=0)

    def _finalize(self, run_id: str, state: RunState, failing: bool) -> bool:
        nodes = self.store.all_nodes(run_id)
        running = state.tasks or any(n["status"] == "running" for n in nodes.values())
        if failing and not running:
            changed = False
            for node_id, node in nodes.items():
                if node["status"] == "ready":
                    self.store.set_node(run_id, node_id, "skipped", reason="el run falla")
                    changed = True
                elif node["status"].startswith("waiting_"):
                    self._cancel_waiting(run_id, node_id)
                    changed = True
            if changed:
                nodes = self.store.all_nodes(run_id)
        active = [n for n in nodes.values() if n["status"] in ACTIVE_NODE]
        if active:
            only_waiting = all(n["status"].startswith("waiting_") for n in active) and not state.tasks
            self.store.set_run_status(run_id, "waiting" if only_waiting else "running")
            return False
        for node_id, node in nodes.items():
            if node["status"] == "pending":
                self.store.set_node(run_id, node_id, "skipped", reason="no alcanzado")
        bad = any(n["unhandled"] or n["status"] in ("interrupted", "cancelled") for n in nodes.values())
        self.store.set_run_status(run_id, "failed" if bad else "succeeded")
        return True

    def _cancel_waiting(self, run_id: str, node_id: str) -> None:
        attempt = self.store.open_attempt(run_id, node_id)
        if attempt:
            self.store.finish_attempt(attempt["id"], "cancelled", error="el run falla")
        self.store.invalidate_approvals(run_id, node_id)
        self.store.set_node(run_id, node_id, "cancelled", reason="el run falla")
