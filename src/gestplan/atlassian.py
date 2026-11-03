"""Conversores de Atlassian: Markdown básico → ADF, ADF → texto y bloques → storage format de Confluence."""

from __future__ import annotations

import base64
import re
from html import escape
from typing import Any

_INLINE = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`)")
_BULLET = re.compile(r"^\s*[-*]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def basic_auth(email: str, token: str) -> str:
    return "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()


# ------------------------------------------------------------------------------- Markdown → ADF --
def _inline(text: str) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for part in _INLINE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            nodes.append({"type": "text", "text": part[2:-2], "marks": [{"type": "strong"}]})
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            nodes.append({"type": "text", "text": part[1:-1], "marks": [{"type": "code"}]})
        else:
            nodes.append({"type": "text", "text": part})
    return nodes


def _paragraph(lines: list[str]) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        if index:
            content.append({"type": "hardBreak"})
        content.extend(_inline(line))
    return {"type": "paragraph", "content": content}


def markdown_to_adf(text: str, marker: str | None = None) -> dict[str, Any]:
    """Subconjunto de Markdown: párrafos, # títulos, listas con - o 1., bloques ``` y **negrita**/`código`."""
    blocks: list[dict[str, Any]] = []
    lines = text.replace("\r\n", "\n").split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
        elif line.strip().startswith("```"):
            language = line.strip()[3:].strip()
            code: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code.append(lines[index])
                index += 1
            index += 1
            node: dict[str, Any] = {"type": "codeBlock", "content": [{"type": "text", "text": "\n".join(code) or " "}]}
            if language:
                node["attrs"] = {"language": language}
            blocks.append(node)
        elif _HEADING.match(line):
            match = _HEADING.match(line)
            blocks.append({"type": "heading", "attrs": {"level": len(match.group(1))},
                           "content": _inline(match.group(2))})
            index += 1
        elif _BULLET.match(line) or _NUMBERED.match(line):
            ordered = bool(_NUMBERED.match(line))
            pattern = _NUMBERED if ordered else _BULLET
            items = []
            while index < len(lines) and pattern.match(lines[index]):
                items.append({"type": "listItem", "content": [
                    {"type": "paragraph", "content": _inline(pattern.match(lines[index]).group(1))}]})
                index += 1
            blocks.append({"type": "orderedList" if ordered else "bulletList", "content": items})
        else:
            paragraph = []
            while index < len(lines) and lines[index].strip() and not lines[index].strip().startswith(("```", "#")) \
                    and not _BULLET.match(lines[index]) and not _NUMBERED.match(lines[index]):
                paragraph.append(lines[index])
                index += 1
            blocks.append(_paragraph(paragraph))
    if marker:
        blocks.append({"type": "paragraph", "content": [{"type": "text", "text": marker, "marks": [{"type": "code"}]}]})
    return {"version": 1, "type": "doc", "content": blocks or [{"type": "paragraph", "content": []}]}


# ------------------------------------------------------------------------------- ADF → texto ----
def adf_to_text(node: Any) -> str:
    """Texto plano de un documento ADF (None o texto simple se devuelven tal cual)."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    return _walk(node).strip()


def _walk(node: dict[str, Any], prefix: str = "") -> str:
    kind = node.get("type")
    content = node.get("content") or []
    if kind == "text":
        return node.get("text", "")
    if kind == "hardBreak":
        return "\n"
    if kind in ("mention", "emoji", "inlineCard", "status"):
        attrs = node.get("attrs", {})
        return str(attrs.get("text") or attrs.get("url") or attrs.get("shortName") or "")
    if kind == "heading":
        return "#" * int(node.get("attrs", {}).get("level", 1)) + " " + "".join(_walk(c) for c in content) + "\n\n"
    if kind in ("bulletList", "orderedList"):
        out = []
        for number, item in enumerate(content, 1):
            bullet = f"{number}. " if kind == "orderedList" else "- "
            out.append(bullet + "".join(_walk(c) for c in item.get("content", [])).strip())
        return "\n".join(out) + "\n\n"
    if kind == "codeBlock":
        return "```\n" + "".join(_walk(c) for c in content) + "\n```\n\n"
    if kind == "table":
        rows = []
        for row in content:
            rows.append(" | ".join("".join(_walk(c) for c in cell.get("content", [])).strip()
                                   for cell in row.get("content", [])))
        return "\n".join(rows) + "\n\n"
    inner = "".join(_walk(c) for c in content)
    return inner + "\n\n" if kind in ("paragraph", "blockquote", "panel") else inner


# ------------------------------------------------------------------ bloques → storage format ----
def _cell(value: Any) -> str:
    return escape(str(value))


def render_storage(blocks: list[dict[str, Any]]) -> str:
    """Renderiza los bloques de la plantilla a XHTML de Confluence (con escape de todo el texto)."""
    out: list[str] = []
    for block in blocks:
        kind = block["type"]
        if kind == "heading":
            level = min(max(int(block.get("level", 2)), 1), 6)
            out.append(f"<h{level}>{escape(block['text'])}</h{level}>")
        elif kind == "paragraph":
            out.append("<p>" + "<br/>".join(escape(line) for line in block["text"].split("\n")) + "</p>")
        elif kind == "list":
            tag = "ol" if block.get("ordered") else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{escape(str(i))}</li>" for i in block["items"]) + f"</{tag}>")
        elif kind == "table":
            head = "".join(f"<th>{escape(str(c))}</th>" for c in block["columns"])
            rows = "".join("<tr>" + "".join(f"<td>{_cell(c)}</td>" for c in row) + "</tr>" for row in block["rows"])
            out.append(f"<table><tbody><tr>{head}</tr>{rows}</tbody></table>")
        elif kind == "code":
            language = block.get("language", "")
            param = f'<ac:parameter ac:name="language">{escape(language)}</ac:parameter>' if language else ""
            body = block["text"].replace("]]>", "]]]]><![CDATA[>")
            out.append(f'<ac:structured-macro ac:name="code">{param}'
                       f"<ac:plain-text-body><![CDATA[{body}]]></ac:plain-text-body></ac:structured-macro>")
    return "".join(out)
