import asyncio
import json

import pytest

from gestplan.engine.engine import PlanInvalid
from gestplan.executors.base import Outcome
from gestplan.plans.loader import parse_plan
from tests.helpers import Scripted, build, dev_outcome, drive, flow_plan, make_engine, statuses


def standard_executors(*, tests=None, validator=None, dev=None):
    """Ejecutores falsos para todo lo que toca el exterior; el flujo y las aprobaciones son reales."""
    return {
        "prepare_workspace": Scripted("prepare_workspace", lambda c: Outcome("ok", {
            "path": "/ws/repo", "local_branch": "b", "remote_branch": "feature/x",
            "branch_existed": False, "base_commit": "abc", "push_ref": "HEAD:refs/heads/feature/x"})),
        "ia_agent": Scripted("ia_agent", dev or (lambda c: dev_outcome())),
        "run_command": Scripted("run_command", tests or (lambda c: Outcome("ok", {
            "exit_code": 0, "passed": True, "output_tail": ""}))),
        "ia_agent_validator": Scripted("ia_agent_validator", validator or (lambda c: Outcome("approved", {
            "verdict": "approved", "feedback": "", "issues": [], "notes": "", "retries_used": 0,
            "cost_usd": 0.0}))),
        "git.commit": Scripted("git.commit", lambda c: Outcome("ok", {"commit_sha": "1", "subject": "s"})),
        "git.push": Scripted("git.push", lambda c: Outcome("ok", {"pushed_sha": "1", "remote_branch": "b"})),
        "git.create_pr": Scripted("git.create_pr", lambda c: Outcome("ok", {"pr_number": 1, "pr_url": "u"})),
    }


def test_flujo_completo_con_aprobacion_manual(tmp_path):
    engine = make_engine(tmp_path, standard_executors())
    run_id = build(engine, flow_plan())

    assert drive(engine, run_id) == "waiting"
    assert statuses(engine, run_id)["approval"] == "waiting_approval"
    assert statuses(engine, run_id)["push"] == "pending"
    approvals = engine.store.list_approvals("pending")
    assert len(approvals) == 1 and approvals[0]["covers"] == ["push", "pr"]

    assert engine.store.decide_approval(approvals[0]["id"], "approved", decided_by="test")
    assert drive(engine, run_id) == "succeeded"
    final = statuses(engine, run_id)
    assert final["push"] == final["pr"] == "succeeded"
    assert engine.store.total_cost(run_id) == pytest.approx(0.01)


def test_rechazo_de_la_aprobacion_sin_arista_hace_fallar_el_run(tmp_path):
    engine = make_engine(tmp_path, standard_executors())
    run_id = build(engine, flow_plan())
    drive(engine, run_id)
    approval = engine.store.list_approvals("pending")[0]
    engine.store.decide_approval(approval["id"], "rejected", comment="no", decided_by="test")
    assert drive(engine, run_id) == "failed"
    final = statuses(engine, run_id)
    assert final["approval"] == "failed" and final["push"] == "skipped"


def test_bucles_reintentan_con_feedback(tmp_path):
    state = {"tests": 0, "val": 0}

    def tests(ctx):
        state["tests"] += 1
        return Outcome("failed" if state["tests"] == 1 else "ok", {"exit_code": 0, "passed": True, "output_tail": ""})

    def validator(ctx):
        state["val"] += 1
        if state["val"] == 1:
            return Outcome("changes_requested", {"verdict": "changes_requested", "feedback": "- arregla X",
                                                  "issues": [], "notes": "", "retries_used": 1, "cost_usd": 0})
        return Outcome("approved", {"verdict": "approved", "feedback": "", "issues": [], "notes": "",
                                    "retries_used": 1, "cost_usd": 0})

    executors = standard_executors(tests=tests, validator=validator)
    engine = make_engine(tmp_path, executors)
    run_id = build(engine, flow_plan())
    assert drive(engine, run_id) == "waiting"

    dev = executors["ia_agent"]
    assert [attempt for _n, attempt, _c in dev.calls] == [1, 2, 3]
    # primera ronda: el feedback del validador aún no existe -> vacío; tercera: llega el feedback
    assert dev.calls[0][2]["variables"]["feedback"] == ""
    assert dev.calls[2][2]["variables"]["feedback"] == "- arregla X"
    assert engine.store.get_node(run_id, "val")["attempts"] == 2


def test_limite_de_intentos_del_bucle(tmp_path):
    executors = standard_executors(tests=lambda c: Outcome("failed", {"exit_code": 1, "passed": False,
                                                                        "output_tail": "rojo"}))
    engine = make_engine(tmp_path, executors)
    data = flow_plan()
    data["nodes"]["dev"]["max_attempts"] = 3
    run_id = build(engine, data)
    assert drive(engine, run_id) == "failed"
    dev = engine.store.get_node(run_id, "dev")
    assert dev["status"] == "failed" and dev["last_event"] == "max_attempts" and dev["attempts"] == 4
    assert statuses(engine, run_id)["val"] == "skipped"


