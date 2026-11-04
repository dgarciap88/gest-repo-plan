"""Artefactos: lo que un run crea fuera de gestplan (PR, ticket, página, rama...) queda registrado y se puede ver."""

import asyncio
import json
import sqlite3

from gestplan.catalog import build_catalog, render_markdown
from gestplan.db import MIGRATIONS, SCHEMA, Store
from gestplan.engine.http import FakeHttp
from gestplan.executors.base import Artifact, Outcome
from gestplan.executors.git import branch_url
from gestplan.nodes.registry import get_node_type
from gestplan.plans.loader import parse_plan
from gestplan.server.app import create_app
from gestplan.settings import Settings
from tests.helpers import Scripted, build, drive, make_engine
from tests.test_api import local_client
from tests.test_atlassian_more import FakeProject, FakeWiki, issue_plan, page_plan, settings_engine
from tests.test_engine import CHILD_PLAN, parent_plan, standard_executors
from tests.test_git_flow import approve, git_env, remote, setup, writes_file  # noqa: F401
from tests.test_integrations import approve_all


def kinds(engine, run_id):
    return [(a["kind"], a["ref"]) for a in engine.store.artifacts_of([run_id])]


def test_un_ticket_creado_es_un_artefacto_y_repetir_el_nodo_no_lo_duplica(tmp_path):
    engine = settings_engine(tmp_path, FakeHttp(FakeProject()))
    run_id = build(engine, issue_plan())
    drive(engine, run_id)
    approve_all(engine, run_id)
    drive(engine, run_id)
    row = engine.store.artifacts_of([run_id])[0]
    assert (row["kind"], row["ref"], row["node_id"]) == ("issue", "KAN-1", "nuevo")
    assert row["title"] == "KAN-1: Revisar logs" and row["url"] == "https://acme.atlassian.net/browse/KAN-1"
    assert row["plan_name"] == "issue"

    run = engine.store.get_run(run_id)
    plan, _ = parse_plan(run["plan_snapshot"])
    ctx = engine._exec_context(run, plan, "nuevo", 99, engine.prepare_config(run, plan, "nuevo"))
    again = asyncio.run(engine.executors["jira.create_issue"].run(ctx))
    assert again.event == "already_done" and again.artifacts[0].ref == "KAN-1"  # el reintento también lo informa
    engine._record_artifacts(run, "nuevo", None, None, again)  # sin tipo no declara nada: se ignora
    assert kinds(engine, run_id) == [("issue", "KAN-1")]
    engine._record_artifacts(run, "nuevo", get_node_type("jira.create_issue"), None, again)
    assert kinds(engine, run_id) == [("issue", "KAN-1")]  # tampoco se registra dos veces


def test_una_pagina_actualizada_es_un_artefacto(tmp_path):
    engine = settings_engine(tmp_path, FakeHttp(FakeWiki()))
    run_id = build(engine, page_plan(title="Informe final"))
    drive(engine, run_id)
    approve_all(engine, run_id)
    drive(engine, run_id)
    row = engine.store.artifacts_of([run_id])[0]
    assert row["kind"] == "page" and row["ref"] == "77" and row["title"] == "Informe final (actualizada, v4)"


def test_rama_y_pr_del_flujo_git(tmp_path, remote):  # noqa: F811
    engine, run_id, _gh = setup(tmp_path, remote, writes_file())
    drive(engine, run_id)
    assert engine.store.artifacts_of([run_id]) == []  # nada se ha creado fuera antes de aprobar
    approve(engine)
    assert drive(engine, run_id) == "succeeded"
    branch, pr = engine.store.artifacts_of([run_id])
    assert (branch["kind"], branch["ref"], branch["title"]) == ("branch", "feature/x", "Rama feature/x")
    assert branch["url"] == ""  # el remoto de prueba no es de GitHub: no hay enlace que inventar
    assert (pr["kind"], pr["ref"], pr["url"]) == ("pr", "7", "https://github.com/o/r/pull/7")
    assert pr["title"].startswith("PR #7: ")


def test_enlace_de_rama_solo_para_remotos_de_github():
    assert branch_url("https://github.com/o/r.git", "KAN-5") == "https://github.com/o/r/tree/KAN-5"
    assert branch_url("git@github.com:o/r.git\n", "feature/x") == "https://github.com/o/r/tree/feature/x"
    assert branch_url("https://tok@github.com/o/r", "a") == "https://github.com/o/r/tree/a"
    assert branch_url("/tmp/remote.git", "a") == "" and branch_url("https://gitlab.com/o/r.git", "a") == ""


def test_un_artefacto_que_el_tipo_no_declara_se_ignora(tmp_path):
    executors = standard_executors()
    executors["run_command"] = Scripted("run_command", lambda c: Outcome("ok", {
        "exit_code": 0, "passed": True, "output_tail": ""}, artifacts=[Artifact("pr", "PR #1", "u", "1")]))
    engine = make_engine(tmp_path, executors)
    run_id = build(engine, {"name": "x", "nodes": {"n": {"type": "run_command", "config": {
        "workspace": "w", "command": ["python", "-V"]}}}, "edges": []})
    assert drive(engine, run_id) == "succeeded"
    assert engine.store.artifacts_of([run_id]) == []


