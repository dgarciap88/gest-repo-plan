"""Problemas detectados al cargar o validar un plan."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    severity: Literal["error", "warning"] = "error"
    node: str | None = None
    path: str | None = None
    hint: str | None = None

    def format(self) -> str:
        label = "ERROR" if self.severity == "error" else "AVISO"
        where = " ".join(part for part in (self.node, self.path) if part)
        head = f"{label} [{self.code}]" + (f" {where}:" if where else ":")
        text = f"{head} {self.message}"
        return text + (f"\n    -> {self.hint}" if self.hint else "")


def error(code: str, message: str, **kwargs: str | None) -> Issue:
    return Issue(code=code, message=message, severity="error", **kwargs)  # type: ignore[arg-type]


def warning(code: str, message: str, **kwargs: str | None) -> Issue:
    return Issue(code=code, message=message, severity="warning", **kwargs)  # type: ignore[arg-type]


@dataclass
class ValidationResult:
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {"ok": self.ok, "issues": [asdict(i) for i in self.issues]}
