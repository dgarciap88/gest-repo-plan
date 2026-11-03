"""Catálogo de nodos autodescriptivo, generado del código.

Es la única referencia que lee quien diseña un plan, así que debe bastarse a sí mismo: formato del plan,
reglas, y para cada nodo sus eventos, configuración, estructuras anidadas y salidas.
"""

from __future__ import annotations

import json
from typing import Any

from gestplan.catalog_docs import PLAN_FORMAT, field_doc
from gestplan.nodes.base import NodeType
from gestplan.nodes.registry import all_node_types
from gestplan.settings import MODEL_TIERS, Settings

_SIDE_EFFECT_LABEL = {
    "none": "ninguno",
    "local": "local (solo el workspace)",
    "external": "EXTERNO (exige manual_approval)",
}
_REF_TYPE = "referencia `${...}`"


def _is_reference(prop: dict[str, Any]) -> bool:
    return prop.get("type") == "string" and str(prop.get("pattern", "")).startswith("^\\$\\{")


def _type_of(prop: dict[str, Any]) -> str:
    if _is_reference(prop):
        return _REF_TYPE
    if "enum" in prop:
        return " | ".join(json.dumps(v, ensure_ascii=False) for v in prop["enum"])
    if "const" in prop:
        return json.dumps(prop["const"], ensure_ascii=False)
    for key in ("anyOf", "oneOf"):
        if key in prop:
            options = [_type_of(option) for option in prop[key]]
            return " | ".join(dict.fromkeys(o for o in options if o != "null"))
    if "$ref" in prop:
        return prop["$ref"].rsplit("/", 1)[-1]
    kind = prop.get("type", "cualquiera")
    if kind == "array":
        item = _type_of(prop.get("items", {}))
        return f"lista de {item}" if " | " not in item else f"lista de ({item})"
    if kind == "object":
        return "objeto"
    return {"string": "texto", "integer": "entero", "number": "número", "boolean": "booleano"}.get(kind, str(kind))


def _fields(schema: dict[str, Any], node_type: str, section: str, owner: str | None = None) -> list[dict[str, Any]]:
    required = set(schema.get("required", []))
    rows = []
    for name, prop in schema.get("properties", {}).items():
        rows.append({
            "name": name,
            "type": _type_of(prop),
            "required": name in required,
            "default": json.dumps(prop["default"], ensure_ascii=False) if "default" in prop else "",
            "description": prop.get("description") or field_doc(node_type, section, name, owner),
        })
    return rows


def _structures(schema: dict[str, Any], node_type: str, section: str) -> list[dict[str, Any]]:
    return [{"name": name, "fields": _fields(definition, node_type, section, owner=name)}
            for name, definition in schema.get("$defs", {}).items()]


def _node_entry(node_type: type[NodeType]) -> dict[str, Any]:
    config, outputs = node_type.ConfigModel.model_json_schema(), node_type.OutputsModel.model_json_schema()
    name = node_type.type
    return {
        "type": name,
        "version": node_type.version,
        "family": node_type.family,
        "description": node_type.description,
        "side_effect": node_type.side_effect,
        "events": [e.model_dump() for e in node_type.events],
        "config_schema": config,
        "outputs_schema": outputs,
        "config_fields": _fields(config, name, "config"),
        "config_structures": _structures(config, name, "config"),
        "outputs_fields": [] if node_type.dynamic_outputs else _fields(outputs, name, "outputs"),
        "outputs_structures": [] if node_type.dynamic_outputs else _structures(outputs, name, "outputs"),
        "default_timeout_s": node_type.default_timeout_s,
        "idle_timeout_s": node_type.idle_timeout_s,
        "dynamic_outputs": node_type.dynamic_outputs,
        "example": node_type.example,
    }


def _limits_text(limits: dict[str, Any]) -> str:
    return "\n".join([
        f"- Timeout máximo de un nodo: {limits['timeout_cap_s']} s.",
        f"- Nivel de modelo máximo: `{limits['max_model_tier']}` (niveles de menor a mayor coste: "
        f"{', '.join(limits['model_tiers'])}). Usa `cheap` salvo que haga falta más; una lista de niveles, "
        "p. ej. [\"cheap\", \"standard\"], sube de nivel en los reintentos.",
        f"- `max_retries` máximo de un validador: {limits['validator_max_retries_cap']}.",
        f"- `max_attempts` máximo de un nodo: {limits['max_attempts_cap']}.",
        f"- Ejecutables permitidos en `run_command`: {', '.join(limits['command_allowlist'])}.",
    ])


def build_catalog(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or Settings.from_env()
    limits = {
        "timeout_cap_s": settings.timeout_cap_s,
        "max_model_tier": settings.max_model_tier,
        "model_tiers": list(MODEL_TIERS),
        "validator_max_retries_cap": settings.validator_max_retries_cap,
        "max_attempts_cap": settings.max_attempts_cap,
        "command_allowlist": list(settings.command_allowlist),
    }
    return {
        "limits": limits,
        "plan_format": [{"title": title, "body": body} for title, body in PLAN_FORMAT]
        + [{"title": "Límites de este servidor", "body": _limits_text(limits)}],
        "nodes": [_node_entry(cls) for cls in all_node_types()],
    }


def _table(fields: list[dict[str, Any]]) -> list[str]:
    if not fields:
        return ["_(sin campos)_"]
    lines = ["| Campo | Tipo | Obligatorio | Por defecto | Descripción |", "|---|---|---|---|---|"]
    for f in fields:
        cells = [f"`{f['name']}`", f["type"], "sí" if f["required"] else "no", f["default"], f["description"]]
        lines.append("| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |")
    return lines


def render_markdown(catalog: dict[str, Any]) -> str:
    out = [
        "# Catálogo de nodos de gestplan",
        "",
        "Referencia completa para escribir planes JSON: primero el formato y las reglas, después cada tipo de nodo.",
    ]
    for section in catalog["plan_format"]:
        out += ["", f"## {section['title']}", "", section["body"]]
    out += ["", "## Nodos"]
    for node in catalog["nodes"]:
        out += [
            "",
            f"### `{node['type']}`",
            "",
            node["description"],
            "",
            f"- Familia: {node['family']} · versión {node['version']}",
            f"- Efecto: {_SIDE_EFFECT_LABEL[node['side_effect']]}",
        ]
        if node["default_timeout_s"]:
            out.append(f"- Timeout por defecto: {node['default_timeout_s']} s")
        out += ["", "**Eventos**", ""]
        if node["events"]:
            out += [f"- `{e['name']}`{' (fallo)' if e['failure'] else ''}: {e['description']}" for e in node["events"]]
        else:
            out.append("- Los define la configuración del nodo (explicado en su descripción).")
        out += ["", "**Configuración**", ""] + _table(node["config_fields"])
        for structure in node["config_structures"]:
            out += ["", f"Estructura `{structure['name']}`:", ""] + _table(structure["fields"])
        out += ["", "**Salidas**", ""]
        if node["dynamic_outputs"]:
            out.append("_Las define el propio script: sus claves quedan en `${nodes.<id>.outputs.<clave>}`._")
        else:
            out += _table(node["outputs_fields"])
            for structure in node["outputs_structures"]:
                out += ["", f"Estructura `{structure['name']}`:", ""] + _table(structure["fields"])
        if node["example"]:
            out += ["", "**Ejemplo de `config`**", "", "```json",
                    json.dumps(node["example"], indent=2, ensure_ascii=False), "```"]
    return "\n".join(out) + "\n"
