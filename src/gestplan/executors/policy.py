"""Política de mensajes de commit y PR (ver DESIGN.md 6.1, "Mensajes de commit y PR")."""

from __future__ import annotations

import re

GENERIC_SUBJECTS = frozenset(
    {"fix", "fixes", "update", "updates", "changes", "change", "wip", "misc", "test", "tests",
     "cambios", "cambio", "arreglo", "actualizar", "actualizacion", "actualización", "mejoras"}
)
_TRAILER_RE = re.compile(r"(?im)^\s*co-authored-by:.*(?:\r?\n|$)")
_TICKET_PREFIX_RE = re.compile(r"^\s*\[?[A-Z][A-Z0-9]*-\d+\]?\s*[:\-]?\s*")


def strip_ai_trailers(text: str) -> str:
    """Elimina trailers de coautoría (el sistema nunca los añade)."""
    return _TRAILER_RE.sub("", text).rstrip()


def check_commit_message(subject: str, body: str = "") -> list[str]:
    problems: list[str] = []
    text = subject.strip()
    if not text:
        return ["el asunto está vacío"]
    if "\n" in text:
        problems.append("el asunto debe ser una sola línea")
    if len(text) > 72:
        problems.append(f"el asunto tiene {len(text)} caracteres (máximo 72)")
    descriptive = _TICKET_PREFIX_RE.sub("", text).strip().rstrip(".").lower()
    if len(descriptive) < 10:
        problems.append("el asunto es demasiado corto para ser descriptivo (mínimo 10 caracteres)")
    if descriptive in GENERIC_SUBJECTS:
        problems.append(f"el asunto '{text}' es genérico")
    if text.endswith("."):
        problems.append("el asunto no debe terminar en punto")
    return problems


def check_pr_message(title: str, body: str) -> list[str]:
    problems: list[str] = []
    if len(_TICKET_PREFIX_RE.sub("", title).strip()) < 10:
        problems.append("el título del PR es demasiado corto para ser descriptivo")
    if len(body.strip()) < 20:
        problems.append("el cuerpo del PR es demasiado corto")
    return problems
