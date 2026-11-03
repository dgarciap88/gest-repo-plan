import dataclasses

from gestplan.plans.loader import parse_plan
from gestplan.plans.validator import validate_plan
from gestplan.settings import Settings


def run(data, root, settings=None):
    plan, issues = parse_plan(data)
    assert plan is not None, issues
    return validate_plan(plan, root, settings or Settings())


def codes(result, severity="error"):
    return sorted(i.code for i in result.issues if i.severity == severity)


def test_el_plan_de_ejemplo_es_valido(example_data, root):
    result = run(example_data, root)
    assert result.ok, [i.format() for i in result.errors]


def test_tipo_de_nodo_desconocido(example_data, root):
    example_data["nodes"]["dev"]["type"] = "ia_agente"
    assert "unknown_node_type" in codes(run(example_data, root))


def test_config_invalida(example_data, root):
    example_data["nodes"]["ws"]["config"].pop("repo_url")
    result = run(example_data, root)
    issue = next(i for i in result.errors if i.code == "invalid_config")
    assert issue.node == "ws" and "repo_url" in issue.path


def test_version_no_soportada(example_data, root):
    example_data["nodes"]["ws"]["version"] = 9
    assert "unsupported_version" in codes(run(example_data, root))


def test_arista_con_evento_inexistente(example_data, root):
    example_data["edges"][0]["from"] = "ws.listo"
    result = run(example_data, root)
    issue = next(i for i in result.errors if i.code == "edge_unknown_event")
    assert "ok" in issue.hint


def test_arista_con_nodos_inexistentes(example_data, root):
    example_data["edges"].append({"from": "nadie.ok", "to": "ws"})
    example_data["edges"].append({"from": "ws.ok", "to": "nadie"})
    assert {"edge_unknown_source", "edge_unknown_target"} <= set(codes(run(example_data, root)))


def test_efecto_externo_sin_aprobacion(example_data, root):
    example_data["nodes"]["approval"]["config"]["approves"] = ["pr"]
    result = run(example_data, root)
    missing = [i for i in result.errors if i.code == "missing_approval"]
    assert [i.node for i in missing] == ["push"]


def test_aprobacion_que_no_domina_todos_los_caminos(example_data, root):
    # un atajo que llega a push sin pasar por la aprobación
    example_data["edges"].append({"from": "commit.nothing_to_commit", "to": "push"})
    result = run(example_data, root)
    assert "missing_approval" in codes(result)


def test_aprobacion_apunta_a_nodo_inexistente(example_data, root):
    example_data["nodes"]["approval"]["config"]["approves"] = ["push", "pr", "fantasma"]
    assert "approval_target_unknown" in codes(run(example_data, root))


def test_input_no_declarado_y_sin_usar(example_data, root):
    example_data["nodes"]["ws"]["config"]["new_branch"] = "x/${inputs.otro}"
    result = run(example_data, root)
    assert "undeclared_input" in codes(result)


def test_input_sin_usar_es_aviso(example_data, root):
    example_data["inputs"]["extra"] = {"type": "string"}
    assert "unused_input" in codes(run(example_data, root), "warning")


def test_salida_inexistente(example_data, root):
    example_data["nodes"]["pr"]["config"]["title"] = "${nodes.dev.outputs.no_existe}"
    result = run(example_data, root)
    issue = next(i for i in result.errors if i.code == "unknown_output")
    assert "suggested_pr_title" in issue.hint


def test_referencia_a_nodo_que_no_es_anterior(example_data, root):
    example_data["nodes"]["ws"]["config"]["new_branch"] = "${nodes.pr.outputs.pr_url}"
    assert "reference_not_upstream" in codes(run(example_data, root))


def test_referencia_con_formato_incorrecto(example_data, root):
    example_data["nodes"]["ws"]["config"]["new_branch"] = "${nodes.dev}"
    example_data["nodes"]["tests"]["config"]["workspace"] = "${foo.bar}"
    assert {"bad_reference"} <= set(codes(run(example_data, root)))


def test_ciclo_sin_limite(example_data, root):
    example_data["nodes"]["dev"].pop("max_attempts")
    example_data["nodes"]["val"]["config"]["max_retries"] = 2
    # el bucle tests.failed -> dev sigue estando acotado por el validador (dev es destino de changes_requested)
    assert run(example_data, root).ok

    example_data["edges"] = [e for e in example_data["edges"] if e["from"] != "val.changes_requested"]
    result = run(example_data, root)
    assert "unbounded_cycle" in codes(result)


def test_ciclo_acotado_con_max_attempts(example_data, root):
    example_data["edges"] = [e for e in example_data["edges"] if e["from"] != "val.changes_requested"]
    # sin ese bucle, dev ya no puede usar el feedback del validador (no se ejecuta antes)
    example_data["nodes"]["dev"]["config"]["variables"].pop("feedback")
    assert run(example_data, root).ok  # el bucle tests.failed -> dev sigue acotado por max_attempts

    example_data["nodes"]["dev"].pop("max_attempts")
    assert "unbounded_cycle" in codes(run(example_data, root))


def test_nodo_inalcanzable(example_data, root):
    example_data["nodes"]["a"] = {"type": "route", "config": {
        "cases": [{"event": "x", "when": {"path": "inputs.base", "op": "exists"}}], "default": "y"}}
    example_data["nodes"]["b"] = {"type": "wait_plan", "config": {"launched_by": "a"}}
    example_data["edges"] += [{"from": "a.x", "to": "b"}, {"from": "a.y", "to": "b"},
                              {"from": "b.all_ok", "to": "a"}]
    result = run(example_data, root)
    assert "unreachable_node" in codes(result)


