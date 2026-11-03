"""Interfaz de línea de comandos de gestplan."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from gestplan.catalog import build_catalog, render_markdown
from gestplan.doctor import run_doctor
from gestplan.dryrun import dry_run
from gestplan.plans.issues import Issue
from gestplan.plans.loader import load_plan
from gestplan.plans.validator import validate_plan


def _print_issues(issues: list[Issue]) -> None:
    for issue in issues:
        print(issue.format())
    errors = sum(1 for i in issues if i.severity == "error")
    warnings = len(issues) - errors
    print(f"\n{errors} error(es), {warnings} aviso(s)")


def _cmd_validate(args: argparse.Namespace) -> int:
    plan, issues = load_plan(Path(args.plan))
    if plan is not None:
        issues = validate_plan(plan, args.root).issues
    if args.json:
        print(json.dumps({"ok": not any(i.severity == "error" for i in issues),
                          "issues": [i.__dict__ for i in issues]}, ensure_ascii=False, indent=2))
    else:
        if not issues:
            print("Plan válido, sin avisos.")
        else:
            _print_issues(issues)
    return 1 if any(i.severity == "error" for i in issues) else 0


def _cmd_catalog(args: argparse.Namespace) -> int:
    catalog = build_catalog()
    if args.format == "json":
        print(json.dumps(catalog, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(catalog))
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gestplan", description="Motor local de planes como grafos")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="valida un plan JSON")
    validate.add_argument("plan")
    validate.add_argument("--root", default=".", help="raíz del proyecto (plantillas, scripts, planes)")
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
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return args.func(args)
