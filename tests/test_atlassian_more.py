"""jira.create_issue y confluence.update_page con un Atlassian simulado."""

import asyncio
import json

from gestplan.atlassian import adf_to_text
from gestplan.engine.http import FakeHttp, HttpResult, json_response
from gestplan.plans.loader import parse_plan
from tests.helpers import build, drive, make_engine, make_settings, statuses
from tests.test_integrations import JIRA, approve_all


class FakeProject:
    def __init__(self):
        self.issues = []

    def __call__(self, method, url, body):
        path = url.split("/rest/api/3/")[-1]
        if path.startswith("project/KAN"):
            return json_response({"issueTypes": [{"id": "1", "name": "Epic", "subtask": False},
                                                 {"id": "2", "name": "Tarea", "subtask": False},
                                                 {"id": "3", "name": "Subtask", "subtask": True}]})
        if path.startswith("project/"):
            return json_response({"errorMessages": ["no existe"]}, 404)
        if path.startswith("search/jql"):
            label = path.split("labels%20%3D%20%22")[-1].split("%22")[0]
            return json_response({"issues": [i for i in self.issues if label in i["labels"]]})
        if path == "issue" and method == "POST":
            fields = body["fields"]
            self.issues.append({"key": f"KAN-{len(self.issues) + 1}", "id": str(100 + len(self.issues)),
                                "labels": fields["labels"], "fields": fields})
            return json_response({"key": self.issues[-1]["key"], "id": self.issues[-1]["id"]}, 201)
        return None


def issue_plan(**config):
    base = {"project": "KAN", "summary": "Revisar logs", "description": "Mira los **logs** de ayer",
            "issue_type": "Task", "labels": ["gestplan"], **config}
    return {"name": "issue", "nodes": {
        "ok": {"type": "manual_approval", "config": {"approves": ["nuevo"]}},
        "nuevo": {"type": "jira.create_issue", "config": base}},
        "edges": [{"from": "ok.approved", "to": "nuevo"}]}


def settings_engine(tmp_path, http):
    engine = make_engine(tmp_path, http=http)
    engine.settings = make_settings(tmp_path, **JIRA)
    engine.sleep = lambda _s: asyncio.sleep(0)
    return engine


def test_crear_ticket_traduce_el_tipo_y_es_idempotente(tmp_path):
    fake = FakeProject()
    engine = settings_engine(tmp_path, FakeHttp(fake))
    run_id = build(engine, issue_plan())
    drive(engine, run_id)
    assert "Revisar logs" in engine.store.list_approvals("pending")[0]["previews"]["nuevo"]["text"]
    approve_all(engine, run_id)
    drive(engine, run_id)

    out = engine.store.latest_outputs(run_id, "nuevo")
    assert out == {"key": "KAN-1", "id": "100", "url": "https://acme.atlassian.net/browse/KAN-1"}
    sent = fake.issues[0]["fields"]
    assert sent["issuetype"] == {"id": "2"}  # "Task" se resuelve al "Tarea" del proyecto (no al Epic)
    assert f"gp-{run_id}-nuevo" in sent["labels"] and "gestplan" in sent["labels"]
    assert "logs" in adf_to_text(sent["description"])

    run = engine.store.get_run(run_id)
    plan, _ = parse_plan(run["plan_snapshot"])
    ctx = engine._exec_context(run, plan, "nuevo", 99, engine.prepare_config(run, plan, "nuevo"))
    again = asyncio.run(engine.executors["jira.create_issue"].run(ctx))
    assert again.event == "already_done" and again.outputs["key"] == "KAN-1" and len(fake.issues) == 1


def test_crear_ticket_errores_claros(tmp_path):
    engine = settings_engine(tmp_path, FakeHttp(FakeProject()))
    for config, event, text in (({"issue_type": "Incidencia"}, "issue_type_not_found", "hay: Epic, Tarea"),
                                ({"project": "ZZZ"}, "not_found", "no existe")):
        run_id = build(engine, issue_plan(**config))
        drive(engine, run_id)
        approve_all(engine, run_id)
        drive(engine, run_id)
        attempt = engine.store.attempts(run_id, "nuevo")[-1]
        assert attempt["event"] == event and text in attempt["error"]
        assert statuses(engine, run_id)["nuevo"] == "failed"


