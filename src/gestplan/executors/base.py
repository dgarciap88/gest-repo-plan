"""Contrato de ejecución de nodos (la definición estática está en gestplan.nodes)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from gestplan.engine.http import HttpResult
from gestplan.engine.runner import ProcResult, redact

if TYPE_CHECKING:
    from gestplan.engine.engine import Engine
    from gestplan.plans.models import NodeSpec, Plan


@dataclass
class Outcome:
    event: str = ""
    outputs: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    cost_usd: float = 0.0
    waiting: str | None = None  # "approval" | "external": el nodo queda esperando


class NodeExecutor:
    type: ClassVar[str]

    async def run(self, ctx: ExecContext) -> Outcome:
        raise NotImplementedError

    async def poll(self, ctx: ExecContext) -> Outcome | None:
        """Para nodos en espera: devuelve el resultado cuando ya se puede completar."""
        return None

    async def preview(self, ctx: ExecContext) -> dict[str, Any] | None:
        """Qué haría el nodo, sin hacerlo (lo muestra la aprobación manual)."""
        return None


class ExecContext:
    def __init__(self, engine: Engine, run: dict, plan: Plan, node_id: str, attempt: int,
                 config: dict[str, Any], attempt_dir: Path) -> None:
        self.engine = engine
        self.run = run
        self.plan = plan
        self.node_id = node_id
        self.attempt = attempt
        self.config = config
        self.attempt_dir = attempt_dir
        self._exec_count = 0
        attempt_dir.mkdir(parents=True, exist_ok=True)

    @property
    def store(self):
        return self.engine.store

    @property
    def settings(self):
        return self.engine.settings

    @property
    def run_id(self) -> str:
        return self.run["id"]

    @property
    def spec(self) -> NodeSpec:
        return self.plan.nodes[self.node_id]

    def node_outputs(self, node_id: str) -> dict[str, Any]:
        return self.store.latest_outputs(self.run_id, node_id) or {}

    def template_context(self) -> dict[str, Any]:
        return self.engine.template_context(self.run, self.plan)

    def log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        with open(self.attempt_dir / "node.log", "a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {redact(message)}\n")

    async def process(self, argv: list[str], *, cwd: Path | str | None = None,
                      env: dict[str, str] | None = None, stdin_text: str | None = None,
                      timeout_s: float | None = None, idle_timeout_s: float | None = None,
                      kind: str = "other") -> ProcResult:
        """Ejecuta un subproceso auditado (comando, salida y código quedan registrados)."""
        self._exec_count += 1
        stem = f"exec-{self._exec_count:02d}-{kind}"
        stdout_path = self.attempt_dir / f"{stem}.stdout.log"
        stderr_path = self.attempt_dir / f"{stem}.stderr.log"
        row_id = self.store.add_execution(
            run_id=self.run_id, node_id=self.node_id, attempt=self.attempt, kind=kind,
            argv=[redact(part) for part in argv], cwd=str(cwd) if cwd else None,
            started_at=self.store.clock())
        result = await self.engine.runner.run(argv, cwd=cwd, env=env, stdin_text=stdin_text,
                                              timeout_s=timeout_s, idle_timeout_s=idle_timeout_s)
        stdout_path.write_text(redact(result.stdout), encoding="utf-8")
        stderr_path.write_text(redact(result.stderr), encoding="utf-8")
        self.store._update("executions", "id=?", (row_id,), exit_code=result.exit_code,
                           timed_out=int(result.timed_out or result.stalled),
                           stdout_path=str(stdout_path), stderr_path=str(stderr_path),
                           finished_at=self.store.clock())
        return result

    async def http(self, method: str, url: str, *, headers: dict[str, str] | None = None, json_body: Any = None,
                   timeout_s: float = 60, keep_body: bool = True) -> HttpResult:
        """Llamada HTTP auditada como una ejecución (método y URL; la cabecera de autenticación no se guarda)."""
        self._exec_count += 1
        stem = f"exec-{self._exec_count:02d}-http"
        stdout_path = self.attempt_dir / f"{stem}.stdout.log"
        row_id = self.store.add_execution(
            run_id=self.run_id, node_id=self.node_id, attempt=self.attempt, kind="http",
            argv=[method, redact(url)], cwd=None, started_at=self.store.clock())
        result = await self.engine.http.request(method, url, headers=headers, json_body=json_body,
                                                timeout_s=timeout_s)
        shown = result.text[:20000] if keep_body else f"({len(result.body)} bytes)"
        stdout_path.write_text(redact(shown), encoding="utf-8")
        self.store._update("executions", "id=?", (row_id,), exit_code=result.status,
                           timed_out=int(result.status == 0), stdout_path=str(stdout_path), stderr_path=None,
                           finished_at=self.store.clock())
        return result
