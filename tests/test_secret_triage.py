"""Triaje con IA de secretos: el agente solo ve fragmentos enmascarados y, ante la duda, es un secreto."""

from gestplan.engine.runner import FakeRunner, RoutingRunner
from gestplan.secretscan import mask_line, masked_context
from tests.helpers import build, drive, make_engine
from tests.test_agents import FakeClaude

REAL = "SuperSecreta123"
FINDINGS = [
    {"file": "app/config.py", "line": 4, "kind": "asignación de secreto", "sample": "Supe***"},
    {"file": "docs/ejemplo.md", "line": 2, "kind": "asignación de secreto", "sample": "ejem***"},
    {"file": ".env", "line": 0, "kind": "fichero que suele contener secretos", "sample": "***"},
]


def workspace(tmp_path):
    ws = tmp_path / "repo"
    (ws / "app").mkdir(parents=True)
    (ws / "docs").mkdir()
    code = f'import os\nimport sys\n\nPASSWORD = "{REAL}"\nHOST = "localhost"\nDEBUG = True\nX = 1\nY = 2\n'
    (ws / "app" / "config.py").write_text(code, encoding="utf-8")
    (ws / "docs" / "ejemplo.md").write_text('Ejemplo:\napi_key = "tu-clave-de-ejemplo"\nfin\n', encoding="utf-8")
    (ws / ".env").write_text("A=1\n", encoding="utf-8")
    return ws


def triage_plan(ws, findings=None):
    return {"name": "triage", "nodes": {"t": {"type": "secret_triage", "config": {
        "workspace": str(ws), "findings": FINDINGS if findings is None else findings}}}, "edges": []}


def run(tmp_path, verdicts, findings=None):
    ws = workspace(tmp_path)
    claude = FakeClaude(val=[{"verdicts": verdicts}])
    engine = make_engine(tmp_path, runner=RoutingRunner({"claude": FakeRunner(claude)}), claude_path="claude")
    run_id = build(engine, triage_plan(ws, findings))
    drive(engine, run_id)
    return engine, run_id, claude, engine.store.attempts(run_id, "t")[-1]


def test_el_agente_solo_ve_fragmentos_enmascarados(tmp_path):
    verdicts = [{"index": 0, "verdict": "secret", "reason": "contraseña real"},
                {"index": 1, "verdict": "false_positive", "reason": "ejemplo de documentación"},
                {"index": 2, "verdict": "false_positive", "reason": "plantilla"}]
    _engine, _run_id, claude, attempt = run(tmp_path, verdicts)
    prompt = claude.calls[0]["stdin"]
    assert REAL not in prompt  # el secreto en claro nunca llega al agente
    assert "PASSWORD = " in prompt and "Supe***" in prompt  # contexto con el valor enmascarado
    assert "4> PASSWORD" in prompt and "HOST" in prompt  # la línea marcada y sus vecinas
    assert "DEBUG = True" in prompt and "import os" not in prompt and "X = 1" not in prompt  # solo ±2 líneas, no el fichero entero
    assert "no se envía su contenido" in prompt  # el hallazgo por nombre de fichero no manda contenido
    assert attempt["event"] == "confirmed"
    assert [f["file"] for f in attempt["outputs"]["confirmed"]] == ["app/config.py"]
    assert {f["file"] for f in attempt["outputs"]["dismissed"]} == {"docs/ejemplo.md", ".env"}
    assert "SECRETO app/config.py:4" in attempt["outputs"]["summary"]


def test_todos_falsos_positivos_deja_continuar(tmp_path):
    verdicts = [{"index": i, "verdict": "false_positive", "reason": "ok"} for i in range(3)]
    _engine, _run_id, _claude, attempt = run(tmp_path, verdicts)
    assert attempt["event"] == "all_false_positive" and attempt["outputs"]["confirmed"] == []


def test_sin_veredicto_se_trata_como_secreto(tmp_path):
    _engine, _run_id, _claude, attempt = run(tmp_path, [{"index": 1, "verdict": "false_positive", "reason": "x"}])
    confirmed = {f["file"]: f["reason"] for f in attempt["outputs"]["confirmed"]}
    assert attempt["event"] == "confirmed" and confirmed["app/config.py"] == "sin veredicto del agente"
    assert ".env" in confirmed


def test_sin_hallazgos_no_llama_al_agente(tmp_path):
    _engine, _run_id, claude, attempt = run(tmp_path, [], findings=[])
    assert attempt["event"] == "all_false_positive" and claude.calls == []


def test_enmascarado_de_lineas_y_rutas(tmp_path):
    assert REAL not in mask_line(f'password = "{REAL}"') and "Supe***" in mask_line(f'password = "{REAL}"')
    assert mask_line("MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7VJTUt9Us8cKj") == "***"  # cuerpo de clave
    assert mask_line("x = 1") == "x = 1"
    ws = workspace(tmp_path)
    assert "fuera del workspace" in masked_context(ws, "../secreto.txt", 1)
    assert "no legible" in masked_context(ws, "no-existe.py", 1)
