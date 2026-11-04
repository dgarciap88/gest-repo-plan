"""Conversores de Atlassian: Markdown básico → ADF, ADF → texto y bloques → storage format de Confluence."""

from __future__ import annotations

import base64
import re
from html import escape
from html.parser import HTMLParser
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


def _param(value: Any) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


def _render_macro(block: dict[str, Any]) -> str:
    params = "".join(
        f'<ac:parameter ac:name="{escape(str(k), quote=True)}">{escape(_param(v))}</ac:parameter>'
        for k, v in (block.get("params") or {}).items()
    )
    body = ""
    if block.get("text"):
        cdata = block["text"].replace("]]>", "]]]]><![CDATA[>")
        body = f"<ac:plain-text-body><![CDATA[{cdata}]]></ac:plain-text-body>"
    elif block.get("body"):
        body = f"<ac:rich-text-body>{render_storage(block['body'])}</ac:rich-text-body>"
    name = escape(block["name"], quote=True)
    return f'<ac:structured-macro ac:name="{name}" ac:schema-version="1">{params}{body}</ac:structured-macro>'


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
        elif kind == "raw":
            out.append(block["storage"])
        elif kind == "macro":
            out.append(_render_macro(block))
        elif kind == "code":
            language = block.get("language", "")
            param = f'<ac:parameter ac:name="language">{escape(language)}</ac:parameter>' if language else ""
            body = block["text"].replace("]]>", "]]]]><![CDATA[>")
            out.append(f'<ac:structured-macro ac:name="code">{param}'
                       f"<ac:plain-text-body><![CDATA[{body}]]></ac:plain-text-body></ac:structured-macro>")
    return "".join(out)


# ------------------------------------------------------------------- storage format → bloques --
class _El:
    def __init__(self, tag: str, attrs: dict[str, str]) -> None:
        self.tag, self.attrs = tag, attrs
        self.children: list[_El | str] = []


class _Tree(HTMLParser):
    _VOID = {"br", "hr", "img", "col", "ri:attachment", "ri:page", "ri:url", "ri:user"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _El("root", {})
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element = _El(tag, {k: v or "" for k, v in attrs})
        self.stack[-1].children.append(element)
        if tag not in self._VOID:
            self.stack.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.stack[-1].children.append(_El(tag, {k: v or "" for k, v in attrs}))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)

    def unknown_decl(self, data: str) -> None:  # <![CDATA[...]]>
        if data.startswith("CDATA["):
            self.stack[-1].children.append(data[6:])


_INLINE_TAGS = {"strong", "b", "em", "i", "u", "s", "del", "code", "a", "span", "sub", "sup", "time", "ac:link"}
_MARKER = re.compile(r"^gestplan run:\S+ node:\S+$")


def _text(element: _El | str, unsupported: set[str]) -> str:
    if isinstance(element, str):
        return element
    if element.tag == "br":
        return "\n"
    if element.tag in {"ac:structured-macro", "ac:image", "ac:link", "ac:emoticon", "ac:task-list"}:
        unsupported.add(f"elemento «{element.attrs.get('ac:name') or element.tag}» dentro de un texto")
    elif element.tag in _INLINE_TAGS - {"span"}:
        unsupported.add(f"formato en línea «{element.tag}» (se conserva el texto, no el formato)")
    return "".join(_text(child, unsupported) for child in element.children)


def _clean(text: str) -> str:
    return re.sub(r"[ \t\r\f\v]+", " ", text.replace("\xa0", " ")).strip()


def _lines(text: str) -> str:
    return "\n".join(_clean(line) for line in text.split("\n")).strip()


def _flat_items(element: _El, unsupported: set[str]) -> list[str]:
    items: list[str] = []
    for child in element.children:
        if isinstance(child, _El) and child.tag == "li":
            own = [c for c in child.children if not (isinstance(c, _El) and c.tag in {"ul", "ol"})]
            items.append(_clean("".join(_text(c, unsupported) for c in own)))
            for nested in child.children:
                if isinstance(nested, _El) and nested.tag in {"ul", "ol"}:
                    unsupported.add("listas anidadas (se aplanan en una sola lista)")
                    items.extend(_flat_items(nested, unsupported))
    return items


