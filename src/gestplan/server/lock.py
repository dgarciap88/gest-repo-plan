"""Candado del servidor: un único motor debe avanzar los runs de una base de datos a la vez.

El servidor escribe un latido en `<data>/server.lock`; la CLI lo consulta antes de ejecutar un run por su cuenta
(dos motores sobre la misma base de datos podrían ejecutar el mismo nodo dos veces o marcar como caído un nodo vivo).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

STALE_AFTER_S = 15.0
BEAT_EVERY_S = 3.0


def lock_path(data_dir: Path) -> Path:
    return data_dir / "server.lock"


def holder(data_dir: Path, now: float | None = None) -> dict | None:
    """Datos del servidor en marcha (pid, hora del último latido) o None si no hay ninguno vivo."""
    try:
        info = json.loads(lock_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return info if (now or time.time()) - float(info.get("at", 0)) < STALE_AFTER_S else None


class ServerLock:
    def __init__(self, data_dir: Path) -> None:
        self.path = lock_path(data_dir)
        self._last = 0.0

    def beat(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last < BEAT_EVERY_S:
            return
        self._last = now
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"pid": os.getpid(), "at": time.time()}), encoding="utf-8")
        except OSError:
            pass  # un fallo al escribir el latido no debe parar el servidor

    def release(self) -> None:
        try:
            if json.loads(self.path.read_text(encoding="utf-8")).get("pid") == os.getpid():
                self.path.unlink()
        except (OSError, ValueError):
            pass
