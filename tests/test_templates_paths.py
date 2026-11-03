import pytest

from gestplan.plans.paths import get_path
from gestplan.plans.templates import UnresolvedReference, find_refs, resolve_template


def test_get_path_navega_dicts_y_listas():
    ctx = {"a": {"b": [10, {"c": 5}]}}
    assert get_path(ctx, "a.b.0") == (True, 10)
    assert get_path(ctx, "a.b.1.c") == (True, 5)
    assert get_path(ctx, "a.x") == (False, None)
    assert get_path(ctx, "a.b.9") == (False, None)


def test_find_refs_recorre_estructuras_anidadas():
    value = {"x": "${inputs.a}", "y": ["pre ${nodes.n.outputs.p} post", 3], "z": "sin refs"}
    refs = {(loc, ref.raw) for loc, ref in find_refs(value)}
    assert refs == {("x", "inputs.a"), ("y[0]", "nodes.n.outputs.p")}


def test_resolve_conserva_el_tipo_si_es_solo_una_referencia():
    ctx = {"inputs": {"n": 3, "lista": [1, 2]}}
    assert resolve_template("${inputs.n}", ctx) == 3
    assert resolve_template("${inputs.lista}", ctx) == [1, 2]


def test_resolve_interpola_texto_mezclado():
    ctx = {"inputs": {"id": "TASK-1"}}
    assert resolve_template("feature/${inputs.id}", ctx) == "feature/TASK-1"
    assert resolve_template({"a": ["x-${inputs.id}"]}, ctx) == {"a": ["x-TASK-1"]}


def test_resolve_lanza_si_la_referencia_no_existe():
    with pytest.raises(UnresolvedReference):
        resolve_template("${inputs.nada}", {"inputs": {}})
