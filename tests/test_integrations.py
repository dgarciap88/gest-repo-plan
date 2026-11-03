"""Jira, Confluence y GitHub (lectura) con un servidor HTTP simulado y un gh falso."""

import asyncio
import json

from gestplan.atlassian import adf_to_text, markdown_to_adf, render_storage
from gestplan.engine.http import FakeHttp, HttpResult, json_response
from gestplan.engine.runner import FakeRunner, ProcResult
from gestplan.plans.loader import parse_plan
from gestplan.plans.validator import validate_plan
from tests.helpers import build, drive, make_engine, make_settings, statuses

JIRA = {"jira_base_url": "https://acme.atlassian.net", "jira_email": "me@acme.com", "jira_token": "tok-123456789",
        "confluence_base_url": "https://acme.atlassian.net/wiki", "confluence_email": "me@acme.com",
        "confluence_token": "tok-123456789"}


def approve_all(engine, run_id):
    for approval in engine.store.list_approvals("pending"):
        engine.store.decide_approval(approval["id"], "approved", decided_by="test")


# ---------------------------------------------------------------------------------- conversores --
def test_markdown_a_adf_y_vuelta_a_texto():
    doc = markdown_to_adf("# Título\n\nUn **párrafo** con `código`.\n\n- uno\n- dos\n\n```py\nx = 1\n```", marker="[m]")
    kinds = [node["type"] for node in doc["content"]]
    assert kinds == ["heading", "paragraph", "bulletList", "codeBlock", "paragraph"]
    assert doc["content"][1]["content"][1]["marks"] == [{"type": "strong"}]
    text = adf_to_text(doc)
    assert "# Título" in text and "- dos" in text and "x = 1" in text and text.endswith("[m]")
    assert adf_to_text(None) == "" and adf_to_text("llano") == "llano"


def test_storage_escapa_el_contenido():
    html = render_storage([
        {"type": "heading", "level": 1, "text": "A & B"},
        {"type": "paragraph", "text": "<script>x</script>"},
        {"type": "table", "columns": ["f"], "rows": [["a<b"]]},
        {"type": "code", "text": "if a ]]> b", "language": "py"},
        {"type": "list", "items": ["x"], "ordered": True},
    ])
    assert "<h1>A &amp; B</h1>" in html and "&lt;script&gt;" in html and "<td>a&lt;b</td>" in html
    assert html.count("<![CDATA[") == 2 and "<ol><li>x</li></ol>" in html


