"""Bucle persistente que hace avanzar todos los runs activos (modo servidor)."""

from __future__ import annotations

import asyncio
import logging

from gestplan.engine.engine import Engine

log = logging.getLogger("gestplan.supervisor")


class Supervisor:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._wake = asyncio.Event()
        self._stopped = False

    def wake(self) -> None:
        """Despierta al bucle (una aprobación, una cancelación o un run nuevo)."""
        self._wake.set()

    def stop(self) -> None:
        self._stopped = True
        self._wake.set()

    async def run(self) -> None:
        recovered = self.engine.recover()
        if recovered:
            log.warning("%d nodo(s) interrumpido(s) por una caída anterior; se pueden reactivar", recovered)
        while not self._stopped:
            progress = False
            tasks: list[asyncio.Task] = []
            for run_id in self.engine.active_roots():
                try:
                    run = self.engine.store.get_run(run_id)
                    if run and run["status"] == "queued":
                        self.engine.store.set_run_status(run_id, "running", reason="inicio")
                    step_progress, step_tasks = await self.engine.step(run_id)
                except Exception:  # noqa: BLE001 - un run defectuoso no debe parar a los demás
                    log.exception("error avanzando el run %s", run_id)
                    continue
                progress |= step_progress
                tasks.extend(step_tasks)
            if tasks:
                await asyncio.wait(tasks, timeout=self.engine.settings.poll_interval_s,
                                   return_when=asyncio.FIRST_COMPLETED)
            elif not progress:
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=0.5)
                except TimeoutError:
                    pass
                self._wake.clear()
        for state in list(self.engine._states.values()):  # parada limpia: se cancelan las tareas en vuelo
            for task in state.tasks.values():
                task.cancel()