def test_el_limite_derivado_del_validador(tmp_path):
    validator = lambda c: Outcome("changes_requested", {"verdict": "x", "feedback": "f", "issues": [],  # noqa: E731
                                                        "notes": "", "retries_used": 1, "cost_usd": 0})
    engine = make_engine(tmp_path, standard_executors(validator=validator))
    data = flow_plan()
    data["nodes"]["dev"].pop("max_attempts")
    data["nodes"]["val"]["config"]["max_retries"] = 1  # dev admite 2 ejecuciones
    run_id = build(engine, data)
    assert drive(engine, run_id) == "failed"
    assert engine.store.get_node(run_id, "dev")["last_event"] == "max_attempts"


def test_fallo_sin_manejar_detiene_el_run(tmp_path):
    executors = standard_executors()
    executors["prepare_workspace"] = Scripted("prepare_workspace", lambda c: Outcome("failed", error="sin red"))
    engine = make_engine(tmp_path, executors)
    run_id = build(engine, flow_plan())
    assert drive(engine, run_id) == "failed"
    final = statuses(engine, run_id)
    assert final["ws"] == "failed" and final["dev"] == "skipped"
    assert engine.store.get_run(run_id)["status"] == "failed"


def test_retry_de_un_nodo_fallido_continua_el_run(tmp_path):
    calls = {"n": 0}

    def ws(ctx):
        calls["n"] += 1
        if calls["n"] == 1:
            return Outcome("failed", error="red caída")
        return Outcome("ok", {"path": "/ws/repo", "local_branch": "b", "remote_branch": "feature/x",
                              "branch_existed": False, "base_commit": "a", "push_ref": "p"})

    executors = standard_executors()
    executors["prepare_workspace"] = Scripted("prepare_workspace", ws)
    engine = make_engine(tmp_path, executors)
    run_id = build(engine, flow_plan())
    assert drive(engine, run_id) == "failed"

    with pytest.raises(ValueError):
        engine.retry_node(run_id, "dev")  # un nodo skipped no se reactiva
    engine.retry_node(run_id, "ws")
    assert drive(engine, run_id) == "waiting"
    assert engine.store.get_node(run_id, "ws")["attempts"] == 2


def test_cancelar_y_reactivar(tmp_path):
    engine = make_engine(tmp_path, standard_executors())
    run_id = build(engine, flow_plan())
    drive(engine, run_id)  # queda esperando la aprobación
    engine.cancel_run(run_id)
    assert engine.store.get_run(run_id)["status"] == "cancelled"
    assert statuses(engine, run_id)["approval"] == "cancelled"
    assert engine.store.list_approvals("pending") == []

    engine.retry_node(run_id, "approval")
    assert drive(engine, run_id) == "waiting"
    approval = engine.store.list_approvals("pending")[0]
    engine.store.decide_approval(approval["id"], "approved", decided_by="test")
    assert drive(engine, run_id) == "succeeded"


def test_recuperacion_tras_una_caida(tmp_path):
    engine = make_engine(tmp_path, standard_executors())
    run_id = build(engine, flow_plan())
    # simula un proceso que murió con 'ws' en ejecución
    engine.store.start_attempt(run_id, "ws", 1, {})
    engine.store.set_node(run_id, "ws", "running", attempts=1)
    engine.store.set_run_status(run_id, "running")

    assert engine.recover() == 1
    assert statuses(engine, run_id)["ws"] == "interrupted"
    assert engine.store.attempts(run_id, "ws")[0]["status"] == "interrupted"
    engine.retry_node(run_id, "ws")
    assert drive(engine, run_id) == "waiting"


def test_ramas_con_route_y_nodos_saltados(tmp_path):
    data = {
        "name": "ramas",
        "inputs": {"kind": {"type": "string", "required": True}},
        "nodes": {
            "r": {"type": "route", "config": {"cases": [
                {"event": "bug", "when": {"path": "inputs.kind", "op": "matches", "value": "^bug"}}],
                "default": "otro"}},
            "a": {"type": "run_command", "config": {"workspace": "w", "command": ["python", "-V"]}},
            "b": {"type": "run_command", "config": {"workspace": "w", "command": ["python", "-V"]}},
            "fin": {"type": "run_command", "join": "any",
                    "config": {"workspace": "w", "command": ["python", "-V"]}},
        },
        "edges": [{"from": "r.bug", "to": "a"}, {"from": "r.otro", "to": "b"},
                  {"from": "a.ok", "to": "fin"}, {"from": "b.ok", "to": "fin"}],
    }
    engine = make_engine(tmp_path, standard_executors())
    run_id = build(engine, data, {"kind": "bug-123"})
    assert drive(engine, run_id) == "succeeded"
    final = statuses(engine, run_id)
    assert final["a"] == "succeeded" and final["b"] == "skipped" and final["fin"] == "succeeded"
    assert engine.store.latest_outputs(run_id, "r")["matched_event"] == "bug"


