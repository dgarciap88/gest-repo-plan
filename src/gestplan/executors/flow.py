"""Ejecutores de control de flujo: route, aprobación manual, scripts y subplanes."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import Any

from pydantic import TypeAdapter

from gestplan.executors.base import ExecContext, NodeExecutor, Outcome
from gestplan.nodes.flow import ScriptMeta, load_script_meta
from gestplan.plans.conditions import Condition, evaluate

_TERMINAL_RUN = ("succeeded", "failed", "cancelled")
_CONDITION = TypeAdapter(Condition)


class RouteExec(NodeExecutor):
    type = "route"

    async def run(self, ctx: ExecContext) -> Outcome:
        context = ctx.template_context()
        captures: dict[str, str] = {}
        for case in ctx.config["cases"]:
            if evaluate(_CONDITION.validate_python(case["when"]), context, captures):
                return Outcome(case["event"], {"matched_event": case["event"], "captures": captures})
        default = ctx.config["default"]
        return Outcome(default, {"matched_event": default, "captures": {}})


class ManualApprovalExec(NodeExecutor):
    type = "manual_approval"

    async def run(self, ctx: ExecContext) -> Outcome:
        if ctx.store.approval_for(ctx.run_id, ctx.node_id, ctx.attempt) is None:
            previews = {}
            for target in ctx.config["approves"]:
                previews[target] = await ctx.engine.preview_node(ctx.run, ctx.plan, target)
            ctx.store.create_approval(ctx.run_id, ctx.node_id, ctx.attempt, list(ctx.config["approves"]),
                                      previews, str(ctx.config.get("message", "")))
        return Outcome(waiting="approval")

    async def poll(self, ctx: ExecContext) -> Outcome | None:
        approval = ctx.store.approval_for(ctx.run_id, ctx.node_id, ctx.attempt)
        if approval is None:
            return None
        decision = {"decided_by": approval["decided_by"] or "", "comment": approval["comment"] or ""}
        if approval["status"] == "approved":
            return Outcome("approved", decision)
        if approval["status"] == "rejected":
            return Outcome("rejected", decision)
        expire = ctx.config.get("expire_after_s")
        if approval["status"] == "pending" and expire:
            created = datetime.fromisoformat(approval["created_at"])
            if (datetime.fromisoformat(ctx.store.clock()) - created).total_seconds() > expire:
                ctx.store.decide_approval(approval["id"], "expired", decided_by="sistema")
                return Outcome("expired", {"decided_by": "sistema", "comment": "caducada"})
        return None


class PythonScriptExec(NodeExecutor):
    type = "python_script"

    async def preview(self, ctx: ExecContext) -> dict[str, Any] | None:
        script = ctx.settings.root / ctx.settings.scripts_dir / f"{ctx.config['script']}.py"
        try:
            meta = load_script_meta(script)
        except (OSError, ValueError, SyntaxError) as exc:
            return {"kind": "error", "message": f"script no válido: {exc}"}
        return {"kind": "keyvalue", "title": f"Script {ctx.config['script']}",
                "items": {"descripción": meta.description or "(sin descripción)", "efecto": meta.side_effect,
                          "eventos": ", ".join(meta.events),
                          "inputs": json.dumps(ctx.config.get("inputs", {}), ensure_ascii=False, indent=2)}}

    async def run(self, ctx: ExecContext) -> Outcome:
        script = ctx.settings.root / ctx.settings.scripts_dir / f"{ctx.config['script']}.py"
        try:
            meta: ScriptMeta = load_script_meta(script)
        except (OSError, ValueError, SyntaxError) as exc:
            return Outcome("failed", error=f"script no válido: {exc}")
        payload = json.dumps({"inputs": ctx.config.get("inputs", {}), "run_id": ctx.run_id,
                              "node_id": ctx.node_id}, ensure_ascii=False)
        runner = (
            "import json, runpy, sys\n"
            "ns = runpy.run_path(sys.argv[1], run_name='gestplan_script')\n"
            "data = json.loads(sys.stdin.read())\n"
            "out = ns['run'](data['inputs'], data)\n"
            "print(json.dumps(out, ensure_ascii=False))\n"
        )
        result = await ctx.process([sys.executable, "-c", runner, str(script)], cwd=ctx.settings.root,
                                   stdin_text=payload, kind="script",
                                   timeout_s=ctx.engine.node_timeout(ctx.run, ctx.node_id))
        if result.timed_out:
            return Outcome("failed", error="el script superó el tiempo máximo")
        if not result.ok:
            return Outcome("failed", error=f"el script falló: {result.combined[-500:]}")
        try:
            data = json.loads(result.stdout.strip().splitlines()[-1])
            event, outputs = data["event"], data.get("outputs", {})
        except (ValueError, KeyError, IndexError):
            return Outcome("failed", error=f"salida del script no válida: {result.stdout[-300:]}")
        if event not in meta.events:
            return Outcome("failed", error=f"el script emitió '{event}', no declarado en META")
        return Outcome(event, {"outputs": outputs, **outputs} if isinstance(outputs, dict) else {"outputs": outputs})


class LaunchPlansExec(NodeExecutor):
    type = "launch_plans"

    async def run(self, ctx: ExecContext) -> Outcome:
        items = ctx.config["items"]
        if not isinstance(items, list):
            return Outcome("failed", error="'items' debe resolverse a una lista de inputs")
        run_ids = []
        for index, inputs in enumerate(items):
            key = f"{ctx.run_id}:{ctx.node_id}:{index}"
            existing = ctx.store.find_run_by_key(key)
            if existing:  # idempotencia: se reutiliza y, si estaba cancelado, se reactiva
                if existing["status"] in ("cancelled", "failed"):
                    ctx.engine.reactivate_run(existing["id"])
                run_ids.append(existing["id"])
                continue
            try:
                child_id = ctx.engine.create_run_from_name(
                    ctx.config["plan"], {k: str(v) if not isinstance(v, str) else v for k, v in inputs.items()},
                    parent_run_id=ctx.run_id, parent_node_id=ctx.node_id, parent_index=index,
                    idempotency_key=key, start_limit=int(ctx.config.get("max_parallel", 2)),
                    title=f"{ctx.run['title']} · {index + 1}" if ctx.run.get("title") else None)
            except Exception as exc:  # noqa: BLE001
                return Outcome("failed", error=f"no se pudo lanzar el plan hijo #{index}: {exc}")
            run_ids.append(child_id)
        return Outcome("ok", {"run_ids": run_ids, "count": len(run_ids)})


class WaitPlanExec(NodeExecutor):
    type = "wait_plan"

    def _check(self, ctx: ExecContext) -> Outcome | None:
        launched = ctx.node_outputs(ctx.config["launched_by"])
        run_ids: list[str] = launched.get("run_ids", [])
        rows = [ctx.store.get_run(run_id) for run_id in run_ids]
        if any(row is None or row["status"] not in _TERMINAL_RUN for row in rows):
            return None
        results: list[dict[str, Any]] = [{"run_id": r["id"], "status": r["status"]} for r in rows if r]
        failed = sum(1 for r in results if r["status"] == "failed")
        cancelled = sum(1 for r in results if r["status"] == "cancelled")
        outputs = {"results": results, "failed_count": failed + cancelled}
        if cancelled:
            return Outcome("cancelled", outputs)
        return Outcome("some_failed" if failed else "all_ok", outputs)

    async def run(self, ctx: ExecContext) -> Outcome:
        return self._check(ctx) or Outcome(waiting="external")

    async def poll(self, ctx: ExecContext) -> Outcome | None:
        return self._check(ctx)


class _Fields(dict):
    """Campos de un elemento para str.format_map: un campo inexistente es un error claro, no un hueco."""

    def __missing__(self, key: str) -> str:
        raise KeyError(key)


class MapItemsExec(NodeExecutor):
    type = "map_items"

    async def run(self, ctx: ExecContext) -> Outcome:
        items = ctx.config["items"]
        if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
            return Outcome("failed", error="'items' debe resolverse a una lista de objetos")
        mapping, skip = ctx.config["mapping"], bool(ctx.config.get("skip_when_empty"))
        result: list[dict[str, Any]] = []
        skipped = 0
        for index, item in enumerate(items):
            built: dict[str, Any] = {}
            try:
                for name, value in mapping.items():
                    built[name] = value.format_map(_Fields(item)) if isinstance(value, str) else value
            except KeyError as exc:
                return Outcome("failed", error=f"el elemento #{index} no tiene el campo {exc} "
                               f"(campos: {', '.join(sorted(item)) or 'ninguno'})")
            except (ValueError, IndexError, AttributeError) as exc:
                return Outcome("failed", error=f"valor del mapping no válido en el elemento #{index}: {exc}")
            if skip and any(v in ("", None) for v in built.values()):
                skipped += 1
                continue
            result.append(built)
        return Outcome("ok" if result else "empty", {"items": result, "count": len(result), "skipped": skipped})
