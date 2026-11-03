"""Escaneo determinista de secretos: patrones, diff, ficheros nuevos y nodo con git real."""

import os
import subprocess

from gestplan.secretscan import entropy, scan_diff, scan_line, scan_text, suspicious_filename
from tests.helpers import build, drive, make_engine

FAKE_GH = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


def test_patrones_conocidos_y_enmascarado():
    assert scan_line("a.py", 1, f'TOKEN = "{FAKE_GH}"')[0].kind in ("token de GitHub", "asignación de secreto")
    kinds = {f.kind for f in scan_line("a.py", 1, "-----BEGIN RSA PRIVATE KEY-----")}
    assert kinds == {"clave privada"}
    assert scan_line("a.py", 1, "clave = AKIAABCDEFGHIJKLMNOP")[0].kind == "AWS access key"
    found = scan_line("a.py", 1, 'password = "SuperSecreta123"')[0]
    assert found.sample == "Supe***" and "SuperSecreta123" not in str(found.as_dict())
    assert scan_line("a.py", 1, "url = https://user:pass1234@host.com/x")[0].kind == "credenciales en URL"


def test_falsos_positivos_y_marcador_de_permiso():
    assert scan_line("a.py", 1, 'password = "changeme"') == []
    assert scan_line("a.py", 1, 'api_key = "${API_KEY}"') == []
    assert scan_line("a.py", 1, 'token = os.environ["TOKEN"]') == []
    assert scan_line("a.py", 1, f'x = "{FAKE_GH}"  # gestplan:allow-secret') == []
    assert scan_line("a.py", 1, 'sha = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"') == []
    assert entropy("aaaa") == 0


def test_solo_se_analizan_las_lineas_anadidas():
    diff = ("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,3 @@\n"
            ' password = "ViejaSecreta99"\n+ok = 1\n+secret = "NuevaSecreta99"\n')
    findings = scan_diff(diff)
    assert [(f.file, f.line) for f in findings] == [("x.py", 3)]
    assert scan_text("n.txt", "a\nb\napi_key = 'abcdef123456'\n")[0].line == 3


def test_nombres_de_fichero_sospechosos():
    assert suspicious_filename("config/.env") and suspicious_filename("certs/server.pem")
    assert suspicious_filename(".env.example") is None and suspicious_filename("src/app.py") is None


def git(ws, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e.com", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@e.com"}
    subprocess.run(["git", *args], cwd=ws, check=True, capture_output=True, env=env)


def scan_plan(ws):
    return {"name": "scan", "nodes": {"s": {"type": "secret_scan", "config": {"workspace": str(ws)}}}, "edges": []}


def test_el_nodo_detecta_secretos_en_ficheros_nuevos_y_modificados(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    git(ws, "init", "-b", "main")
    (ws / "base.py").write_text("x = 1\n", encoding="utf-8")
    git(ws, "add", "-A")
    git(ws, "commit", "-m", "inicial")

    engine = make_engine(tmp_path)
    limpio = build(engine, scan_plan(ws))
    drive(engine, limpio)
    assert engine.store.attempts(limpio, "s")[-1]["event"] == "clean"

    (ws / "base.py").write_text(f'x = 1\ntoken = "{FAKE_GH}"\n', encoding="utf-8")
    (ws / "nuevo.py").write_text("password = 'OtraSecreta123'\n", encoding="utf-8")
    (ws / ".env").write_text("A=1\n", encoding="utf-8")
    sucio = build(engine, scan_plan(ws))
    drive(engine, sucio)
    attempt = engine.store.attempts(sucio, "s")[-1]
    assert attempt["event"] == "secrets_detected"
    files = {f["file"] for f in attempt["outputs"]["findings"]}
    assert files == {"base.py", "nuevo.py", ".env"}
    assert FAKE_GH not in attempt["outputs"]["summary"] and "OtraSecreta123" not in str(attempt["outputs"])