def test_los_artefactos_sobreviven_al_borrado_del_run(tmp_path):
    engine = settings_engine(tmp_path, FakeHttp(FakeProject()))
    run_id = build(engine, issue_plan(), title="Mi lote")
    drive(engine, run_id)
    approve_all(engine, run_id)
    drive(engine, run_id)
    engine.store.delete_runs([run_id])
    rows = engine.store.list_artifacts()
    assert [(r["ref"], r["run_id"], r["run_title"]) for r in rows] == [("KAN-1", run_id, "Mi lote")]


def test_migracion_desde_el_esquema_2(tmp_path):
    path = tmp_path / "viejo.db"
    conn = sqlite3.connect(path, isolation_level=None)
    for statement in filter(None, (s.strip() for s in SCHEMA.split(";"))):
        conn.execute(statement)
    for statement in MIGRATIONS[2]:
        conn.execute(statement)
    conn.execute("PRAGMA user_version=2")
    conn.close()
    store = Store(path)
    assert store.conn.execute("PRAGMA user_version").fetchone()[0] == 3
    assert store.add_artifact("r", "n", 1, "pr", "t", "u", "1", "plan", None) is True
    assert store.add_artifact("r", "n", 2, "pr", "otro", "u", "1", "plan", None) is False  # misma referencia


def test_el_catalogo_documenta_lo_que_cada_nodo_deja_creado():
    catalog = build_catalog(Settings())
    nodes = {n["type"]: n for n in catalog["nodes"]}
    declared = {t: [a["kind"] for a in n["artifacts"]] for t, n in nodes.items() if n["artifacts"]}
    assert declared == {
        "git.push": ["branch"], "git.create_pr": ["pr"], "github.merge_pr": ["merge"], "jira.comment": ["comment"],
        "jira.create_subtask": ["subtask"], "jira.create_issue": ["issue"], "confluence.create_page": ["page"],
        "confluence.update_page": ["page"]}
    text = render_markdown(catalog)
    assert "Artefactos externos que deja creados" in text and "- `pr`: Pull request abierto" in text


def test_api_de_artefactos_por_run_con_hijos_y_global(tmp_path):
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "child.json").write_text(json.dumps(CHILD_PLAN), encoding="utf-8")
    engine = make_engine(tmp_path, standard_executors())
    run_id = build(engine, parent_plan(items=2), title="Lote")
    assert drive(engine, run_id) == "succeeded"
    child = sorted(c["id"] for c in engine.store.children_of(run_id))[0]
    engine.store.add_artifact(run_id, "launch", 1, "page", "Página padre", "http://x/p", "1", "parent", "Lote")
    engine.store.add_artifact(child, "step", 1, "pr", "PR #9: hijo", "http://x/9", "9", "child", "Lote · 1")
    with local_client(create_app(engine=engine)) as client:
        mine = client.get(f"/api/runs/{run_id}/artifacts").json()
        assert [(a["kind"], a["from_child"]) for a in mine] == [("page", False), ("pr", True)]
        assert client.get(f"/api/runs/{child}/artifacts").json()[0]["from_child"] is False
        assert client.get("/api/runs/no-existe/artifacts").status_code == 404
        every = client.get("/api/artifacts").json()
        assert [a["ref"] for a in every] == ["9", "1"]  # los más recientes primero
        assert all(a["run_exists"] for a in every)
        assert [a["ref"] for a in client.get("/api/artifacts?kind=pr").json()] == ["9"]
        assert [a["ref"] for a in client.get("/api/artifacts?q=padre").json()] == ["1"]
        engine.store.delete_runs([child])
        gone = client.get("/api/artifacts?kind=pr").json()[0]
        assert gone["run_exists"] is False and gone["run_title"] == "Lote · 1"


def test_backfill_reconstruye_los_artefactos_de_runs_anteriores(tmp_path):
    from gestplan.artifacts import backfill_artifacts

    engine = settings_engine(tmp_path, FakeHttp(FakeProject()))
    run_id = build(engine, issue_plan())
    drive(engine, run_id)
    approve_all(engine, run_id)
    drive(engine, run_id)
    engine.store.conn.execute("DELETE FROM artifacts")  # como un run de antes de que existiera la tabla
    assert engine.store.artifacts_of([run_id]) == []
    assert backfill_artifacts(engine.store, "https://acme.atlassian.net") == 1
    row = engine.store.artifacts_of([run_id])[0]
    assert (row["kind"], row["ref"], row["title"]) == ("issue", "KAN-1", "KAN-1: Revisar logs")
    assert backfill_artifacts(engine.store) == 0  # repetirlo no duplica
