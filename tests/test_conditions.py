import pytest
from pydantic import TypeAdapter, ValidationError

from gestplan.plans.conditions import Condition, evaluate, iter_predicates

ADAPTER = TypeAdapter(Condition)
CTX = {
    "inputs": {"summary": "[BUG] fallo al guardar", "labels": ["a", "skip-review"], "n": 5},
    "nodes": {"dev": {"outputs": {"files_changed": 30}}},
}


def check(data, captures=None):
    return evaluate(ADAPTER.validate_python(data), CTX, captures)


def test_operadores_basicos():
    assert check({"path": "inputs.n", "op": "eq", "value": 5})
    assert check({"path": "inputs.n", "op": "ne", "value": 4})
    assert check({"path": "inputs.n", "op": "gt", "value": 4})
    assert check({"path": "inputs.n", "op": "gte", "value": 5})
    assert check({"path": "inputs.n", "op": "lt", "value": 6})
    assert check({"path": "inputs.n", "op": "lte", "value": 5})
    assert check({"path": "inputs.n", "op": "in", "value": [1, 5]})
    assert check({"path": "inputs.labels", "op": "contains", "value": "skip-review"})
    assert check({"path": "inputs.n", "op": "exists"})
    assert not check({"path": "inputs.zzz", "op": "exists"})


def test_ruta_ausente_solo_cumple_ne():
    assert check({"path": "inputs.zzz", "op": "ne", "value": 1})
    assert not check({"path": "inputs.zzz", "op": "eq", "value": 1})


def test_regex_con_grupos_con_nombre():
    captures: dict[str, str] = {}
    assert check(
        {"path": "inputs.summary", "op": "matches", "value": r"^\[(?P<kind>[A-Z]+)\]"}, captures
    )
    assert captures == {"kind": "BUG"}


def test_combinadores():
    cond = {
        "all": [
            {"path": "nodes.dev.outputs.files_changed", "op": "gt", "value": 20},
            {"not": {"path": "inputs.labels", "op": "contains", "value": "otra"}},
            {"any": [{"path": "inputs.n", "op": "eq", "value": 0}, {"path": "inputs.n", "op": "eq", "value": 5}]},
        ]
    }
    assert check(cond)
    assert not check({"not": cond})


def test_tipos_incomparables_dan_falso_sin_romper():
    assert not check({"path": "inputs.summary", "op": "gt", "value": 3})


def test_modelo_rechaza_condiciones_invalidas():
    with pytest.raises(ValidationError):
        ADAPTER.validate_python({"path": "a", "op": "gt"})  # falta value
    with pytest.raises(ValidationError):
        ADAPTER.validate_python({"path": "a", "op": "in", "value": "no-lista"})
    with pytest.raises(ValidationError):
        ADAPTER.validate_python({"path": "a", "op": "matches", "value": 3})
    with pytest.raises(ValidationError):
        ADAPTER.validate_python({"path": "a", "op": "eq", "value": 1, "extra": True})
    with pytest.raises(ValidationError):
        ADAPTER.validate_python({"all": []})


def test_iter_predicates():
    cond = ADAPTER.validate_python(
        {"all": [{"path": "a", "op": "exists"}, {"not": {"path": "b", "op": "exists"}}]}
    )
    assert [p.path for p in iter_predicates(cond)] == ["a", "b"]
