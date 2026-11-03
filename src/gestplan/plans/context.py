"""Contexto compartido durante la validación de un plan."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from gestplan.plans.graph import PlanGraph
from gestplan.plans.models import Plan
from gestplan.settings import Settings


@dataclass
class ValidationContext:
    plan: Plan
    root: Path
    settings: Settings
    graph: PlanGraph = field(init=False)
    used_inputs: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.graph = PlanGraph(self.plan)
