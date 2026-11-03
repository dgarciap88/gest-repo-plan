"""Flujo completo con git real: remoto bare local, workspace, commit, aprobación, push y PR (gh falso)."""

import asyncio
import subprocess
from pathlib import Path

import pytest

from gestplan.engine.runner import FakeRunner, ProcResult, RoutingRunner
from gestplan.executors.base import ExecContext, Outcome
from tests.helpers import Scripted, build, dev_outcome, drive, flow_plan, make_engine, statuses


def git(*args, cwd):
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


@pytest.fixture
def git_env(tmp_path, monkeypatch):
    config = tmp_path / "gitconfig"
    config.write_text("[user]\n\tname = Test\n\temail = test@example.com\n[init]\n\tdefaultBranch = main\n"
                      "[core]\n\tautocrlf = false\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture
def remote(tmp_path, git_env):
    """Remoto bare con una rama main; devuelve (ruta del remoto, clon 'seed' para manipularlo)."""
    bare = tmp_path / "remote.git"
    git("init", "--bare", "-b", "main", str(bare), cwd=tmp_path)
    seed = tmp_path / "seed"
    seed.mkdir()
    git("init", "-b", "main", cwd=seed)
    (seed / "README.md").write_text("hola\n", encoding="utf-8")
    git("add", "-A", cwd=seed)
    git("commit", "-m", "Commit inicial del repositorio", cwd=seed)
    git("remote", "add", "origin", str(bare), cwd=seed)
    git("push", "origin", "main", cwd=seed)
    return bare, seed


def gh_runner():
    calls = []

    def handler(argv, cwd, stdin):
        calls.append(argv)
        if argv[1:3] == ["pr", "list"]:
            return ProcResult(0, "")
        if argv[1:3] == ["pr", "create"]:
            return ProcResult(0, "https://github.com/o/r/pull/7\n")
        return ProcResult(1, "", "gh: comando inesperado")

    return RoutingRunner({"gh": FakeRunner(handler)}), calls


def writes_file(name="nueva.py", content="print('hola')\n", **extra):
    def handler(ctx):
        (Path(ctx.config["workspace"]) / name).write_text(content, encoding="utf-8")
        return dev_outcome(**extra)

    return handler


def real_executors(dev):
    return {
        "ia_agent": Scripted("ia_agent", dev),
        "run_command": Scripted("run_command", lambda c: Outcome("ok", {"exit_code": 0, "passed": True,
                                                                       "output_tail": ""})),
        "ia_agent_validator": Scripted("ia_agent_validator", lambda c: Outcome("approved", {
            "verdict": "approved", "feedback": "", "issues": [], "notes": "", "retries_used": 0, "cost_usd": 0})),
    }


def setup(tmp_path, remote, dev, plan_edit=None):
    runner, gh_calls = gh_runner()
    engine = make_engine(tmp_path, real_executors(dev), runner=runner, workspaces_dir=tmp_path / "ws")
    data = flow_plan()
    data["nodes"]["ws"]["config"]["repo_url"] = str(remote[0])
    if plan_edit:
        plan_edit(data)
    return engine, build(engine, data), gh_calls


def approve(engine):
    approval = engine.store.list_approvals("pending")[0]
    assert engine.store.decide_approval(approval["id"], "approved", decided_by="test")
    return approval


def remote_branches(bare):
    out = git("ls-remote", "--heads", str(bare), cwd=bare)
    return sorted(line.split("refs/heads/")[1] for line in out.splitlines())


def test_flujo_real_hasta_el_pr(tmp_path, remote):
    bare, _seed = remote
    dev = writes_file(suggested_commit_body="Motivo del cambio.\n\nCo-Authored-By: Un Asistente <a@b.c>")
    engine, run_id, gh_calls = setup(tmp_path, remote, dev)

    assert drive(engine, run_id) == "waiting"
    ws = engine.store.latest_outputs(run_id, "ws")
    assert ws["branch_existed"] is False and ws["local_branch"] == ws["remote_branch"] == "feature/x"
    assert remote_branches(bare) == ["main"]  # nada publicado antes de aprobar

    previews = engine.store.list_approvals("pending")[0]["previews"]
    assert previews["push"]["kind"] == "keyvalue" and previews["pr"]["kind"] == "markdown"
    assert "TASK-1" in previews["pr"]["title"]

    approve(engine)
    assert drive(engine, run_id) == "succeeded"
    assert remote_branches(bare) == ["feature/x", "main"]
    message = git("log", "-1", "--format=%B", "feature/x", cwd=bare)
    assert message.startswith("TASK-1: Añadir la función de ejemplo")
    assert "Co-Authored-By" not in message  # el sistema nunca añade coautoría de IA
    assert engine.store.latest_outputs(run_id, "pr") == {"pr_number": 7, "pr_url": "https://github.com/o/r/pull/7"}
    create = next(c for c in gh_calls if c[1:3] == ["pr", "create"])
    assert create[create.index("--head") + 1] == "feature/x" and create[create.index("--base") + 1] == "main"


def test_la_rama_ya_existe_en_el_remoto(tmp_path, remote):
    bare, seed = remote
    git("checkout", "-b", "feature/x", cwd=seed)
    (seed / "previo.txt").write_text("trabajo previo\n", encoding="utf-8")
    git("add", "-A", cwd=seed)
    git("commit", "-m", "Trabajo previo en la rama existente", cwd=seed)
    git("push", "origin", "feature/x", cwd=seed)

    engine, run_id, _ = setup(tmp_path, remote, writes_file())
    assert drive(engine, run_id) == "waiting"
    ws = engine.store.latest_outputs(run_id, "ws")
    assert ws["branch_existed"] is True
    assert ws["remote_branch"] == "feature/x"
    assert ws["local_branch"].startswith("feature/x-") and len(ws["local_branch"]) == len("feature/x-") + 6

    approve(engine)
    assert drive(engine, run_id) == "succeeded"
    assert remote_branches(bare) == ["feature/x", "main"]  # se actualizó la original, no hay rama con sufijo
    log = git("log", "--format=%s", "feature/x", cwd=bare).splitlines()
    assert log[0].startswith("TASK-1:") and "Trabajo previo en la rama existente" in log


def test_push_rechazado_si_el_remoto_avanza(tmp_path, remote):
    bare, seed = remote
    engine, run_id, _ = setup(tmp_path, remote, writes_file())
    assert drive(engine, run_id) == "waiting"

    # alguien publica la rama con otro historial mientras esperamos la aprobación
    git("checkout", "-b", "feature/x", cwd=seed)
    (seed / "otro.txt").write_text("otro\n", encoding="utf-8")
    git("add", "-A", cwd=seed)
    git("commit", "-m", "Cambio de otra persona en la misma rama", cwd=seed)
    git("push", "origin", "feature/x", cwd=seed)

    approve(engine)
    assert drive(engine, run_id) == "failed"
    push = engine.store.get_node(run_id, "push")
    assert push["last_event"] == "rejected" and push["unhandled"] == 1
    assert git("log", "-1", "--format=%s", "feature/x", cwd=bare).startswith("Cambio de otra persona")


def test_sin_cambios_no_hay_commit(tmp_path, remote):
    engine, run_id, _ = setup(tmp_path, remote, lambda ctx: dev_outcome())
    assert drive(engine, run_id) == "succeeded"
    assert engine.store.get_node(run_id, "commit")["last_event"] == "nothing_to_commit"
    assert statuses(engine, run_id)["approval"] == "skipped"


def test_mensaje_de_commit_generico_se_rechaza(tmp_path, remote):
    dev = writes_file(suggested_commit_subject="fix")
    engine, run_id, _ = setup(tmp_path, remote, dev)
    assert drive(engine, run_id) == "failed"
    assert engine.store.get_node(run_id, "commit")["last_event"] == "invalid_message"


def test_prepare_workspace_es_idempotente_y_cleanup_borra(tmp_path, remote):
    engine, run_id, _ = setup(tmp_path, remote, writes_file())
    drive(engine, run_id)
    run = engine.store.get_run(run_id)
    plan = engine._state(run).plan
    cfg = {"repo_url": str(remote[0]), "base_branch": "main", "new_branch": "feature/x"}
    repo = Path(engine.store.latest_outputs(run_id, "ws")["path"])
    (repo / ".git" / "marca-de-prueba").write_text("x", encoding="utf-8")

    ctx = ExecContext(engine, run, plan, "ws", 2, cfg, tmp_path / "a2")
    again = asyncio.run(engine.executors["prepare_workspace"].run(ctx))
    assert again.event == "ok" and (repo / ".git" / "marca-de-prueba").exists()  # no se volvió a clonar

    ctx = ExecContext(engine, run, plan, "ws", 1, {"workspace": str(repo)}, tmp_path / "a3")
    assert asyncio.run(engine.executors["cleanup_workspace"].run(ctx)).event == "ok"
    assert not repo.exists()

    outside = ExecContext(engine, run, plan, "ws", 1, {"workspace": str(tmp_path)}, tmp_path / "a4")
    assert asyncio.run(engine.executors["cleanup_workspace"].run(outside)).event == "failed"
    assert tmp_path.exists()


def test_rama_base_inexistente_falla_con_mensaje_claro(tmp_path, remote):
    def edit(data):
        data["nodes"]["ws"]["config"]["base_branch"] = "no-existe"

    engine, run_id, _ = setup(tmp_path, remote, writes_file(), plan_edit=edit)
    assert drive(engine, run_id) == "failed"
    assert "no-existe" in engine.store.attempts(run_id, "ws")[0]["error"]


def test_auditoria_de_comandos_ejecutados(tmp_path, remote):
    engine, run_id, _ = setup(tmp_path, remote, writes_file())
    drive(engine, run_id)
    approve(engine)
    drive(engine, run_id)
    executions = engine.store.executions(run_id)
    kinds = {e["kind"] for e in executions}
    assert {"git", "gh"} <= kinds
    clone = next(e for e in executions if e["argv"][:2] == ["git", "-c"] and "clone" in e["argv"])
    assert clone["exit_code"] == 0 and Path(clone["stdout_path"]).exists()


def test_el_commit_funciona_con_data_dir_relativo(tmp_path, monkeypatch):
    """Regresión: con ./data relativo, git -F recibía una ruta relativa al cwd equivocado."""
    from gestplan.db import Store
    from gestplan.engine.engine import Engine
    from gestplan.settings import Settings

    monkeypatch.chdir(tmp_path)
    engine = Engine(Store(Settings(data_dir=Path("data")).db_path), Settings(data_dir=Path("data")))
    assert engine.attempt_dir("r1", "commit", 1).is_absolute()


def test_la_vista_previa_del_pr_no_muestra_atribucion_a_una_ia(tmp_path):
    from gestplan.executors.git import GitCreatePrExec

    engine = make_engine(tmp_path)
    config = {"workspace": ".", "base_branch": "main", "head_branch": "feature/x", "title": "Título largo del PR",
              "body": "Contexto del cambio.\n\n\U0001f916 Generated with [Claude Code](https://claude.com/claude-code)"}
    ctx = ExecContext(engine, {"id": "r"}, None, "pr", 1, config, tmp_path / "a")
    preview = asyncio.run(GitCreatePrExec().preview(ctx))
    assert "Contexto del cambio." in preview["text"] and "Claude" not in preview["text"]
