"""Contrato de los tipos de nodo (ver DESIGN.md sección 5).

Cada tipo declara en código sus metadatos; de ahí se generan el catálogo para la IA y las
comprobaciones del validador. `side_effect` lo decide el código del nodo, nunca el plan.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

if TYPE_CHECKING:
    from gestplan.plans.context import ValidationContext
    from gestplan.plans.issues import Issue
    from gestplan.plans.models import NodeSpec

SideEffect = Literal["none", "local", "external"]


class EventSpec(BaseModel):
    name: str
    description: str
    failure: bool = False


def event(name: str, description: str, failure: bool = False) -> EventSpec:
    return EventSpec(name=name, description=description, failure=failure)


class EmptyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NodeType:
    type: ClassVar[str]
    version: ClassVar[int] = 1
    family: ClassVar[str]
    description: ClassVar[str]
    side_effect: ClassVar[SideEffect] = "none"
    events: ClassVar[tuple[EventSpec, ...]] = ()
    ConfigModel: ClassVar[type[BaseModel]] = EmptyConfig
    OutputsModel: ClassVar[type[BaseModel]] = EmptyConfig
    example: ClassVar[dict[str, Any]] = {}
    default_timeout_s: ClassVar[int | None] = None
    idle_timeout_s: ClassVar[int | None] = None
    # True si las salidas no están fijadas por el tipo (p. ej. scripts): no se comprueban
    dynamic_outputs: ClassVar[bool] = False
    # Evento que devuelve el trabajo a un nodo anterior y cuyo número de rondas acota el bucle
    loop_event: ClassVar[str | None] = None

    @classmethod
    def parse_config(cls, config: dict[str, Any]) -> BaseModel | None:
        try:
            return cls.ConfigModel.model_validate(config)
        except ValidationError:
            return None

    @classmethod
    def event_specs(
        cls, config: dict[str, Any], ctx: ValidationContext | None = None
    ) -> list[EventSpec]:
        return list(cls.events)

    @classmethod
    def effective_side_effect(
        cls, config: dict[str, Any], ctx: ValidationContext | None = None
    ) -> SideEffect:
        return cls.side_effect

    @classmethod
    def loop_bound(cls, config: dict[str, Any]) -> int | None:
        """Nº máximo de ejecuciones que este nodo impone al nodo al que devuelve el trabajo."""
        return None

    @classmethod
    def check(cls, node_id: str, spec: NodeSpec, ctx: ValidationContext) -> list[Issue]:
        """Comprobaciones propias del tipo (ficheros, topes, referencias)."""
        return []
