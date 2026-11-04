"""Reconstruye el registro de artefactos de runs anteriores a su introducción, a partir de las salidas de los nodos."""

from __future__ import annotations

from typing import Any

from gestplan.db import Store
from gestplan.executors.git import branch_url
from gestplan.nodes.registry import get_node_type
from gestplan.plans.loader import parse_plan

_DONE = ("ok", "already_done")


def _derive(node_type: str, out: dict[str, Any], config: dict[str, Any], inputs: dict[str, Any],
            jira_base: str) -> list[tuple[str, str, str, str]]:
    """(tipo, título, enlace, referencia) de lo que un nodo dejó creado, según sus salidas y su configuración."""
    if node_type == "jira.create_issue" and out.get("key"):
        return [("issue", f"{out['key']}: {config.get('summary', '')}", out.get("url", ""), out["key"])]
    if node_type == "jira.create_subtask":
        summaries = [s.get("summary", "") for s in config.get("subtasks", [])]
        return [("subtask", f"{k}: {summaries[i] if i < len(summaries) else ''}", f"{jira_base}/browse/{k}", k)
                for i, k in enumerate(out.get("keys", []))]
    if node_type == "jira.comment" and out.get("comment_id"):
        key = config.get("key", "")
        return [("comment", f"Comentario en {key}", f"{jira_base}/browse/{key}?focusedCommentId={out['comment_id']}",
                 str(out["comment_id"]))]
    if node_type == "confluence.create_page" and out.get("page_id"):
        return [("page", out.get("title", ""), out.get("url", ""), str(out["page_id"]))]
    if node_type == "confluence.update_page" and out.get("page_id"):
        return [("page", f"{out.get('title', '')} (actualizada, v{out.get('version', '?')})", out.get("url", ""),
                 str(out["page_id"]))]
    if node_type == "git.create_pr" and out.get("pr_number"):
        return [("pr", f"PR #{out['pr_number']}: {config.get('title', '')}", out.get("pr_url", ""),
                 str(out["pr_number"]))]
    if node_type == "github.merge_pr" and out.get("number"):
        return [("merge", f"PR #{out['number']} fusionado", out.get("url", ""), str(out.get("sha") or out["number"]))]
    if node_type == "git.push" and out.get("remote_branch"):
        branch = out["remote_branch"]
        return [("branch", f"Rama {branch}", branch_url(str(inputs.get("repo_url", "")), branch), branch)]
    return []


def backfill_artifacts(store: Store, jira_base: str = "") -> int:
    """Registra los artefactos de los intentos correctos que aún no están. Devuelve cuántos se añadieron."""
    added = 0
    for run in store.conn.execute("SELECT id FROM runs ORDER BY created_at").fetchall():
        run = store.get_run(run["id"])
        if run is None:
            continue
        plan, _ = parse_plan(run["plan_snapshot"])
        if plan is None:
            continue
        for node_id, spec in plan.nodes.items():
            node_type = get_node_type(spec.type)
            if node_type is None or not node_type.artifacts:
                continue
            for attempt in store.attempts(run["id"], node_id):
                if attempt["event"] not in _DONE or not attempt["outputs"]:
                    continue
                config = attempt["resolved_config"] or {}
                for kind, title, url, ref in _derive(spec.type, attempt["outputs"], config, run["inputs"], jira_base):
                    added += store.add_artifact(run["id"], node_id, attempt["attempt"], kind, title, url, ref,
                                                run["plan_name"], run.get("title"), attempt["finished_at"])
    return added