# -------------------------------------------------------------------------------------- Jira -----
class FakeJira:
    def __init__(self):
        self.status = "To Do"
        self.assignee = None
        self.comments = []
        self.subtasks = []

    def __call__(self, method, url, body):
        path = url.split("/rest/api/3/")[-1]
        if path.startswith("issue/NOPE-1"):
            return json_response({"errorMessages": ["no existe"]}, 404)
        if path.startswith("myself"):
            return json_response({"accountId": "acc-me", "displayName": "Yo"})
        if path.startswith("search/jql"):
            label = path.split("labels%20%3D%20%22")[-1].split("%22")[0]
            return json_response({"issues": [s for s in self.subtasks if label in s["labels"]]})
        if path.startswith("issue/createmeta"):
            return json_response({"issueTypes": [{"id": "1", "subtask": False}, {"id": "9", "subtask": True}]})
        if path.endswith("/assignee") and method == "PUT":
            self.assignee = body["accountId"]
            return HttpResult(204)
        if path.endswith("/comment") and method == "POST":
            self.comments.append({"id": str(len(self.comments) + 1), "body": body["body"], "author": {}})
            return json_response({"id": str(len(self.comments))}, 201)
        if "/comment" in path:
            return json_response({"comments": self.comments})
        if path.endswith("/transitions") and method == "POST":
            self.status = "In Review"
            return HttpResult(204)
        if path.endswith("/transitions"):
            return json_response({"transitions": [{"id": "31", "name": "Revisar", "to": {"name": "In Review"}}]})
        if path == "issue" and method == "POST":
            self.subtasks.append({"key": f"ABC-{100 + len(self.subtasks)}", "labels": body["fields"]["labels"]})
            return json_response({"key": self.subtasks[-1]["key"]}, 201)
        if path.startswith("issue/ABC-1"):
            return json_response({"key": "ABC-1", "fields": {
                "summary": "Arreglar login", "status": {"name": self.status}, "issuetype": {"name": "Bug"},
                "priority": {"name": "High"}, "labels": ["x"], "project": {"key": "ABC"},
                "assignee": {"accountId": self.assignee, "displayName": "A"} if self.assignee else None,
                "description": {"type": "doc", "version": 1, "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "Falla al entrar"}]}]},
                "subtasks": [], "reporter": {"displayName": "R"}}})
        return None


JIRA_PLAN = {
    "name": "jira",
    "inputs": {"key": {"type": "string", "default": "ABC-1"}},
    "nodes": {
        "tk": {"type": "jira.get_issue", "config": {"key": "${inputs.key}"}},
        "cm": {"type": "jira.get_comments", "config": {"key": "${inputs.key}"}},
        "ok": {"type": "manual_approval", "config": {"approves": ["asignar", "comentar", "mover", "subtareas"]}},
        "asignar": {"type": "jira.assign", "config": {"key": "${inputs.key}", "assignee": "me"}},
        "comentar": {"type": "jira.comment",
                     "config": {"key": "${inputs.key}", "body": "Resumen: ${nodes.tk.outputs.summary}"}},
        "mover": {"type": "jira.transition", "config": {"key": "${inputs.key}", "to": "In Review"}},
        "subtareas": {"type": "jira.create_subtask",
                      "config": {"key": "${inputs.key}", "subtasks": [{"summary": "Tests"}, {"summary": "Docs"}]}},
    },
    "edges": [{"from": "tk.ok", "to": "cm"}, {"from": "cm.ok", "to": "ok"}, {"from": "ok.approved", "to": "asignar"},
              {"from": "asignar.ok", "to": "comentar"}, {"from": "comentar.ok", "to": "mover"},
              {"from": "mover.ok", "to": "subtareas"}],
}


def jira_engine(tmp_path, fake):
    http = FakeHttp(fake)
    engine = make_engine(tmp_path, http=http)
    engine.settings = make_settings(tmp_path, **JIRA)
    engine.sleep = lambda _s: asyncio.sleep(0)
    return engine, http


def test_jira_flujo_completo_e_idempotencia(tmp_path):
    fake = FakeJira()
    engine, http = jira_engine(tmp_path, fake)
    run_id = build(engine, JIRA_PLAN)
    drive(engine, run_id)

    issue = engine.store.latest_outputs(run_id, "tk")
    assert issue["summary"] == "Arreglar login" and issue["description"] == "Falla al entrar"
    assert issue["status"] == "To Do" and issue["url"] == "https://acme.atlassian.net/browse/ABC-1"
    assert statuses(engine, run_id)["ok"] == "waiting_approval" and fake.assignee is None
    preview = engine.store.list_approvals("pending")[0]["previews"]
    assert "Tests" in preview["subtareas"]["text"] and preview["asignar"]["items"]["asignar a"] == "me"

    approve_all(engine, run_id)
    drive(engine, run_id)
    assert set(statuses(engine, run_id).values()) == {"succeeded"}
    assert fake.assignee == "acc-me" and fake.status == "In Review" and len(fake.comments) == 1
    assert engine.store.latest_outputs(run_id, "subtareas") == {"keys": ["ABC-100", "ABC-101"], "created": 2}
    assert "Resumen: Arreglar login" in adf_to_text(fake.comments[0]["body"])
    # la autenticación va en cabecera y el token nunca queda en los registros de auditoría
    assert http.calls[0]["headers"]["Authorization"].startswith("Basic ")
    assert all("tok-123456789" not in json.dumps(dict(e)) for e in engine.store.executions(run_id))

    # repetir cada nodo con efecto no duplica nada
    run = engine.store.get_run(run_id)
    plan, _ = parse_plan(run["plan_snapshot"])
    for node, expected in (("asignar", "already_done"), ("comentar", "already_done"), ("mover", "already_done")):
        ctx = engine._exec_context(run, plan, node, 99, engine.prepare_config(run, plan, node))
        assert asyncio.run(engine.executors[plan.nodes[node].type].run(ctx)).event == expected
    ctx = engine._exec_context(run, plan, "subtareas", 99, engine.prepare_config(run, plan, "subtareas"))
    again = asyncio.run(engine.executors["jira.create_subtask"].run(ctx))
    assert again.outputs == {"keys": ["ABC-100", "ABC-101"], "created": 0} and len(fake.subtasks) == 2


def test_jira_errores(tmp_path):
    engine, _ = jira_engine(tmp_path, FakeJira())
    plan = {"name": "x", "inputs": {"k": {"type": "string"}}, "nodes": {"a": {"type": "jira.get_issue",
            "config": {"key": "${inputs.k}"}}}, "edges": []}
    missing = build(engine, plan, {"k": "NOPE-1"})
    drive(engine, missing)
    assert statuses(engine, missing)["a"] == "failed"
    invalid = build(engine, plan, {"k": "no es clave"})
    drive(engine, invalid)
    attempt = engine.store.open_attempt(invalid, "a") or engine.store.attempts(invalid, "a")[-1]
    assert "no válida" in (attempt["error"] or "")

    sin_credenciales = make_engine(tmp_path / "otro", http=FakeHttp(FakeJira()))
    run_id = build(sin_credenciales, plan, {"k": "ABC-1"})
    drive(sin_credenciales, run_id)
    assert statuses(sin_credenciales, run_id)["a"] == "failed"


def test_jira_transicion_imposible_y_reintento_ante_429(tmp_path):
    fake = FakeJira()
    calls = {"n": 0}

    def flaky(method, url, body):
        if url.endswith("issue/ABC-1?fields=status") and calls["n"] == 0:
            calls["n"] += 1
            return HttpResult(429)
        return None

    engine = make_engine(tmp_path, http=FakeHttp(flaky, fake))
    engine.settings = make_settings(tmp_path, **JIRA)
    engine.sleep = lambda _s: asyncio.sleep(0)
    plan = json.loads(json.dumps(JIRA_PLAN))
    plan["nodes"]["mover"]["config"]["to"] = "Done"
    run_id = build(engine, plan)
    drive(engine, run_id)
    approve_all(engine, run_id)
    drive(engine, run_id)
    assert calls["n"] == 1
    assert statuses(engine, run_id)["mover"] == "failed"  # sin evento 'no_transition' conectado
    attempt = engine.store.attempts(run_id, "mover")[-1]
    assert attempt["event"] == "no_transition" and "disponibles: In Review" in attempt["error"]


# -------------------------------------------------------------------------------- Confluence ----
class FakeConfluence:
    def __init__(self):
        self.pages = []

    def __call__(self, method, url, body):
        if method == "GET" and "/rest/api/content?" in url:
            return json_response({"results": self.pages})
        if method == "POST" and url.endswith("/rest/api/content"):
            page = {"id": "77", "title": body["title"], "body": {"storage": body["body"]["storage"]},
                    "_links": {"base": "https://acme.atlassian.net/wiki", "webui": "/spaces/DOC/pages/77"}}
            self.pages.append(page)
            return json_response(page, 200)
        return None


CONF_PLAN = {
    "name": "conf",
    "nodes": {
        "ok": {"type": "manual_approval", "config": {"approves": ["pagina"]}},
        "pagina": {"type": "confluence.create_page", "config": {
            "space": "DOC", "title": "Informe", "parent_id": "5",
            "blocks": [{"type": "heading", "level": 1, "text": "Resumen"},
                       {"type": "table", "columns": ["a"], "rows": [["1"]]}]}},
    },
    "edges": [{"from": "ok.approved", "to": "pagina"}],
}


def test_confluence_crea_pagina_una_sola_vez(tmp_path):
    fake = FakeConfluence()
    http = FakeHttp(fake)
    engine = make_engine(tmp_path, http=http)
    engine.settings = make_settings(tmp_path, **JIRA)
    run_id = build(engine, CONF_PLAN)
    drive(engine, run_id)
    assert "# Resumen" in engine.store.list_approvals("pending")[0]["previews"]["pagina"]["text"]
    approve_all(engine, run_id)
    drive(engine, run_id)
    out = engine.store.latest_outputs(run_id, "pagina")
    assert out == {"page_id": "77", "title": "Informe", "url": "https://acme.atlassian.net/wiki/spaces/DOC/pages/77"}
    post = next(c for c in http.calls if c["method"] == "POST")
    assert post["json"]["ancestors"] == [{"id": "5"}] and "<h1>Resumen</h1>" in post["json"]["body"]["storage"]["value"]

    run = engine.store.get_run(run_id)
    plan, _ = parse_plan(run["plan_snapshot"])
    ctx = engine._exec_context(run, plan, "pagina", 99, engine.prepare_config(run, plan, "pagina"))
    assert asyncio.run(engine.executors["confluence.create_page"].run(ctx)).event == "already_done"
    fake.pages[0]["body"]["storage"]["value"] = "<p>otra</p>"  # misma página, pero de otro autor
    assert asyncio.run(engine.executors["confluence.create_page"].run(ctx)).event == "title_exists"


def test_confluence_valida_los_bloques(tmp_path):
    bad = json.loads(json.dumps(CONF_PLAN))
    bad["nodes"]["pagina"]["config"]["blocks"] = [{"type": "video", "text": "x"}]
    plan, issues = parse_plan(bad)
    result = validate_plan(plan, tmp_path, make_settings(tmp_path))
    assert not result.ok


# ------------------------------------------------------------------------------------ GitHub ----
def gh_handler(state):
    def handler(argv, cwd, stdin):
        if argv[0] != "gh":
            return None
        if argv[1:3] == ["pr", "view"] and "reviews" in argv[-1]:
            return ProcResult(0, json.dumps({
                "number": 7, "reviews": [{"author": {"login": "rev"}, "state": "CHANGES_REQUESTED", "body": "Falta un test"},
                                         {"author": {"login": "bot"}, "state": "COMMENTED", "body": "ruido"}],
                "comments": [{"author": {"login": "otro"}, "body": "¿y la doc?"}]}))
        if argv[1:3] == ["pr", "view"]:
            return ProcResult(0, json.dumps({"state": state["pr"], "number": 7, "url": "u", "mergeable": "MERGEABLE",
                                             "reviewDecision": ""}))
        if argv[1] == "api":
            return ProcResult(0, json.dumps([{"user": {"login": "rev"}, "path": "a.py", "line": 3, "body": "renombra"}]))
        if argv[1:3] == ["pr", "checks"]:
            state["polls"] += 1
            bucket = state["buckets"][min(state["polls"] - 1, len(state["buckets"]) - 1)]
            return ProcResult(1 if bucket == "fail" else 8 if bucket == "pending" else 0, json.dumps(
                [{"name": "build", "bucket": bucket, "state": bucket.upper(), "link": "http://ci"}]))
        if argv[1:3] == ["run", "list"]:
            return ProcResult(0, json.dumps([{"databaseId": 1, "conclusion": "success"}]))
        return None
    return handler


def gh_plan(node_type, **config):
    return {"name": "gh", "nodes": {"n": {"type": node_type, "config": {"workspace": ".", "pr": "7", **config}}},
            "edges": []}


def test_github_estado_y_comentarios(tmp_path):
    state = {"pr": "MERGED", "polls": 0, "buckets": ["pass"]}
    engine = make_engine(tmp_path, runner=FakeRunner(gh_handler(state)))
    run_id = build(engine, gh_plan("github.get_pr_status"))
    drive(engine, run_id)
    attempt = engine.store.attempts(run_id, "n")[-1]
    assert attempt["event"] == "merged" and attempt["outputs"]["review_decision"] == ""

    comments = make_engine(tmp_path / "c", runner=FakeRunner(gh_handler(state)))
    run_id = build(comments, gh_plan("github.get_review_comments", ignore_authors=["bot"]))
    drive(comments, run_id)
    attempt = comments.store.attempts(run_id, "n")[-1]
    assert attempt["event"] == "has_comments" and attempt["outputs"]["changes_requested"] is True
    assert attempt["outputs"]["count"] == 3 and "a.py:3" in attempt["outputs"]["text"]
    assert "ruido" not in attempt["outputs"]["text"]


def test_github_espera_checks(tmp_path):
    state = {"pr": "OPEN", "polls": 0, "buckets": ["pending", "pending", "pass"]}
    engine = make_engine(tmp_path, runner=FakeRunner(gh_handler(state)))
    run_id = build(engine, gh_plan("github.wait_checks", poll_interval_s=0))
    drive(engine, run_id)
    assert state["polls"] == 3 and engine.store.attempts(run_id, "n")[-1]["event"] == "success"

    failing = {"pr": "OPEN", "polls": 0, "buckets": ["fail"]}
    engine = make_engine(tmp_path / "f", runner=FakeRunner(gh_handler(failing)))
    run_id = build(engine, gh_plan("github.wait_checks", poll_interval_s=0))
    drive(engine, run_id)
    attempt = engine.store.attempts(run_id, "n")[-1]
    assert attempt["event"] == "failure" and attempt["outputs"]["failed_checks"][0]["name"] == "build"

    slow = {"pr": "OPEN", "polls": 0, "buckets": ["pending"]}
    engine = make_engine(tmp_path / "s", runner=FakeRunner(gh_handler(slow)))
    run_id = build(engine, gh_plan("github.wait_checks", poll_interval_s=0, max_wait_s=0))
    drive(engine, run_id)
    assert engine.store.attempts(run_id, "n")[-1]["event"] == "timeout"


def test_github_workflows(tmp_path):
    engine = make_engine(tmp_path, runner=FakeRunner(gh_handler({"pr": "OPEN", "polls": 0, "buckets": ["pass"]})))
    plan = {"name": "w", "nodes": {"n": {"type": "github.get_workflow_runs",
                                           "config": {"workspace": ".", "branch": "main"}}}, "edges": []}
    run_id = build(engine, plan)
    drive(engine, run_id)
    assert engine.store.attempts(run_id, "n")[-1]["outputs"]["latest_conclusion"] == "success"
