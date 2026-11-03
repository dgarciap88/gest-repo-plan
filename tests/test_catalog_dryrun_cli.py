import json

from gestplan.catalog import build_catalog, render_markdown
from gestplan.cli import main
from gestplan.doctor import run_doctor
from gestplan.dryrun import dry_run
from gestplan.nodes.registry import all_node_types
from gestplan.plans.loader import parse_plan
from gestplan.settings import Settings

EXAMPLE = "plans/examples/implement-task.json"


def test_el_catalogo_incluye_todos_los_nodos_con_schemas():
    catalog = build_catalog(Settings())
    types = {n["type"] for n in catalog["nodes"]}
    assert types == {cls.type for cls in all_node_types()}
    assert {"prepare_workspace", "ia_agent", "ia_agent_validator", "git.push", "route",
            "manual_approval", "launch_plans", "wait_plan", "run_command"} <= types
    for node in catalog["nodes"]:
        assert node["config_schema"]["type"] == "object"
        assert node["description"]


def test_los_ejemplos_del_catalogo_son_configs_validas():
    for cls in all_node_types():
        if cls.example:
            assert cls.parse_config(cls.example) is not None, cls.type


def test_markdown_del_catalogo():
    text = render_markdown(build_catalog(Settings()))
    assert "### `git.push`" in text
    assert "EXTERNO" in text
    assert "`changes_requested`" in text
    assert "approves" in text


def test_dry_run_del_ejemplo(root, example_data):
    plan, _ = parse_plan(example_data)
    report = dry_run(plan, {"task_id": "TASK-1", "repo_url": "u"}, root, Settings())
    assert report.ok
    by_id = {n.id: n for n in report.nodes}
    assert by_id["push"].side_effect == "external"
    assert by_id["push"].approved_by == ["approval"]
    assert by_id["dev"].loop_back == []
    assert by_id["val"].loop_back == ["dev"]
    assert "cubierto por: approval" in report.render()


def test_dry_run_con_inputs_incorrectos(root, example_data):
    plan, _ = parse_plan(example_data)
    report = dry_run(plan, {}, root, Settings())
    assert not report.ok and not report.nodes


def test_doctor_con_runner_falso():
    outputs = {
        "--version": (0, "version-x"),
        "config": (0, "yo@users.noreply.github.com"),
        "status": (0, "Logged in"),
    }

    def runner(argv):
        return outputs.get(argv[1], (0, "ok"))

    found = {"git": "git", "gh": "gh", "claude": "claude", "copilot": None}
    checks = run_doctor(runner, found.get)
    status = {c.name: c.status for c in checks}
    assert status["git"] == "ok" and status["gh auth"] == "ok" and status["claude"] == "ok"
    assert status["git user.email"] == "ok"
    assert status["copilot"] == "warn"


def test_doctor_detecta_fallos_y_correo_privado():
    def runner(argv):
        if argv[1:] == ["config", "user.email"]:
            return 0, "yo@hotmail.com"
        if argv[1:] == ["auth", "status"]:
            return 1, "not logged"
        return 0, "ok"

    checks = run_doctor(runner, lambda name: name if name != "claude" else None)
    status = {c.name: c.status for c in checks}
    assert status["git user.email"] == "warn"
    assert status["gh auth"] == "fail"


def test_cli_validate_ok_y_error(root, tmp_path, capsys, example_data):
    assert main(["validate", str(root / EXAMPLE), "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "0 error(es)" in out

    example_data["nodes"]["approval"]["config"]["approves"] = ["pr"]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(example_data), encoding="utf-8")
    assert main(["validate", str(bad), "--root", str(root)]) == 1
    assert "missing_approval" in capsys.readouterr().out


def test_cli_validate_json_y_fichero_inexistente(root, tmp_path, capsys):
    assert main(["validate", str(tmp_path / "x.json"), "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and data["issues"][0]["code"] == "file_not_found"


def test_cli_catalog(capsys):
    assert main(["catalog"]) == 0
    assert "# Catálogo de nodos" in capsys.readouterr().out
    assert main(["catalog", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["nodes"]


def test_cli_dry_run(root, capsys):
    argv = ["dry-run", str(root / EXAMPLE), "--root", str(root),
            "--input", "task_id=TASK-3", "--input", "repo_url=https://x/y.git"]
    assert main(argv) == 0
    assert "Orden de ejecución" in capsys.readouterr().out
    assert main(["dry-run", str(root / EXAMPLE), "--root", str(root)]) == 1
