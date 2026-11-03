import json

from gestplan.plans.inputs import resolve_inputs
from gestplan.plans.loader import load_plan, parse_plan


def test_carga_el_plan_de_ejemplo(root):
    plan, issues = load_plan(root / "plans" / "examples" / "implement-task.json")
    assert issues == []
    assert plan.name == "implement-task"
    assert plan.edges[0].source_node == "ws"
    assert plan.edges[0].source_event == "ok"


def test_campos_desconocidos_son_error(example_data):
    example_data["nodos"] = {}
    plan, issues = parse_plan(example_data)
    assert plan is None
    assert any(i.code == "schema" and "nodos" in (i.path or "") for i in issues)


def test_arista_con_formato_incorrecto(example_data):
    example_data["edges"][0]["from"] = "ws"
    plan, issues = parse_plan(example_data)
    assert plan is None
    assert any("nodo.evento" in i.message for i in issues)


def test_id_de_nodo_no_valido(example_data):
    example_data["nodes"]["Mal-Id"] = example_data["nodes"].pop("ws")
    plan, issues = parse_plan(example_data)
    assert plan is None


def test_json_invalido_da_linea_y_columna(tmp_path):
    path = tmp_path / "p.json"
    path.write_text('{"name": ', encoding="utf-8")
    plan, issues = load_plan(path)
    assert plan is None
    assert issues[0].code == "json_syntax"
    assert "línea" in issues[0].message


def test_fichero_inexistente(tmp_path):
    plan, issues = load_plan(tmp_path / "nada.json")
    assert plan is None and issues[0].code == "file_not_found"


def test_acepta_bom(tmp_path, example_data):
    path = tmp_path / "p.json"
    path.write_text(json.dumps(example_data), encoding="utf-8-sig")
    plan, issues = load_plan(path)
    assert issues == [] and plan is not None


def test_resolve_inputs_defaults_tipos_y_errores(example_data):
    plan, _ = parse_plan(example_data)
    values, issues = resolve_inputs(plan, {"task_id": "TASK-7", "repo_url": "https://x/y.git"})
    assert issues == []
    assert values["base"] == "main"

    _, issues = resolve_inputs(plan, {"repo_url": "https://x/y.git"})
    assert [i.code for i in issues] == ["missing_input"]

    _, issues = resolve_inputs(plan, {"task_id": "malo", "repo_url": "u"})
    assert [i.code for i in issues] == ["input_pattern"]

    _, issues = resolve_inputs(plan, {"task_id": "TASK-1", "repo_url": "u", "otro": "1"})
    assert [i.code for i in issues] == ["unknown_input"]