def _rows(table: _El) -> list[list[_El]]:
    rows: list[list[_El]] = []

    def walk(node: _El) -> None:
        for child in node.children:
            if isinstance(child, _El):
                if child.tag == "tr":
                    rows.append([c for c in child.children if isinstance(c, _El) and c.tag in {"th", "td"}])
                elif child.tag in {"tbody", "thead", "tfoot"}:
                    walk(child)

    walk(table)
    return rows


def _serialize(node: _El | str, parent: str = "") -> str:
    if isinstance(node, str):
        if parent == "ac:plain-text-body":
            return "<![CDATA[" + node.replace("]]>", "]]]]><![CDATA[>") + "]]>"
        return escape(node)
    attrs = "".join(f' {k}="{escape(v, quote=True)}"' for k, v in node.attrs.items())
    if not node.children and node.tag in _Tree._VOID:
        return f"<{node.tag}{attrs}/>"
    inner = "".join(_serialize(c, node.tag) for c in node.children)
    return f"<{node.tag}{attrs}>{inner}</{node.tag}>"


def _child(element: _El, tag: str) -> _El | None:
    return next((c for c in element.children if isinstance(c, _El) and c.tag == tag), None)


def _descendants(element: _El) -> list[_El | str]:
    found: list[_El | str] = []
    for child in element.children:
        found.append(child)
        if isinstance(child, _El):
            found.extend(_descendants(child))
    return found


def _has_confluence_elements(element: _El) -> bool:
    return any(isinstance(c, _El) and c.tag.startswith(("ac:", "ri:")) for c in _descendants(element))


def _macro_block(node: _El) -> dict[str, Any]:
    """Macro → bloque `macro`; si no se puede representar sin perder nada, se conserva tal cual (`raw`)."""
    raw = {"type": "raw", "storage": _serialize(node)}
    params: dict[str, str] = {}
    for child in node.children:
        if isinstance(child, _El) and child.tag == "ac:parameter":
            if any(isinstance(c, _El) for c in child.children):
                return raw  # parámetro con enlaces u otros elementos
            params[child.attrs.get("ac:name", "")] = "".join(c for c in child.children if isinstance(c, str)).strip()
    others = [c for c in node.children if isinstance(c, _El)
              and c.tag not in {"ac:parameter", "ac:plain-text-body", "ac:rich-text-body"}]
    if others:
        return raw
    block: dict[str, Any] = {"type": "macro", "name": node.attrs.get("ac:name", ""), "params": params}
    plain, rich = _child(node, "ac:plain-text-body"), _child(node, "ac:rich-text-body")
    if plain is not None:
        block["text"] = "".join(c for c in plain.children if isinstance(c, str))
    elif rich is not None:
        inner: set[str] = set()
        body = _convert(rich.children, inner)
        if inner:
            return raw  # el cuerpo tiene algo que los bloques aplanarían
        block["body"] = body
    return block


