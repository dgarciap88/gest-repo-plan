"""Proveedor Copilot con un CLI falso (formato no verificado con modelos reales)."""

import json

from gestplan.engine.runner import FakeRunner, ProcResult, RoutingRunner
from gestplan.plans.loader import parse_plan
from gestplan.plans.validator import validate_plan
from tests.helpers import build, drive, make_engine, make_settings
from tests.test_agents import APPROVED, DEV_OK, agent_plan, make_workspace

MODELS = {"cheap": "modelo-barato", "standard": "modelo-medio"}


class FakeCopilot:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, argv, cwd, stdin):
        if argv[1:] == ["--version"]:
            return ProcResult(0, "GitHub Copilot CLI 1.0.105.")
        self.calls.append({"argv": argv, "stdin": stdin})
        return ProcResult(0, "Listo:\n```json\n" + json.dumps(self.responses.pop(0)) + "\n```")


def test_copilot_agente_y_validador(tmp_path):
    ws = make_workspace(tmp_path)
    fake = FakeCopilot(DEV_OK, APPROVED)
    engine = make_engine(tmp_path, runner=RoutingRunner({"copilot": FakeRunner(fake)}),
                         copilot_path="copilot", copilot_models=MODELS)
    plan = agent_plan(ws, dev={"provider": "copilot"}, val={"provider": "copilot"})
    run_id = build(engine, plan, {"workspace": str(ws)})
    assert drive(engine, run_id) == "succeeded"

    dev, val = (c["argv"] for c in fake.calls)
    assert dev[dev.index("--model") + 1] == "modelo-barato" and "--session-id" in dev
    assert "write" in dev and "shell(git push)" in dev[dev.index("--deny-tool"):]
    assert "write" not in val  # el validador es de solo lectura
    assert engine.store.invocations(run_id)[0]["provider"] == "copilot"
    assert engine.store.latest_outputs(run_id, "dev")["suggested_commit_subject"].startswith("TASK-1")


def test_copilot_sin_modelo_configurado_no_valida(tmp_path):
    plan, _ = parse_plan(agent_plan(tmp_path, dev={"provider": "copilot"}))
    result = validate_plan(plan, tmp_path, make_settings(tmp_path))
    assert any(i.code == "copilot_model_missing" for i in result.errors)