def test_route_exige_aristas_y_regex_valida(example_data, root):
    example_data["nodes"]["r"] = {"type": "route", "config": {
        "cases": [{"event": "bug", "when": {"path": "inputs.task_id", "op": "matches", "value": "("}}],
        "default": "otro"}}
    example_data["edges"].append({"from": "ws.failed", "to": "r"})
    result = run(example_data, root)
    assert {"invalid_regex", "route_event_unconnected"} <= set(codes(result))


def test_route_valido(example_data, root):
    example_data["nodes"]["r"] = {"type": "route", "config": {
        "cases": [{"event": "bug", "when": {"path": "inputs.task_id", "op": "matches", "value": "^TASK-1"}}],
        "default": "otro"}}
    example_data["nodes"]["fin1"] = {"type": "cleanup_workspace",
                                      "config": {"workspace": "${nodes.ws.outputs.path}"}}
    example_data["edges"] += [{"from": "ws.failed", "to": "r"}, {"from": "r.bug", "to": "fin1"},
                              {"from": "r.otro", "to": "fin1"}]
    assert run(example_data, root).ok


def test_script_inexistente_y_con_meta(example_data, root, tmp_path):
    example_data["nodes"]["s"] = {"type": "python_script", "config": {"script": "hola"}}
    example_data["edges"].append({"from": "ws.failed", "to": "s"})
    result = run(example_data, root, Settings())
    assert "missing_script" in codes(result)

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "hola.py").write_text(
        'META = {"description": "x", "side_effect": "none", "events": ["ok", "failed"]}\n'
        "def run(inputs, ctx):\n    return {'event': 'ok', 'outputs': {}}\n",
        encoding="utf-8",
    )
    example_data["nodes"]["dev"]["config"]["prompt_template"] = "templates/prompts/implement-task.md"
    (tmp_path / "templates" / "prompts").mkdir(parents=True)
    for name in ("implement-task.md", "validate-task.md"):
        (tmp_path / "templates" / "prompts" / name).write_text("x", encoding="utf-8")
    result = run(example_data, tmp_path, Settings())
    assert "missing_script" not in codes(result)


def test_script_con_efecto_externo_necesita_aprobacion(example_data, tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "publicar.py").write_text('META = {"events": ["ok", "failed"]}\n', encoding="utf-8")
    example_data["nodes"]["s"] = {"type": "python_script", "config": {"script": "publicar"}}
    example_data["edges"].append({"from": "ws.failed", "to": "s"})
    (tmp_path / "templates" / "prompts").mkdir(parents=True)
    for name in ("implement-task.md", "validate-task.md"):
        (tmp_path / "templates" / "prompts" / name).write_text("x", encoding="utf-8")
    result = run(example_data, tmp_path, Settings())
    assert any(i.code == "missing_approval" and i.node == "s" for i in result.errors)


def test_plantilla_de_prompt_inexistente(example_data, tmp_path):
    result = run(example_data, tmp_path)
    assert "missing_template" in codes(result)


def test_nivel_de_modelo_sobre_el_tope(example_data, root):
    example_data["nodes"]["dev"]["config"]["model_tier"] = "premium"
    assert "model_tier_above_cap" in codes(run(example_data, root))
    permissive = dataclasses.replace(Settings(), max_model_tier="premium")
    assert run(example_data, root, permissive).ok


def test_modelo_explicito_fuera_de_la_lista(example_data, root):
    example_data["nodes"]["dev"]["config"]["model"] = "caro"
    settings = dataclasses.replace(Settings(), allowed_models=("barato",))
    assert "model_not_allowed" in codes(run(example_data, root, settings))


def test_topes_de_timeout_intentos_y_reintentos(example_data, root):
    example_data["nodes"]["dev"]["timeout_s"] = 10**6
    example_data["nodes"]["dev"]["max_attempts"] = 99
    example_data["nodes"]["val"]["config"]["max_retries"] = 50
    assert {"timeout_above_cap", "attempts_above_cap", "retries_above_cap"} <= set(
        codes(run(example_data, root))
    )


def test_comando_fuera_de_la_lista_de_permitidos(example_data, root):
    example_data["nodes"]["tests"]["config"]["command"] = ["rm", "-rf", "."]
    assert "command_not_allowed" in codes(run(example_data, root))


def test_wait_plan_y_launch_plans(example_data, root):
    example_data["nodes"]["launch"] = {"type": "launch_plans", "config": {
        "plan": "implement-task", "items": [{"task_id": "TASK-1", "repo_url": "u"}]}}
    example_data["nodes"]["wait"] = {"type": "wait_plan", "config": {"launched_by": "launch"}}
    example_data["edges"] += [{"from": "ws.failed", "to": "launch"}, {"from": "launch.ok", "to": "wait"}]
    assert run(example_data, root).ok

    example_data["nodes"]["wait"]["config"]["launched_by"] = "ws"
    assert "wait_target_invalid" in codes(run(example_data, root))


def test_plan_hijo_inexistente(example_data, root):
    example_data["nodes"]["launch"] = {"type": "launch_plans", "config": {
        "plan": "no-existe", "items": []}}
    example_data["edges"].append({"from": "ws.failed", "to": "launch"})
    assert "missing_plan" in codes(run(example_data, root))


def test_avisos_de_eventos_sin_conectar(example_data, root):
    warnings = codes(run(example_data, root), "warning")
    assert "unconnected_failure_event" in warnings