def _convert(nodes: list[_El | str], unsupported: set[str]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for node in nodes:
        if isinstance(node, str):
            if node.strip():
                blocks.append({"type": "paragraph", "text": _lines(node)})
            continue
        tag = node.tag
        if re.fullmatch(r"h[1-6]", tag):
            blocks.append({"type": "heading", "level": int(tag[1]), "text": _clean(_text(node, unsupported))})
        elif tag == "p":
            found: set[str] = set()
            text = _lines(_text(node, found))
            if text and _MARKER.match(text):
                continue  # el marcador de idempotencia no forma parte del contenido
            if _has_confluence_elements(node):
                blocks.append({"type": "raw", "storage": _serialize(node)})  # macro, imagen o enlace en línea
            elif text:
                unsupported |= found
                blocks.append({"type": "paragraph", "text": text})
        elif tag in {"ul", "ol"}:
            blocks.append({"type": "list", "ordered": tag == "ol", "items": _flat_items(node, unsupported)})
        elif tag == "table":
            rows = _rows(node)
            if not rows or not rows[0]:
                continue
            if any(c.attrs.get("colspan", "1") != "1" or c.attrs.get("rowspan", "1") != "1" for r in rows for c in r):
                unsupported.add("celdas combinadas en una tabla")
            first_is_header = all(c.tag == "th" for c in rows[0])
            head = [f"col{i + 1}" for i in range(len(rows[0]))]
            if first_is_header:
                head = [_clean(_text(c, unsupported)) for c in rows[0]]
            else:
                unsupported.add("tabla sin fila de cabecera (se inventan columnas col1, col2...)")
            body = rows[1:] if first_is_header else rows
            blocks.append({"type": "table", "columns": head,
                           "rows": [[_clean(_text(c, unsupported)) for c in r] for r in body]})
        elif tag == "ac:structured-macro" and node.attrs.get("ac:name") == "code":
            language, code = "", ""
            for child in node.children:
                if isinstance(child, _El) and child.tag == "ac:parameter" and child.attrs.get("ac:name") == "language":
                    language = _clean(_text(child, unsupported))
                elif isinstance(child, _El) and child.tag == "ac:plain-text-body":
                    code = "".join(c for c in child.children if isinstance(c, str))
            blocks.append({"type": "code", "language": language, "text": code})
        elif tag == "ac:structured-macro":
            blocks.append(_macro_block(node))
        else:
            blocks.append({"type": "raw", "storage": _serialize(node)})  # cualquier otro elemento se conserva tal cual
    return blocks


def storage_to_blocks(storage: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Convierte el storage format de una página a bloques de plantilla.

    Devuelve (bloques, avisos). Las macros pasan a bloques `macro` y todo lo demás sin equivalente exacto
    (imágenes, enlaces, estructuras raras) se conserva tal cual como `raw`, así que se puede reescribir sin
    perderlo. Solo se pierde, y se avisa, el formato en línea de los párrafos (negrita, enlaces), las listas
    anidadas (se aplanan) y las celdas combinadas.
    """
    parser = _Tree()
    parser.feed(storage)
    parser.close()
    unsupported: set[str] = set()
    return _convert(parser.root.children, unsupported), sorted(unsupported)


def blocks_to_markdown(blocks: list[dict[str, Any]]) -> str:
    """Vista de lectura de los bloques (también sirve de texto para un prompt)."""
    lines: list[str] = []
    for block in blocks:
        kind = block["type"]
        if kind == "heading":
            lines.append("#" * block.get("level", 2) + " " + block["text"])
        elif kind == "paragraph":
            lines.append(block["text"])
        elif kind == "list":
            ordered = block.get("ordered")
            lines.extend(f"{n + 1}. {item}" if ordered else f"- {item}" for n, item in enumerate(block["items"]))
        elif kind == "table":
            lines.append("| " + " | ".join(block["columns"]) + " |")
            lines.append("|" + " --- |" * len(block["columns"]))
            lines.extend("| " + " | ".join(str(c) for c in row) + " |" for row in block["rows"])
        elif kind == "code":
            lines.append(f"```{block.get('language', '')}\n{block['text']}\n```")
        elif kind == "macro":
            params = " ".join(f"{k}={v}" for k, v in (block.get("params") or {}).items())
            lines.append(f"[macro {block['name']}{' ' + params if params else ''}]")
            if block.get("text"):
                lines.append(block["text"])
            if block.get("body"):
                lines.append(blocks_to_markdown(block["body"]))
            lines.append(f"[/macro {block['name']}]")
        elif kind == "raw":
            lines.append(f"[contenido de Confluence conservado tal cual: {block['storage'][:80]}…]")
        lines.append("")
    return "\n".join(lines).strip()
