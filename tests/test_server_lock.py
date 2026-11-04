"""Un único motor por base de datos: la CLI no ejecuta runs ni recupera nodos mientras hay un servidor vivo."""

import json
import os
import time

from gestplan.cli import main
from gestplan.engine.engine import Engine
from gestplan.server.lock import STALE_AFTER_S, ServerLock, holder, lock_path
from tests.test_api import local_client
from tests.test_cli_run import project, run_id_from


def fake_server(data_dir, age=0.0):
    data_dir.mkdir(parents=True, exist_ok=True)
    lock_path(data_dir).write_text(json.dumps({"pid": 4242, "at": time.time() - age}), encoding="utf-8")


def test_latido_vigente_caducado_y_ausente(tmp_path):
    assert holder(tmp_path) is None
    fake_server(tmp_path)
    assert holder(tmp_path)["pid"] == 4242
    fake_server(tmp_path, age=STALE_AFTER_S + 1)
    assert holder(tmp_path) is None
    lock_path(tmp_path).write_text("no es json", encoding="utf-8")
    assert holder(tmp_path) is None


def test_solo_el_dueno_libera_el_candado(tmp_path):
    lock = ServerLock(tmp_path)
    lock.beat(force=True)
    assert holder(tmp_path)["pid"] == os.getpid()
    fake_server(tmp_path)  # otro proceso lo reclamó
    lock.release()
    assert holder(tmp_path)["pid"] == 4242
    fake_server(tmp_path)
    lock_path(tmp_path).write_text(json.dumps({"pid": os.getpid(), "at": time.time()}), encoding="utf-8")
    lock.release()
    assert not lock_path(tmp_path).exists()


def test_el_servidor_publica_el_latido_y_lo_retira_al_parar(tmp_path):
    from gestplan.server.app import create_app
    from tests.helpers import make_engine

    engine = make_engine(tmp_path)
    with local_client(create_app(engine=engine, serve_ui=False)):
        deadline = time.time() + 5
        while holder(engine.settings.data_dir) is None and time.time() < deadline:
            time.sleep(0.05)
        assert holder(engine.settings.data_dir)["pid"] == os.getpid()
    assert holder(engine.settings.data_dir) is None


def test_con_servidor_vivo_la_cli_no_recupera_ni_ejecuta(tmp_path, capsys, monkeypatch):
    common = project(tmp_path)
    data = tmp_path / "data"
    assert main(["run", "demo", *common]) == 3
    run_id = run_id_from(capsys.readouterr().out)

    def prohibido(*_a, **_k):
        raise AssertionError("la CLI no debe tocar el motor con un servidor vivo")

    monkeypatch.setattr(Engine, "recover", prohibido)
    monkeypatch.setattr(Engine, "run_until_quiescent", prohibido)
    fake_server(data)
    assert main(["resume", run_id, *common]) == 3  # solo espera y muestra: sigue esperando la aprobación
    assert "Hay un servidor en marcha (pid 4242)" in capsys.readouterr().out


def test_no_se_arrancan_dos_servidores(tmp_path, capsys):
    common = project(tmp_path)
    fake_server(tmp_path / "data")
    assert main(["serve", *common]) == 2
    assert "Ya hay un servidor en marcha" in capsys.readouterr().out
