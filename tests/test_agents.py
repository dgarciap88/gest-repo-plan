"""Agente y validador con un CLI de Claude falso (sin red ni coste)."""

import json
import subprocess

import pytest

from gestplan.engine.runner import FakeRunner, ProcResult, RoutingRunner
from tests.helpers import build, drive, make_engine


def make_workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    env = {"GIT_CONFIG_GLOBAL": str(tmp_path / "gc"), "GIT_CONFIG_NOSYSTEM": "1"}
    (tmp_path / "gc").write_text("[user]\n\tname = T\n\temail = t@e.com\n", encoding="utf-8")
    import os
    full = {**os.environ, **env}
    for args in (["init", "-b", "main"], ["add", "-A"]):
        subprocess.run(["git", *args], cwd=ws, check=True, capture_output=True, env=full)
    (ws / "base.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=ws, check=True, capture_output=True, env=full)
    subprocess.run(["git", "commit", "-m", "inicial"], cwd=ws, check=True, capture_output=True, env=full)
    (ws / "cambio.py").write_text("x = 1\n", encoding="utf-8")  # cambio sin commitear
    return ws


DEV_OK = {"summary": "hecho", "suggested_commit_subject": "TASK-1: Añadir la constante x",
          "suggested_commit_body": "porque sí", "suggested_pr_title": "[TASK-1] Constante x",
          "suggested_pr_body": "Contexto y cambios del PR", "assumptions": ["a1"], "open_questions": []}
APPROVED = {"verdict": "approved", "issues": [], "notes": "todo bien"}


def cli_result(payload, *, cost=0.02, session="sess-1", subtype="success", is_error=False):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return ProcResult(0, json.dumps({
        "result": text, "is_error": is_error, "session_id": session, "total_cost_usd": cost, "subtype": subtype,
        "usage": {"input_tokens": 3, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 10,
                  "output_tokens": 5}}))


class FakeClaude:
    """Responde con colas distintas para el agente y el validador (se distinguen por el prompt)."""

    def __init__(self, dev=(), val=()):
        self.dev, self.val = list(dev), list(val)
        self.calls = []

    def __call__(self, argv, cwd, stdin):
        if argv[1:] == ["--version"]:
            return ProcResult(0, "2.1.317 (Claude Code)")
        self.calls.append({"argv": argv, "stdin": stdin})
        queue = self.val if "## DATOS NO CONFIABLES:" in (stdin or "") else self.dev
        if (stdin or "").startswith("Tu respuesta anterior"):  # ronda de reparación: misma cola que la última
            queue = self.val if self.calls[-2]["stdin"] and "## DATOS NO CONFIABLES:" in self.calls[-2]["stdin"] else self.dev
        response = queue.pop(0)
        return response if isinstance(response, ProcResult) else cli_result(response)


def agent_plan(workspace, **node_overrides):
    return {
        "name": "agentes",
        "inputs": {"workspace": {"type": "string", "required": True}},
        "nodes": {
            "start": {"type": "run_command", "config": {"workspace": "${inputs.workspace}",
                                                         "command": ["python", "-V"]}},
            "dev": {"type": "ia_agent", "max_attempts": 4, "config": {
                "workspace": "${inputs.workspace}", "prompt": "Haz la tarea.\n\n{{feedback}}",
                "variables": {"feedback": "${nodes.val.outputs.feedback}"}, **node_overrides.get("dev", {})}},
            "val": {"type": "ia_agent_validator", "config": {
                "workspace": "${inputs.workspace}", "prompt": "Valida los cambios.", "max_retries": 1,
                **node_overrides.get("val", {})}},
        },
        "edges": [{"from": "start.ok", "to": "dev"}, {"from": "dev.done", "to": "val"},
                  {"from": "val.changes_requested", "to": "dev"}],
    }


def engine_with(tmp_path, claude, **kwargs):
    runner = RoutingRunner({"claude": FakeRunner(claude)})
    return make_engine(tmp_path, runner=runner, claude_path="claude", **kwargs)


def run_plan(tmp_path, claude, plan_overrides=None, **kwargs):
    ws = make_workspace(tmp_path)
    engine = engine_with(tmp_path, claude, **kwargs)
    run_id = build(engine, agent_plan(ws, **(plan_overrides or {})), {"workspace": str(ws)})
    return engine, run_id, drive(engine, run_id)


def test_agente_y_validador_aprueban(tmp_path):
    claude = FakeClaude(dev=[DEV_OK], val=[APPROVED])
    engine, run_id, status = run_plan(tmp_path, claude)
    assert status == "succeeded"

    dev = engine.store.latest_outputs(run_id, "dev")
    assert dev["suggested_commit_subject"].startswith("TASK-1") and dev["assumptions"] == ["a1"]
    assert "cambio.py" in dev["files"] and dev["files_changed"] == 1 and dev["session_id"] == "sess-1"
    assert engine.store.latest_outputs(run_id, "val")["verdict"] == "approved"

    first = claude.calls[0]
    assert first["argv"][first["argv"].index("--model") + 1] == "claude-haiku-5-5"
    assert first["argv"][first["argv"].index("--permission-mode") + 1] == "acceptEdits"
    assert "--dangerously-skip-permissions" not in first["argv"]
    assert "NO hagas commit" in first["stdin"] and "suggested_commit_subject" in first["stdin"]
    # el validador recibe el diff como dato no confiable y no puede editar
    second = claude.calls[1]
    assert "cambio.py" in second["stdin"] and "## DATOS NO CONFIABLES:" in second["stdin"]
    assert second["argv"][second["argv"].index("--permission-mode") + 1] == "plan"

    invocations = engine.store.invocations(run_id)
    assert [i["kind"] for i in invocations] == ["main", "main"]
    assert invocations[0]["cli_version"] == "2.1.317 (Claude Code)"
    assert invocations[0]["tokens_in"] == 113 and invocations[0]["cost_usd"] == pytest.approx(0.02)
    assert len(invocations[0]["sha256"]["prompt"]) == 64
    assert engine.store.total_cost(run_id) == pytest.approx(0.04)


def test_bucle_del_validador_con_feedback_y_limite(tmp_path):
    changes = {"verdict": "changes_requested", "notes": "",
               "issues": [{"severity": "high", "file": "cambio.py", "line": 1, "description": "falta test",
                           "suggestion": "añade un test"}]}
    claude = FakeClaude(dev=[DEV_OK, DEV_OK], val=[changes, {**changes, "issues": [
        {**changes["issues"][0], "description": "falta otro test"}]}])
    engine, run_id, status = run_plan(tmp_path, claude)

    assert status == "failed"  # max_retries_reached no tiene arista
    val = engine.store.get_node(run_id, "val")
    assert val["last_event"] == "max_retries_reached" and val["attempts"] == 2
    assert engine.store.get_node(run_id, "dev")["attempts"] == 2
    # la segunda ronda del agente recibe el feedback del validador en su prompt
    dev_prompts = [c["stdin"] for c in claude.calls if "## DATOS NO CONFIABLES:" not in c["stdin"]]
    assert "falta test" not in dev_prompts[0] and "falta test" in dev_prompts[1]
    assert "[high] cambio.py:1" in dev_prompts[1]


def test_sin_progreso_corta_antes_de_agotar_las_rondas(tmp_path):
    changes = {"verdict": "changes_requested", "notes": "",
               "issues": [{"severity": "medium", "file": "a.py", "description": "mismo problema"}]}
    claude = FakeClaude(dev=[DEV_OK, DEV_OK], val=[changes, changes])
    engine, run_id, status = run_plan(tmp_path, claude, {"val": {"max_retries": 5}})
    assert status == "failed"
    assert engine.store.get_node(run_id, "val")["last_event"] == "no_progress"


def test_incidencias_no_bloqueantes_aprueban_con_notas(tmp_path):
    low = {"verdict": "changes_requested", "notes": "ojo",
           "issues": [{"severity": "low", "description": "nombre mejorable"}]}
    engine, run_id, status = run_plan(tmp_path, FakeClaude(dev=[DEV_OK], val=[low]))
    assert status == "succeeded"
    out = engine.store.latest_outputs(run_id, "val")
    assert out["verdict"] == "approved" and out["issues"][0]["severity"] == "low"


def test_ronda_de_reparacion_cuando_la_respuesta_no_es_valida(tmp_path):
    claude = FakeClaude(dev=["no es json", DEV_OK], val=[APPROVED])
    engine, run_id, status = run_plan(tmp_path, claude)
    assert status == "succeeded"
    kinds = [i["kind"] for i in engine.store.invocations(run_id, "dev")]
    assert kinds == ["main", "repair"]
    repair = claude.calls[1]
    assert "--resume" in repair["argv"] and "JSON" in repair["stdin"]


def test_respuesta_invalida_tras_la_reparacion_falla(tmp_path):
    claude = FakeClaude(dev=["no es json", "tampoco"], val=[])
    engine, run_id, status = run_plan(tmp_path, claude)
    assert status == "failed"
    assert engine.store.get_node(run_id, "dev")["last_event"] == "failed"
    assert "JSON" in engine.store.attempts(run_id, "dev")[0]["error"]


def test_presupuesto_agotado(tmp_path):
    claude = FakeClaude(dev=[cli_result("", subtype="error_max_budget_usd", is_error=True, cost=0.5)])
    engine, run_id, status = run_plan(tmp_path, claude, {"dev": {"max_cost_usd": 0.5}})
    assert status == "failed"
    assert engine.store.get_node(run_id, "dev")["last_event"] == "budget_exceeded"
    assert "--max-budget-usd" in claude.calls[0]["argv"]


def test_timeout_del_cli(tmp_path):
    engine, run_id, status = run_plan(tmp_path, FakeClaude(dev=[ProcResult(None, "", "", timed_out=True)]))
    assert status == "failed"
    assert engine.store.get_node(run_id, "dev")["last_event"] == "timeout"


def test_inactividad_del_cli(tmp_path):
    engine, run_id, _ = run_plan(tmp_path, FakeClaude(dev=[ProcResult(None, "", "", stalled=True)]))
    assert engine.store.get_node(run_id, "dev")["last_event"] == "stalled"


def test_escalado_de_nivel_de_modelo_en_los_reintentos(tmp_path):
    changes = {"verdict": "changes_requested", "notes": "",
               "issues": [{"severity": "high", "description": "arregla"}]}
    claude = FakeClaude(dev=[DEV_OK, DEV_OK], val=[changes, APPROVED])
    run_plan(tmp_path, claude, {"dev": {"model_tier": ["cheap", "standard"]}, "val": {"max_retries": 2}})
    models = [c["argv"][c["argv"].index("--model") + 1] for c in claude.calls if "## DATOS NO CONFIABLES:" not in c["stdin"]]
    assert models == ["claude-haiku-5-5", "claude-sonnet-5-5"]


def test_sesion_continuada_entre_rondas(tmp_path):
    changes = {"verdict": "changes_requested", "notes": "",
               "issues": [{"severity": "high", "description": "arregla"}]}
    claude = FakeClaude(dev=[DEV_OK, DEV_OK], val=[changes, APPROVED])
    run_plan(tmp_path, claude, {"dev": {"session_mode": "continue"}, "val": {"max_retries": 2}})
    dev_calls = [c["argv"] for c in claude.calls if "## DATOS NO CONFIABLES:" not in c["stdin"]]
    assert "--session-id" in dev_calls[0] and "--resume" in dev_calls[1]
    assert dev_calls[0][dev_calls[0].index("--session-id") + 1] == dev_calls[1][dev_calls[1].index("--resume") + 1]
