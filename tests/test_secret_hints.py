"""Pistas del triaje: el agente no ve el valor, pero sí cuánto parece un marcador de posición (calculado por el sistema)."""

from gestplan.secretscan import PLACEHOLDER_THRESHOLD, finding_hints, value_hints


def percent(hint: str) -> int:
    return int(hint.split("; ")[1].split(" %")[0])


def test_un_token_de_ejemplo_se_reconoce_como_marcador():
    hint = value_hints("ghp_FAKE0000000000000000000000000000000000")
    assert percent(hint) >= PLACEHOLDER_THRESHOLD
    assert "«0» seguidos" in hint and "«FAKE»" in hint and "42 caracteres" in hint


def test_un_secreto_aleatorio_no_parece_un_marcador_ni_se_revela():
    value = "ghp_a8K3mQ9xLp2ZrT7vB1nW4cY6dE0fGhJ5kUaS"
    hint = value_hints(value)
    assert percent(hint) == 0 and "a8K3" not in hint and value[4:] not in hint


def test_esconder_la_palabra_fake_dentro_de_un_secreto_real_no_engana():
    assert percent(value_hints("ghp_realrealrealrealrealrealrealFAKEtail1")) < PLACEHOLDER_THRESHOLD


def test_pistas_de_una_linea_del_workspace(tmp_path):
    (tmp_path / "config.py").write_text('# x\nAPI_TOKEN = "ghp_FAKE0000000000000000000000000000000000"\n',
                                        encoding="utf-8")
    hints = finding_hints(tmp_path, "config.py", 2)
    assert hints.startswith("valor 1: ") and percent(hints.replace("valor 1: ", "")) >= PLACEHOLDER_THRESHOLD
    assert finding_hints(tmp_path, "config.py", 99) == "" and finding_hints(tmp_path, "../fuera.py", 1) == ""
    assert finding_hints(tmp_path, "config.py", 0) == ""


def test_el_prompt_del_triaje_lleva_las_pistas_fuera_de_los_datos_no_confiables(tmp_path):
    from tests.test_secret_triage import run

    ws_file = tmp_path / "repo" / "app"
    findings = [{"file": "app/config.py", "line": 4, "kind": "asignación de secreto", "sample": "Supe***"}]
    _engine, _run_id, claude, _attempt = run(tmp_path, [{"index": 0, "verdict": "secret", "reason": "x"}], findings)
    assert ws_file.exists()
    prompt = claude.calls[0]["stdin"]
    trusted, untrusted = prompt.split("## DATOS NO CONFIABLES:", 1)
    assert "## Pistas del sistema" in trusted and "- Hallazgo 0: valor 1: " in trusted
    assert "caracteres; 0 % del valor parece un marcador" in trusted  # un secreto real: ningún indicio de marcador
    assert "Supe***" in untrusted and "Pistas del sistema" not in untrusted
