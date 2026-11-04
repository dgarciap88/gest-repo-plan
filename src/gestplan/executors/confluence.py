"""Ejecutor de Confluence Cloud (API REST de contenido)."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from gestplan.atlassian import basic_auth, blocks_to_markdown, render_storage, storage_to_blocks
from gestplan.executors.base import Artifact, ExecContext, NodeExecutor, Outcome


class ConfluenceCreatePageExec(NodeExecutor):
    type = "confluence.create_page"

    @staticmethod
    def _marker(ctx: ExecContext) -> str:
        return f"gestplan run:{ctx.run_id} node:{ctx.node_id}"

    def _body(self, ctx: ExecContext) -> str:
        blocks = ctx.config["blocks"]
        if not isinstance(blocks, list):
            raise ValueError("'blocks' debe resolverse a una lista de bloques")
        return render_storage(blocks) + f"<p><code>{self._marker(ctx)}</code></p>"

    async def preview(self, ctx: ExecContext) -> dict[str, Any] | None:
        blocks = ctx.config["blocks"]
        text = blocks_to_markdown(blocks if isinstance(blocks, list) else [])
        return {"kind": "markdown", "title": f"Página «{ctx.config['title']}» en {ctx.config['space']}", "text": text}

    async def run(self, ctx: ExecContext) -> Outcome:
        cfg = ctx.settings
        if not (cfg.confluence_base_url and cfg.confluence_email and cfg.confluence_token):
            return Outcome("failed", error="faltan CONFLUENCE_BASE_URL, el correo o el token en el .env")
        try:
            storage = self._body(ctx)
        except (ValueError, KeyError, TypeError) as exc:
            return Outcome("failed", error=f"bloques no válidos: {exc}")
        headers = {"Authorization": basic_auth(cfg.confluence_email, cfg.confluence_token)}
        base = f"{cfg.confluence_base_url}/rest/api/content"
        space, title = ctx.config["space"], ctx.config["title"]
        timeout = min(ctx.engine.node_timeout(ctx.run, ctx.node_id) or 60, 60)

        found = await ctx.http("GET", f"{base}?spaceKey={quote(space)}&title={quote(title)}&expand=body.storage",
                               headers=headers, timeout_s=timeout)
        if not found.ok:
            return Outcome("failed", error=f"Confluence respondió {found.status}: {found.text[:300]}")
        for page in found.json().get("results", []):
            body = ((page.get("body") or {}).get("storage") or {}).get("value", "")
            outputs = {"page_id": page["id"], "title": title, "url": self._url(cfg.confluence_base_url, page)}
            if self._marker(ctx) in body:
                return Outcome("already_done", outputs, artifacts=[Artifact("page", title, outputs["url"], page["id"])])
            return Outcome("title_exists", outputs, error=f"ya existe una página '{title}' en el espacio {space}")

        payload: dict[str, Any] = {"type": "page", "title": title, "space": {"key": space},
                                   "body": {"storage": {"value": storage, "representation": "storage"}}}
        if ctx.config.get("parent_id"):
            payload["ancestors"] = [{"id": str(ctx.config["parent_id"])}]
        created = await ctx.http("POST", base, headers=headers, json_body=payload, timeout_s=timeout)
        if not created.ok:
            return Outcome("failed", error=f"Confluence respondió {created.status}: {created.text[:300]}")
        page = created.json()
        url = self._url(cfg.confluence_base_url, page)
        return Outcome("ok", {"page_id": page["id"], "title": title, "url": url},
                       artifacts=[Artifact("page", title, url, page["id"])])

    @staticmethod
    def _url(base: str, page: dict[str, Any]) -> str:
        links = page.get("_links") or {}
        if links.get("webui"):
            return f"{links.get('base') or base}{links['webui']}"
        return f"{base}/pages/viewpage.action?pageId={page['id']}"


class ConfluenceUpdatePageExec(ConfluenceCreatePageExec):
    type = "confluence.update_page"

    async def preview(self, ctx: ExecContext) -> dict[str, Any] | None:
        shown = await super().preview(ctx) or {"kind": "none", "message": "sin vista previa"}
        return {**shown, "title": f"Reemplaza el contenido de la página {ctx.config['page_id']}"}

    async def run(self, ctx: ExecContext) -> Outcome:
        cfg = ctx.settings
        if not (cfg.confluence_base_url and cfg.confluence_email and cfg.confluence_token):
            return Outcome("failed", error="faltan CONFLUENCE_BASE_URL, el correo o el token en el .env")
        try:
            storage = self._body(ctx)
        except (ValueError, KeyError, TypeError) as exc:
            return Outcome("failed", error=f"bloques no válidos: {exc}")
        headers = {"Authorization": basic_auth(cfg.confluence_email, cfg.confluence_token)}
        url = f"{cfg.confluence_base_url}/rest/api/content/{quote(str(ctx.config['page_id']))}"
        timeout = min(ctx.engine.node_timeout(ctx.run, ctx.node_id) or 60, 60)

        current = await ctx.http("GET", f"{url}?expand=body.storage,version", headers=headers, timeout_s=timeout)
        if current.status == 404:
            return Outcome("not_found", error=f"no existe la página {ctx.config['page_id']}")
        if not current.ok:
            return Outcome("failed", error=f"Confluence respondió {current.status}: {current.text[:300]}")
        page = current.json()
        title = ctx.config.get("title") or page["title"]
        version = int(page["version"]["number"])
        done = {"page_id": str(page["id"]), "title": page["title"], "version": version,
                "url": self._url(cfg.confluence_base_url, page)}
        if self._marker(ctx) in ((page.get("body") or {}).get("storage") or {}).get("value", ""):
            return Outcome("already_done", done,
                           artifacts=[Artifact("page", f"{done['title']} (actualizada)", done["url"], done["page_id"])])

        payload = {"id": page["id"], "type": "page", "title": title,
                   "version": {"number": version + 1, "message": f"gestplan {ctx.run_id}"},
                   "body": {"storage": {"value": storage, "representation": "storage"}}}
        updated = await ctx.http("PUT", url, headers=headers, json_body=payload, timeout_s=timeout)
        if not updated.ok:
            return Outcome("failed", error=f"Confluence respondió {updated.status}: {updated.text[:300]}")
        result = updated.json()
        shown = self._url(cfg.confluence_base_url, result)
        return Outcome("ok", {"page_id": str(result["id"]), "title": title, "version": version + 1, "url": shown},
                       artifacts=[Artifact("page", f"{title} (actualizada, v{version + 1})", shown,
                                           str(result["id"]))])


class ConfluenceGetPageExec(NodeExecutor):
    type = "confluence.get_page"

    async def run(self, ctx: ExecContext) -> Outcome:
        cfg = ctx.settings
        if not (cfg.confluence_base_url and cfg.confluence_email and cfg.confluence_token):
            return Outcome("failed", error="faltan CONFLUENCE_BASE_URL, el correo o el token en el .env")
        headers = {"Authorization": basic_auth(cfg.confluence_email, cfg.confluence_token)}
        base = f"{cfg.confluence_base_url}/rest/api/content"
        expand = "expand=body.storage,version,space,ancestors"
        timeout = min(ctx.engine.node_timeout(ctx.run, ctx.node_id) or 60, 60)
        page_id = str(ctx.config.get("page_id") or "")
        if page_id:
            result = await ctx.http("GET", f"{base}/{quote(page_id)}?{expand}", headers=headers, timeout_s=timeout)
        else:
            query = f"spaceKey={quote(ctx.config['space'])}&title={quote(ctx.config['title'])}&{expand}"
            result = await ctx.http("GET", f"{base}?{query}", headers=headers, timeout_s=timeout)
        if result.status == 404:
            return Outcome("not_found", error="la página no existe o no hay permiso")
        if not result.ok:
            return Outcome("failed", error=f"Confluence respondió {result.status}: {result.text[:300]}")
        page = result.json()
        if not page_id:
            results = page.get("results", [])
            if not results:
                return Outcome("not_found", error=f"no hay una página '{ctx.config['title']}' en {ctx.config['space']}")
            page = results[0]
        blocks, unsupported = storage_to_blocks(((page.get("body") or {}).get("storage") or {}).get("value", ""))
        ancestors = page.get("ancestors") or []
        outputs = {
            "page_id": str(page["id"]), "title": page["title"], "space": (page.get("space") or {}).get("key", ""),
            "parent_id": str(ancestors[-1]["id"]) if ancestors else "", "version": int(page["version"]["number"]),
            "url": ConfluenceCreatePageExec._url(cfg.confluence_base_url, page), "blocks": blocks,
            "text": blocks_to_markdown(blocks), "lossless": not unsupported, "unsupported": unsupported,
        }
        if unsupported and ctx.config.get("strict"):
            return Outcome("lossy", outputs, error="la página tiene contenido que no se puede representar: "
                           + "; ".join(unsupported))
        return Outcome("ok", outputs)
