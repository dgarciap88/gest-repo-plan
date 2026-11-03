"""Plan apply-skill: la skill va primero en el prompt, el agente de Copilot y la rama del ticket."""

import dataclasses
import json

import pytest

from gestplan.db import Store
from gestplan.engine.engine import Engine
from gestplan.engine.runner import FakeRunner, RoutingRunner
from gestplan.executors import default_executors
from gestplan.executors.base import Outcome
from gestplan.executors.prompts import build_prompt
from gestplan.plans.loader import load_plan, parse_plan
from gestplan.plans.validator import validate_plan
from gestplan.settings import Settings
from tests.conftest import ROOT
from tests.helpers import Scripted, build, dev_outcome, drive, make_engine, statuses
from tests.test_agents import APPROVED, DEV_OK, agent_plan, make_workspace
from tests.test_copilot import FakeCopilot

INPUTS = {"repo_url": "u", "jira_id": "ABC-123", "skill": "refactor", "agent": "revisor"}


def test_la_skill_es_lo_primero_del_prompt():
    from gestplan.executors.agents import DevResult

    prompt = build_prompt(language="es", task="Haz algo", data_blocks=[], result_model=DevResult, skill="refactor")
    assert prompt.startswith("/refactor\n\n# Reglas del sistema")
    assert not build_prompt(language="es", task="x", data_blocks=[], result_model=DevResult).startswith("/")


def test_copilot_recibe_skill_agente_y_carpeta_de_agentes(tmp_path):
    ws = make_workspace(tmp_path)
    fake = FakeCopilot(DEV_OK, APPROVED)
    home = tmp_path / "mi-copilot"
    seen = {}

    def spy(argv, cwd, stdin):
        seen.setdefault("calls", []).append(argv)
        return fake(argv, cwd, stdin)

    runner = FakeRunner(spy)
    runner_with_env = runner.run

    async def run_capturing_env(argv, **kwargs):
        seen["env"] = kwargs.get("env")
        return await runner_with_env(argv, **kwargs)

    runner.run = run_capturing_env
    engine = make_engine(tmp_path, runner=RoutingRunner({"copilot": runner}), copilot_path="copilot",
                         copilot_models={"cheap": "m"}, agents_home=home)
    plan = agent_plan(ws, dev={"provider": "copilot", "agent": "revisor", "skill": "refactor"},
                      val={"provider": "copilot"})
    run_id = build(engine, plan, {"workspace": str(ws)})
    assert drive(engine, run_id) == "succeeded"
    dev_argv = seen["calls"][0]
    assert dev_argv[dev_argv.index("--agent") + 1] == "revisor"
    assert seen["env"]["COPILOT_HOME"] == str(home)
    assert fake.calls[0]["stdin"].startswith("/refactor\n")
    assert not fake.calls[1]["stdin"].startswith("/")  # el validador no lleva skill


def test_aviso_si_la_skill_o_el_agente_no_estan_en_la_carpeta_de_usuario(tmp_path):
    home = tmp_path / "home"
    (home / "skills" / "refactor").mkdir(parents=True)
    (home / "skills" / "refactor" / "SKILL.md").write_text("# skill", encoding="utf-8")
    (home / "agents").mkdir()
    (home / "agents" / "revisor.agent.md").write_text("# agente", encoding="utf-8")

    def codes(skill, agent):
        plan, _ = parse_plan(agent_plan(tmp_path, dev={"skill": skill, "agent": agent}))
        settings = dataclasses.replace(Settings(), root=tmp_path, agents_home=home)
        return {i.code for i in validate_plan(plan, tmp_path, settings).issues}

    assert not codes("refactor", "revisor") & {"skill_not_found", "agent_not_found"}
    assert {"skill_not_found", "agent_not_found"} <= codes("otra", "otro")


@pytest.fixture
def flow(tmp_path):
    plan, issues = load_plan(ROOT / "plans" / "apply-skill.json")
    assert plan is not None, issues
    settings = dataclasses.replace(Settings(), root=ROOT, data_dir=tmp_path / "data", poll_interval_s=0.01)
    executors = default_executors()
    calls = []

    def scripted(name, outcome):
        def handler(ctx):
            calls.append((name, ctx.config))
            return outcome
        executors[name] = Scripted(name, handler)

    scripted("prepare_workspace", Outcome("ok", {"path": str(tmp_path), "local_branch": "x", "remote_branch": "ABC-123",
                                                 "branch_existed": False, "base_commit": "c", "push_ref": "r"}))
    scripted("ia_agent", dev_outcome())
    scripted("ia_agent_validator", Outcome("approved", {"verdict": "approved", "feedback": "", "issues": [],
                                                        "notes": "", "retries_used": 0, "cost_usd": 0}))
    scripted("secret_scan", Outcome("clean", {"count": 0, "findings": [], "summary": "", "skipped": []}))
    scripted("git.commit", Outcome("ok", {"commit_sha": "s", "subject": "ABC-123: x"}))
    scripted("git.push", Outcome("ok", {"pushed_sha": "s", "remote_branch": "ABC-123"}))
    scripted("git.create_pr", Outcome("ok", {"pr_number": 1, "pr_url": "http://pr"}))
    scripted("cleanup_workspace", Outcome("ok"))
    engine = Engine(Store(settings.db_path), settings, executors=executors)

    def go(inputs):
        run_id = engine.create_run(plan, {**INPUTS, **inputs})
        assert drive(engine, run_id) == "waiting"
        approval = engine.store.list_approvals("pending")[0]
        engine.store.decide_approval(approval["id"], "approved", decided_by="test")
        assert drive(engine, run_id) == "succeeded"
        return run_id, [name for name, _ in calls], dict(calls)

    return engine, go


def test_rama_distinta_abre_pr_contra_la_rama_de_origen(flow):
    engine, go = flow
    run_id, called, configs = go({"source_branch": "develop"})
    assert "git.create_pr" in called and statuses(engine, run_id)["pr"] == "succeeded"
    assert configs["git.create_pr"]["base_branch"] == "develop"
    assert configs["prepare_workspace"]["new_branch"] == "ABC-123"  # la rama de trabajo es el jira_id tal cual
    assert configs["prepare_workspace"]["base_branch"] == "develop"
    assert configs["ia_agent"]["skill"] == "refactor" and configs["ia_agent"]["provider"] == "copilot"
    assert configs["ia_agent"]["model"] == "auto"


def test_misma_rama_hace_push_y_no_abre_pr(flow):
    engine, go = flow
    run_id, called, _ = go({"source_branch": "ABC-123"})
    assert "git.push" in called and "git.create_pr" not in called
    states = statuses(engine, run_id)
    assert states["pr"] == "skipped" and states["cleanup"] == "succeeded"
    assert json.dumps(engine.store.latest_outputs(run_id, "same_branch")).count("same_branch") >= 1
