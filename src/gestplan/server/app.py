"""Servidor web: API REST + eventos en tiempo real (SSE) + interfaz estática (ver DESIGN.md sección 11)."""

from __future__ import annotations

import asyncio
import getpass
import json
import re
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from gestplan.catalog import render_markdown
from gestplan.db import Store
from gestplan.engine.engine import Engine, PlanInvalid
from gestplan.plans.loader import parse_plan
from gestplan.plans.validator import validate_plan
from gestplan.server import views
from gestplan.server.supervisor import Supervisor
from gestplan.settings import Settings

SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


class RunCreate(BaseModel):
    plan: str
    inputs: dict[str, Any] = Field(default_factory=dict)


class Decision(BaseModel):
    decision: Literal["approved", "rejected"]
    comment: str | None = None


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

    def eng() -> Engine:
        return app.state.engine

    def wake() -> None:
        app.state.supervisor.wake()

    # --- general ---------------------------------------------------------------------------
    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "version": "0.1.0", "root": str(settings.root.resolve())}

    @app.get("/api/stats")
    async def stats() -> dict[str, Any]:
        store = eng().store
        roots = [r for r in store.list_runs(limit=1000) if r["parent_run_id"] is None]
        counts: dict[str, int] = {}
        for run in roots:
            counts[run["status"]] = counts.get(run["status"], 0) + 1
        midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
        return {"runs": counts, "pending_approvals": len(store.list_approvals("pending")),
                "cost_today_usd": round(store.cost_since(midnight), 6),
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
            if q and q.lower() not in (run["id"] + json.dumps(run["inputs"], ensure_ascii=False)).lower():
                continue
            result.append(views.run_summary(eng(), run))
        return result

    @app.post("/api/runs", status_code=201)
    async def create_run(body: RunCreate) -> dict[str, Any]:
        try:
            run_id = eng().create_run_from_name(body.plan, body.inputs)
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
            row["inputs"] = run["inputs"] if run else {}
        return rows

    @app.get("/api/approvals/{approval_id}")
    async def approval(approval_id: int) -> dict[str, Any]:
        row = eng().store.get_approval(approval_id)
        if row is None:
            raise HTTPException(404, "no existe la aprobación")
        run = eng().store.get_run(row["run_id"])
        return {**row, "plan_name": run["plan_name"] if run else "", "inputs": run["inputs"] if run else {}}

    @app.post("/api/approvals/{approval_id}/decision")
    async def decide(approval_id: int, body: Decision) -> dict[str, Any]:
        store = eng().store
        if store.get_approval(approval_id) is None:
            raise HTTPException(404, "no existe la aprobación")
        if not store.decide_approval(approval_id, body.decision, comment=body.comment, decided_by=getpass.getuser()):
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
