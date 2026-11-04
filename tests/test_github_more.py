"""github.merge_pr y github.get_failed_logs con un gh falso."""

import json

import pytest

from gestplan.engine.runner import FakeRunner, ProcResult
from tests.helpers import build, drive, make_engine, statuses
from tests.test_integrations import approve_all


class FakeGh:
    def __init__(self, state="OPEN", mergeable="MERGEABLE", merge_result=None, checks=None, log=""):
        self.state, self.mergeable, self.merge_result = state, mergeable, merge_result or ProcResult(0, "")
        self.checks, self.log = checks if checks is not None else [], log
        self.merged_with = None

    def __call__(self, argv, cwd, stdin):
        if argv[0] != "gh":
            return None
        if argv[1:3] == ["pr", "merge"]:
            self.merged_with = argv
            if self.merge_result.ok:
                self.state = "MERGED"
            return self.merge_result
        if argv[1:3] == ["pr", "view"]:
            return ProcResult(0, json.dumps({
                "state": self.state, "number": 7, "url": "https://github.com/o/r/pull/7", "mergeable": self.mergeable,
                "mergeCommit": {"oid": "abc123"} if self.state == "MERGED" else None, "title": "Un PR de prueba",
                "headRefName": "feature/x", "baseRefName": "main"}))
        if argv[1:3] == ["pr", "checks"]:
            return ProcResult(0, json.dumps(self.checks))
        if argv[1:3] == ["run", "view"]:
            return ProcResult(0, self.log)
        return None


def merge_plan(**config):
    return {"name": "merge", "nodes": {
        "ok": {"type": "manual_approval", "config": {"approves": ["merge"]}},
        "merge": {"type": "github.merge_pr", "config": {"workspace": ".", "pr": "7", **config}}},
        "edges": [{"from": "ok.approved", "to": "merge"}]}


def run_merge(tmp_path, gh, **config):
    engine = make_engine(tmp_path, runner=FakeRunner(gh))
    run_id = build(engine, merge_plan(**config))
    drive(engine, run_id)
    preview = engine.store.list_approvals("pending")[0]["previews"]["merge"]
    approve_all(engine, run_id)
    drive(engine, run_id)
    return engine, run_id, preview


def test_fusiona_un_pr_abierto_con_el_metodo_pedido(tmp_path):
    gh = FakeGh()
    engine, run_id, preview = run_merge(tmp_path, gh, method="rebase", delete_branch=True)
    assert gh.merged_with == ["gh", "pr", "merge", "7", "--rebase", "--delete-branch"]
    attempt = engine.store.attempts(run_id, "merge")[-1]
    assert attempt["event"] == "ok" and attempt["outputs"] == {"number": 7, "sha": "abc123",
                                                               "url": "https://github.com/o/r/pull/7"}
    assert preview["items"]["método"] == "rebase" and preview["items"]["rama"] == "feature/x → main"
    assert preview["items"]["borrar la rama"] == "sí"


@pytest.mark.parametrize(("gh", "event"), [
    (FakeGh(state="MERGED"), "already_done"),
    (FakeGh(state="CLOSED"), "closed"),
    (FakeGh(mergeable="CONFLICTING"), "not_mergeable"),
    (FakeGh(merge_result=ProcResult(1, "", "Pull request is not mergeable: required status check is expected")),
     "not_mergeable"),
    (FakeGh(merge_result=ProcResult(1, "", "HTTP 500")), "failed"),
])
def test_casos_de_la_fusion(tmp_path, gh, event):
    engine, run_id, _ = run_merge(tmp_path, gh)
    assert engine.store.attempts(run_id, "merge")[-1]["event"] == event
    if event == "already_done":
        assert gh.merged_with is None  # no se vuelve a fusionar


def logs_plan(**config):
    return {"name": "logs", "nodes": {"n": {"type": "github.get_failed_logs",
                                            "config": {"workspace": ".", "pr": "7", **config}}}, "edges": []}


def test_logs_de_checks_fallidos_se_recortan_por_la_cola(tmp_path):
    checks = [{"name": "tests", "bucket": "fail", "link": "https://github.com/o/r/actions/runs/123/job/9"},
              {"name": "lint", "bucket": "pass", "link": "https://github.com/o/r/actions/runs/124/job/1"}]
    log = "linea inicial\n" + "ruido de instalacion\n" * 400 + "AssertionError: el error de verdad"
    engine = make_engine(tmp_path, runner=FakeRunner(FakeGh(checks=checks, log=log)))
    run_id = build(engine, logs_plan(max_chars=600))
    drive(engine, run_id)
    attempt = engine.store.attempts(run_id, "n")[-1]
    out = attempt["outputs"]
    assert attempt["event"] == "ok" and out["truncated"] is True and out["runs"] == [{"run_id": "123", "name": "tests"}]
    assert out["logs"].endswith("AssertionError: el error de verdad") and "linea inicial" not in out["logs"]
    assert len(out["logs"]) < 800


