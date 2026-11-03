"""Catálogo de nodos autodescriptivo, generado del código (ver DESIGN.md sección 5)."""

from __future__ import annotations

import json
from typing import Any

from gestplan.nodes.base import NodeType
from gestplan.nodes.registry import all_node_types
from gestplan.settings import MODEL_TIERS, Settings

_SIDE_EFFECT_LABEL = {
    "none": "ninguno",
    "local": "local (solo el workspace)",
    "external": "EXTERNO (exige manual_approval)",
}


def _node_entry(node_type: type[NodeType]) -> dict[str, Any]:
    return {
        "type": node_type.type,
        "version": node_type.version,
        "family": node_type.family,
        "description": node_type.description,
        "side_effect": node_type.side_effect,
        "events": [e.model_dump() for e in node_type.events],
        "config_schema": node_type.ConfigModel.model_json_schema(),
        "outputs_schema": node_type.OutputsModel.model_json_schema(),
        "default_timeout_s": node_type.default_timeout_s,
        "idle_timeout_s": node_type.idle_timeout_s,
        "dynamic_outputs": node_type.dynamic_outputs,
        "example": node_type.example,
    }


def build_catalog(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or Settings.from_env()
    return {
        "limits": {
            "timeout_cap_s": settings.timeout_cap_s,
            "max_model_tier": settings.max_model_tier,
            "model_tiers": list(MODEL_TIERS),
            "validator_max_retries_cap": settings.validator_max_retries_cap,
            "max_attempts_cap": settings.max_attempts_cap,
            "command_allowlist": list(settings.command_allowlist),
        },
        "nodes": [_node_entry(cls) for cls in all_node_types()],
    }


def _type_of(prop: dict[str, Any]) -> str:
    if "enum" in prop:
        return " | ".join(json.dumps(v, ensure_ascii=False) for v in prop["enum"])
    if "anyOf" in prop:
        options = [_type_of(option) for option in prop["anyOf"]]
        return " | ".join(dict.fromkeys(o for o in options if o != "null"))
    if "$ref" in prop:
        return prop["$ref"].rsplit("/", 1)[-1]
    kind = prop.get("type", "any")
    if kind == "array":
        return f"lista de {_type_of(prop.get('items', {}))}"
    return str(kind)


def _fields_table(schema: dict[str, Any]) -> list[str]:
    properties = schema.get("properties", {})
    if not properties:
        return ["_(sin campos)_"]
    required = set(schema.get("required", []))
    lines = ["| Campo | Tipo | Obligatorio | Por defecto | Descripción |", "|---|---|---|---|---|"]
    for name, prop in properties.items():
        default = json.dumps(prop["default"], ensure_ascii=False) if "default" in prop else ""
        description = prop.get("description", "").replace("|", "\\|")
        kind = _type_of(prop).replace("|", "\\|")
        mandatory = "sí" if name in required else "no"
        lines.append(f"| `{name}` | {kind} | {mandatory} | {default} | {description} |")
    return lines


def render_markdown(catalog: dict[str, Any]) -> str:
    limits = catalog["limits"]
    out = [
        "# Catálogo de nodos de gestplan",
        "",
        "Documento generado del código. Úsalo como referencia para escribir planes JSON.",
        "",
        "## Reglas del plan",
        "",
        "- Un plan tiene `name`, `inputs`, `settings`, `nodes` (id → tipo, `config`), `edges` y "
        "opcionalmente `layout`.",
        "- Las aristas conectan eventos con nodos: `{\"from\": \"nodo.evento\", \"to\": \"otro_nodo\"}`.",
        "- Referencias en la config: `${inputs.x}`, `${nodes.<id>.outputs.<campo>}`, `${run.id}`, "
        "`${env.NOMBRE}`. Solo se pueden usar salidas de nodos que se ejecutan antes.",
        "- **Aprobación:** todo nodo con efecto EXTERNO debe tener un `manual_approval` que lo cubra "
        "(`approves`) en todos los caminos que llegan a él.",
        "- **Ciclos:** todo ciclo debe estar acotado: `max_attempts` en un nodo del ciclo o un "
        "`ia_agent_validator` con `max_retries` cuyo `changes_requested` vuelva al nodo del bucle.",
        f"- **Topes:** timeout máximo {limits['timeout_cap_s']} s; nivel de modelo máximo "
        f"`{limits['max_model_tier']}` (niveles: {', '.join(limits['model_tiers'])}); "
        f"máximo de `max_retries` del validador {limits['validator_max_retries_cap']}; "
        f"máximo de `max_attempts` {limits['max_attempts_cap']}.",
        f"- **Comandos permitidos en `run_command`:** {', '.join(limits['command_allowlist'])}.",
        "- Usa por defecto el nivel de modelo `cheap`; sube de nivel solo si es necesario.",
        "",
        "## Nodos",
    ]
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
        events = node["events"]
        out += ["", "**Eventos**", ""]
        if events:
            out += [
                f"- `{e['name']}`{' (fallo)' if e['failure'] else ''}: {e['description']}"
                for e in events
            ]
        else:
            out.append("- Dinámicos: dependen de la configuración (ver descripción).")
        out += ["", "**Configuración**", ""] + _fields_table(node["config_schema"])
        out += ["", "**Salidas**", ""]
        if node["dynamic_outputs"]:
            out.append("_Dinámicas (definidas por el script)._")
        else:
            out += _fields_table(node["outputs_schema"])
        if node["example"]:
            out += ["", "**Ejemplo de `config`**", "", "```json",
                    json.dumps(node["example"], indent=2, ensure_ascii=False), "```"]
    return "\n".join(out) + "\n"
