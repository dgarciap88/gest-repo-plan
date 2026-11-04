"""Parámetros de configuración editables desde la web: catálogo, validación y escritura del .env."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gestplan.settings import MODEL_TIERS

GROUPS = {
    "atlassian": "Atlassian (Jira y Confluence)",
    "ia": "Agentes de IA",
    "limits": "Límites y presupuestos",
    "git": "Git y rutas",
    "security": "Seguridad de la API",
}


@dataclass(frozen=True)
class Param:
    name: str
    group: str
    label: str
    kind: str = "text"  # text | secret | int | float | list | tier | path
    restart: bool = False  # solo se aplica al reiniciar el servidor
    example: str = ""


PARAMS: tuple[Param, ...] = (
    Param("JIRA_BASE_URL", "atlassian", "URL de Jira Cloud", example="https://tuempresa.atlassian.net"),
    Param("JIRA_EMAIL", "atlassian", "Correo de la cuenta de Atlassian"),
    Param("JIRA_API_TOKEN", "atlassian", "Token de API de Atlassian", "secret"),
    Param("CONFLUENCE_BASE_URL", "atlassian", "URL de Confluence (vacío: la de Jira + /wiki)"),
    Param("CONFLUENCE_EMAIL", "atlassian", "Correo de Confluence (vacío: el de Jira)"),
    Param("CONFLUENCE_API_TOKEN", "atlassian", "Token de Confluence (vacío: el de Jira)", "secret"),
    Param("GP_CLAUDE_PATH", "ia", "Ruta del ejecutable de Claude (vacío: el del PATH)", "path"),
    Param("GP_CLAUDE_SETTING_SOURCES", "ia", "Fuentes de configuración de Claude (user,project,local)"),
    Param("GP_COPILOT_PATH", "ia", "Ruta del ejecutable de Copilot (vacío: el del PATH)", "path"),
    Param("GP_COPILOT_MODEL_CHEAP", "ia", "Modelo de Copilot para el nivel barato (vacío: auto)"),
    Param("GP_COPILOT_MODEL_STANDARD", "ia", "Modelo de Copilot para el nivel estándar"),
    Param("GP_COPILOT_MODEL_PREMIUM", "ia", "Modelo de Copilot para el nivel premium"),
    Param("GP_AGENTS_HOME", "ia", "Carpeta de agentes y skills de usuario (vacío: ~/.copilot)", "path"),
    Param("GP_MAX_MODEL_TIER", "limits", "Nivel de modelo máximo que puede pedir un plan", "tier"),
    Param("GP_ALLOWED_MODELS", "limits", "Modelos permitidos, separados por comas (vacío: cualquiera)", "list"),
    Param("GP_RUN_BUDGET_USD", "limits", "Presupuesto máximo por run en USD (0: sin límite)", "float"),
    Param("GP_DAILY_BUDGET_USD", "limits", "Presupuesto máximo por día en USD (0: sin límite)", "float"),
    Param("GP_TIMEOUT_CAP_S", "limits", "Tope de timeout de un nodo (segundos)", "int"),
    Param("GP_MAX_ATTEMPTS_CAP", "limits", "Tope de reintentos por nodo", "int"),
    Param("GP_VALIDATOR_MAX_RETRIES_CAP", "limits", "Tope de rondas del validador", "int"),
    Param("GP_MAX_CONCURRENT_NODES", "limits", "Nodos que se ejecutan a la vez", "int", restart=True),
    Param("GP_MAX_AGENTS_PARALLEL", "limits", "Agentes de IA a la vez", "int", restart=True),
    Param("GP_COMMAND_ALLOWLIST", "limits", "Comandos permitidos en run_command, separados por comas", "list"),
    Param("GP_ENV_ALLOWLIST", "limits", "Variables de entorno que heredan los procesos, separadas por comas", "list"),
    Param("GP_DATA_DIR", "git", "Carpeta de datos (base de datos, logs)", "path", restart=True),
    Param("GP_WORKSPACES_DIR", "git", "Carpeta de los workspaces (vacío: dentro de la de datos)", "path"),
    Param("GP_GIT_USER_NAME", "git", "Nombre de autor de los commits (vacío: el del repositorio)"),
    Param("GP_GIT_USER_EMAIL", "git", "Correo de autor de los commits (vacío: el del repositorio)"),
    Param("GP_API_TOKEN", "security", "Token de la API local (la web se abre con ?token=…)", "secret", restart=True),
    Param("GP_ALLOWED_HOSTS", "security", "Hosts extra permitidos en la API (separados por comas)", "list",
          restart=True),
)
BY_NAME = {p.name: p for p in PARAMS}
_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return values
    for line in lines:
        found = _LINE.match(line)
        if found and not line.lstrip().startswith("#"):
            value = found.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[found.group(1)] = value
    return values


def describe(path: Path) -> list[dict[str, Any]]:
    """Estado de cada parámetro. Los secretos nunca salen: solo si están definidos."""
    stored = read_env_file(path)
    out = []
    for param in PARAMS:
        file_value, live = stored.get(param.name), os.environ.get(param.name)
        secret = param.kind == "secret"
        source = "default"
        if live not in (None, ""):
            source = "env" if file_value is None or file_value != live else "file"
        elif file_value:
            source = "file"
        out.append({
            "name": param.name, "group": param.group, "label": param.label, "kind": param.kind,
            "restart": param.restart, "example": param.example, "secret": secret,
            "value": "" if secret else (file_value or ""),
            "effective": "" if secret else (live or ""),
            "configured": bool(live or file_value),
            "source": source,
            "shadowed": bool(file_value is not None and live not in (None, file_value)),
        })
    return out


def validate(name: str, value: str) -> str | None:
    """Mensaje de error si el valor no es válido para ese parámetro."""
    param = BY_NAME.get(name)
    if param is None:
        return "parámetro desconocido"
    if "\n" in value or "\r" in value:
        return "no puede contener saltos de línea"
    if value == "":
        return None
    if param.kind == "int" and not value.isdigit():
        return "debe ser un entero positivo"
    if param.kind == "int" and int(value) < 1:
        return "debe ser al menos 1"
    if param.kind == "float":
        try:
            if float(value) < 0:
                return "no puede ser negativo"
        except ValueError:
            return "debe ser un número"
    if param.kind == "tier" and value not in MODEL_TIERS:
        return f"debe ser uno de: {', '.join(MODEL_TIERS)}"
    if name.endswith("_BASE_URL") and not re.match(r"^https?://[^\s]+$", value):
        return "debe ser una URL http(s)"
    return None


def write_env_file(path: Path, changes: dict[str, str | None]) -> None:
    """Actualiza (o añade) las claves, conservando comentarios y el resto de líneas; None borra la clave."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        lines = []
    pending = dict(changes)
    out: list[str] = []
    for line in lines:
        found = _LINE.match(line)
        name = found.group(1) if found and not line.lstrip().startswith("#") else None
        if name in pending:
            value = pending.pop(name)
            if value is not None:
                out.append(f"{name}={value}")
            continue
        out.append(line)
    for name, value in pending.items():
        if value is not None:
            out.append(f"{name}={value}")
    path.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")
