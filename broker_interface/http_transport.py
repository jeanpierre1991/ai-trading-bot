"""Minimal injectable HTTP transport for broker adapters (stdlib only).

Keeps venue adapters testable without network and avoids coupling the core
runtime to any specific HTTP client library.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class HttpResponse:
    """Neutral HTTP response used by broker adapters."""

    status_code: int
    body: bytes
    headers: Mapping[str, str]

    def json(self) -> Any:
        if not self.body:
            return None
        return json.loads(self.body.decode("utf-8"))

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class HttpTransport(Protocol):
    """Broker-agnostic HTTP port (injectable for tests)."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float = 10.0,
    ) -> HttpResponse:
        """Perform an HTTP request and return a normalized response."""


class UrllibHttpTransport:
    """Default transport using ``urllib`` (no third-party HTTP dependency)."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float = 10.0,
    ) -> HttpResponse:
        request = urllib.request.Request(
            url=url,
            data=body,
            method=method.upper(),
            headers=dict(headers or {}),
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                status = int(getattr(response, "status", response.getcode()))
                resp_headers = {
                    str(k).lower(): str(v) for k, v in response.headers.items()
                }
                return HttpResponse(
                    status_code=status,
                    body=raw,
                    headers=resp_headers,
                )
        except urllib.error.HTTPError as exc:
            raw = exc.read() if hasattr(exc, "read") else b""
            try:
                raw = raw or b""
            except Exception:
                raw = b""
            resp_headers = {
                str(k).lower(): str(v)
                for k, v in getattr(exc, "headers", {}).items()
            }
            return HttpResponse(
                status_code=int(exc.code),
                body=raw,
                headers=resp_headers,
            )
        except urllib.error.URLError as exc:
            raise TimeoutError(f"HTTP transport error for {method} {url}: {exc}") from exc
