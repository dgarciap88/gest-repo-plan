"""Todos los planes y scripts del repositorio deben ser válidos."""

import dataclasses

import pytest

from gestplan.db import Store
from gestplan.engine.engine import Engine
from gestplan.executors import default_executors
from gestplan.executors.base import Outcome
from gestplan.nodes.flow import load_script_meta
from gestplan.plans.loader import load_plan
from gestplan.plans.validator import validate_plan
from gestplan.settings import Settings
from tests.conftest import ROOT
from tests.helpers import Scripted, drive

PLAN_FILES = sorted((ROOT / "plans").rglob("*.json"))


@pytest.mark.parametrize("path", PLAN_FILES, ids=lambda p: p.name)
def test_los_planes_del_repo_son_validos(path):
    plan, issues = load_plan(path)
    assert plan is not None, [i.format() for i in issues]
    result = validate_plan(plan, ROOT, Settings())
    assert result.ok, [i.format() for i in result.errors]
    assert plan.name == path.stem


@pytest.mark.parametrize("path", sorted((ROOT / "scripts").glob("*.py")), ids=lambda p: p.name)
def test_los_scripts_declaran_meta(path):
    assert load_script_meta(path).events


def test_task_items_construye_los_inputs_de_los_hijos():
    from importlib.util import module_from_spec, spec_from_file_location

    spec = spec_from_file_location("task_items", ROOT / "scripts" / "task_items.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)

    out = module.run({"task_ids": "TASK-001, TASK-002", "repo_url": "u", "base": ""}, {})
    assert out["event"] == "ok" and out["outputs"]["count"] == 2
    assert out["outputs"]["items"][1] == {"task_id": "TASK-002", "repo_url": "u", "base": "main"}
    assert module.run({"task_ids": [], "repo_url": "u"}, {})["event"] == "failed"


def test_las_tareas_referenciadas_existen():
    tasks = sorted(p.name for p in (ROOT / "tasks").glob("TASK-*.md"))
    assert {"TASK-001.md", "TASK-002.md"} <= set(tasks)


def test_implement_tasks_lanza_un_implement_task_por_tarea(tmp_path):
    """Fan-out real con los planes y scripts del repo; los hijos se quedan esperando en su primer nodo."""
    plan, _ = load_plan(ROOT / "plans" / "implement-tasks.json")
    settings = dataclasses.replace(Settings(), root=ROOT, data_dir=tmp_path / "data", poll_interval_s=0.01)
    executors = default_executors()
    executors["prepare_workspace"] = Scripted("prepare_workspace", lambda ctx: Outcome(waiting="external"))
    engine = Engine(Store(settings.db_path), settings, executors=executors)

    run_id = engine.create_run(plan, {"task_ids": "TASK-001,TASK-002", "repo_url": "u", "max_parallel": "1"})
    assert drive(engine, run_id) == "waiting"

    children = engine.store.children_of(run_id)
    assert len(children) == 2 and {c["plan_name"] for c in children} == {"implement-task"}
    assert {c["inputs"]["task_id"] for c in children} == {"TASK-001", "TASK-002"}
    assert {c["inputs"]["repo_url"] for c in children} == {"u"}
    # max_parallel=1: solo un hijo activo, el otro en cola
    assert sorted(c["status"] for c in children) == ["queued", "waiting"]
