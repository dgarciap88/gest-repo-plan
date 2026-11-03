"""CLI de operación de extremo a extremo con scripts Python reales (sin mocks de proceso)."""

import json

from gestplan.cli import main

PLAN = {
    "name": "demo",
    "inputs": {"texto": {"type": "string", "default": "hola"}},
    "nodes": {
        "saludo": {"type": "python_script", "config": {"script": "saludo", "inputs": {"texto": "${inputs.texto}"}}},
        "ok": {"type": "manual_approval", "config": {"approves": ["publicar"], "message": "¿Publicamos?"}},
        "publicar": {"type": "python_script", "config": {"script": "publicar",
                                                          "inputs": {"mensaje": "${nodes.saludo.outputs.mensaje}"}}},
    },
    "edges": [{"from": "saludo.ok", "to": "ok"}, {"from": "ok.approved", "to": "publicar"}],
}

SALUDO = (
    'META = {"description": "saluda", "side_effect": "none", "events": ["ok", "failed"]}\n'
    "def run(inputs, ctx):\n"
    "    return {'event': 'ok', 'outputs': {'mensaje': inputs['texto'].upper()}}\n"
)
PUBLICAR = (
    'META = {"description": "publica", "side_effect": "external", "events": ["ok", "failed"]}\n'
    "def run(inputs, ctx):\n"
    "    return {'event': 'ok', 'outputs': {'publicado': inputs['mensaje']}}\n"
)


def project(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "saludo.py").write_text(SALUDO, encoding="utf-8")
    (tmp_path / "scripts" / "publicar.py").write_text(PUBLICAR, encoding="utf-8")
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "demo.json").write_text(json.dumps(PLAN), encoding="utf-8")
    return ["--root", str(tmp_path), "--data-dir", str(tmp_path / "data")]


def run_id_from(output: str) -> str:
    return next(line.split(": ", 1)[1].strip() for line in output.splitlines() if line.startswith("Run creado"))


def test_ciclo_completo_por_cli(tmp_path, capsys):
    common = project(tmp_path)

    assert main(["run", "demo", "--input", "texto=buenas", *common]) == 3  # espera aprobación
    out = capsys.readouterr().out
    run_id = run_id_from(out)
    assert "WAITING" in out and "aprobación #1 pendiente" in out

    assert main(["approvals", "list", *common]) == 0
    assert "#1" in capsys.readouterr().out
    assert main(["approvals", "show", "1", *common]) == 0
    shown = capsys.readouterr().out
    assert "¿Publicamos?" in shown and "publicar" in shown
    # la vista previa del script muestra qué se va a ejecutar, con los inputs ya resueltos
    assert "efecto: external" in shown and "BUENAS" in shown

    assert main(["approvals", "approve", "1", "-c", "adelante", *common]) == 0
    capsys.readouterr()
    assert main(["resume", run_id, *common]) == 0
    assert "SUCCEEDED" in capsys.readouterr().out

    assert main(["status", run_id, *common]) == 0
    assert "publicar" in capsys.readouterr().out
    assert main(["status", *common]) == 0
    assert run_id in capsys.readouterr().out
    assert main(["logs", run_id, "publicar", *common]) == 0
    assert "exec-01-script" in capsys.readouterr().out
    assert main(["cost", run_id, *common]) == 0
    assert "Total: 0.0000 USD" in capsys.readouterr().out


def test_rechazar_y_reanudar(tmp_path, capsys):
    common = project(tmp_path)
    main(["run", "demo", *common])
    run_id = run_id_from(capsys.readouterr().out)
    assert main(["approvals", "reject", "1", "-c", "no", *common]) == 0
    capsys.readouterr()
    assert main(["resume", run_id, *common]) == 1  # 'rejected' sin arista: el run falla
    assert "FAILED" in capsys.readouterr().out
    # decidir dos veces la misma aprobación no está permitido
    assert main(["approvals", "approve", "1", *common]) == 2


def test_cancelar_por_cli(tmp_path, capsys):
    common = project(tmp_path)
    main(["run", "demo", *common])
    run_id = run_id_from(capsys.readouterr().out)
    assert main(["cancel", run_id, *common]) == 0
    assert "CANCELLED" in capsys.readouterr().out
    assert main(["approvals", "list", *common]) == 0
    assert "No hay aprobaciones pendientes" in capsys.readouterr().out
    assert main(["retry", run_id, "ok", "--drive", *common]) == 3  # vuelve a esperar la aprobación
    capsys.readouterr()


def test_errores_de_uso(tmp_path, capsys):
    common = project(tmp_path)
    assert main(["run", "no-existe", *common]) == 2
    assert main(["run", "demo", "--input", "otro=1", *common]) == 1  # input no declarado
    assert "unknown_input" in capsys.readouterr().out
    assert main(["status", "zzz", *common]) == 2
    assert main(["resume", "zzz", *common]) == 2
    assert main(["approvals", "show", "99", *common]) == 2