class FakeWiki:
    def __init__(self):
        self.page = {"id": "77", "title": "Informe", "version": {"number": 3},
                     "body": {"storage": {"value": "<p>viejo</p>"}},
                     "_links": {"base": "https://acme.atlassian.net/wiki", "webui": "/spaces/DOC/pages/77"}}
        self.puts = []

    def __call__(self, method, url, body):
        if "/rest/api/content/404" in url:
            return HttpResult(404, b"{}")
        if "/rest/api/content/77" in url and method == "GET":
            return json_response(self.page)
        if "/rest/api/content/77" in url and method == "PUT":
            self.puts.append(body)
            self.page = {**self.page, "title": body["title"], "version": body["version"],
                         "body": {"storage": body["body"]["storage"]}}
            return json_response(self.page)
        return None


def page_plan(page_id="77", **config):
    return {"name": "wiki", "nodes": {
        "ok": {"type": "manual_approval", "config": {"approves": ["pagina"]}},
        "pagina": {"type": "confluence.update_page", "config": {
            "page_id": page_id, "blocks": [{"type": "heading", "level": 1, "text": "Nuevo"},
                                           {"type": "paragraph", "text": "Contenido <b>"}], **config}}},
        "edges": [{"from": "ok.approved", "to": "pagina"}]}


def test_actualizar_pagina_sube_la_version_y_es_idempotente(tmp_path):
    wiki = FakeWiki()
    engine = settings_engine(tmp_path, FakeHttp(wiki))
    run_id = build(engine, page_plan(title="Informe final"))
    drive(engine, run_id)
    approve_all(engine, run_id)
    drive(engine, run_id)

    out = engine.store.latest_outputs(run_id, "pagina")
    assert out["version"] == 4 and out["title"] == "Informe final" and out["page_id"] == "77"
    sent = wiki.puts[0]
    assert sent["version"]["number"] == 4 and "<h1>Nuevo</h1>" in sent["body"]["storage"]["value"]
    assert "&lt;b&gt;" in sent["body"]["storage"]["value"]  # el texto va escapado
    assert f"run:{run_id}" in sent["body"]["storage"]["value"]

    run = engine.store.get_run(run_id)
    plan, _ = parse_plan(run["plan_snapshot"])
    ctx = engine._exec_context(run, plan, "pagina", 99, engine.prepare_config(run, plan, "pagina"))
    again = asyncio.run(engine.executors["confluence.update_page"].run(ctx))
    assert again.event == "already_done" and len(wiki.puts) == 1  # no se actualiza dos veces


def test_actualizar_pagina_inexistente(tmp_path):
    engine = settings_engine(tmp_path, FakeHttp(FakeWiki()))
    run_id = build(engine, page_plan(page_id="404"))
    drive(engine, run_id)
    approve_all(engine, run_id)
    drive(engine, run_id)
    assert engine.store.attempts(run_id, "pagina")[-1]["event"] == "not_found"
    assert json.dumps(engine.store.attempts(run_id, "pagina")[-1]["error"]).count("404") == 1


def test_la_vista_previa_de_actualizar_pagina_se_muestra_al_aprobar(tmp_path):
    """La aprobación enseña el contenido que va a reemplazar la página (antes fallaba con KeyError: 'space')."""
    engine = settings_engine(tmp_path, FakeHttp(FakeWiki()))
    run_id = build(engine, page_plan(title="Informe final"))
    drive(engine, run_id)
    preview = engine.store.list_approvals("pending")[0]["previews"]["pagina"]
    assert preview["kind"] == "markdown" and "página 77" in preview["title"] and "Informe final" in preview["title"]
    assert "# Nuevo" in preview["text"] and "Contenido <b>" in preview["text"]
