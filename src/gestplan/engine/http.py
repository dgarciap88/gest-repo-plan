"""Cliente HTTP del motor (Jira, Confluence): real con la biblioteca estándar y falso para los tests."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class HttpResult:
    status: int
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8")) if self.body else None


class HttpClient(Protocol):
    async def request(self, method: str, url: str, *, headers: dict[str, str] | None = None,
                      json_body: Any = None, data: bytes | None = None,
                      timeout_s: float = 60) -> HttpResult: ...


class RealHttp:
    """urllib en un hilo; un fallo de red se devuelve como status 0 (el nodo decide qué evento emite)."""

    async def request(self, method: str, url: str, *, headers: dict[str, str] | None = None,
                      json_body: Any = None, data: bytes | None = None, timeout_s: float = 60) -> HttpResult:
        return await asyncio.to_thread(self._send, method, url, headers or {}, json_body, data, timeout_s)

    @staticmethod
    def _send(method: str, url: str, headers: dict[str, str], json_body: Any, data: bytes | None,
              timeout_s: float) -> HttpResult:
        head = {"Accept": "application/json", **headers}
        if json_body is not None:
            data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            head.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(url, data=data, headers=head, method=method)  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
                return HttpResult(resp.status, resp.read(), dict(resp.headers))
        except urllib.error.HTTPError as exc:
            return HttpResult(exc.code, exc.read(), dict(exc.headers))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return HttpResult(0, str(exc).encode("utf-8"))


Handler = Callable[[str, str, Any], "HttpResult | None"]


class FakeHttp:
    """Servidor simulado: cada manejador recibe (método, url, cuerpo JSON) y devuelve un HttpResult o None."""

    def __init__(self, *handlers: Handler) -> None:
        self.handlers = list(handlers)
        self.calls: list[dict[str, Any]] = []

    async def request(self, method: str, url: str, *, headers: dict[str, str] | None = None,
                      json_body: Any = None, data: bytes | None = None, timeout_s: float = 60) -> HttpResult:
        self.calls.append({"method": method, "url": url, "json": json_body, "headers": headers or {}})
        for handler in self.handlers:
            result = handler(method, url, json_body)
            if result is not None:
                return result
        return HttpResult(404, b'{"errorMessages": ["sin manejador"]}')


def json_response(payload: Any, status: int = 200) -> HttpResult:
    return HttpResult(status, json.dumps(payload).encode("utf-8"))
