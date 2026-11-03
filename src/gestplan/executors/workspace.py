"""Ejecutores de workspace: clonado con rama de trabajo y limpieza."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
from pathlib import Path

from gestplan.executors.base import ExecContext, NodeExecutor, Outcome


def remove_tree(path: Path) -> None:
    """Borra un árbol; los ficheros de .git son de solo lectura en Windows."""

    def fix_permissions(func, target, _info):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=fix_permissions)
    else:
        shutil.rmtree(path, onerror=fix_permissions)


class PrepareWorkspaceExec(NodeExecutor):
    type = "prepare_workspace"

    async def run(self, ctx: ExecContext) -> Outcome:
        cfg = ctx.config
        ws_dir = ctx.settings.workspaces_root / ctx.run_id / ctx.node_id
        repo = ws_dir / "repo"
        marker = ws_dir / "workspace.json"
        if marker.is_file() and repo.is_dir():  # idempotencia: el workspace del run ya está listo
            ctx.log("workspace ya preparado, se reutiliza")
            return Outcome("ok", json.loads(marker.read_text(encoding="utf-8")))

        if ws_dir.exists():
            remove_tree(ws_dir)
        ws_dir.mkdir(parents=True)

        async def git(*args: str, cwd: Path = repo, timeout: float = 600):
            return await ctx.process(["git", *args], cwd=cwd, timeout_s=timeout, kind="git")

        clone = await git("-c", "core.longpaths=true", "clone", cfg["repo_url"], "repo",
                          cwd=ws_dir, timeout=900)
        if not clone.ok:
            return Outcome("failed", error=f"git clone falló: {clone.combined[-500:]}")
        await git("config", "core.longpaths", "true")

        base, wanted = cfg["base_branch"], cfg["new_branch"]
        base_ref = await git("rev-parse", "--verify", f"origin/{base}")
        if not base_ref.ok:
            return Outcome("failed", error=f"no existe la rama base '{base}' en el remoto")
        base_commit = base_ref.stdout.strip()

        listing = await git("ls-remote", "--heads", "origin", wanted)
        if not listing.ok:
            return Outcome("failed", error=f"git ls-remote falló: {listing.combined[-300:]}")
        existed = bool(listing.stdout.strip())
        suffix = hashlib.sha1(ctx.run_id.encode()).hexdigest()[:6]

        if existed:
            policy = cfg.get("on_branch_exists", "track_remote")
            if policy == "fail":
                return Outcome("failed", error=f"la rama '{wanted}' ya existe en el remoto")
            if policy == "new_branch_from_base":
                local = remote = f"{wanted}-{suffix}"
                start = f"origin/{base}"
            else:
                local, remote, start = f"{wanted}-{suffix}", wanted, f"origin/{wanted}"
        else:
            local = remote = wanted
            start = f"origin/{base}"

        checkout = await git("checkout", "-b", local, start)
        if not checkout.ok:
            return Outcome("failed", error=f"git checkout falló: {checkout.combined[-300:]}")

        outputs = {
            "path": str(repo),
            "local_branch": local,
            "remote_branch": remote,
            "branch_existed": existed,
            "base_commit": base_commit,
            "push_ref": f"HEAD:refs/heads/{remote}",
        }
        marker.write_text(json.dumps(outputs, ensure_ascii=False), encoding="utf-8")
        return Outcome("ok", outputs)


class CleanupWorkspaceExec(NodeExecutor):
    type = "cleanup_workspace"

    async def run(self, ctx: ExecContext) -> Outcome:
        path = Path(ctx.config["workspace"]).resolve()
        root = ctx.settings.workspaces_root.resolve()
        if root not in path.parents:
            return Outcome("failed", error=f"'{path}' no está bajo la carpeta de workspaces ({root})")
        target = path.parent if (path.parent / "workspace.json").exists() else path
        if target.exists():
            remove_tree(target)
        return Outcome("ok")
