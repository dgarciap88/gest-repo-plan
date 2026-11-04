"""Script de pruebas de robustez: espera unos segundos (para matar, cancelar o agotar el timeout del nodo)."""

import os
import time

META = {
    "description": "Espera `seconds` segundos y devuelve su pid",
    "side_effect": "none",
    "events": ["ok", "failed"],
    "failure_events": ["failed"],
}


def run(inputs, ctx):
    time.sleep(float(inputs.get("seconds", 5)))
    return {"event": "ok", "outputs": {"pid": os.getpid()}}
