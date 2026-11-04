"""Administración de la configuración: lectura sin secretos, validación y escritura del .env."""

import os

import pytest
from fastapi.testclient import TestClient

from gestplan import config_admin
from gestplan.server.app import create_app
from tests.helpers import make_engine, make_settings
from tests.test_api import local_client

H = {"X-Gestplan": "1"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    for param in config_admin.PARAMS:
        monkeypatch.delenv(param.name, raising=False)
    (tmp_path / ".env").write_text("# comentario\nJIRA_EMAIL=a@b.c\nJIRA_API_TOKEN=s3cr3t\nOTRA=1\n", encoding="utf-8")
    engine = make_engine(tmp_path)
    engine.settings = make_settings(tmp_path)
    with local_client(create_app(engine=engine, serve_ui=False)) as c:
        c.tmp = tmp_path
        yield c


def params(client):
    return {p["name"]: p for p in client.get("/api/config").json()["params"]}


def test_lectura_no_expone_secretos(client):
    data = client.get("/api/config").json()
    token = next(p for p in data["params"] if p["name"] == "JIRA_API_TOKEN")
    assert token["configured"] is True and token["value"] == "" and token["effective"] == ""
    assert "s3cr3t" not in client.get("/api/config").text
    assert next(p for p in data["params"] if p["name"] == "JIRA_EMAIL")["value"] == "a@b.c"
    assert set(data["groups"]) >= {p.group for p in config_admin.PARAMS}


def test_guardar_conserva_el_resto_del_fichero_y_aplica_en_caliente(client):
    reply = client.put("/api/config", headers=H, json={"values": {
        "GP_DAILY_BUDGET_USD": "5.5", "JIRA_EMAIL": "nuevo@b.c", "JIRA_API_TOKEN": "", "GP_MAX_AGENTS_PARALLEL": "3"}})
    assert reply.status_code == 200
    assert reply.json() == {"changed": ["GP_DAILY_BUDGET_USD", "GP_MAX_AGENTS_PARALLEL", "JIRA_EMAIL"],
                            "restart_required": ["GP_MAX_AGENTS_PARALLEL"]}
    text = (client.tmp / ".env").read_text(encoding="utf-8")
    assert "# comentario" in text and "OTRA=1" in text and "JIRA_API_TOKEN=s3cr3t" in text  # '' no toca el secreto
    assert "JIRA_EMAIL=nuevo@b.c" in text and "GP_DAILY_BUDGET_USD=5.5" in text
    assert client.get("/api/stats").json()["daily_budget_usd"] == 5.5  # sin reiniciar
    assert params(client)["JIRA_EMAIL"]["effective"] == "nuevo@b.c"


def test_borrar_un_parametro_y_un_secreto(client):
    client.put("/api/config", headers=H, json={"values": {"JIRA_EMAIL": None, "JIRA_API_TOKEN": None}})
    text = (client.tmp / ".env").read_text(encoding="utf-8")
    assert "JIRA_EMAIL" not in text and "JIRA_API_TOKEN" not in text and "OTRA=1" in text
    assert "JIRA_EMAIL" not in os.environ and params(client)["JIRA_API_TOKEN"]["configured"] is False


def test_validacion_y_parametros_desconocidos(client):
    reply = client.put("/api/config", headers=H, json={"values": {
        "GP_DAILY_BUDGET_USD": "mucho", "GP_MAX_MODEL_TIER": "gigante", "JIRA_BASE_URL": "ftp://x",
        "GP_TIMEOUT_CAP_S": "0", "PATH": "x", "JIRA_EMAIL": "a\nb"}})
    assert reply.status_code == 422
    assert set(reply.json()["detail"]["errors"]) == {
        "GP_DAILY_BUDGET_USD", "GP_MAX_MODEL_TIER", "JIRA_BASE_URL", "GP_TIMEOUT_CAP_S", "PATH", "JIRA_EMAIL"}
    assert "PATH" not in (client.tmp / ".env").read_text(encoding="utf-8")  # nada se escribió


def test_escritura_exige_la_cabecera_anti_csrf(client):
    plain = TestClient(client.app, base_url="http://127.0.0.1:8765")
    reply = plain.put("/api/config", json={"values": {"GP_DAILY_BUDGET_USD": "1"}})
    assert reply.status_code == 403 and "GP_DAILY" not in (client.tmp / ".env").read_text(encoding="utf-8")
