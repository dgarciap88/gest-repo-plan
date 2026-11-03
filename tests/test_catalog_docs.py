"""El catálogo debe ser autocontenido: sin remisiones a otros ficheros y con todos los campos descritos."""

import json
import re

from gestplan.catalog import build_catalog, render_markdown
from gestplan.settings import Settings


def walk_fields(node):
    for key in ("config_fields", "outputs_fields"):
        for field in node[key]:
            yield node["type"], key, field
    for key in ("config_structures", "outputs_structures"):
        for structure in node[key]:
            for field in structure["fields"]:
                yield node["type"], f"{key}:{structure['name']}", field


def test_todos_los_campos_tienen_descripcion():
    catalog = build_catalog(Settings())
    missing = [f"{t} {where} {f['name']}" for node in catalog["nodes"] for t, where, f in walk_fields(node)
               if not f["description"].strip()]
    assert missing == []


def test_el_catalogo_no_remite_a_otros_documentos():
    catalog = build_catalog(Settings())
    text = json.dumps(catalog, ensure_ascii=False) + render_markdown(catalog)
    assert "DESIGN" not in text and "NICE_TO_HAVE" not in text and "README" not in text
    assert not re.search(r"\bver (la )?(secci|descripci|doc)", text, re.IGNORECASE)


def test_las_estructuras_anidadas_y_los_tipos_se_explican():
    catalog = build_catalog(Settings())
    md = render_markdown(catalog)
    assert "Estructura `Heading`" in md and "Estructura `Predicate`" in md and "Estructura `SubtaskSpec`" in md
    assert "lista de any" not in md and "cualquiera" not in md.split("## Nodos")[0]
    page = next(n for n in catalog["nodes"] if n["type"] == "confluence.create_page")
    blocks = next(f for f in page["config_fields"] if f["name"] == "blocks")
    assert "Heading" in blocks["type"] and "referencia" in blocks["type"]
    titles = [s["title"] for s in catalog["plan_format"]]
    assert {"Estructura de un plan", "Inputs", "Aristas y eventos", "Referencias"} <= set(titles)
