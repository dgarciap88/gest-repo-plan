"""Ejecutor del escaneo de secretos sobre los cambios del workspace."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gestplan.executors.base import ExecContext, NodeExecutor, Outcome
from gestplan.secretscan import Finding, scan_diff, scan_text, suspicious_filename


async def scan_workspace(ctx: ExecContext, workspace: str, ref: str, max_bytes: int) -> tuple[list[Finding], list[str]]:
    git = lambda *a: ctx.process(["git", *a], cwd=workspace, kind="git", timeout_s=120)  # noqa: E731
    diff = await git("diff", "--no-color", "--unified=0", ref)
    if not diff.ok:
        raise RuntimeError(f"git diff falló: {diff.combined[-300:]}")
    findings = scan_diff(diff.stdout)
    changed = await git("diff", "--name-only", "--diff-filter=AM", ref)
    untracked = await git("ls-files", "--others", "--exclude-standard")
    skipped: list[str] = []
    for path in untracked.stdout.splitlines():
        file = Path(workspace) / path
        try:
            if file.stat().st_size > max_bytes:
                skipped.append(path)
                continue
            text = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            skipped.append(path)  # binario o ilegible
            continue
        findings.extend(scan_text(path, text))
    for path in [*changed.stdout.splitlines(), *untracked.stdout.splitlines()]:
        reason = suspicious_filename(path)
        if reason:
            findings.append(Finding(path, 0, reason, "***"))
    return findings, skipped


class SecretScanExec(NodeExecutor):
    type = "secret_scan"

    async def run(self, ctx: ExecContext) -> Outcome:
        cfg = ctx.config
        try:
            findings, skipped = await scan_workspace(ctx, cfg["workspace"], cfg.get("ref", "HEAD"),
                                                     int(cfg.get("max_file_bytes", 1_000_000)))
        except RuntimeError as exc:
            return Outcome("failed", error=str(exc))
        unique = list({(f.file, f.line, f.kind): f for f in findings}.values())
        lines = [f"- {f.file}{':' + str(f.line) if f.line else ''}: {f.kind} ({f.sample})" for f in unique]
        outputs: dict[str, Any] = {"count": len(unique), "findings": [f.as_dict() for f in unique],
                                   "summary": "\n".join(lines), "skipped": skipped}
        if unique:
            return Outcome("secrets_detected", outputs, error=f"{len(unique)} posible(s) secreto(s) en los cambios")
        return Outcome("clean", outputs)
