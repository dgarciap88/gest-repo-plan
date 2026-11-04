"""Ejecutores de GitHub de lectura (gh): estado del PR, checks, comentarios de revisión y workflows."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from gestplan.executors.base import ExecContext, NodeExecutor, Outcome


async def _gh_json(ctx: ExecContext, *args: str) -> Any:
    result = await ctx.process(["gh", *args], cwd=ctx.config["workspace"], timeout_s=60, kind="gh")
    if args[:2] == ("pr", "checks") and "no checks reported" in result.combined.lower():
        return []  # un repositorio sin CI no es un error: simplemente no hay checks
    if not result.ok and not (args[:2] == ("pr", "checks") and result.stdout.strip()):
        # 'gh pr checks' devuelve código 8 mientras haya checks pendientes y 1 si alguno falla, pero imprime JSON
        raise RuntimeError(f"gh {' '.join(args[:2])} falló: {result.combined[-300:]}")
    return json.loads(result.stdout) if result.stdout.strip() else None


class GithubGetPrStatusExec(NodeExecutor):
    type = "github.get_pr_status"

    async def run(self, ctx: ExecContext) -> Outcome:
        try:
            data = await _gh_json(ctx, "pr", "view", str(ctx.config["pr"]), "--json",
                                  "state,number,url,mergeable,reviewDecision")
        except (RuntimeError, ValueError) as exc:
            return Outcome("failed", error=str(exc))
        state = str(data.get("state", "")).upper()
        outputs = {"state": state.lower(), "number": data.get("number", 0), "url": data.get("url", ""),
                   "mergeable": str(data.get("mergeable", "")).lower(),
                   "review_decision": str(data.get("reviewDecision") or "").lower()}
        return Outcome({"MERGED": "merged", "CLOSED": "closed"}.get(state, "open"), outputs)


class GithubWaitChecksExec(NodeExecutor):
    type = "github.wait_checks"

    def __init__(self) -> None:
        self._last_poll: dict[tuple[str, str, int], float] = {}

    async def run(self, ctx: ExecContext) -> Outcome:
        return await self._check(ctx, force=True) or Outcome(waiting="external")

    async def poll(self, ctx: ExecContext) -> Outcome | None:
        return await self._check(ctx, force=False)

    async def _check(self, ctx: ExecContext, *, force: bool) -> Outcome | None:
        now = datetime.fromisoformat(ctx.store.clock()).timestamp()
        key = (ctx.run_id, ctx.node_id, ctx.attempt)
        attempt = ctx.store.open_attempt(ctx.run_id, ctx.node_id)
        started = datetime.fromisoformat(attempt["started_at"]).timestamp() if attempt else now
        expired = now - started >= int(ctx.config.get("max_wait_s", 3600))
        if not force and not expired and now - self._last_poll.get(key, 0) < int(ctx.config.get("poll_interval_s", 30)):
            return None
        self._last_poll[key] = now
        try:
            checks = await _gh_json(ctx, "pr", "checks", str(ctx.config["pr"]), "--json", "name,bucket,link,state")
        except (RuntimeError, ValueError) as exc:
            return Outcome("failed", error=str(exc))
        checks = checks or []
        if not checks and not expired and now - started < int(ctx.config.get("no_checks_wait_s", 90)):
            return None  # justo tras abrir el PR GitHub aún no ha registrado el CI: «sin checks» no significa «sin CI»
        failed = [{"name": c.get("name", ""), "link": c.get("link", ""), "state": c.get("state", "")}
                  for c in checks if c.get("bucket") in ("fail", "cancel")]
        pending = [c for c in checks if c.get("bucket") == "pending"]
        summary = "\n".join(f"- {c.get('name')}: {c.get('state')} {c.get('link', '')}".rstrip() for c in checks)
        outputs = {"failed_checks": failed, "summary": summary}
        if failed:
            self._last_poll.pop(key, None)
            return Outcome("failure", {"state": "failure", **outputs})
        if not pending:
            self._last_poll.pop(key, None)
            return Outcome("success", {"state": "success", **outputs})
        if expired:
            self._last_poll.pop(key, None)
            return Outcome("timeout", {"state": "pending", **outputs}, error="los checks no terminaron a tiempo")
        return None


class GithubGetReviewCommentsExec(NodeExecutor):
    type = "github.get_review_comments"

    async def run(self, ctx: ExecContext) -> Outcome:
        ignored = {a.lower() for a in ctx.config.get("ignore_authors") or []}
        try:
            data = await _gh_json(ctx, "pr", "view", str(ctx.config["pr"]), "--json", "number,reviews,comments")
            number = data["number"]
            inline = await _gh_json(ctx, "api", f"repos/{{owner}}/{{repo}}/pulls/{number}/comments")
        except (RuntimeError, ValueError, KeyError) as exc:
            return Outcome("failed", error=str(exc))
        comments: list[dict[str, Any]] = []
        changes_requested = False
        for review in data.get("reviews", []):
            author = (review.get("author") or {}).get("login", "")
            if author.lower() in ignored:
                continue
            changes_requested |= review.get("state") == "CHANGES_REQUESTED"
            if review.get("body", "").strip():
                comments.append({"author": author, "path": "", "line": 0, "body": review["body"]})
        for comment in data.get("comments", []):
            author = (comment.get("author") or {}).get("login", "")
            if author.lower() not in ignored and comment.get("body", "").strip():
                comments.append({"author": author, "path": "", "line": 0, "body": comment["body"]})
        for comment in inline or []:
            author = (comment.get("user") or {}).get("login", "")
            if author.lower() not in ignored:
                comments.append({"author": author, "path": comment.get("path", ""),
                                 "line": comment.get("line") or comment.get("original_line") or 0,
                                 "body": comment.get("body", "")})
        text = "\n\n".join(
            f"{c['author']}" + (f" en {c['path']}:{c['line']}" if c["path"] else "") + f":\n{c['body']}"
            for c in comments)
        outputs = {"count": len(comments), "changes_requested": changes_requested, "comments": comments, "text": text}
        return Outcome("has_comments" if comments or changes_requested else "no_comments", outputs)


class GithubGetWorkflowRunsExec(NodeExecutor):
    type = "github.get_workflow_runs"

    async def run(self, ctx: ExecContext) -> Outcome:
        try:
            runs = await _gh_json(ctx, "run", "list", "--branch", str(ctx.config["branch"]), "--limit",
                                  str(int(ctx.config.get("limit", 10))), "--json",
                                  "databaseId,name,status,conclusion,url,headSha")
        except (RuntimeError, ValueError) as exc:
            return Outcome("failed", error=str(exc))
        runs = runs or []
        return Outcome("ok", {"runs": runs, "count": len(runs),
                              "latest_conclusion": str(runs[0].get("conclusion") or "") if runs else ""})
