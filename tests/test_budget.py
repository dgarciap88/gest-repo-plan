"""Presupuestos de IA por run (con sus hijos) y por día."""

import json

from gestplan.plans.loader import parse_plan
from gestplan.plans.validator import validate_plan
from gestplan.settings import Settings
from tests.helpers import build, dev_outcome, drive, flow_plan, make_engine, make_settings
from tests.test_api import local_client
from tests.test_engine import standard_executors


def run_flow(tmp_path, **settings):
    engine = make_engine(tmp_path, standard_executors(), **settings)
    run_id = build(engine, flow_plan())
    drive(engine, run_id)
    return engine, run_id


def test_sin_presupuesto_no_se_limita_nada(tmp_path):
    engine, run_id = run_flow(tmp_path)
    assert engine.store.get_node(run_id, "val")["last_event"] == "approved"


def test_el_presupuesto_del_run_impide_lanzar_otro_agente(tmp_path):
    engine, run_id = run_flow(tmp_path, run_budget_usd=0.005)  # el agente cuesta 0,01: se agota tras el primero
    assert engine.store.get_node(run_id, "dev")["last_event"] == "done"
    val = engine.store.get_node(run_id, "val")
    assert val["last_event"] == "budget_exceeded"  # el validador ni llega a lanzarse
    attempt = engine.store.attempts(run_id, "val")[-1]
    assert "presupuesto del run" in attempt["error"] and "0.0100" in attempt["error"]


def test_el_presupuesto_diario_cuenta_todos_los_runs(tmp_path):
    engine, first = run_flow(tmp_path, daily_budget_usd=0.015)
    assert engine.store.get_node(first, "val")["last_event"] == "approved"  # 0,01 gastado: aún cabe
    second = build(engine, flow_plan())
    drive(engine, second)
    assert engine.store.get_node(second, "dev")["last_event"] == "done"  # 0,01 < 0,015 al empezar
    third = build(engine, flow_plan())
    drive(engine, third)
    assert engine.store.get_node(third, "dev")["last_event"] == "budget_exceeded"  # ya van 0,02 hoy
    assert "presupuesto diario" in engine.store.attempts(third, "dev")[-1]["error"]


def test_el_plan_puede_fijar_un_presupuesto_mas_bajo(tmp_path):
    engine = make_engine(tmp_path, standard_executors(), run_budget_usd=5.0)
    data = flow_plan(settings={"max_cost_usd": 0.005})
    run_id = build(engine, data)
    drive(engine, run_id)
    assert engine.store.get_node(run_id, "val")["last_event"] == "budget_exceeded"


def test_un_plan_no_puede_superar_el_tope_global(tmp_path):
    plan, _ = parse_plan(flow_plan(settings={"max_cost_usd": 10}))
    result = validate_plan(plan, tmp_path, make_settings(tmp_path, run_budget_usd=2.0))
    assert any(i.code == "budget_above_cap" for i in result.errors)
    sin_tope = validate_plan(plan, tmp_path, Settings(root=tmp_path))
    assert not any(i.code == "budget_above_cap" for i in sin_tope.issues)  # sin tope global no se compara


def test_las_estadisticas_publican_los_presupuestos(tmp_path):
    from gestplan.server.app import create_app

    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "flow.json").write_text(json.dumps(flow_plan()), encoding="utf-8")
    engine = make_engine(tmp_path, standard_executors(), daily_budget_usd=3.0, run_budget_usd=1.0)
    with local_client(create_app(engine=engine)) as client:
        stats = client.get("/api/stats").json()
    assert stats["daily_budget_usd"] == 3.0 and stats["run_budget_usd"] == 1.0 and dev_outcome().cost_usd == 0.01
