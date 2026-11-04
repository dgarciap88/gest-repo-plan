"""Persistencia en SQLite (modo WAL). Usa solo la biblioteca estándar."""

from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2

# Cambios posteriores al esquema inicial (versión 1); se aplican en orden a las bases existentes.
MIGRATIONS: dict[int, tuple[str, ...]] = {
    2: (
        "ALTER TABLE runs ADD COLUMN title TEXT",
        "ALTER TABLE approvals ADD COLUMN nonce TEXT",
        "ALTER TABLE approvals ADD COLUMN overrides TEXT",
    ),
}

SCHEMA = """
CREATE TABLE runs (
    id TEXT PRIMARY KEY,
    plan_name TEXT NOT NULL,
    plan_snapshot TEXT NOT NULL,
    inputs TEXT NOT NULL,
    status TEXT NOT NULL,
    parent_run_id TEXT,
    parent_node_id TEXT,
    parent_index INTEGER,
    idempotency_key TEXT UNIQUE,
    start_limit INTEGER NOT NULL DEFAULT 1000,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE TABLE node_states (
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    base_attempts INTEGER NOT NULL DEFAULT 0,
    last_event TEXT,
    unhandled INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, node_id)
);
CREATE TABLE node_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    status TEXT NOT NULL,
    event TEXT,
    resolved_config TEXT,
    outputs TEXT,
    error TEXT,
    cost_usd REAL NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX idx_attempts ON node_attempts (run_id, node_id, attempt);
CREATE TABLE approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    status TEXT NOT NULL,
    covers TEXT NOT NULL,
    previews TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    comment TEXT,
    decided_by TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE TABLE transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    node_id TEXT,
    from_status TEXT,
    to_status TEXT NOT NULL,
    event TEXT,
    reason TEXT,
    at TEXT NOT NULL
);
CREATE TABLE invocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    kind TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT,
    argv TEXT NOT NULL,
    cwd TEXT,
    cli_version TEXT,
    prompt_path TEXT,
    stdout_path TEXT,
    stderr_path TEXT,
    exit_code INTEGER,
    timed_out INTEGER NOT NULL DEFAULT 0,
    result_raw TEXT,
    result_parsed TEXT,
    validation_errors TEXT,
    session_id TEXT,
    tokens_in INTEGER,
    tokens_out INTEGER,
    cost_usd REAL NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    sha256 TEXT
);
CREATE TABLE executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    kind TEXT NOT NULL,
    argv TEXT NOT NULL,
    cwd TEXT,
    exit_code INTEGER,
    timed_out INTEGER NOT NULL DEFAULT 0,
    stdout_path TEXT,
    stderr_path TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
"""

RUN_TABLES = ("node_states", "node_attempts", "approvals", "transitions", "invocations", "executions")
JSON_COLUMNS = {
    "runs": {"plan_snapshot", "inputs"},
    "node_attempts": {"resolved_config", "outputs"},
    "approvals": {"covers", "previews", "overrides"},
    "invocations": {"argv", "result_parsed", "validation_errors", "sha256"},
    "executions": {"argv"},
}

Row = dict[str, Any]


