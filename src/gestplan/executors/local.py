"""Ejecutor de comandos locales permitidos (tests, lint, build)."""

from __future__ import annotations

from gestplan.executors.base import ExecContext, NodeExecutor, Outcome

TAIL_CHARS = 4000


class RunCommandExec(NodeExecutor):
    type = "run_command"

    async def run(self, ctx: ExecContext) -> Outcome:
        result = await ctx.process(
            list(ctx.config["command"]), cwd=ctx.config["workspace"], kind="command",
            timeout_s=ctx.engine.node_timeout(ctx.run, ctx.node_id),
            idle_timeout_s=ctx.engine.node_idle_timeout(ctx.run, ctx.node_id))
        outputs = {"exit_code": result.exit_code if result.exit_code is not None else -1,
                   "passed": result.ok, "output_tail": result.combined[-TAIL_CHARS:]}
        if result.timed_out or result.stalled:
            return Outcome("timeout", outputs, error="el comando superó el tiempo máximo")
        return Outcome("ok" if result.ok else "failed", outputs)
