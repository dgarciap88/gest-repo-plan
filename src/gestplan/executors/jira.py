"""Ejecutores de Jira Cloud (API REST v3)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from gestplan.atlassian import adf_to_text, basic_auth, markdown_to_adf
from gestplan.engine.http import HttpResult
from gestplan.executors.base import ExecContext, NodeExecutor, Outcome

_KEY = re.compile(r"^[A-Z][A-Z0-9_]*-\d+$")


class JiraError(Exception):
    def __init__(self, event: str, message: str) -> None:
        super().__init__(message)
        self.event = event


def _error_text(result: HttpResult) -> str:
    try:
        data = result.json() or {}
        parts = list(data.get("errorMessages", [])) + [f"{k}: {v}" for k, v in data.get("errors", {}).items()]
        if parts:
            return "; ".join(parts)
    except (ValueError, AttributeError):
        pass
    return result.text[:300] or "sin respuesta"


class JiraBase(NodeExecutor):
    """Utilidades comunes: credenciales, llamadas con reintento ante 429/5xx y traducción de errores."""

    async def call(self, ctx: ExecContext, method: str, path: str, *, json_body: Any = None,
                   query: str = "") -> HttpResult:
        cfg = ctx.settings
        if not (cfg.jira_base_url and cfg.jira_email and cfg.jira_token):
            raise JiraError("failed", "faltan JIRA_BASE_URL, JIRA_EMAIL o JIRA_API_TOKEN en el .env")
        url = f"{cfg.jira_base_url}/rest/api/3/{path}{query}"
        headers = {"Authorization": basic_auth(cfg.jira_email, cfg.jira_token)}
        timeout = min(ctx.engine.node_timeout(ctx.run, ctx.node_id) or 60, 60)
        result = await ctx.http(method, url, headers=headers, json_body=json_body, timeout_s=timeout)
        for delay in (1, 3):
            if result.status not in (0, 429, 500, 502, 503, 504):
                break
            await ctx.engine.sleep(delay)
            result = await ctx.http(method, url, headers=headers, json_body=json_body, timeout_s=timeout)
        return result

    async def json(self, ctx: ExecContext, method: str, path: str, **kwargs: Any) -> Any:
        result = await self.call(ctx, method, path, **kwargs)
        if result.status in (401, 403):
            raise JiraError("failed", f"Jira rechazó las credenciales o el permiso ({result.status})")
        if result.status == 404:
            raise JiraError("not_found", _error_text(result))
        if not result.ok:
            raise JiraError("failed", f"Jira respondió {result.status}: {_error_text(result)}")
        return result.json()

    async def run(self, ctx: ExecContext) -> Outcome:
        key = str(ctx.config["key"]).strip()
        if not _KEY.match(key):
            return Outcome("failed", error=f"clave de ticket no válida: '{key}'")
        try:
            return await self.execute(ctx, key)
        except JiraError as exc:
            return Outcome(exc.event, error=str(exc))

    async def execute(self, ctx: ExecContext, key: str) -> Outcome:
        raise NotImplementedError

    @staticmethod
    def marker(ctx: ExecContext) -> str:
        return f"[gestplan run:{ctx.run_id} node:{ctx.node_id}]"


def _name(user: dict | None) -> str:
    return (user or {}).get("displayName", "") if user else ""


class JiraGetIssueExec(JiraBase):
    type = "jira.get_issue"

    async def execute(self, ctx: ExecContext, key: str) -> Outcome:
        data = await self.json(ctx, "GET", f"issue/{quote(key)}")
        fields = data.get("fields", {})
        return Outcome("ok", {
            "key": data.get("key", key),
            "url": f"{ctx.settings.jira_base_url}/browse/{data.get('key', key)}",
            "summary": fields.get("summary", ""),
            "description": adf_to_text(fields.get("description")),
            "status": (fields.get("status") or {}).get("name", ""),
            "issue_type": (fields.get("issuetype") or {}).get("name", ""),
            "priority": (fields.get("priority") or {}).get("name", ""),
            "labels": fields.get("labels", []),
            "assignee": _name(fields.get("assignee")),
            "reporter": _name(fields.get("reporter")),
            "parent_key": (fields.get("parent") or {}).get("key", ""),
            "subtasks": [s.get("key", "") for s in fields.get("subtasks", [])],
        })


class JiraGetCommentsExec(JiraBase):
    type = "jira.get_comments"

    async def execute(self, ctx: ExecContext, key: str) -> Outcome:
        limit = max(1, min(int(ctx.config.get("max_results", 50)), 100))
        data = await self.json(ctx, "GET", f"issue/{quote(key)}/comment", query=f"?maxResults={limit}&orderBy=created")
        comments = [{"id": c.get("id", ""), "author": _name(c.get("author")), "created": c.get("created", ""),
                     "body": adf_to_text(c.get("body"))} for c in data.get("comments", [])]
        text = "\n\n".join(f"{c['author']} ({c['created']}):\n{c['body']}" for c in comments)
        return Outcome("ok", {"count": len(comments), "comments": comments, "text": text})


class JiraGetAttachmentsExec(JiraBase):
    type = "jira.get_attachments"

    async def execute(self, ctx: ExecContext, key: str) -> Outcome:
        data = await self.json(ctx, "GET", f"issue/{quote(key)}", query="?fields=attachment")
        target = Path(ctx.config["workspace"]) / ctx.config["directory"]
        limit = int(ctx.config.get("max_bytes", 10_000_000))
        cfg = ctx.settings
        files: list[str] = []
        skipped: list[str] = []
        target.mkdir(parents=True, exist_ok=True)  # siempre existe: puede pasarse como carpeta adicional al agente
        for item in data.get("fields", {}).get("attachment", []):
            name = re.sub(r"[^\w.\- ]", "_", item.get("filename", "adjunto")) or "adjunto"
            if item.get("size", 0) > limit or not item.get("content"):
                skipped.append(name)
                continue
            result = await ctx.http("GET", item["content"], headers={
                "Authorization": basic_auth(cfg.jira_email, cfg.jira_token), "Accept": "*/*"},
                timeout_s=120, keep_body=False)
            if not result.ok:
                skipped.append(name)
                continue
            (target / name).write_bytes(result.body)
            files.append(str(Path(ctx.config["directory"]) / name))
        notes = (f"{name} (omitido: demasiado grande o no descargable)" for name in skipped)
        listing = "\n".join([*files, *notes])
        return Outcome("ok", {"files": files, "skipped": skipped, "directory": str(target.resolve()),
                              "listing": listing})


class JiraAssignExec(JiraBase):
    type = "jira.assign"

    async def _resolve(self, ctx: ExecContext) -> tuple[str, str]:
        who = str(ctx.config["assignee"]).strip()
        if who.lower() == "me":
            me = await self.json(ctx, "GET", "myself")
            return me["accountId"], me.get("displayName", "")
        if "@" in who:
            found = await self.json(ctx, "GET", "user/search", query=f"?query={quote(who)}")
            if not found:
                raise JiraError("user_not_found", f"no hay ningún usuario con el correo {who}")
            return found[0]["accountId"], found[0].get("displayName", "")
        return who, who

    async def preview(self, ctx: ExecContext) -> dict[str, Any] | None:
        return {"kind": "keyvalue", "title": "Asignar ticket",
                "items": {"ticket": ctx.config["key"], "asignar a": ctx.config["assignee"]}}

    async def execute(self, ctx: ExecContext, key: str) -> Outcome:
        account, name = await self._resolve(ctx)
        issue = await self.json(ctx, "GET", f"issue/{quote(key)}", query="?fields=assignee")
        if ((issue.get("fields") or {}).get("assignee") or {}).get("accountId") == account:
            return Outcome("already_done", {"assignee_id": account, "assignee_name": name})
        await self.json(ctx, "PUT", f"issue/{quote(key)}/assignee", json_body={"accountId": account})
        return Outcome("ok", {"assignee_id": account, "assignee_name": name})


class JiraCommentExec(JiraBase):
    type = "jira.comment"

    async def preview(self, ctx: ExecContext) -> dict[str, Any] | None:
        return {"kind": "markdown", "title": f"Comentario en {ctx.config['key']}", "text": str(ctx.config["body"])}

    async def execute(self, ctx: ExecContext, key: str) -> Outcome:
        marker = self.marker(ctx)
        existing = await self.json(ctx, "GET", f"issue/{quote(key)}/comment", query="?maxResults=100&orderBy=-created")
        for comment in existing.get("comments", []):
            if marker in adf_to_text(comment.get("body")):
                return Outcome("already_done", {"comment_id": comment.get("id", "")})
        created = await self.json(ctx, "POST", f"issue/{quote(key)}/comment",
                                  json_body={"body": markdown_to_adf(str(ctx.config["body"]), marker)})
        return Outcome("ok", {"comment_id": created.get("id", "")})


class JiraTransitionExec(JiraBase):
    type = "jira.transition"

    async def preview(self, ctx: ExecContext) -> dict[str, Any] | None:
        return {"kind": "keyvalue", "title": "Cambio de estado",
                "items": {"ticket": ctx.config["key"], "estado destino": ctx.config["to"]}}

    async def execute(self, ctx: ExecContext, key: str) -> Outcome:
        target = str(ctx.config["to"]).strip().lower()
        issue = await self.json(ctx, "GET", f"issue/{quote(key)}", query="?fields=status")
        current = ((issue.get("fields") or {}).get("status") or {}).get("name", "")
        if current.lower() == target:
            return Outcome("already_done", {"from_status": current, "status": current})
        options = (await self.json(ctx, "GET", f"issue/{quote(key)}/transitions")).get("transitions", [])
        match = next((t for t in options if (t.get("to") or {}).get("name", "").lower() == target
                      or t.get("name", "").lower() == target), None)
        if match is None:
            names = ", ".join(sorted({(t.get("to") or {}).get("name", "") for t in options}))
            return Outcome("no_transition", error=f"desde '{current}' no se puede pasar a '{ctx.config['to']}'"
                                                  f" (disponibles: {names or 'ninguno'})")
        await self.json(ctx, "POST", f"issue/{quote(key)}/transitions", json_body={"transition": {"id": match["id"]}})
        return Outcome("ok", {"from_status": current, "status": (match.get("to") or {}).get("name", target)})


class JiraCreateSubtaskExec(JiraBase):
    type = "jira.create_subtask"

    async def preview(self, ctx: ExecContext) -> dict[str, Any] | None:
        lines = "\n".join(f"- {s['summary']}" for s in ctx.config["subtasks"])
        return {"kind": "markdown", "title": f"Subtareas de {ctx.config['key']}", "text": lines}

    async def execute(self, ctx: ExecContext, key: str) -> Outcome:
        parent = await self.json(ctx, "GET", f"issue/{quote(key)}", query="?fields=project,issuetype")
        project = parent["fields"]["project"]["key"]
        types = await self.json(ctx, "GET", f"issue/createmeta/{quote(project)}/issuetypes")
        entries = types.get("issueTypes") or types.get("values") or []
        subtask = next((t for t in entries if t.get("subtask")), None)
        if subtask is None:
            return Outcome("no_subtask_type", error=f"el proyecto {project} no tiene tipo de subtarea")
        keys: list[str] = []
        created = 0
        for index, spec in enumerate(ctx.config["subtasks"]):
            label = f"gp-{ctx.run_id}-{ctx.node_id}-{index}".replace("_", "-")
            jql = f'parent = "{key}" AND labels = "{label}"'
            found = await self.json(ctx, "GET", "search/jql", query=f"?jql={quote(jql)}&fields=summary")
            if found.get("issues"):
                keys.append(found["issues"][0]["key"])
                continue
            fields: dict[str, Any] = {"project": {"key": project}, "parent": {"key": key},
                                      "issuetype": {"id": subtask["id"]}, "summary": spec["summary"],
                                      "labels": [label]}
            if spec.get("description"):
                fields["description"] = markdown_to_adf(spec["description"])
            keys.append((await self.json(ctx, "POST", "issue", json_body={"fields": fields}))["key"])
            created += 1
        return Outcome("ok", {"keys": keys, "created": created})


class JiraCreateIssueExec(JiraBase):
    type = "jira.create_issue"

    _ALIASES = ({"task", "tarea"}, {"bug", "error"}, {"story", "historia"}, {"epic", "épica", "epica"})

    async def preview(self, ctx: ExecContext) -> dict[str, Any] | None:
        cfg = ctx.config
        extra = f"\n\n{cfg['description']}" if cfg.get("description") else ""
        return {"kind": "markdown", "title": f"Nuevo ticket en {cfg['project']}",
                "text": f"**{cfg['summary']}** ({cfg['issue_type']}){extra}"}

    async def run(self, ctx: ExecContext) -> Outcome:
        try:
            return await self.execute(ctx, str(ctx.config["project"]).strip())
        except JiraError as exc:
            return Outcome(exc.event, error=str(exc))

    def _pick_type(self, types: list[dict[str, Any]], wanted: str) -> dict[str, Any] | None:
        name = wanted.strip().lower()
        names = next((group for group in self._ALIASES if name in group), {name})  # Task = Tarea, Bug = Error...
        return next((t for t in types if not t.get("subtask") and t.get("name", "").lower() in names), None)

    async def execute(self, ctx: ExecContext, project: str) -> Outcome:
        cfg = ctx.config
        meta = await self.json(ctx, "GET", f"project/{quote(project)}")
        chosen = self._pick_type(meta.get("issueTypes", []), str(cfg["issue_type"]))
        if chosen is None:
            available = ", ".join(t["name"] for t in meta.get("issueTypes", []) if not t.get("subtask"))
            return Outcome("issue_type_not_found",
                           error=f"el proyecto {project} no tiene el tipo '{cfg['issue_type']}' (hay: {available})")
        label = f"gp-{ctx.run_id}-{ctx.node_id}".replace("_", "-")
        jql = f'project = "{project}" AND labels = "{label}"'
        found = await self.json(ctx, "GET", "search/jql", query=f"?jql={quote(jql)}&fields=summary")
        base = ctx.settings.jira_base_url
        if found.get("issues"):
            existing = found["issues"][0]
            return Outcome("already_done", {"key": existing["key"], "id": existing.get("id", ""),
                                            "url": f"{base}/browse/{existing['key']}"})
        fields: dict[str, Any] = {"project": {"key": project}, "issuetype": {"id": chosen["id"]},
                                  "summary": cfg["summary"], "labels": [*(cfg.get("labels") or []), label]}
        if cfg.get("description"):
            fields["description"] = markdown_to_adf(cfg["description"])
        if cfg.get("parent_key"):
            fields["parent"] = {"key": cfg["parent_key"]}
        created = await self.json(ctx, "POST", "issue", json_body={"fields": fields})
        return Outcome("ok", {"key": created["key"], "id": str(created.get("id", "")),
                              "url": f"{base}/browse/{created['key']}"})