def test_sin_checks_fallidos(tmp_path):
    checks = [{"name": "tests", "bucket": "pass", "link": "https://github.com/o/r/actions/runs/1/job/1"}]
    engine = make_engine(tmp_path, runner=FakeRunner(FakeGh(checks=checks)))
    run_id = build(engine, logs_plan())
    drive(engine, run_id)
    attempt = engine.store.attempts(run_id, "n")[-1]
    assert attempt["event"] == "no_failures" and attempt["outputs"]["logs"] == ""
    assert statuses(engine, run_id)["n"] in ("failed", "succeeded")


def test_un_repo_sin_ci_no_es_un_error_al_esperar_checks(tmp_path):
    def gh(argv, cwd, stdin):
        if argv[1:3] == ["pr", "checks"]:
            return ProcResult(1, "", "no checks reported on the 'feature/x' branch")
        return None

    engine = make_engine(tmp_path, runner=FakeRunner(gh))
    plan = {"name": "w", "nodes": {"n": {"type": "github.wait_checks",
                                          "config": {"workspace": ".", "pr": "7", "poll_interval_s": 0,
                                                     "no_checks_wait_s": 0}}}, "edges": []}
    run_id = build(engine, plan)
    drive(engine, run_id)
    attempt = engine.store.attempts(run_id, "n")[-1]
    assert attempt["event"] == "success" and attempt["outputs"]["summary"] == ""


def test_checks_que_aun_no_se_han_registrado_no_cuentan_como_sin_ci(tmp_path):
    """Justo tras abrir el PR GitHub tarda unos segundos en registrar el CI: no se debe dar success antes."""
    polls = {"n": 0}

    def gh(argv, cwd, stdin):
        if argv[1:3] != ["pr", "checks"]:
            return None
        polls["n"] += 1
        if polls["n"] <= 2:
            return ProcResult(1, "", "no checks reported on the 'feature/x' branch")
        bucket = "pending" if polls["n"] == 3 else "pass"
        return ProcResult(0, json.dumps([{"name": "tests", "bucket": bucket, "state": bucket.upper(), "link": "l"}]), "")

    engine = make_engine(tmp_path, runner=FakeRunner(gh))
    plan = {"name": "w", "nodes": {"n": {"type": "github.wait_checks",
                                          "config": {"workspace": ".", "pr": "7", "poll_interval_s": 0}}}, "edges": []}
    run_id = build(engine, plan)
    for _ in range(8):  # el servidor sondea de forma continua; aquí cada drive da un sondeo más
        drive(engine, run_id)
        if engine.store.attempts(run_id, "n")[-1]["event"]:
            break
    attempt = engine.store.attempts(run_id, "n")[-1]
    assert polls["n"] == 4 and attempt["event"] == "success" and "tests" in attempt["outputs"]["summary"]


def test_el_log_de_actions_se_limpia_antes_de_pasarlo_a_un_agente():
    from gestplan.executors.github_extra import clean_log

    raw = (
        "tests\tFallo forzado (escenario)\t﻿2026-11-04T07:36:02.4039236Z ##[group]Run if [ -f x ]\n"
        "tests\tFallo forzado (escenario)\t2026-11-04T07:36:02.4040950Z \x1b[36;1mexit 1\x1b[0m\n"
        "tests\tFallo forzado (escenario)\t2026-11-04T07:36:02.5Z ##[error]Process completed with exit code 1.\n"
        "tests\tEjecutar tests\t2026-11-04T07:36:03.1Z FAILED test_a.py::test_x - assert 1 == 2\n"
        "linea sin formato de actions\n"
    )
    assert clean_log(raw) == (
        "--- paso: Fallo forzado (escenario) ---\n"
        "##[group]Run if [ -f x ]\nexit 1\n##[error]Process completed with exit code 1.\n"
        "--- paso: Ejecutar tests ---\nFAILED test_a.py::test_x - assert 1 == 2\n"
        "linea sin formato de actions"
    )
    assert "﻿" not in clean_log(raw) and "\x1b" not in clean_log(raw) and "2026-11" not in clean_log(raw)
