"""Ejecutores de IA: agente que implementa y validador cruzado (proveedor Claude)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from gestplan.engine.runner import redact
from gestplan.executors.base import ExecContext, NodeExecutor, Outcome
from gestplan.executors.claude import (
    DEV_TOOLS,
    VALIDATOR_TOOLS,
    ClaudeOptions,
    build_argv,
    build_copilot_argv,
    claude_executable,
    copilot_executable,
    extract_json_object,
    parse_cli_output,
    resolve_model,
    session_uuid,
)
from gestplan.executors.prompts import build_prompt, render_template

MAX_DIFF_CHARS = 60_000


class DevResult(BaseModel):
    """Lo que el agente devuelve como respuesta final."""

    model_config = ConfigDict(extra="ignore")

    summary: str
    suggested_commit_subject: str
    suggested_commit_body: str = ""
    suggested_pr_title: str
    suggested_pr_body: str
    assumptions: list[str] = []
    open_questions: list[str] = []


class ReviewIssue(BaseModel):
    model_config = ConfigDict(extra="ignore")

    severity: Literal["high", "medium", "low"]
    file: str = ""
    line: int | None = None
    description: str
    suggestion: str = ""


class ValidatorResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    verdict: Literal["approved", "changes_requested"]
    issues: list[ReviewIssue] = []
    notes: str = ""


@dataclass
class AgentRun:
    result: BaseModel | None = None
    errors: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    session_id: str = ""
    timed_out: bool = False
    stalled: bool = False
    budget_exceeded: bool = False


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _cli_version(ctx: ExecContext, exe: str) -> str | None:
    cache = ctx.engine.cli_versions
    if exe not in cache:
        result = await ctx.engine.runner.run([exe, "--version"], timeout_s=30)
        cache[exe] = result.stdout.strip() or None if result.ok else None
    return cache[exe]


async def _call(
    ctx: ExecContext, *, kind: str, prompt: str, opts: ClaudeOptions, number: int
) -> tuple[dict | None, Any]:
    copilot = opts.provider == "copilot"
    exe = copilot_executable(ctx.settings) if copilot else claude_executable(ctx.settings)
    workspace = ctx.config["workspace"]
    prompt_path = ctx.attempt_dir / f"prompt-{number:02d}.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    argv = build_copilot_argv(exe, opts) if copilot else build_argv(exe, opts)
    started = ctx.store.clock()
    proc = await ctx.process(
        argv, cwd=workspace, stdin_text=prompt, kind="claude",
        timeout_s=ctx.engine.node_timeout(ctx.run, ctx.node_id),
        idle_timeout_s=ctx.engine.node_idle_timeout(ctx.run, ctx.node_id))
    if copilot:  # texto plano, sin coste ni tokens: se adapta al formato que espera el resto del flujo
        data = {"result": proc.stdout.strip(), "session_id": opts.session_id, "is_error": not proc.ok}
    else:
        data = parse_cli_output(proc.stdout)
    stdout_path = ctx.attempt_dir / f"{opts.provider}-{number:02d}.stdout.json"
    stdout_path.write_text(redact(proc.stdout), encoding="utf-8")
    usage = (data or {}).get("usage") or {}
    ctx.store.add_invocation(
        run_id=ctx.run_id, node_id=ctx.node_id, attempt=ctx.attempt, kind=kind, provider=opts.provider,
        model=opts.model, argv=[redact(a) for a in argv], cwd=str(workspace),
        cli_version=await _cli_version(ctx, exe), prompt_path=str(prompt_path),
        stdout_path=str(stdout_path), exit_code=proc.exit_code,
        timed_out=int(proc.timed_out or proc.stalled),
        result_raw=redact((data or {}).get("result") or proc.stdout[-2000:]),
        session_id=(data or {}).get("session_id") or opts.session_id,
        tokens_in=(usage.get("input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0)
        + (usage.get("cache_creation_input_tokens") or 0),
        tokens_out=usage.get("output_tokens"), cost_usd=float((data or {}).get("total_cost_usd") or 0),
        started_at=started, finished_at=ctx.store.clock(),
        sha256={"prompt": _sha(prompt), "stdout": _sha(proc.stdout)})
    return data, proc


async def invoke_agent(ctx: ExecContext, *, role: Literal["dev", "validator"], result_model: type[BaseModel],
                       task: str, data_blocks: list[tuple[str, str]], extra_rules: str = "") -> AgentRun:
    cfg = ctx.config
    settings = ctx.settings
    provider = cfg.get("provider", "claude")
    try:
        model = resolve_model(settings, tier=cfg.get("model_tier", "cheap"), model=cfg.get("model"),
                              attempt=ctx.attempt, provider=provider)
    except ValueError as exc:
        return AgentRun(session_id="", errors=[str(exc)])
    previous = ctx.store.invocations(ctx.run_id, ctx.node_id)
    if cfg.get("session_mode") == "continue":
        session_id, resume = session_uuid(ctx.run_id, ctx.node_id), bool(previous)
    else:
        session_id, resume = session_uuid(ctx.run_id, ctx.node_id, ctx.attempt), False
    tools = tuple(cfg.get("allowed_tools") or (DEV_TOOLS if role == "dev" else VALIDATOR_TOOLS))
    opts = ClaudeOptions(
        model=model, permission_mode=cfg.get("permission_mode", "acceptEdits"), allowed_tools=tools,
        session_id=session_id, resume=resume, effort=cfg.get("effort"), agent=cfg.get("agent"),
        extra_dirs=list(cfg.get("extra_dirs") or []), max_cost_usd=cfg.get("max_cost_usd"),
        setting_sources=settings.claude_setting_sources, provider=provider, read_only=role == "validator")
    run = AgentRun(session_id=session_id)
    prompt = build_prompt(language=ctx.plan.settings.language, task=task, data_blocks=data_blocks,
                          result_model=result_model, extra_rules=extra_rules)

    for number, kind in ((1, "main"), (2, "repair")):
        data, proc = await _call(ctx, kind=kind, prompt=prompt, opts=opts, number=number)
        run.cost_usd += float((data or {}).get("total_cost_usd") or 0)
        if data and data.get("session_id"):
            run.session_id = data["session_id"]  # el CLI manda: es la sesión que se puede reanudar
        if proc.timed_out:
            run.timed_out = True
            return run
        if proc.stalled:
            run.stalled = True
            return run
        if data is None or data.get("is_error"):
            reason = (data or {}).get("subtype") or proc.combined[-300:] or "sin salida"
            run.budget_exceeded = "budget" in str(reason).lower()
            run.errors.append(f"el CLI de {opts.provider} falló: {reason}")
            return run
        if "budget" in str(data.get("subtype", "")).lower():
            run.budget_exceeded = True
            return run
        parsed = extract_json_object(str(data.get("result", "")))
        try:
            if parsed is None:
                raise ValueError("la respuesta no contiene un objeto JSON")
            run.result = result_model.model_validate(parsed)
            run.errors = []
            return run
        except (ValueError, ValidationError) as exc:
            run.errors = [str(exc)[:500]]
        # ronda de reparación dentro de la misma sesión
        opts = ClaudeOptions(
            **{**opts.__dict__, "resume": True, "session_id": data.get("session_id") or opts.session_id}
        )
        prompt = ("Tu respuesta anterior no cumple el formato pedido "
                  f"({run.errors[0]}). Responde ÚNICAMENTE con el objeto JSON válido, sin texto adicional.")
    return run


def _task_text(ctx: ExecContext) -> str:
    cfg = ctx.config
    variables = cfg.get("variables") or {}
    if cfg.get("prompt_template"):
        text = (ctx.settings.root / cfg["prompt_template"]).read_text(encoding="utf-8")
    else:
        text = cfg.get("prompt", "")
    return render_template(text, variables)


def _failure(run: AgentRun) -> Outcome:
    if run.timed_out:
        return Outcome("timeout", error="el agente superó el tiempo máximo", cost_usd=run.cost_usd)
    if run.stalled:
        return Outcome("stalled", error="el agente estuvo demasiado tiempo sin actividad", cost_usd=run.cost_usd)
    if run.budget_exceeded:
        return Outcome("budget_exceeded", error="se superó el presupuesto de coste", cost_usd=run.cost_usd)
    return Outcome("failed", error="; ".join(run.errors) or "el agente no devolvió un resultado válido",
                   cost_usd=run.cost_usd)


async def _changed_files(ctx: ExecContext) -> list[str]:
    status = await ctx.process(["git", "status", "--porcelain"], cwd=ctx.config["workspace"], kind="git",
                               timeout_s=60)
    files = []
    for line in status.stdout.splitlines():
        path = line[3:].strip().strip('"')
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path:
            files.append(path)
    return files


class IaAgentExec(NodeExecutor):
    type = "ia_agent"

    async def run(self, ctx: ExecContext) -> Outcome:
        agent = await invoke_agent(ctx, role="dev", result_model=DevResult, task=_task_text(ctx), data_blocks=[],
                                   extra_rules=("`suggested_commit_subject`: una línea, en imperativo, "
                                                "descriptiva, de 10 a 72 caracteres."))
        if agent.result is None:
            return _failure(agent)
        result: DevResult = agent.result  # type: ignore[assignment]
        files = await _changed_files(ctx)
        outputs = {**result.model_dump(), "files": files, "files_changed": len(files),
                   "cost_usd": agent.cost_usd, "session_id": agent.session_id}
        return Outcome("done", outputs, cost_usd=agent.cost_usd)


def _format_feedback(issues: list[ReviewIssue]) -> str:
    lines = []
    for issue in issues:
        where = f"{issue.file}:{issue.line}" if issue.file and issue.line else issue.file
        text = f"- [{issue.severity}] {where + ' — ' if where else ''}{issue.description}"
        lines.append(text + (f" (sugerencia: {issue.suggestion})" if issue.suggestion else ""))
    return "\n".join(lines)


class IaAgentValidatorExec(NodeExecutor):
    type = "ia_agent_validator"

    async def run(self, ctx: ExecContext) -> Outcome:
        cfg = ctx.config
        cwd = cfg["workspace"]
        await ctx.process(["git", "add", "-N", "."], cwd=cwd, kind="git", timeout_s=60)
        diff = await ctx.process(["git", "diff", "--no-color", "HEAD"], cwd=cwd, kind="git", timeout_s=120)
        status = await ctx.process(["git", "status", "--short"], cwd=cwd, kind="git", timeout_s=60)
        diff_text = diff.stdout
        if len(diff_text) > MAX_DIFF_CHARS:
            diff_text = diff_text[:MAX_DIFF_CHARS] + "\n[... diff truncado ...]"
        blocks = [("Cambios del workspace (git diff)", diff_text or "(sin cambios)"),
                  ("Estado (git status)", status.stdout or "(limpio)")]
        if cfg.get("criteria"):
            blocks.append(("Criterios de aceptación", str(cfg["criteria"])))
        agent = await invoke_agent(
            ctx, role="validator", result_model=ValidatorResult, task=_task_text(ctx), data_blocks=blocks,
            extra_rules="Usa `verdict` = `changes_requested` solo si hay incidencias bloqueantes.")
        if agent.result is None:
            return _failure(agent)
        result: ValidatorResult = agent.result  # type: ignore[assignment]

        blocking_severities = set(cfg.get("blocking_severities") or ["high", "medium"])
        blocking = [i for i in result.issues if i.severity in blocking_severities]
        previous = [a for a in ctx.store.attempts(ctx.run_id, ctx.node_id) if a["attempt"] < ctx.attempt]
        retries_used = sum(1 for a in previous if a["event"] == "changes_requested")
        feedback = _format_feedback(blocking)
        outputs = {"verdict": "changes_requested" if blocking else "approved", "feedback": feedback,
                   "issues": [i.model_dump() for i in result.issues], "notes": result.notes,
                   "retries_used": retries_used, "cost_usd": agent.cost_usd}
        if not blocking:
            return Outcome("approved", outputs, cost_usd=agent.cost_usd)
        if retries_used >= int(cfg.get("max_retries", 2)):
            return Outcome("max_retries_reached", outputs, cost_usd=agent.cost_usd)
        last_feedback = next((a["outputs"].get("feedback") for a in reversed(previous)
                              if a["event"] == "changes_requested" and a["outputs"]), None)
        if cfg.get("stop_on_no_progress", True) and last_feedback and _sha(last_feedback) == _sha(feedback):
            return Outcome("no_progress", outputs, cost_usd=agent.cost_usd)
        outputs["retries_used"] = retries_used + 1
        return Outcome("changes_requested", outputs, cost_usd=agent.cost_usd)
