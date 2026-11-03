"""Interfaz de línea de comandos de gestplan."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import getpass
import json
import sys
from pathlib import Path

from gestplan.catalog import build_catalog, render_markdown
from gestplan.db import Store
from gestplan.doctor import run_doctor
from gestplan.dryrun import dry_run
from gestplan.engine.engine import Engine, PlanInvalid
from gestplan.plans.issues import Issue
from gestplan.plans.loader import find_plan_file, load_plan
from gestplan.plans.validator import validate_plan
from gestplan.settings import Settings

STATUS_MARK = {
    "succeeded": "OK", "failed": "FALLO", "running": "...", "waiting_approval": "APROBAR",
    "waiting_external": "ESPERA", "ready": "listo", "pending": "pend.", "skipped": "salta",
    "cancelled": "cancel.", "interrupted": "INTERR.",
}


def _print_issues(issues: list[Issue]) -> None:
    for issue in issues:
        print(issue.format())
    errors = sum(1 for i in issues if i.severity == "error")
    print(f"\n{errors} error(es), {len(issues) - errors} aviso(s)")


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings.from_env()
    root = getattr(args, "root", None)
    data_dir = getattr(args, "data_dir", None)
    if root:
        settings = dataclasses.replace(settings, root=Path(root))
    if data_dir:
        settings = dataclasses.replace(settings, data_dir=Path(data_dir))
    return settings


def _open(args: argparse.Namespace) -> tuple[Settings, Store, Engine]:
    settings = _settings(args)
    store = Store(settings.db_path)
    return settings, store, Engine(store, settings)


def _resolve_plan_path(settings: Settings, ref: str) -> Path | None:
    path = Path(ref)
    if path.is_file():
        return path
    return find_plan_file(settings.root, ref)


# --- validate / catalog / dry-run / doctor ---------------------------------------------------
def _cmd_validate(args: argparse.Namespace) -> int:
    plan, issues = load_plan(Path(args.plan))
    if plan is not None:
        issues = validate_plan(plan, args.root).issues
    failed = any(i.severity == "error" for i in issues)
    if args.json:
        print(json.dumps({"ok": not failed, "issues": [i.__dict__ for i in issues]},
                         ensure_ascii=False, indent=2))
    elif not issues:
        print("Plan válido, sin avisos.")
    else:
        _print_issues(issues)
    return 1 if failed else 0


def _cmd_catalog(args: argparse.Namespace) -> int:
    catalog = build_catalog()
    print(json.dumps(catalog, ensure_ascii=False, indent=2) if args.format == "json" else render_markdown(catalog))
    return 0


def _parse_inputs(pairs: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for pair in pairs:
        name, separator, value = pair.partition("=")
        if not separator or not name:
            raise SystemExit(f"--input debe tener el formato nombre=valor (recibido: '{pair}')")
        values[name] = value
    return values


def _cmd_dry_run(args: argparse.Namespace) -> int:
    plan, issues = load_plan(Path(args.plan))
    if plan is None:
        _print_issues(issues)
        return 1
    report = dry_run(plan, _parse_inputs(args.input), args.root)
    print(report.render())
    return 0 if report.ok else 1


def _cmd_doctor(_args: argparse.Namespace) -> int:
    checks = run_doctor()
    for check in checks:
        print(check.format())
    failed = [c for c in checks if c.status == "fail"]
    print(f"\n{len(failed)} comprobación(es) fallida(s)")
    return 1 if failed else 0


def _cmd_plans(args: argparse.Namespace) -> int:
    """Lista los planes de plans/ y plans/examples/, ordenados por nombre; los no válidos se marcan."""
    entries: list[tuple[str, str]] = []
    for folder in (Path(args.root) / "plans", Path(args.root) / "plans" / "examples"):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            plan, issues = load_plan(path)
            if plan is not None:
                issues = validate_plan(plan, args.root).issues
            errors = [i for i in issues if i.severity == "error"]
            if plan is None or errors:
                reason = errors[0].message if errors else "no se pudo cargar"
                entries.append((path.stem, f"{path.stem:<28} [NO VÁLIDO] {reason}"))
                continue
            required = [name for name, spec in plan.inputs.items() if spec.required]
            entries.append((plan.name, f"{plan.name:<28} {len(plan.nodes):>3} nodos  "
                                       f"obligatorios: {', '.join(required) or '-'}  {plan.description}"))
    if not entries:
        print("No hay planes en plans/ ni en plans/examples/.")
        return 0
    for _name, line in sorted(entries, key=lambda entry: entry[0]):
        print(line.rstrip())
    return 0


# --- ejecución --------------------------------------------------------------------------------
def render_run(store: Store, run_id: str, indent: str = "") -> str:
    run = store.get_run(run_id)
    assert run is not None
    cost = store.total_cost(run_id)
    lines = [f"{indent}Run {run['id']} · plan {run['plan_name']} · {run['status'].upper()}"
             + (f" · coste {cost:.4f} USD" if cost else "")]
    if run["error"]:
        lines.append(f"{indent}  error: {run['error']}")
    for node_id, node in store.all_nodes(run_id).items():
        mark = STATUS_MARK.get(node["status"], node["status"])
        event = f" -> {node['last_event']}" if node["last_event"] else ""
        attempts = f" (intentos: {node['attempts']})" if node["attempts"] > 1 else ""
        lines.append(f"{indent}  {node_id:<14} {mark:<8}{event}{attempts}")
        last = store.attempts(run_id, node_id)[-1:] or []
        if last and last[0]["error"] and node["status"] in ("failed", "interrupted", "cancelled"):
            lines.append(f"{indent}      ! {str(last[0]['error'])[:200]}")
    for approval in store.list_approvals("pending"):
        if approval["run_id"] == run_id:
            lines.append(f"{indent}  >> aprobación #{approval['id']} pendiente en '{approval['node_id']}': "
                         f"gestplan approvals show {approval['id']}")
    for child in store.children_of(run_id):
        lines.append(render_run(store, child["id"], indent + "    "))
    return "\n".join(lines)


def run_summary(store: Store, run_id: str) -> dict:
    """Estado de un run en forma de dict (base de `status --json`)."""
    run = store.get_run(run_id)
    assert run is not None
    return {
        "id": run["id"],
        "plan": run["plan_name"],
        "status": run["status"],
        "cost_usd": store.total_cost(run_id),
        # orden por id: la consulta no garantiza orden, así que la salida JSON sería inestable
        "nodes": {node_id: {"status": node["status"], "attempts": node["attempts"],
                            "last_event": node["last_event"]}
                  for node_id, node in sorted(store.all_nodes(run_id).items())},
        "children": [child["id"] for child in store.children_of(run_id)],
    }


def _drive(engine: Engine, store: Store, run_id: str) -> int:
    try:
        status = asyncio.run(engine.run_until_quiescent(run_id))
    except KeyboardInterrupt:
        print("\nInterrumpido. Usa 'gestplan resume' para continuar.")
        return 130
    print(render_run(store, run_id))
    return {"succeeded": 0, "waiting": 3, "failed": 1, "cancelled": 1}.get(status, 1)


def _cmd_run(args: argparse.Namespace) -> int:
    settings, store, engine = _open(args)
    path = _resolve_plan_path(settings, args.plan)
    if path is None:
        print(f"No se encuentra el plan '{args.plan}'")
        return 2
    plan, issues = load_plan(path)
    if plan is None:
        _print_issues(issues)
        return 1
    try:
        run_id = engine.create_run(plan, _parse_inputs(args.input))
    except PlanInvalid as exc:
        _print_issues(exc.issues)
        return 1
    print(f"Run creado: {run_id}")
    return _drive(engine, store, run_id)


def _cmd_resume(args: argparse.Namespace) -> int:
    _settings_, store, engine = _open(args)
    if store.get_run(args.run_id) is None:
        print(f"No existe el run {args.run_id}")
        return 2
    recovered = engine.recover()
    if recovered:
        print(f"{recovered} nodo(s) interrumpido(s) por una caída; usa 'gestplan retry' para reactivarlos.")
    return _drive(engine, store, args.run_id)


def _cmd_status(args: argparse.Namespace) -> int:
    _s, store, _e = _open(args)
    if args.run_id:
        if store.get_run(args.run_id) is None:
            print(f"No existe el run {args.run_id}")
            return 2
        if args.json:
            print(json.dumps(run_summary(store, args.run_id), ensure_ascii=False, indent=2))
        else:
            print(render_run(store, args.run_id))
        return 0
    runs = store.list_runs(limit=args.limit)
    if args.json:
        listing = [{"id": run["id"], "plan": run["plan_name"], "status": run["status"]}
                   for run in runs if run["parent_run_id"] is None]
        print(json.dumps(listing, ensure_ascii=False, indent=2))
        return 0
    if not runs:
        print("No hay runs.")
    for run in runs:
        if run["parent_run_id"] is None:
            print(f"{run['id']}  {run['status']:<10} {run['plan_name']}  {run['inputs']}")
    pending = store.list_approvals("pending")
    if pending:
        print(f"\n{len(pending)} aprobación(es) pendiente(s): gestplan approvals list")
    return 0


def _cmd_logs(args: argparse.Namespace) -> int:
    settings, store, _e = _open(args)
    attempts = store.attempts(args.run_id, args.node)
    if not attempts:
        print("Sin intentos para ese nodo.")
        return 2
    number = args.attempt or attempts[-1]["attempt"]
    folder = settings.data_dir / "runs" / args.run_id / args.node / str(number)
    for path in sorted(folder.glob("*")):
        if path.is_file() and path.suffix in (".log", ".md", ".json", ".txt"):
            print(f"===== {path.name} =====")
            print(path.read_text(encoding="utf-8", errors="replace").rstrip())
    return 0


def _cmd_cancel(args: argparse.Namespace) -> int:
    _s, store, engine = _open(args)
    engine.cancel_run(args.run_id)
    print(render_run(store, args.run_id))
    return 0


def _cmd_retry(args: argparse.Namespace) -> int:
    _s, store, engine = _open(args)
    engine.recover()
    try:
        engine.retry_node(args.run_id, args.node)
    except ValueError as exc:
        print(exc)
        return 2
    return _drive(engine, store, args.run_id) if args.drive else (print("Nodo reactivado. Ejecuta 'gestplan resume "
                                                                          f"{args.run_id}'.") or 0)


def _print_preview(name: str, preview: dict) -> None:
    kind = preview.get("kind")
    print(f"--- {name}: {preview.get('title', kind)} ---")
    if kind == "keyvalue":
        for key, value in preview.get("items", {}).items():
            print(f"{key}:\n{value}\n" if "\n" in str(value) else f"{key}: {value}")
    elif kind == "markdown":
        print(preview.get("text", ""))
    else:
        print(preview.get("message", json.dumps(preview, ensure_ascii=False)))


def _cmd_approvals(args: argparse.Namespace) -> int:
    _s, store, _e = _open(args)
    if args.action == "list":
        approvals = store.list_approvals(None if args.all else "pending")
        if not approvals:
            print("No hay aprobaciones pendientes.")
        for a in approvals:
            covers = ", ".join(a["covers"])
            print(f"#{a['id']}  {a['status']:<11} run {a['run_id']}  nodo {a['node_id']}  cubre {covers}")
        return 0
    approval = store.get_approval(args.id)
    if approval is None:
        print(f"No existe la aprobación #{args.id}")
        return 2
    if args.action == "show":
        print(f"Aprobación #{approval['id']} · run {approval['run_id']} · nodo {approval['node_id']} · "
              f"{approval['status']}")
        if approval["message"]:
            print(approval["message"])
        for name, preview in approval["previews"].items():
            print()
            _print_preview(name, preview)
        return 0
    status = "approved" if args.action == "approve" else "rejected"
    if store.decide_approval(args.id, status, comment=args.comment, decided_by=getpass.getuser()):
        print(f"Aprobación #{args.id}: {status}. Continúa con: gestplan resume {approval['run_id']}")
        return 0
    print(f"La aprobación #{args.id} ya no está pendiente ({approval['status']}).")
    return 2


def _cmd_cost(args: argparse.Namespace) -> int:
    _s, store, _e = _open(args)
    runs = [store.get_run(args.run_id)] if args.run_id else store.list_runs(limit=args.limit)
    total = 0.0
    for run in runs:
        if run is None:
            print(f"No existe el run {args.run_id}")
            return 2
        cost = store.total_cost(run["id"])
        total += cost
        print(f"{run['id']}  {run['plan_name']:<20} {cost:.4f} USD")
    print(f"Total: {total:.4f} USD")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn

        from gestplan.server.app import create_app
    except ImportError:
        print("Faltan las dependencias del servidor: pip install -e \".[api]\"")
        return 2
    settings = _settings(args)
    print(f"gestplan en http://{args.host}:{args.port}  (raíz: {settings.root.resolve()})")
    uvicorn.run(create_app(settings), host=args.host, port=args.port, log_level="warning")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gestplan", description="Motor local de planes como grafos")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--root", default=".", help="raíz del proyecto (plantillas, scripts, planes)")
        p.add_argument("--data-dir", default=None, help="carpeta de datos (por defecto ./data o GP_DATA_DIR)")

    validate = sub.add_parser("validate", help="valida un plan JSON")
    validate.add_argument("plan")
    validate.add_argument("--root", default=".")
    validate.add_argument("--json", action="store_true", help="salida en JSON")
    validate.set_defaults(func=_cmd_validate)

    catalog = sub.add_parser("catalog", help="muestra el catálogo de nodos")
    catalog.add_argument("--format", choices=("markdown", "json"), default="markdown")
    catalog.set_defaults(func=_cmd_catalog)

    dry = sub.add_parser("dry-run", help="simula el grafo sin ejecutar nada")
    dry.add_argument("plan")
    dry.add_argument("--input", action="append", default=[], metavar="NOMBRE=VALOR")
    dry.add_argument("--root", default=".")
    dry.set_defaults(func=_cmd_dry_run)

    doctor = sub.add_parser("doctor", help="comprueba el entorno")
    doctor.set_defaults(func=_cmd_doctor)

    plans = sub.add_parser("plans", help="lista los planes de plans/ y plans/examples/")
    common(plans)
    plans.set_defaults(func=_cmd_plans)

    run = sub.add_parser("run", help="lanza un plan (ruta o nombre) y lo ejecuta hasta que termina o espera")
    run.add_argument("plan")
    run.add_argument("--input", action="append", default=[], metavar="NOMBRE=VALOR")
    common(run)
    run.set_defaults(func=_cmd_run)

    resume = sub.add_parser("resume", help="continúa un run (tras aprobar, reactivar o una caída)")
    resume.add_argument("run_id")
    common(resume)
    resume.set_defaults(func=_cmd_resume)

    status = sub.add_parser("status", help="lista los runs o muestra uno")
    status.add_argument("run_id", nargs="?")
    status.add_argument("--limit", type=int, default=20)
    status.add_argument("--json", action="store_true", help="salida en JSON (estable, para UI y scripts)")
    common(status)
    status.set_defaults(func=_cmd_status)

    logs = sub.add_parser("logs", help="muestra los logs de un nodo")
    logs.add_argument("run_id")
    logs.add_argument("node")
    logs.add_argument("--attempt", type=int)
    common(logs)
    logs.set_defaults(func=_cmd_logs)

    cancel = sub.add_parser("cancel", help="cancela un run (y sus hijos)")
    cancel.add_argument("run_id")
    common(cancel)
    cancel.set_defaults(func=_cmd_cancel)

    retry = sub.add_parser("retry", help="reactiva un nodo failed, cancelled o interrumpido")
    retry.add_argument("run_id")
    retry.add_argument("node")
    retry.add_argument("--drive", action="store_true", help="ejecutar el run tras reactivar")
    common(retry)
    retry.set_defaults(func=_cmd_retry)

    approvals = sub.add_parser("approvals", help="gestiona las aprobaciones manuales")
    asub = approvals.add_subparsers(dest="action", required=True)
    alist = asub.add_parser("list")
    alist.add_argument("--all", action="store_true")
    common(alist)
    for name in ("show", "approve", "reject"):
        p = asub.add_parser(name)
        p.add_argument("id", type=int)
        if name != "show":
            p.add_argument("-c", "--comment", default=None)
        common(p)
    approvals.set_defaults(func=_cmd_approvals)
    alist.set_defaults(func=_cmd_approvals)
    for p in asub.choices.values():
        p.set_defaults(func=_cmd_approvals)

    serve = sub.add_parser("serve", help="arranca el servidor web (API, tiempo real y UI)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    common(serve)
    serve.set_defaults(func=_cmd_serve)

    cost = sub.add_parser("cost", help="coste acumulado de los runs")
    cost.add_argument("run_id", nargs="?")
    cost.add_argument("--limit", type=int, default=20)
    common(cost)
    cost.set_defaults(func=_cmd_cost)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return args.func(args)
