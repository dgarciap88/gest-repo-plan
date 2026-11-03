"""Ejecutores de git: commit, push y PR (con idempotencia y vista previa)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from gestplan.executors.base import ExecContext, NodeExecutor, Outcome
from gestplan.executors.policy import check_commit_message, check_pr_message, strip_ai_trailers

_REJECTED_HINTS = ("rejected", "non-fast-forward", "fetch first", "gh007", "protected branch", "denied")


async def _git(ctx: ExecContext, cwd: str | Path, *args: str, timeout: float = 300):
    return await ctx.process(["git", *args], cwd=cwd, timeout_s=timeout, kind="git")


class GitCommitExec(NodeExecutor):
    type = "git.commit"

    def _message(self, ctx: ExecContext) -> tuple[str, str]:
        subject = strip_ai_trailers(str(ctx.config["subject"])).strip()
        body = strip_ai_trailers(str(ctx.config.get("body", "")))
        return subject, body

    async def preview(self, ctx: ExecContext) -> dict[str, Any] | None:
        subject, body = self._message(ctx)
        stat = await _git(ctx, ctx.config["workspace"], "status", "--short")
        return {"kind": "markdown", "title": "Commit",
                "text": f"**{subject}**\n\n{body}\n\n```\n{stat.stdout.strip()}\n```"}

    async def run(self, ctx: ExecContext) -> Outcome:
        cwd = ctx.config["workspace"]
        subject, body = self._message(ctx)
        problems = check_commit_message(subject, body)
        if problems:
            return Outcome("invalid_message", error="; ".join(problems))
        paths = ctx.config.get("paths") or ["."]
        added = await _git(ctx, cwd, "add", "-A", "--", *paths)
        if not added.ok:
            return Outcome("failed", error=f"git add falló: {added.combined[-300:]}")
        pending = await _git(ctx, cwd, "diff", "--cached", "--quiet")
        if pending.exit_code == 0:
            return Outcome("nothing_to_commit")
        message_file = ctx.attempt_dir / "commit-message.txt"
        message_file.write_text(subject + (f"\n\n{body}" if body else "") + "\n", encoding="utf-8")
        done = await _git(ctx, cwd, "commit", "-F", str(message_file))
        if not done.ok:
            kind = "hook_failed" if "hook" in done.combined.lower() else "failed"
            return Outcome(kind, error=f"git commit falló: {done.combined[-500:]}")
        sha = (await _git(ctx, cwd, "rev-parse", "HEAD")).stdout.strip()
        return Outcome("ok", {"commit_sha": sha, "subject": subject})


class GitPushExec(NodeExecutor):
    type = "git.push"

    async def preview(self, ctx: ExecContext) -> dict[str, Any] | None:
        cwd = ctx.config["workspace"]
        log = await _git(ctx, cwd, "log", "-n", "5", "--format=%h %s")
        stat = await _git(ctx, cwd, "show", "--stat", "--format=", "HEAD")
        return {"kind": "keyvalue", "title": "Push",
                "items": {"remoto": ctx.config.get("remote", "origin"),
                          "rama remota": ctx.config["remote_branch"],
                          "últimos commits": log.stdout.strip(),
                          "cambios del último commit": stat.stdout.strip()}}

    async def run(self, ctx: ExecContext) -> Outcome:
        cwd = ctx.config["workspace"]
        remote = ctx.config.get("remote", "origin")
        branch = ctx.config["remote_branch"]
        head = (await _git(ctx, cwd, "rev-parse", "HEAD")).stdout.strip()
        remote_ref = await _git(ctx, cwd, "ls-remote", remote, f"refs/heads/{branch}")
        if remote_ref.ok and remote_ref.stdout.split()[:1] == [head]:
            return Outcome("ok", {"pushed_sha": head, "remote_branch": branch})  # ya publicado
        pushed = await _git(ctx, cwd, "push", remote, f"HEAD:refs/heads/{branch}")
        if pushed.timed_out:
            return Outcome("timeout", error="git push superó el tiempo máximo")
        if not pushed.ok:
            lowered = pushed.combined.lower()
            kind = "rejected" if any(hint in lowered for hint in _REJECTED_HINTS) else "failed"
            return Outcome(kind, error=f"git push falló: {pushed.combined[-500:]}")
        return Outcome("ok", {"pushed_sha": head, "remote_branch": branch})


class GitCreatePrExec(NodeExecutor):
    type = "git.create_pr"

    def _body(self, ctx: ExecContext) -> str:
        marker = f"<!-- gestplan run:{ctx.run_id} node:{ctx.node_id} -->"
        return f"{strip_ai_trailers(str(ctx.config['body']))}\n\n{marker}\n"

    async def preview(self, ctx: ExecContext) -> dict[str, Any] | None:
        return {"kind": "markdown", "title": f"PR: {ctx.config['title']}",
                "text": f"`{ctx.config['head_branch']}` → `{ctx.config['base_branch']}`\n\n{ctx.config['body']}"}

    async def run(self, ctx: ExecContext) -> Outcome:
        cfg = ctx.config
        cwd = cfg["workspace"]
        problems = check_pr_message(str(cfg["title"]), str(cfg["body"]))
        if problems:
            return Outcome("failed", error="; ".join(problems))
        existing = await ctx.process(
            ["gh", "pr", "list", "--head", cfg["head_branch"], "--base", cfg["base_branch"],
             "--state", "open", "--json", "number,url", "--jq", ".[0] | select(. != null) | "
             "\"\\(.number) \\(.url)\""], cwd=cwd, timeout_s=60, kind="gh")
        if existing.ok and existing.stdout.strip():
            number, url = existing.stdout.strip().split(" ", 1)
            return Outcome("ok", {"pr_number": int(number), "pr_url": url})  # ya existe
        body_file = ctx.attempt_dir / "pr-body.md"
        body_file.write_text(self._body(ctx), encoding="utf-8")
        argv = ["gh", "pr", "create", "--base", cfg["base_branch"], "--head", cfg["head_branch"],
                "--title", cfg["title"], "--body-file", str(body_file)]
        if cfg.get("draft"):
            argv.append("--draft")
        for reviewer in cfg.get("reviewers") or []:
            argv += ["--reviewer", reviewer]
        for label in cfg.get("labels") or []:
            argv += ["--label", label]
        created = await ctx.process(argv, cwd=cwd, timeout_s=120, kind="gh")
        if not created.ok:
            return Outcome("failed", error=f"gh pr create falló: {created.combined[-500:]}")
        match = re.search(r"https?://\S+/pull/(\d+)", created.stdout)
        if not match:
            return Outcome("failed", error=f"no se pudo leer la URL del PR: {created.stdout[-300:]}")
        return Outcome("ok", {"pr_number": int(match.group(1)), "pr_url": match.group(0)})
