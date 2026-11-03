"""API web con el motor real y ejecutores falsos (el supervisor corre dentro de la app)."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from gestplan.server.app import create_app
from tests.helpers import dev_outcome, flow_plan, make_engine
from tests.test_engine import standard_executors


def wait_for(client, url, predicate, timeout=15):
    deadline = time.time() + timeout
    data = None
    while time.time() < deadline:
        data = client.get(url).json()
        if predicate(data):
            return data
        time.sleep(0.05)
    raise AssertionError(f"timeout esperando {url}: {json.dumps(data)[:400]}")


@pytest.fixture
def client(tmp_path):
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "flow.json").write_text(json.dumps(flow_plan()), encoding="utf-8")
    engine = make_engine(tmp_path, standard_executors())
    with TestClient(create_app(engine=engine)) as client:
        yield client


def test_salud_catalogo_y_planes(client):
    assert client.get("/api/health").json()["status"] == "ok"
    types = {n["type"] for n in client.get("/api/catalog").json()["nodes"]}
    assert {"ia_agent", "git.push", "manual_approval"} <= types

    plans = client.get("/api/plans").json()
    flow = next(p for p in plans if p["name"] == "flow")
    assert flow["valid"] and flow["node_count"] == 8 and flow["errors"] == 0

    detail = client.get("/api/plans/flow").json()
    assert detail["valid"] and "dev" in detail["graph"]["nodes"]
    assert client.get("/api/plans/no-existe").status_code == 404


def test_ciclo_completo_por_la_api(client):
    created = client.post("/api/runs", json={"plan": "flow", "inputs": {}})
    assert created.status_code == 201
    run_id = created.json()["id"]

    detail = wait_for(client, f"/api/runs/{run_id}", lambda d: d["run"]["status"] == "waiting")
    assert detail["nodes"]["approval"]["status"] == "waiting_approval"
    assert detail["run"]["pending_approvals"] == 1 and detail["run"]["progress"]["total"] == 8

    # el grafo trae metadatos de nodo, aristas (con las de retorno marcadas) y posiciones
    graph = detail["plan"]
    assert graph["nodes"]["push"]["side_effect"] == "external"
    back = {(e["from"], e["to"]) for e in graph["edges"] if e["back"]}
    assert ("tests", "dev") in back and ("val", "dev") in back
    assert set(graph["layout"]) == set(graph["nodes"])
    assert graph["layout"]["ws"]["x"] < graph["layout"]["dev"]["x"] < graph["layout"]["tests"]["x"]

    approvals = client.get("/api/approvals").json()
    assert len(approvals) == 1 and approvals[0]["plan_name"] == "flow"
    approval_id = approvals[0]["id"]
    assert client.get(f"/api/approvals/{approval_id}").json()["covers"] == ["push", "pr"]

    decision = client.post(f"/api/approvals/{approval_id}/decision", json={"decision": "approved", "comment": "ok"})
    assert decision.status_code == 200
    assert client.post(f"/api/approvals/{approval_id}/decision", json={"decision": "rejected"}).status_code == 409

    done = wait_for(client, f"/api/runs/{run_id}", lambda d: d["run"]["status"] == "succeeded")
    assert done["nodes"]["pr"]["status"] == "succeeded"
    assert client.get("/api/approvals").json() == []

    listed = client.get("/api/runs").json()
    assert listed[0]["id"] == run_id and listed[0]["progress"]["succeeded"] >= 7
    assert client.get("/api/runs", params={"q": "zzz-nada"}).json() == []
    assert client.get("/api/runs", params={"status": "succeeded"}).json()[0]["id"] == run_id

    stats = client.get("/api/stats").json()
    assert stats["runs"]["succeeded"] == 1 and stats["pending_approvals"] == 0 and stats["cost_today_usd"] > 0


def test_detalle_de_nodo_logs_y_eventos(client):
    run_id = client.post("/api/runs", json={"plan": "flow"}).json()["id"]
    wait_for(client, f"/api/runs/{run_id}", lambda d: d["run"]["status"] == "waiting")

    node = client.get(f"/api/runs/{run_id}/nodes/dev").json()
    assert node["attempts"][0]["event"] == "done" and node["attempts"][0]["outputs"]["files"] == ["a.py"]
    assert client.get(f"/api/runs/{run_id}/nodes/zzz").status_code == 404

    # los eventos (SSE) reproducen las transiciones desde un id; 'once' termina al vaciar la cola
    stream = client.get("/api/events", params={"after": 0, "once": True})
    assert stream.headers["content-type"].startswith("text/event-stream")
    body = stream.text
    assert "event: transition" in body and '"to": "running"' in body and "approval_requested" in body
    assert "id: 1\n" in body


def test_errores_de_creacion_de_runs(client):
    assert client.post("/api/runs", json={"plan": "no-existe"}).status_code == 422
    bad = client.post("/api/runs", json={"plan": "flow", "inputs": {"otro": "1"}})
    assert bad.status_code == 422
    assert bad.json()["detail"]["issues"][0]["code"] == "unknown_input"


def test_cancelar_y_reactivar_por_la_api(client):
    run_id = client.post("/api/runs", json={"plan": "flow"}).json()["id"]
    wait_for(client, f"/api/runs/{run_id}", lambda d: d["run"]["status"] == "waiting")

    assert client.post(f"/api/runs/{run_id}/nodes/dev/retry").status_code == 409  # dev ya terminó bien
    assert client.post(f"/api/runs/{run_id}/cancel").status_code == 200
    cancelled = wait_for(client, f"/api/runs/{run_id}", lambda d: d["run"]["status"] == "cancelled")
    assert cancelled["nodes"]["approval"]["status"] == "cancelled"

    assert client.post(f"/api/runs/{run_id}/nodes/approval/retry").status_code == 200
    wait_for(client, f"/api/runs/{run_id}", lambda d: d["run"]["status"] == "waiting")
    assert client.post("/api/runs/zzz/cancel").status_code == 404
    assert client.get("/api/runs/zzz").status_code == 404


def test_validar_un_plan_por_la_api(client):
    ok = client.post("/api/plans/validate", json=flow_plan()).json()
    assert ok["ok"] is True
    broken = flow_plan()
    broken["nodes"]["approval"]["config"]["approves"] = ["pr"]
    result = client.post("/api/plans/validate", json=broken).json()
    assert result["ok"] is False and any(i["code"] == "missing_approval" for i in result["issues"])
    assert client.post("/api/plans/validate", json={"nodos": 1}).json()["ok"] is False


def test_ficheros_de_un_intento_con_nombre_seguro(client):
    run_id = client.post("/api/runs", json={"plan": "flow"}).json()["id"]
    wait_for(client, f"/api/runs/{run_id}", lambda d: d["run"]["status"] == "waiting")
    files = client.get(f"/api/runs/{run_id}/nodes/ws/attempts/1/files").json()
    assert isinstance(files, list)
    assert client.get(f"/api/runs/{run_id}/nodes/ws/attempts/1/files/..%2F..%2Fx").status_code in (400, 404)
    assert client.get(f"/api/runs/{run_id}/nodes/ws/attempts/1/files/no-existe.log").status_code == 404


def test_el_grafo_usa_el_efecto_real_de_los_scripts(tmp_path):
    from tests.test_cli_run import PLAN, PUBLICAR, SALUDO

    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "saludo.py").write_text(SALUDO, encoding="utf-8")
    (tmp_path / "scripts" / "publicar.py").write_text(PUBLICAR, encoding="utf-8")
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "demo.json").write_text(json.dumps(PLAN), encoding="utf-8")
    with TestClient(create_app(engine=make_engine(tmp_path))) as client:
        nodes = client.get("/api/plans/demo").json()["graph"]["nodes"]
        assert nodes["saludo"]["side_effect"] == "none"  # el META del script manda, no el valor conservador
        assert nodes["publicar"]["side_effect"] == "external"
        assert nodes["ok"]["events"] == ["approved", "rejected", "expired"]


def test_se_sirve_la_interfaz_estatica(tmp_path):
    dist = tmp_path / "web" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>gestplan-ui</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    engine = make_engine(tmp_path, standard_executors())
    with TestClient(create_app(engine=engine)) as client:
        assert "gestplan-ui" in client.get("/").text
        assert "gestplan-ui" in client.get("/runs/abc").text  # el fallback sirve el SPA
        assert client.get("/assets/app.js").text == "console.log(1)"
        assert client.get("/api/health").status_code == 200  # la API no queda tapada


def test_dev_outcome_helper_sigue_valido():
    assert dev_outcome().event == "done"
