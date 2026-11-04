"""jira.search y map_items: de una consulta JQL a un run hijo por ticket."""

import asyncio
import json

from gestplan.engine.http import FakeHttp, HttpResult, json_response
from tests.helpers import build, drive, make_engine, make_settings
from tests.test_engine import standard_executors
from tests.test_integrations import JIRA

ISSUES = [
    {"key": "KAN-3", "fields": {"summary": "Subtarea 1", "status": {"name": "Tareas por hacer"},
                                "issuetype": {"name": "Subtarea"}, "priority": {"name": "Medium"},
                                "assignee": {"displayName": "Ana"}, "parent": {"key": "KAN-2"}, "labels": ["a"]}},
    {"key": "KAN-4", "fields": {"summary": "Subtarea 2", "status": {"name": "En curso"},
                                "issuetype": {"name": "Subtarea"}, "assignee": None, "parent": None, "labels": []}},
]


class FakeJira:
    def __init__(self, issues=ISSUES, is_last=True, status=200):
        self.issues, self.is_last, self.status, self.queries = issues, is_last, status, []

    def __call__(self, method, url, body):
        if "/rest/api/3/search/jql" in url:
            self.queries.append(url)
            if self.status != 200:
                return HttpResult(self.status, b'{"errorMessages": ["JQL no valida"]}')
            return json_response({"issues": self.issues, "isLast": self.is_last})
        return None


def engine_with(tmp_path, jira):
    engine = make_engine(tmp_path, standard_executors(), http=FakeHttp(jira))
    engine.settings = make_settings(tmp_path, **JIRA)
    engine.sleep = lambda _s: asyncio.sleep(0)
    return engine


def run_single(tmp_path, jira, node):
    engine = engine_with(tmp_path, jira)
    run_id = build(engine, {"name": "uno", "nodes": {"n": node}})
    drive(engine, run_id)
    return engine, run_id


def last(engine, run_id, node="n"):
    return engine.store.attempts(run_id, node)[-1]


def test_search_devuelve_los_tickets_con_sus_campos(tmp_path):
    jira = FakeJira()
    engine, run_id = run_single(tmp_path, jira, {"type": "jira.search", "config": {
        "jql": "parent is not EMPTY ORDER BY key", "max_results": 10}})
    out = engine.store.latest_outputs(run_id, "n")
    assert last(engine, run_id)["event"] == "ok" and out["count"] == 2 and out["keys"] == ["KAN-3", "KAN-4"]
    assert out["issues"][0] == {"key": "KAN-3", "url": f"{JIRA['jira_base_url']}/browse/KAN-3",
                                "summary": "Subtarea 1", "status": "Tareas por hacer", "issue_type": "Subtarea",
                                "priority": "Medium", "assignee": "Ana", "parent_key": "KAN-2", "labels": ["a"]}
    assert out["issues"][1]["assignee"] == "" and out["truncated"] is False
    assert "KAN-4 [En curso] Subtarea 2" in out["text"]
    assert "jql=parent%20is%20not%20EMPTY%20ORDER%20BY%20key" in jira.queries[0] and "maxResults=10" in jira.queries[0]


def test_search_vacia_truncada_y_con_error(tmp_path):
    engine, run_id = run_single(tmp_path, FakeJira(issues=[]), {"type": "jira.search", "config": {"jql": "x"}})
    assert last(engine, run_id)["event"] == "empty"
    engine, run_id = run_single(tmp_path, FakeJira(is_last=False), {"type": "jira.search", "config": {
        "jql": "x", "max_results": 1}})
    out = engine.store.latest_outputs(run_id, "n")
    assert out["count"] == 1 and out["truncated"] is True  # había más de los que se pidieron
    engine, run_id = run_single(tmp_path, FakeJira(status=400), {"type": "jira.search", "config": {"jql": "x"}})
    assert last(engine, run_id)["event"] == "failed" and "JQL no valida" in last(engine, run_id)["error"]