def local_midnight() -> str:
    """Inicio del día natural local, en el mismo formato que los instantes guardados (UTC)."""
    start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC).isoformat(timespec="seconds")


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path, clock: Callable[[], str] = utcnow) -> None:
        self.clock = clock
        target = str(path)
        if target != ":memory:":
            Path(target).parent.mkdir(parents=True, exist_ok=True)
        # el servidor usa la conexión desde el hilo del bucle y desde los tests: sqlite3 serializa el acceso
        self.conn = sqlite3.connect(target, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._migrate()

    def close(self) -> None:
        self.conn.close()

    def _migrate(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version >= SCHEMA_VERSION:
            return
        self.conn.execute("BEGIN")
        if version == 0:  # base nueva: esquema inicial y después todas las migraciones
            for statement in filter(None, (s.strip() for s in SCHEMA.split(";"))):
                self.conn.execute(statement)
            version = 1
        for target in range(version + 1, SCHEMA_VERSION + 1):
            for statement in MIGRATIONS[target]:
                self.conn.execute(statement)
        self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        self.conn.execute("COMMIT")

    # --- utilidades --------------------------------------------------------------------------
    def _decode(self, table: str, row: sqlite3.Row | None) -> Row | None:
        if row is None:
            return None
        data = dict(row)
        for column in JSON_COLUMNS.get(table, ()):
            if data.get(column) is not None:
                data[column] = json.loads(data[column])
        return data

    def _encode(self, value: Any) -> Any:
        return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value

    def _insert(self, table: str, **fields: Any) -> int:
        columns = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        cursor = self.conn.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({marks})",
            [self._encode(v) for v in fields.values()],
        )
        return cursor.lastrowid or 0

    def _update(self, table: str, where: str, args: tuple, **fields: Any) -> None:
        assignments = ", ".join(f"{column}=?" for column in fields)
        self.conn.execute(
            f"UPDATE {table} SET {assignments} WHERE {where}",
            [self._encode(v) for v in fields.values()] + list(args),
        )

    def _one(self, table: str, sql: str, args: tuple = ()) -> Row | None:
        return self._decode(table, self.conn.execute(sql, args).fetchone())

    def _many(self, table: str, sql: str, args: tuple = ()) -> list[Row]:
        return [self._decode(table, r) for r in self.conn.execute(sql, args).fetchall()]  # type: ignore[misc]

    # --- runs --------------------------------------------------------------------------------
    def create_run(self, run_id: str, plan_name: str, snapshot: dict, inputs: dict, *,
                   status: str = "queued", parent_run_id: str | None = None,
                   parent_node_id: str | None = None, parent_index: int | None = None,
                   idempotency_key: str | None = None, start_limit: int = 1000, title: str | None = None) -> None:
        self._insert("runs", id=run_id, plan_name=plan_name, plan_snapshot=snapshot, inputs=inputs, title=title,
                     status=status, parent_run_id=parent_run_id, parent_node_id=parent_node_id,
                     parent_index=parent_index, idempotency_key=idempotency_key,
                     start_limit=start_limit, created_at=self.clock())
        self.transition(run_id, None, to_status=status, reason="run creado")

    def get_run(self, run_id: str) -> Row | None:
        return self._one("runs", "SELECT * FROM runs WHERE id=?", (run_id,))

    def find_run_by_key(self, key: str) -> Row | None:
        return self._one("runs", "SELECT * FROM runs WHERE idempotency_key=?", (key,))

    def list_runs(self, status: str | None = None, limit: int = 50) -> list[Row]:
        if status:
            return self._many("runs", "SELECT * FROM runs WHERE status=? ORDER BY created_at DESC, id DESC LIMIT ?",
                              (status, limit))
        return self._many("runs", "SELECT * FROM runs ORDER BY created_at DESC, id DESC LIMIT ?", (limit,))

    def delete_runs(self, run_ids: list[str]) -> None:
        """Borra runs con todo su histórico (nodos, intentos, aprobaciones, auditoría). No se puede deshacer."""
        self.conn.execute("BEGIN")
        try:
            for run_id in run_ids:
                for table in RUN_TABLES:
                    self.conn.execute(f"DELETE FROM {table} WHERE run_id=?", (run_id,))
                self.conn.execute("DELETE FROM runs WHERE id=?", (run_id,))
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def children_of(self, run_id: str) -> list[Row]:
        return self._many("runs", "SELECT * FROM runs WHERE parent_run_id=? ORDER BY parent_index, id", (run_id,))

    def set_run_status(self, run_id: str, status: str, *, reason: str | None = None,
                       error: str | None = None) -> None:
        run = self.get_run(run_id)
        if run is None or run["status"] == status:
            return
        fields: dict[str, Any] = {"status": status}
        if status == "running" and not run["started_at"]:
            fields["started_at"] = self.clock()
        if status in ("succeeded", "failed", "cancelled"):
            fields["finished_at"] = self.clock()
        elif run["finished_at"]:
            fields["finished_at"] = None
        if error is not None:
            fields["error"] = error
        self._update("runs", "id=?", (run_id,), **fields)
        self.transition(run_id, None, to_status=status, from_status=run["status"], reason=reason)

    # --- nodos -------------------------------------------------------------------------------
    def init_nodes(self, run_id: str, node_ids: list[str]) -> None:
        for node_id in node_ids:
            self._insert("node_states", run_id=run_id, node_id=node_id, status="pending",
                         updated_at=self.clock())

    def get_node(self, run_id: str, node_id: str) -> Row:
        row = self._one("node_states", "SELECT * FROM node_states WHERE run_id=? AND node_id=?",
                        (run_id, node_id))
        assert row is not None, f"nodo desconocido {run_id}/{node_id}"
        return row

    def all_nodes(self, run_id: str) -> dict[str, Row]:
        rows = self._many("node_states", "SELECT * FROM node_states WHERE run_id=?", (run_id,))
        return {r["node_id"]: r for r in rows}

    def set_node(self, run_id: str, node_id: str, status: str, *, event: str | None = None,
                 reason: str | None = None, **fields: Any) -> None:
        current = self.get_node(run_id, node_id)
        if event is not None:
            fields["last_event"] = event
        self._update("node_states", "run_id=? AND node_id=?", (run_id, node_id), status=status,
                     updated_at=self.clock(), **fields)
        if current["status"] != status:
            self.transition(run_id, node_id, to_status=status, from_status=current["status"],
                            event=event, reason=reason)

    def transition(self, run_id: str, node_id: str | None, *, to_status: str,
                   from_status: str | None = None, event: str | None = None,
                   reason: str | None = None) -> None:
        self._insert("transitions", run_id=run_id, node_id=node_id, from_status=from_status,
                     to_status=to_status, event=event, reason=reason, at=self.clock())

    def transitions(self, run_id: str) -> list[Row]:
        return self._many("transitions", "SELECT * FROM transitions WHERE run_id=? ORDER BY id", (run_id,))

    def events_after(self, after_id: int, limit: int = 200) -> list[Row]:
        """Eventos (transiciones) posteriores a un id: alimentan el canal SSE y permiten reanudarlo."""
        return self._many("transitions", "SELECT * FROM transitions WHERE id>? ORDER BY id LIMIT ?",
                          (after_id, limit))

    def last_transition(self, run_id: str) -> Row | None:
        rows = self._many("transitions", "SELECT * FROM transitions WHERE run_id=? ORDER BY id DESC LIMIT 1",
                          (run_id,))
        return rows[0] if rows else None

    def last_event_id(self) -> int:
        return int(self.conn.execute("SELECT COALESCE(MAX(id),0) FROM transitions").fetchone()[0])

    def active_roots(self) -> list[Row]:
        return self._many("runs", "SELECT * FROM runs WHERE parent_run_id IS NULL "
                                  "AND status IN ('queued','running','waiting') ORDER BY created_at, id")

    def cost_since(self, since_iso: str) -> float:
        row = self.conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM node_attempts WHERE started_at>=?",
                                (since_iso,)).fetchone()
        return float(row[0])

    # --- intentos ----------------------------------------------------------------------------
    def start_attempt(self, run_id: str, node_id: str, attempt: int, resolved_config: dict | None) -> int:
        return self._insert("node_attempts", run_id=run_id, node_id=node_id, attempt=attempt,
                            status="running", resolved_config=resolved_config, started_at=self.clock())

    def finish_attempt(self, attempt_id: int, status: str, *, event: str | None = None,
                       outputs: dict | None = None, error: str | None = None, cost_usd: float = 0.0) -> None:
        self._update("node_attempts", "id=?", (attempt_id,), status=status, event=event,
                     outputs=outputs, error=error, cost_usd=cost_usd, finished_at=self.clock())

    def set_attempt_status(self, attempt_id: int, status: str) -> None:
        self._update("node_attempts", "id=?", (attempt_id,), status=status)

    def attempts(self, run_id: str, node_id: str) -> list[Row]:
        return self._many("node_attempts",
                          "SELECT * FROM node_attempts WHERE run_id=? AND node_id=? ORDER BY attempt, id",
                          (run_id, node_id))

    def open_attempt(self, run_id: str, node_id: str) -> Row | None:
        return self._one("node_attempts",
                         "SELECT * FROM node_attempts WHERE run_id=? AND node_id=? "
                         "AND status IN ('running','waiting') ORDER BY id DESC LIMIT 1", (run_id, node_id))

    def latest_outputs(self, run_id: str, node_id: str) -> dict | None:
        row = self._one("node_attempts",
                        "SELECT * FROM node_attempts WHERE run_id=? AND node_id=? AND outputs IS NOT NULL "
                        "ORDER BY attempt DESC, id DESC LIMIT 1", (run_id, node_id))
        return row["outputs"] if row else None

    def total_cost(self, run_id: str) -> float:
        row = self.conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM node_attempts WHERE run_id=?",
                                (run_id,)).fetchone()
        return float(row[0])

    # --- aprobaciones ------------------------------------------------------------------------
    def create_approval(self, run_id: str, node_id: str, attempt: int, covers: list[str],
                        previews: dict, message: str = "") -> int:
        approval_id = self._insert("approvals", run_id=run_id, node_id=node_id, attempt=attempt,
                                   status="pending", covers=covers, previews=previews, message=message,
                                   nonce=secrets.token_urlsafe(16), created_at=self.clock())
        self.transition(run_id, node_id, to_status="approval_requested", reason=str(approval_id))
        return approval_id

    def get_approval(self, approval_id: int) -> Row | None:
        return self._one("approvals", "SELECT * FROM approvals WHERE id=?", (approval_id,))

    def approval_for(self, run_id: str, node_id: str, attempt: int) -> Row | None:
        return self._one("approvals",
                         "SELECT * FROM approvals WHERE run_id=? AND node_id=? AND attempt=? "
                         "ORDER BY id DESC LIMIT 1", (run_id, node_id, attempt))

    def list_approvals(self, status: str | None = "pending") -> list[Row]:
        if status:
            return self._many("approvals", "SELECT * FROM approvals WHERE status=? ORDER BY id", (status,))
        return self._many("approvals", "SELECT * FROM approvals ORDER BY id")

    def decide_approval(self, approval_id: int, status: str, *, comment: str | None = None,
                        decided_by: str | None = None, overrides: dict | None = None) -> bool:
        """Decide una aprobación pendiente. Devuelve False si ya no estaba pendiente.

        `overrides` ({nodo: {campo: valor}}) son los textos que quien aprueba ha editado; solo se guardan al aprobar.
        """
        stored = self._encode(overrides) if overrides and status == "approved" else None
        cursor = self.conn.execute(
            "UPDATE approvals SET status=?, comment=?, decided_by=?, decided_at=?, overrides=? "
            "WHERE id=? AND status='pending'", (status, comment, decided_by, self.clock(), stored, approval_id))
        if cursor.rowcount != 1:
            return False
        approval = self.get_approval(approval_id)
        if approval:
            self.transition(approval["run_id"], approval["node_id"], to_status=f"approval_{status}",
                            reason=str(approval_id))
        return True

    def approval_overrides(self, run_id: str, node_id: str) -> dict[str, Any]:
        """Campos editados al aprobar para un nodo (de la aprobación aceptada más reciente que lo cubre)."""
        rows = self._many("approvals", "SELECT * FROM approvals WHERE run_id=? AND status='approved' ORDER BY id DESC",
                          (run_id,))
        for row in rows:
            if node_id in row["covers"]:
                return (row.get("overrides") or {}).get(node_id, {})
        return {}

    def invalidate_approvals(self, run_id: str, node_id: str) -> None:
        self.conn.execute(
            "UPDATE approvals SET status='invalidated', decided_at=? "
            "WHERE run_id=? AND node_id=? AND status='pending'", (self.clock(), run_id, node_id))

    # --- auditoría ---------------------------------------------------------------------------
    def add_invocation(self, **fields: Any) -> int:
        return self._insert("invocations", **fields)

    def invocations(self, run_id: str, node_id: str | None = None) -> list[Row]:
        if node_id:
            return self._many("invocations", "SELECT * FROM invocations WHERE run_id=? AND node_id=? ORDER BY id",
                              (run_id, node_id))
        return self._many("invocations", "SELECT * FROM invocations WHERE run_id=? ORDER BY id", (run_id,))

    def add_execution(self, **fields: Any) -> int:
        return self._insert("executions", **fields)

    def executions(self, run_id: str, node_id: str | None = None) -> list[Row]:
        if node_id:
            return self._many("executions", "SELECT * FROM executions WHERE run_id=? AND node_id=? ORDER BY id",
                              (run_id, node_id))
        return self._many("executions", "SELECT * FROM executions WHERE run_id=? ORDER BY id", (run_id,))
