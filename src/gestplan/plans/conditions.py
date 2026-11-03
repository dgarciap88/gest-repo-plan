"""Condiciones declarativas en JSON para el nodo `route`."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from gestplan.plans.paths import get_path

Op = Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "contains", "exists", "matches"]
_NEEDS_VALUE = ("eq", "ne", "gt", "gte", "lt", "lte", "in", "contains", "matches")


class Predicate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    op: Op
    value: Any = None

    @model_validator(mode="after")
    def _check_value(self) -> Predicate:
        if self.op in _NEEDS_VALUE and self.value is None:
            raise ValueError(f"el operador '{self.op}' necesita 'value'")
        if self.op == "in" and not isinstance(self.value, list):
            raise ValueError("el operador 'in' necesita una lista en 'value'")
        if self.op == "matches" and not isinstance(self.value, str):
            raise ValueError("el operador 'matches' necesita una expresión regular (texto)")
        return self


class AllOf(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    all_: list[Condition] = Field(alias="all", min_length=1)


class AnyOf(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    any_: list[Condition] = Field(alias="any", min_length=1)


class NotOf(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    not_: Condition = Field(alias="not")


Condition = Union[Predicate, AllOf, AnyOf, NotOf]  # noqa: UP007 (necesario para la recursión)

for _model in (AllOf, AnyOf, NotOf):
    _model.model_rebuild()


def iter_predicates(cond: Condition) -> Iterator[Predicate]:
    if isinstance(cond, Predicate):
        yield cond
    elif isinstance(cond, AllOf):
        for item in cond.all_:
            yield from iter_predicates(item)
    elif isinstance(cond, AnyOf):
        for item in cond.any_:
            yield from iter_predicates(item)
    elif isinstance(cond, NotOf):
        yield from iter_predicates(cond.not_)


def evaluate(cond: Condition, context: dict[str, Any], captures: dict[str, str] | None = None) -> bool:
    """Evalúa una condición sobre el contexto.

    Si `captures` se pasa, se rellena con los grupos con nombre del primer `matches` que acierta.
    """
    if isinstance(cond, Predicate):
        return _eval_predicate(cond, context, captures)
    if isinstance(cond, AllOf):
        return all(evaluate(item, context, captures) for item in cond.all_)
    if isinstance(cond, AnyOf):
        return any(evaluate(item, context, captures) for item in cond.any_)
    return not evaluate(cond.not_, context, None)


def _eval_predicate(pred: Predicate, context: dict[str, Any], captures: dict[str, str] | None) -> bool:
    found, actual = get_path(context, pred.path)
    if pred.op == "exists":
        return found
    if not found:
        return pred.op == "ne"
    expected = pred.value
    try:
        if pred.op == "eq":
            return actual == expected
        if pred.op == "ne":
            return actual != expected
        if pred.op == "gt":
            return actual > expected
        if pred.op == "gte":
            return actual >= expected
        if pred.op == "lt":
            return actual < expected
        if pred.op == "lte":
            return actual <= expected
        if pred.op == "in":
            return actual in expected
        if pred.op == "contains":
            return expected in actual
        if pred.op == "matches":
            match = re.search(expected, str(actual))
            if match and captures is not None:
                captures.update({k: v for k, v in match.groupdict().items() if v is not None})
            return match is not None
    except (TypeError, re.error):
        return False
    return False
