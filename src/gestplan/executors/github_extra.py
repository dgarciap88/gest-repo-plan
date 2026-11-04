"""Ejecutores de GitHub añadidos después: logs de checks fallidos y fusión de PR."""

from __future__ import annotations

import re
from typing import Any

from gestplan.executors.base import Artifact, ExecContext, NodeExecutor, Outcome
from gestplan.executors.github import _gh_json

_RUN_URL = re.compile(r"/actions/runs/(\d+)")
_BLOCKED_HINTS = ("not mergeable", "required", "protected", "blocked", "conflict")


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_LOG_LINE = re.compile(r"^[^\t]*\t([^\t]*)\t﻿?\d{4}-\d\d-\d\dT[\d:.]+Z ?(.*)$")


def clean_log(text: str) -> str:
    """Quita lo que ensucia el log de Actions (BOM, colores ANSI, marcas de hora y el prefijo job/paso repetido)."""
    out: list[str] = []
    step = None
    for raw in _ANSI.sub("", text.replace("﻿", "")).splitlines():
        found = _LOG_LINE.match(raw)
        if not found:
            out.append(raw)
            continue
        if found.group(1) != step:
            step = found.group(1)
            out.append(f"--- paso: {step} ---")
        out.append(found.group(2))
    return "\n".join(out)


class GithubGetFailedLogsExec(NodeExecutor):
    type = "github.get_failed_logs"

    async def run(self, ctx: ExecContext) -> Outcome:
        try:
            checks = await _gh_json(ctx, "pr", "checks", str(ctx.config["pr"]), "--json", "name,bucket,link") or []
        except (RuntimeError, ValueError) as exc:
            return Outcome("failed", error=str(exc))
        failed: dict[str, str] = {}  # id de ejecución -> nombre del primer check fallido de esa ejecución
        for check in checks:
            match = _RUN_URL.search(check.get("link", ""))
            if check.get("bucket") in ("fail", "cancel") and match:
                failed.setdefault(match.group(1), check.get("name", ""))
        if not failed:
            return Outcome("no_failures", {"logs": "", "runs": [], "truncated": False})
        limit = max(500, int(ctx.config.get("max_chars", 8000)))
        parts: list[str] = []
        runs: list[dict[str, Any]] = []
        for run_id, name in list(failed.items())[:3]:
            result = await ctx.process(["gh", "run", "view", run_id, "--log-failed"], cwd=ctx.config["workspace"],
                                       timeout_s=120, kind="gh")
            if not result.ok:
                return Outcome("failed", error=f"gh run view falló: {result.combined[-300:]}")
            runs.append({"run_id": run_id, "name": name})
            parts.append(f"=== {name} (ejecución {run_id}) ===\n{clean_log(result.stdout).strip()}")
        text = "\n\n".join(parts)
        truncated = len(text) > limit
        if truncated:
            text = "[... log recortado: se conservan los últimos caracteres ...]\n" + text[-limit:]
        return Outcome("ok", {"logs": text, "runs": runs, "truncated": truncated})


class GithubMergePrExec(NodeExecutor):
    type = "github.merge_pr"

    async def preview(self, ctx: ExecContext) -> dict[str, Any] | None:
        try:
            data = await _gh_json(ctx, "pr", "view", str(ctx.config["pr"]), "--json",
                                  "title,number,state,mergeable,url,headRefName,baseRefName")
        except (RuntimeError, ValueError) as exc:
            return {"kind": "error", "message": str(exc)}
        return {"kind": "keyvalue", "title": f"Fusionar PR #{data['number']}",
                "items": {"título": data["title"], "rama": f"{data['headRefName']} → {data['baseRefName']}",
                          "estado": f"{data['state']} ({data['mergeable']})", "método": ctx.config["method"],
                          "borrar la rama": "sí" if ctx.config.get("delete_branch") else "no", "enlace": data["url"]}}

    async def run(self, ctx: ExecContext) -> Outcome:
        pr = str(ctx.config["pr"])
        try:
            data = await _gh_json(ctx, "pr", "view", pr, "--json", "state,number,url,mergeable,mergeCommit")
        except (RuntimeError, ValueError) as exc:
            return Outcome("failed", error=str(exc))
        base = {"number": data.get("number", 0), "url": data.get("url", ""),
                "sha": (data.get("mergeCommit") or {}).get("oid", "")}
        state = str(data.get("state", "")).upper()
        if state == "MERGED":
            return Outcome("already_done", base, artifacts=self._merged(base))
        if state == "CLOSED":
            return Outcome("closed", error="el PR está cerrado sin fusionar")
        if str(data.get("mergeable", "")).upper() == "CONFLICTING":
            return Outcome("not_mergeable", error="el PR tiene conflictos con la rama base")
        argv = ["gh", "pr", "merge", pr, f"--{ctx.config['method']}"]
        if ctx.config.get("delete_branch"):
            argv.append("--delete-branch")
        merged = await ctx.process(argv, cwd=ctx.config["workspace"], timeout_s=120, kind="gh")
        if not merged.ok:
            blocked = any(word in merged.combined.lower() for word in _BLOCKED_HINTS)
            return Outcome("not_mergeable" if blocked else "failed",
                           error=f"gh pr merge falló: {merged.combined[-400:]}")
        after = await _gh_json(ctx, "pr", "view", pr, "--json", "mergeCommit")
        sha = ((after or {}).get("mergeCommit") or {}).get("oid", "")
        return Outcome("ok", {**base, "sha": sha}, artifacts=self._merged({**base, "sha": sha}))

    @staticmethod
    def _merged(data: dict[str, Any]) -> list[Artifact]:
        return [Artifact("merge", f"PR #{data['number']} fusionado", data["url"], str(data["sha"] or data["number"]))]