ITEMS = [{"key": "KAN-3", "summary": "Uno", "n": 7}, {"key": "KAN-4", "summary": "", "n": 8}]


def map_node(**config):
    return {"type": "map_items", "config": {"items": ITEMS, **config}}


def test_map_items_formatea_campos_y_copia_valores_fijos(tmp_path):
    engine, run_id = run_single(tmp_path, FakeJira(), map_node(mapping={
        "jira_id": "{key}", "titulo": "{key}: {summary}", "repo": "https://x/repo", "n": "{n}", "fijo": 5}))
    out = engine.store.latest_outputs(run_id, "n")
    assert last(engine, run_id)["event"] == "ok" and out["count"] == 2 and out["skipped"] == 0
    assert out["items"][0] == {"jira_id": "KAN-3", "titulo": "KAN-3: Uno", "repo": "https://x/repo", "n": "7", "fijo": 5}


def test_map_items_campo_inexistente_es_un_error_claro(tmp_path):
    engine, run_id = run_single(tmp_path, FakeJira(), map_node(mapping={"x": "{no_existe}"}))
    assert last(engine, run_id)["event"] == "failed"
    assert "no_existe" in last(engine, run_id)["error"] and "campos: key, n, summary" in last(engine, run_id)["error"]


def test_map_items_skip_when_empty_y_lista_vacia(tmp_path):
    engine, run_id = run_single(tmp_path, FakeJira(), map_node(mapping={"t": "{summary}"}, skip_when_empty=True))
    out = engine.store.latest_outputs(run_id, "n")
    assert out["count"] == 1 and out["skipped"] == 1 and out["items"] == [{"t": "Uno"}]
    engine, run_id = run_single(tmp_path, FakeJira(), {"type": "map_items", "config": {"items": [], "mapping": {"a": "b"}}})
    assert last(engine, run_id)["event"] == "empty"
    engine, run_id = run_single(tmp_path, FakeJira(), map_node(mapping={"a": "{ }"}))  # llave mal usada
    assert last(engine, run_id)["event"] == "failed"


CHILD = {"name": "child", "inputs": {"jira_id": {"type": "string", "required": True},
                                       "titulo": {"type": "string", "default": ""}},
         "nodes": {"step": {"type": "run_command", "config": {"workspace": "w", "command": ["python", "-V"]}}},
         "edges": []}


def test_de_una_consulta_a_un_run_hijo_por_ticket(tmp_path):
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "child.json").write_text(json.dumps(CHILD), encoding="utf-8")
    engine = engine_with(tmp_path, FakeJira())
    run_id = build(engine, {"name": "padre", "nodes": {
        "buscar": {"type": "jira.search", "config": {"jql": "parent is not EMPTY"}},
        "mapear": {"type": "map_items", "config": {
            "items": "${nodes.buscar.outputs.issues}", "mapping": {"jira_id": "{key}", "titulo": "{summary}"}}},
        "lanzar": {"type": "launch_plans", "config": {"plan": "child", "items": "${nodes.mapear.outputs.items}"}},
        "esperar": {"type": "wait_plan", "config": {"launched_by": "lanzar"}}},
        "edges": [{"from": "buscar.ok", "to": "mapear"}, {"from": "mapear.ok", "to": "lanzar"},
                  {"from": "lanzar.ok", "to": "esperar"}]}, title="Por tickets")
    assert drive(engine, run_id) == "succeeded"
    children = sorted(engine.store.children_of(run_id), key=lambda c: c["title"])
    assert [c["inputs"]["jira_id"] for c in children] == ["KAN-3", "KAN-4"]
    assert {c["title"] for c in children} == {"Por tickets · 1", "Por tickets · 2"}
    assert all(c["status"] == "succeeded" for c in children)
    assert engine.store.latest_outputs(run_id, "esperar")["failed_count"] == 0
