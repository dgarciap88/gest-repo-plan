"""Utilidades compartidas por los tests del motor."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

from gestplan.db import Store
from gestplan.engine.engine import Engine
from gestplan.executors import default_executors
from gestplan.executors.base import ExecContext, NodeExecutor, Outcome
from gestplan.plans.loader import parse_plan
from gestplan.settings import Settings


class Scripted(NodeExecutor):
    """Ejecutor falso: el manejador recibe el contexto y devuelve un Outcome o el nombre del evento."""

    def __init__(self, type_name: str, handler: Callable[[ExecContext], Any]) -> None:
        self.type = type_name
        self.handler = handler
        self.calls: list[tuple[str, int, dict]] = []

    async def run(self, ctx: ExecContext) -> Outcome:
        self.calls.append((ctx.node_id, ctx.attempt, ctx.config))
        result = self.handler(ctx)
        if asyncio.iscoroutine(result):
            result = await result
        return result if isinstance(result, Outcome) else Outcome(str(result))


def make_settings(tmp_path: Path, **kwargs: Any) -> Settings:
    return Settings(root=tmp_path, data_dir=tmp_path / "data", poll_interval_s=0.01, **kwargs)


def make_engine(tmp_path: Path, executors: dict[str, NodeExecutor] | None = None, runner=None,
                **settings_kwargs: Any) -> Engine:
    settings = make_settings(tmp_path, **settings_kwargs)
    store = Store(settings.db_path)
    merged = default_executors()
    merged.update(executors or {})
    return Engine(store, settings, runner=runner, executors=merged)


def flow_plan(**overrides: Any) -> dict:
    """Plan parecido al de ejemplo, pero con configuración inline (sin plantillas en disco)."""
    plan: dict[str, Any] = {
        "name": "flow",
        "nodes": {
            "ws": {"type": "prepare_workspace",
                   "config": {"repo_url": "r", "base_branch": "main", "new_branch": "feature/x"}},
            "dev": {"type": "ia_agent", "max_attempts": 5,
                    "config": {"workspace": "${nodes.ws.outputs.path}", "prompt": "haz la tarea",
                               "variables": {"feedback": "${nodes.val.outputs.feedback}"}}},
            "tests": {"type": "run_command",
                      "config": {"workspace": "${nodes.ws.outputs.path}", "command": ["python", "-V"]}},
            "val": {"type": "ia_agent_validator",
                    "config": {"workspace": "${nodes.ws.outputs.path}", "prompt": "valida", "max_retries": 2}},
            "commit": {"type": "git.commit",
                       "config": {"workspace": "${nodes.ws.outputs.path}",
                                  "subject": "${nodes.dev.outputs.suggested_commit_subject}"}},
            "approval": {"type": "manual_approval", "config": {"approves": ["push", "pr"]}},
            "push": {"type": "git.push",
                     "config": {"workspace": "${nodes.ws.outputs.path}",
                                "remote_branch": "${nodes.ws.outputs.remote_branch}"}},
            "pr": {"type": "git.create_pr",
                   "config": {"workspace": "${nodes.ws.outputs.path}", "base_branch": "main",
                              "head_branch": "${nodes.ws.outputs.remote_branch}",
                              "title": "${nodes.dev.outputs.suggested_pr_title}",
                              "body": "${nodes.dev.outputs.suggested_pr_body}"}},
        },
        "edges": [
            {"from": "ws.ok", "to": "dev"}, {"from": "dev.done", "to": "tests"},
            {"from": "tests.ok", "to": "val"}, {"from": "tests.failed", "to": "dev"},
            {"from": "val.changes_requested", "to": "dev"}, {"from": "val.approved", "to": "commit"},
            {"from": "val.max_retries_reached", "to": "commit"}, {"from": "commit.ok", "to": "approval"},
            {"from": "approval.approved", "to": "push"}, {"from": "push.ok", "to": "pr"},
        ],
    }
    plan.update(copy.deepcopy(overrides))
    return plan


def build(engine: Engine, data: dict, inputs: dict | None = None) -> str:
    plan, issues = parse_plan(data)
    assert plan is not None, issues
    return engine.create_run(plan, inputs or {})


def drive(engine: Engine, run_id: str) -> str:
    return asyncio.run(engine.run_until_quiescent(run_id))


def statuses(engine: Engine, run_id: str) -> dict[str, str]:
    return {n: s["status"] for n, s in engine.store.all_nodes(run_id).items()}


def dev_outcome(**extra: Any) -> Outcome:
    outputs = {"summary": "s", "files": ["a.py"], "files_changed": 1,
               "suggested_commit_subject": "TASK-1: Añadir la función de ejemplo",
               "suggested_commit_body": "porque sí", "suggested_pr_title": "[TASK-1] Función de ejemplo",
               "suggested_pr_body": "Contexto y cambios del PR de ejemplo", "assumptions": [],
               "open_questions": [], "cost_usd": 0.01, "session_id": "s", **extra}
    return Outcome("done", outputs, cost_usd=0.01)