def test_union_all_espera_a_todas_las_ramas(tmp_path):
    data = {
        "name": "union",
        "nodes": {k: {"type": "run_command", "config": {"workspace": "w", "command": ["python", "-V"]}}
                  for k in ("a", "b", "c")},
        "edges": [{"from": "a.ok", "to": "c"}, {"from": "b.ok", "to": "c"}],
    }
    engine = make_engine(tmp_path, standard_executors())
    run_id = build(engine, data)
    assert drive(engine, run_id) == "succeeded"
    assert statuses(engine, run_id) == {"a": "succeeded", "b": "succeeded", "c": "succeeded"}


def test_timeout_del_nodo_emite_el_evento_timeout(tmp_path):
    async def slow(ctx):
        await asyncio.sleep(5)
        return Outcome("ok")

    executors = standard_executors()
    executors["prepare_workspace"] = Scripted("prepare_workspace", slow)
    engine = make_engine(tmp_path, executors)
    data = flow_plan()
    data["nodes"]["ws"]["timeout_s"] = 1
    run_id = build(engine, data)
    assert drive(engine, run_id) == "failed"
    ws = engine.store.get_node(run_id, "ws")
    assert ws["last_event"] == "timeout" and ws["unhandled"] == 1


def test_excepcion_en_el_ejecutor_es_un_fallo_controlado(tmp_path):
    def boom(ctx):
        raise RuntimeError("explotó")

    executors = standard_executors()
    executors["prepare_workspace"] = Scripted("prepare_workspace", boom)
    engine = make_engine(tmp_path, executors)
    run_id = build(engine, flow_plan())
    assert drive(engine, run_id) == "failed"
    assert "explotó" in engine.store.attempts(run_id, "ws")[0]["error"]


def test_referencia_sin_resolver_falla_el_nodo(tmp_path):
    executors = standard_executors()
    executors["prepare_workspace"] = Scripted("prepare_workspace", lambda c: Outcome("ok", {"path": "p"}))
    engine = make_engine(tmp_path, executors)
    run_id = build(engine, flow_plan())
    assert drive(engine, run_id) == "waiting"
    approval = engine.store.list_approvals("pending")[0]
    engine.store.decide_approval(approval["id"], "approved", decided_by="test")
    # push usa ${nodes.ws.outputs.remote_branch}, que el ejecutor falso de ws no produjo
    assert drive(engine, run_id) == "failed"
    assert statuses(engine, run_id)["dev"] == "succeeded"
    assert "remote_branch" in engine.store.attempts(run_id, "push")[0]["error"]


def test_run_invalido_no_se_crea(tmp_path):
    engine = make_engine(tmp_path, standard_executors())
    data = flow_plan()
    data["nodes"]["approval"]["config"]["approves"] = ["pr"]
    plan, _ = parse_plan(data)
    with pytest.raises(PlanInvalid) as error:
        engine.create_run(plan, {})
    assert any(i.code == "missing_approval" for i in error.value.issues)


def test_estado_persistido_y_auditoria_de_transiciones(tmp_path):
    engine = make_engine(tmp_path, standard_executors())
    run_id = build(engine, flow_plan())
    drive(engine, run_id)
    transitions = engine.store.transitions(run_id)
    assert transitions[0]["to_status"] == "queued"
    assert any(t["node_id"] == "dev" and t["to_status"] == "running" for t in transitions)
    attempt = engine.store.attempts(run_id, "dev")[0]
    assert attempt["event"] == "done" and attempt["outputs"]["files"] == ["a.py"]
    assert json.dumps(attempt["resolved_config"])  # la config resuelta queda guardada


CHILD_PLAN = {
    "name": "child",
    "inputs": {"n": {"type": "string", "required": True}},
    "nodes": {"step": {"type": "run_command", "config": {"workspace": "w", "command": ["python", "-V"]}}},
    "edges": [],
}


def parent_plan(max_parallel=2, items=3):
    return {
        "name": "parent",
        "nodes": {
            "launch": {"type": "launch_plans", "config": {
                "plan": "child", "items": [{"n": str(i)} for i in range(items)], "max_parallel": max_parallel}},
            "wait": {"type": "wait_plan", "config": {"launched_by": "launch"}},
            "fin": {"type": "run_command", "config": {"workspace": "w", "command": ["python", "-V"]}},
        },
        "edges": [{"from": "launch.ok", "to": "wait"}, {"from": "wait.all_ok", "to": "fin"},
                  {"from": "wait.some_failed", "to": "fin"}],
    }


