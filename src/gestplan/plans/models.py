"""Modelos del plan (JSON). Son estrictos: cualquier campo desconocido es un error."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

NODE_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
PLAN_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
EDGE_SOURCE_RE = re.compile(r"^([a-z][a-z0-9_]*)\.([a-z][a-z0-9_]*)$")

InputType = Literal["string", "integer", "number", "boolean", "array", "object"]


class InputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: InputType = "string"
    required: bool = False
    default: Any = None
    description: str = ""
    pattern: str | None = None


class Edge(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(alias="from")
    to: str

    @field_validator("from_")
    @classmethod
    def _check_source(cls, value: str) -> str:
        if not EDGE_SOURCE_RE.match(value):
            raise ValueError("debe tener el formato 'nodo.evento' (minúsculas, dígitos y '_')")
        return value

    @property
    def source_node(self) -> str:
        return self.from_.split(".", 1)[0]

    @property
    def source_event(self) -> str:
        return self.from_.split(".", 1)[1]


class NodeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    version: int = Field(default=1, ge=1)
    description: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    join: Literal["all", "any"] = "all"
    max_attempts: int | None = Field(default=None, ge=1)
    timeout_s: int | None = Field(default=None, ge=1)


class PlanSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_agents_parallel: int = Field(default=2, ge=1)
    on_unhandled_failure: Literal["fail_run", "skip_dependents"] = "fail_run"
    language: Literal["es", "en"] = "es"
    run_timeout_s: int | None = Field(default=None, ge=1)
    max_cost_usd: float | None = Field(default=None, gt=0, description="Presupuesto de IA de este run (y sus hijos)")
    display_inputs: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    name: str
    description: str = ""
    inputs: dict[str, InputSpec] = Field(default_factory=dict)
    settings: PlanSettings = Field(default_factory=PlanSettings)
    nodes: dict[str, NodeSpec] = Field(min_length=1)
    edges: list[Edge] = Field(default_factory=list)
    layout: dict[str, dict[str, float]] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        if not PLAN_NAME_RE.match(value):
            raise ValueError("el nombre solo admite minúsculas, dígitos, '-' y '_'")
        return value

    @field_validator("nodes")
    @classmethod
    def _check_node_ids(cls, value: dict[str, NodeSpec]) -> dict[str, NodeSpec]:
        for node_id in value:
            if not NODE_ID_RE.match(node_id):
                raise ValueError(
                    f"id de nodo '{node_id}' no válido: minúsculas, dígitos y '_', empezando por letra"
                )
        return value

    @field_validator("inputs")
    @classmethod
    def _check_input_names(cls, value: dict[str, InputSpec]) -> dict[str, InputSpec]:
        for name in value:
            if not NODE_ID_RE.match(name):
                raise ValueError(f"nombre de input '{name}' no válido")
        return value
