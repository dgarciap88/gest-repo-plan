"""Subcomando `gestplan plans`: lista los planes de plans/ y plans/examples/."""

import json

from gestplan.cli import main
from tests.conftest import ROOT

SALUDO = (
    'META = {"description": "saluda", "side_effect": "none", "events": ["ok", "failed"]}\n'
    "def run(inputs, ctx):\n"
    "    return {'event': 'ok', 'outputs': {}}\n"
)


def _plan(name: str, description: str = "") -> dict:
    return {
        "name": name,
        "description": description,
        "inputs": {
            "texto": {"type": "string", "required": True},
            "opcional": {"type": "string", "default": "x"},
        },
        "nodes": {"saludo": {"type": "python_script", "config": {"script": "saludo",
                                                                  "inputs": {"texto": "${inputs.texto}"}}}},
    }


def test_lista_los_planes_del_repo(capsys):
    assert main(["plans", "--root", str(ROOT)]) == 0
    lines = capsys.readouterr().out.splitlines()
    names = [line.split()[0] for line in lines]
    assert "implement-task" in names and "implement-tasks" in names
    assert names == sorted(names)
    assert not any("NO VÁLIDO" in line for line in lines)
    task_line = next(line for line in lines if line.startswith("implement-task "))
    assert "obligatorios: task_id, repo_url" in task_line


def test_plan_invalido_se_marca_y_no_rompe(tmp_path, capsys):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "saludo.py").write_text(SALUDO, encoding="utf-8")
    (tmp_path / "plans" / "examples").mkdir(parents=True)
    (tmp_path / "plans" / "roto.json").write_text("{ esto no es json", encoding="utf-8")
    (tmp_path / "plans" / "bueno.json").write_text(json.dumps(_plan("bueno", "Un plan bueno")), encoding="utf-8")
    (tmp_path / "plans" / "examples" / "ejemplo.json").write_text(
        json.dumps(_plan("ejemplo")), encoding="utf-8")

    assert main(["plans", "--root", str(tmp_path)]) == 0
    lines = capsys.readouterr().out.splitlines()

    assert [line.split()[0] for line in lines] == ["bueno", "ejemplo", "roto"]  # ordenados por nombre
    roto = lines[2]
    assert "[NO VÁLIDO]" in roto
    bueno = lines[0]
    assert "1 nodos" in bueno and "obligatorios: texto" in bueno and "Un plan bueno" in bueno
    assert "[NO VÁLIDO]" not in bueno and "[NO VÁLIDO]" not in lines[1]


def test_sin_planes(tmp_path, capsys):
    assert main(["plans", "--root", str(tmp_path)]) == 0
    assert "No hay planes" in capsys.readouterr().out