def write_child(tmp_path):
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "child.json").write_text(json.dumps(CHILD_PLAN), encoding="utf-8")


def test_launch_y_wait_de_planes_hijos(tmp_path):
    write_child(tmp_path)
    engine = make_engine(tmp_path, standard_executors())
    run_id = build(engine, parent_plan())
    assert drive(engine, run_id) == "succeeded"
    children = engine.store.children_of(run_id)
    assert len(children) == 3 and all(c["status"] == "succeeded" for c in children)
    assert engine.store.latest_outputs(run_id, "wait")["failed_count"] == 0
    assert engine.store.latest_outputs(run_id, "launch")["count"] == 3


def test_limite_de_hijos_simultaneos(tmp_path):
    write_child(tmp_path)
    active = {"now": 0, "max": 0}

    async def step(ctx):
        active["now"] += 1
        active["max"] = max(active["max"], active["now"])
        await asyncio.sleep(0.05)
        active["now"] -= 1
        return Outcome("ok", {"exit_code": 0, "passed": True, "output_tail": ""})

    executors = standard_executors()
    executors["run_command"] = Scripted("run_command", step)
    engine = make_engine(tmp_path, executors)
    run_id = build(engine, parent_plan(max_parallel=1, items=3))
    assert drive(engine, run_id) == "succeeded"
    assert active["max"] == 1


def test_un_hijo_fallido_hace_some_failed(tmp_path):
    write_child(tmp_path)
    count = {"n": 0}

    def step(ctx):
        count["n"] += 1
        # los hijos tienen 'w' como workspace; el nodo 'fin' del padre también: falla solo el segundo hijo
        failing = ctx.run["plan_name"] == "child" and ctx.run["inputs"]["n"] == "1"
        return Outcome("failed" if failing else "ok", {"exit_code": 1 if failing else 0, "passed": not failing,
                                                        "output_tail": ""})

    executors = standard_executors()
    executors["run_command"] = Scripted("run_command", step)
    engine = make_engine(tmp_path, executors)
    run_id = build(engine, parent_plan())
    assert drive(engine, run_id) == "succeeded"  # some_failed está conectado a 'fin'
    out = engine.store.latest_outputs(run_id, "wait")
    assert out["failed_count"] == 1


def test_cancelar_el_padre_cancela_los_hijos_y_se_reactivan(tmp_path):
    write_child(tmp_path)

    def hold(ctx):
        return Outcome(waiting="external") if ctx.run["plan_name"] == "child" else Outcome("ok", {
            "exit_code": 0, "passed": True, "output_tail": ""})

    executors = standard_executors()
    executors["run_command"] = Scripted("run_command", hold)
    engine = make_engine(tmp_path, executors)
    run_id = build(engine, parent_plan(items=2))
    assert drive(engine, run_id) == "waiting"

    engine.cancel_run(run_id)
    children = engine.store.children_of(run_id)
    assert {c["status"] for c in children} == {"cancelled"}
    assert statuses(engine, children[0]["id"])["step"] == "cancelled"

    # reactivar la espera reactiva los hijos cancelados; no se crean runs nuevos
    engine.retry_node(run_id, "wait")
    engine.executors["run_command"] = Scripted("run_command", lambda c: Outcome("ok", {
        "exit_code": 0, "passed": True, "output_tail": ""}))
    assert drive(engine, run_id) == "succeeded"
    children = engine.store.children_of(run_id)
    assert len(children) == 2 and {c["status"] for c in children} == {"succeeded"}


def test_relanzar_launch_plans_reutiliza_los_hijos(tmp_path):
    write_child(tmp_path)
    engine = make_engine(tmp_path, standard_executors())
    run_id = build(engine, parent_plan(items=2))
    assert drive(engine, run_id) == "succeeded"
    first = [c["id"] for c in engine.store.children_of(run_id)]

    # un segundo intento del mismo nodo (misma clave de idempotencia) no crea runs duplicados
    ctx_run = engine.store.get_run(run_id)
    plan = engine._state(ctx_run).plan
    config = {"plan": "child", "items": [{"n": "0"}, {"n": "1"}], "max_parallel": 2}
    from gestplan.executors.base import ExecContext
    ctx = ExecContext(engine, ctx_run, plan, "launch", 2, config, tmp_path / "x")
    outcome = asyncio.run(engine.executors["launch_plans"].run(ctx))
    assert outcome.outputs["run_ids"] == first
