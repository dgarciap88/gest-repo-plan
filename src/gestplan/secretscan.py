"""Primera pasada determinista del escaneo de secretos (NICE_TO_HAVE 1.2): patrones, entropía y nombres de fichero."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path, PurePath

ALLOW_MARKER = "gestplan:allow-secret"
MAX_LINE = 2000

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("clave privada", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("token de GitHub", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}\b")),
    ("clave de Anthropic", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("token de Slack", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("credenciales en URL", re.compile(r"://[^/\s:@]+:[^/\s:@]{6,}@[A-Za-z0-9.\-]+")),
    ("asignación de secreto", re.compile(
        r"""(?i)\b[\w.-]*(?:password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|token)\b"""
        r"""["']?\s*[:=]\s*["']([^"'\s]{8,})["']""")),
)
_QUOTED = re.compile(r"""["']([A-Za-z0-9+/=_\-]{32,})["']""")
_SUSPICIOUS_NAMES = (".env", ".pem", ".pfx", ".p12", ".key", ".jks", ".keystore")
_SAFE_NAMES = (".env.example", ".env.sample", ".env.template")
_PLACEHOLDER = re.compile(
    r"(?i)^(?:x+|\*+|<.*>|\$\{.*\}|changeme|example|dummy|placeholder|your[_-].*|test.*|none|null)$")


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    kind: str
    sample: str  # el valor ya enmascarado: el secreto en claro nunca sale de aquí

    def as_dict(self) -> dict[str, object]:
        return {"file": self.file, "line": self.line, "kind": self.kind, "sample": self.sample}


def mask(value: str) -> str:
    return value[:4] + "***" if len(value) > 8 else "***"


def entropy(value: str) -> float:
    counts = {c: value.count(c) for c in set(value)}
    return -sum(n / len(value) * math.log2(n / len(value)) for n in counts.values())


def suspicious_filename(path: str) -> str | None:
    name = PurePath(path).name.lower()
    if name in _SAFE_NAMES:
        return None
    if name == ".env" or name.startswith(".env.") or name in ("id_rsa", "id_ed25519") \
            or name.endswith(_SUSPICIOUS_NAMES[1:]):
        return "fichero que suele contener secretos"
    return None


def scan_line(path: str, number: int, line: str) -> list[Finding]:
    if ALLOW_MARKER in line or len(line) > MAX_LINE:
        return []
    found: list[Finding] = []
    for kind, pattern in _PATTERNS:
        match = pattern.search(line)
        if not match:
            continue
        value = match.group(1) if pattern.groups else match.group(0)
        if pattern.groups and _PLACEHOLDER.match(value):
            continue
        found.append(Finding(path, number, kind, mask(value)))
    if not found:
        for match in _QUOTED.finditer(line):
            value = match.group(1)
            if len(set(value)) > 12 and entropy(value) > 4.3 and not value.isalpha():
                found.append(Finding(path, number, "cadena de alta entropía", mask(value)))
                break
    return found


def scan_diff(diff: str) -> list[Finding]:
    """Analiza solo las líneas añadidas de un diff unificado."""
    findings: list[Finding] = []
    path, number = "", 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            path = raw[4:].removeprefix("b/")
        elif raw.startswith("@@"):
            match = re.search(r"\+(\d+)", raw)
            number = int(match.group(1)) - 1 if match else 0
        elif raw.startswith("+") and not raw.startswith("+++"):
            number += 1
            findings.extend(scan_line(path, number, raw[1:]))
        elif not raw.startswith(("-", "\\")):
            number += 1
    return findings


def scan_text(path: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), 1):
        findings.extend(scan_line(path, number, line))
    return findings


_BASE64_LINE = re.compile(r"^\s*[A-Za-z0-9+/=]{40,}\s*$")


def mask_line(line: str) -> str:
    """Sustituye por su versión enmascarada todo lo que parezca un secreto (para enseñarlo a una IA)."""
    if _BASE64_LINE.match(line):  # cuerpo de una clave privada u otro blob
        return "***"
    for _kind, pattern in _PATTERNS:

        def replace(match: re.Match[str], pattern: re.Pattern[str] = pattern) -> str:
            value = match.group(1) if pattern.groups else match.group(0)
            if pattern.groups and _PLACEHOLDER.match(value):
                return match.group(0)
            return match.group(0).replace(value, mask(value))

        line = pattern.sub(replace, line)

    def replace_quoted(match: re.Match[str]) -> str:
        value = match.group(1)
        if len(set(value)) > 12 and entropy(value) > 4.3 and not value.isalpha():
            return match.group(0).replace(value, mask(value))
        return match.group(0)

    return _QUOTED.sub(replace_quoted, line)[:300]


def masked_context(workspace: Path, file: str, line: int, radius: int = 2) -> str:
    """Unas pocas líneas alrededor de un hallazgo, ya enmascaradas. Nunca devuelve el fichero entero."""
    if line <= 0:
        return "(hallazgo sobre el nombre del fichero: no se envía su contenido)"
    root = workspace.resolve()
    path = (root / file).resolve()
    if root not in path.parents:
        return "(ruta fuera del workspace: no se envía contenido)"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return "(fichero no legible o binario: no se envía contenido)"
    first, last = max(1, line - radius), min(len(lines), line + radius)
    return "\n".join(f"{number}{'>' if number == line else ' '} {mask_line(lines[number - 1])}"
                     for number in range(first, last + 1))
