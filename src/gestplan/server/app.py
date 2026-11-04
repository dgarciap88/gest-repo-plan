"""Servidor web: API REST + eventos en tiempo real (SSE) + interfaz estática."""

from __future__ import annotations

import asyncio
import getpass
import json
import os
import re
import secrets
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from gestplan import config_admin
from gestplan.catalog import render_markdown
from gestplan.db import Store, local_midnight
from gestplan.engine.engine import Engine, PlanInvalid
from gestplan.nodes.registry import get_node_type
from gestplan.plans.loader import find_plan_file, parse_plan
from gestplan.plans.models import Plan
from gestplan.plans.validator import validate_plan
from gestplan.server import views
from gestplan.server.supervisor import Supervisor
from gestplan.settings import Settings

SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
PLAN_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")
CSRF_HEADER = "x-gestplan"
TOKEN_HEADER = "x-gestplan-token"


def _host_name(value: str) -> str:
    """Nombre sin puerto de una cabecera Host u Origin (admite IPv6 entre corchetes)."""
    value = value.split("://", 1)[-1].split("/", 1)[0]
    return value.split("]")[0] + "]" if value.startswith("[") else value.split(":")[0]


def _check_overrides(engine: Engine, approval: dict, overrides: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """Solo se pueden editar campos declarados como editables de nodos que la aprobación cubre."""
    run = engine.store.get_run(approval["run_id"])
    plan = Plan.model_validate(run["plan_snapshot"]) if run else None
    for node_id, fields in overrides.items():
        spec = plan.nodes.get(node_id) if plan else None
        node_type = get_node_type(spec.type) if spec else None
        if node_id not in approval["covers"] or node_type is None:
            raise HTTPException(422, f"la aprobación no cubre el nodo '{node_id}'")
        for field in fields:
            if field not in node_type.editable_fields:
                raise HTTPException(422, f"el campo '{field}' de '{node_id}' no se puede editar al aprobar")
    return overrides


def guard_api(app: FastAPI, settings: Settings) -> None:
    """Protege la API local frente a páginas web ajenas (NICE_TO_HAVE 1.1): DNS rebinding, CSRF y token."""
    allowed = set(LOCAL_HOSTS) | set(settings.allowed_hosts)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            deny = None
            if _host_name(request.headers.get("host", "")) not in allowed:
                deny = "host no permitido"
            elif request.method not in ("GET", "HEAD", "OPTIONS"):
                origin = request.headers.get("origin")
                if origin and _host_name(origin) != _host_name(request.headers.get("host", "")):
                    deny = "origen no permitido"
                elif CSRF_HEADER not in request.headers:
                    deny = f"falta la cabecera {CSRF_HEADER}"
            if deny is None and settings.api_token:
                supplied = request.headers.get(TOKEN_HEADER) or request.query_params.get("token", "")
                if not secrets.compare_digest(supplied, settings.api_token):
                    deny = "token no válido"
            if deny:
                return JSONResponse({"detail": deny}, status_code=403)
        return await call_next(request)


class RunCreate(BaseModel):
    plan: str
    title: str | None = Field(default=None, max_length=120)
    inputs: dict[str, Any] = Field(default_factory=dict)


class ConfigUpdate(BaseModel):
    values: dict[str, str | None] = Field(
        description="Nombre → valor nuevo. '' o null borra el parámetro (en un secreto, '' lo deja como está)")


class Cleanup(BaseModel):
    older_than_days: int = 0
    statuses: list[Literal["succeeded", "failed", "cancelled"]] = Field(
        default_factory=lambda: ["succeeded", "failed", "cancelled"])


class Decision(BaseModel):
    decision: Literal["approved", "rejected"]
    comment: str | None = None
    nonce: str = Field(min_length=1, description="Código de un solo uso de la aprobación (lo da GET /api/approvals)")
    overrides: dict[str, dict[str, str]] = Field(default_factory=dict, description="Textos editados: nodo, campo")


def create_app(settings: Settings | None = None, engine: Engine | None = None, *, serve_ui: bool = True) -> FastAPI:
    settings = engine.settings if engine else (settings or Settings.from_env())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        eng = engine or Engine(Store(settings.db_path), settings)
        supervisor = Supervisor(eng)
        app.state.engine, app.state.supervisor = eng, supervisor
        task = asyncio.create_task(supervisor.run())
        try:
            yield
        finally:
            supervisor.stop()
            with suppress(Exception):
                await asyncio.wait_for(task, 5)
            task.cancel()

    app = FastAPI(title="gestplan", version="0.1.0", lifespan=lifespan)

    guard_api(app, settings)

    def eng() -> Engine:
        return app.state.engine

    def wake() -> None:
        app.state.supervisor.wake()

    # --- general ---------------------------------------------------------------------------
    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "version": "0.1.0", "root": str(settings.root.resolve())}

    # --- configuración ---------------------------------------------------------------------
    @app.get("/api/config")
    async def get_config() -> dict[str, Any]:
        return {"file": str(settings.root.resolve() / ".env"), "groups": config_admin.GROUPS,
                "params": config_admin.describe(settings.root / ".env")}

    @app.put("/api/config")
    async def put_config(body: ConfigUpdate) -> dict[str, Any]:
        nonlocal settings
        errors = {n: e for n, v in body.values.items() if (e := config_admin.validate(n, v or ""))}
        if errors:
            raise HTTPException(422, {"errors": errors})
        changes: dict[str, str | None] = {}
        for name, value in body.values.items():
            if config_admin.BY_NAME[name].kind == "secret" and value == "":
                continue  # un secreto vacío significa «no cambiar»
            changes[name] = value or None
        if not changes:
            return {"changed": [], "restart_required": []}
        config_admin.write_env_file(settings.root / ".env", changes)
        for name, value in changes.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        settings = Settings.from_env(settings.root)
        eng().settings = settings
        return {"changed": sorted(changes),
                "restart_required": sorted(n for n in changes if config_admin.BY_NAME[n].restart)}

    @app.get("/api/stats")
    async def stats() -> dict[str, Any]:
        store = eng().store
        roots = [r for r in store.list_runs(limit=1000) if r["parent_run_id"] is None]
        counts: dict[str, int] = {}
        for run in roots:
            counts[run["status"]] = counts.get(run["status"], 0) + 1
        return {"runs": counts, "pending_approvals": len(store.list_approvals("pending")),
                "cost_today_usd": round(store.cost_since(local_midnight()), 6),
                "daily_budget_usd": settings.daily_budget_usd, "run_budget_usd": settings.run_budget_usd,
                "interrupted_nodes": sum(
                    1 for r in roots for n in store.all_nodes(r["id"]).values() if n["status"] == "interrupted")}

    @app.get("/api/catalog")
    async def catalog() -> dict[str, Any]:
        return views.catalog_view()

    @app.get("/api/catalog/markdown", response_class=PlainTextResponse)
    async def catalog_markdown() -> str:
        return render_markdown(views.catalog_view())

    # --- planes ----------------------------------------------------------------------------
    @app.get("/api/plans")
    async def plans() -> list[dict[str, Any]]:
        return views.plan_list(settings.root)

    @app.get("/api/plans/{name}")
    async def plan(name: str) -> dict[str, Any]:
        detail = views.plan_detail(settings.root, name, settings)
        if detail is None:
            raise HTTPException(404, f"no existe el plan '{name}'")
        return detail

    @app.post("/api/plans/validate")
    async def validate(body: dict[str, Any]) -> dict[str, Any]:
        parsed, issues = parse_plan(body)
        if parsed is not None:
            issues = validate_plan(parsed, settings.root, settings).issues
        return {"ok": not any(i.severity == "error" for i in issues), "issues": [i.__dict__ for i in issues]}

    @app.put("/api/plans/{name}")
    async def save_plan(name: str, body: dict[str, Any]) -> dict[str, Any]:
        """Guarda un plan editado (o nuevo) en plans/. Solo se escribe si es válido: nunca queda un plan roto."""
        if not PLAN_NAME.match(name) or body.get("name") != name:
            raise HTTPException(422, "el nombre del plan debe coincidir con la URL: minúsculas, dígitos, - y _")
        parsed, issues = parse_plan(body)
        if parsed is not None:
            issues = validate_plan(parsed, settings.root, settings).issues
        errors = [i for i in issues if i.severity == "error"]
        if errors:
            raise HTTPException(422, {"message": f"el plan tiene {len(errors)} error(es)",
                                      "issues": [i.__dict__ for i in issues]})
        target = find_plan_file(settings.root, name) or (settings.root / "plans" / f"{name}.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        # solo lo que se aparta de los valores por defecto: el fichero queda corto y legible
        data = {"schema_version": 1,
                **parsed.model_dump(by_alias=True, mode="json", exclude_defaults=True)}  # type: ignore[union-attr]
        target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return {"ok": True, "path": str(target.relative_to(settings.root)),
                "issues": [i.__dict__ for i in issues]}

    # --- runs ------------------------------------------------------------------------------
    @app.get("/api/runs")
    async def runs(status: str | None = None, plan: str | None = None, q: str | None = None,
                   limit: int = Query(100, le=500)) -> list[dict[str, Any]]:
        store = eng().store
        result = []
        for run in store.list_runs(status=status, limit=limit):
            if run["parent_run_id"] is not None:
                continue
            if plan and run["plan_name"] != plan:
                continue
            haystack = run["id"] + (run.get("title") or "") + json.dumps(run["inputs"], ensure_ascii=False)
            if q and q.lower() not in haystack.lower():
                continue
            result.append(views.run_summary(eng(), run))
        return result

    @app.post("/api/runs", status_code=201)
    async def create_run(body: RunCreate) -> dict[str, Any]:
        try:
            run_id = eng().create_run_from_name(body.plan, body.inputs, title=body.title)
        except PlanInvalid as exc:
            raise HTTPException(422, {"message": str(exc), "issues": [i.__dict__ for i in exc.issues]}) from exc
        wake()
        return {"id": run_id}

    @app.get("/api/runs/{run_id}")
    async def run(run_id: str) -> dict[str, Any]:
        detail = views.run_detail(eng(), run_id)
        if detail is None:
            raise HTTPException(404, f"no existe el run {run_id}")
        return detail

    # --- mantenimiento -----------------------------------------------------------------------
    def _cleanup_args(older_than_days: int, statuses: str) -> tuple[int, tuple[str, ...]]:
        chosen = tuple(x for x in statuses.split(",") if x in ("succeeded", "failed", "cancelled"))
        if not chosen:
            raise HTTPException(422, "indica al menos un estado: succeeded, failed o cancelled")
        return max(older_than_days, 0), chosen

    @app.get("/api/maintenance/runs")
    async def cleanup_preview(older_than_days: int = 0, statuses: str = "succeeded,failed,cancelled") -> dict[str, Any]:
        days, chosen = _cleanup_args(older_than_days, statuses)
        found = eng().cleanup_candidates(days, chosen)
        return {"count": len(found), "runs_total": sum(len(r["family"]) for r in found),
                "runs": [{k: v for k, v in r.items() if k != "family"} for r in found[:50]]}

    @app.post("/api/maintenance/runs/cleanup")
    async def cleanup(body: Cleanup) -> dict[str, Any]:
        days, chosen = _cleanup_args(body.older_than_days, ",".join(body.statuses))
        found = eng().cleanup_candidates(days, chosen)
        result = eng().delete_runs([r["id"] for r in found])
        return {"deleted_roots": len(found), **result}

    @app.delete("/api/runs/{run_id}")
    async def delete_run(run_id: str) -> dict[str, Any]:
        if eng().store.get_run(run_id) is None:
            raise HTTPException(404, "run no encontrado")
        candidate = next((r for r in eng().cleanup_candidates() if r["id"] == run_id), None)
        if candidate is None:
            raise HTTPException(409, "solo se pueden borrar runs raíz terminados, sin aprobaciones pendientes")
        return eng().delete_runs([run_id])

    @app.get("/api/runs/{run_id}/timeline")
    async def timeline(run_id: str) -> list[dict[str, Any]]:
        store = eng().store
        if store.get_run(run_id) is None:
            raise HTTPException(404, "run no encontrado")
        attempts = sorted(((a["id"], node_id, a) for node_id in store.all_nodes(run_id)
                           for a in store.attempts(run_id, node_id)), key=lambda item: item[0])
        return [{"node_id": node_id, "attempt": a["attempt"], "status": a["status"], "event": a["event"],
                 "started_at": a["started_at"], "finished_at": a["finished_at"], "cost_usd": a["cost_usd"]}
                for _, node_id, a in attempts]

    @app.post("/api/runs/{run_id}/cancel")
    async def cancel(run_id: str) -> dict[str, Any]:
        if eng().store.get_run(run_id) is None:
            raise HTTPException(404, f"no existe el run {run_id}")
        eng().cancel_run(run_id)
        wake()
        return {"ok": True}

    @app.post("/api/runs/{run_id}/nodes/{node_id}/retry")
    async def retry(run_id: str, node_id: str) -> dict[str, Any]:
        try:
            eng().retry_node(run_id, node_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        wake()
        return {"ok": True}

    @app.get("/api/runs/{run_id}/nodes/{node_id}")
    async def node(run_id: str, node_id: str) -> dict[str, Any]:
        detail = views.node_detail(eng(), run_id, node_id)
        if detail is None:
            raise HTTPException(404, "nodo o run desconocido")
        return detail

    @app.get("/api/runs/{run_id}/nodes/{node_id}/attempts/{attempt}/files")
    async def files(run_id: str, node_id: str, attempt: int) -> list[dict[str, Any]]:
        return views.attempt_files(eng(), run_id, node_id, attempt)

    @app.get("/api/runs/{run_id}/nodes/{node_id}/attempts/{attempt}/files/{name}")
    async def file(run_id: str, node_id: str, attempt: int, name: str) -> dict[str, Any]:
        if not SAFE_NAME.match(name):
            raise HTTPException(400, "nombre de fichero no válido")
        path = eng().attempt_dir(run_id, node_id, attempt) / name
        if not path.is_file():
            raise HTTPException(404, "no existe el fichero")
        return {"name": name, "text": views.read_text(path)}

    @app.get("/api/invocations/{invocation_id}")
    async def invocation(invocation_id: int) -> dict[str, Any]:
        row = eng().store.conn.execute("SELECT * FROM invocations WHERE id=?", (invocation_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "no existe la invocación")
        data = eng().store._decode("invocations", row) or {}
        return {**{k: v for k, v in data.items() if k != "result_raw"},
                "prompt": views.read_text(data.get("prompt_path")), "stdout": views.read_text(data.get("stdout_path")),
                "result": data.get("result_raw") or ""}

    # --- aprobaciones ----------------------------------------------------------------------
    @app.get("/api/approvals")
    async def approvals(status: str | None = "pending") -> list[dict[str, Any]]:
        store = eng().store
        rows = store.list_approvals(None if status in (None, "all") else status)
        for row in rows:
            run = store.get_run(row["run_id"])
            row["plan_name"] = run["plan_name"] if run else ""
            row["run_title"] = run.get("title") if run else None
            row["inputs"] = run["inputs"] if run else {}
        return rows

    @app.get("/api/approvals/{approval_id}")
    async def approval(approval_id: int) -> dict[str, Any]:
        row = eng().store.get_approval(approval_id)
        if row is None:
            raise HTTPException(404, "no existe la aprobación")
        run = eng().store.get_run(row["run_id"])
        return {**row, "plan_name": run["plan_name"] if run else "", "run_title": run.get("title") if run else None,
                "inputs": run["inputs"] if run else {}}

    @app.post("/api/approvals/{approval_id}/decision")
    async def decide(approval_id: int, body: Decision) -> dict[str, Any]:
        store = eng().store
        approval = store.get_approval(approval_id)
        if approval is None:
            raise HTTPException(404, "no existe la aprobación")
        # el nonce solo lo conoce quien puede leer la API: una página ajena no puede aprobar a ciegas
        if not secrets.compare_digest(body.nonce, approval.get("nonce") or ""):
            raise HTTPException(403, "código de aprobación no válido")
        overrides = _check_overrides(eng(), approval, body.overrides) if body.decision == "approved" else {}
        if not store.decide_approval(approval_id, body.decision, comment=body.comment, decided_by=getpass.getuser(),
                                     overrides=overrides):
            raise HTTPException(409, "la aprobación ya no está pendiente")
        wake()
        return {"ok": True}

    # --- eventos en tiempo real ------------------------------------------------------------
    @app.get("/api/events")
    async def events(request: Request, after: int | None = None, once: bool = False) -> StreamingResponse:
        store = eng().store
        header = request.headers.get("last-event-id")
        last = after if after is not None else int(header) if header and header.isdigit() else store.last_event_id()

        async def stream():
            nonlocal last
            yield "retry: 2000\n\n"
            idle = 0
            while not await request.is_disconnected():
                rows = store.events_after(last)
                for row in rows:
                    last = row["id"]
                    payload = {"id": row["id"], "run_id": row["run_id"], "node_id": row["node_id"],
                               "from": row["from_status"], "to": row["to_status"], "event": row["event"],
                               "reason": row["reason"], "at": row["at"]}
                    yield f"id: {row['id']}\nevent: transition\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                if once:
                    return
                if not rows:
                    idle += 1
                    if idle % 20 == 0:
                        yield ": keep-alive\n\n"
                    await asyncio.sleep(0.4)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # --- interfaz estática -----------------------------------------------------------------
    dist = Path(settings.root) / "web" / "dist"
    if serve_ui and (dist / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            candidate = dist / path
            if path and candidate.is_file() and dist in candidate.resolve().parents:
                return FileResponse(candidate)
            # los assets llevan hash en el nombre; el index debe revalidarse para ver cada nueva versión
            return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})

    return app
