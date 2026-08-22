"""Bounded, header-safe forwarding from the CPU edge to the GPU API."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os

import requests
from fastapi import Response


DEFAULT_EDGE_ORIGIN_TIMEOUT_SECONDS = 300.0

_FORWARDED_REQUEST_HEADERS = {
    "accept",
    "authorization",
    "cache-control",
    "user-agent",
    "x-request-id",
    "x-tenant-id",
}
_FORWARDED_RESPONSE_HEADERS = {
    "cache-control",
    "content-type",
    "retry-after",
    "x-compression-cache",
    "x-compression-content-cache",
    "x-request-id",
}


class EdgeOriginUnavailable(RuntimeError):
    """Raised when a configured GPU origin cannot return a response."""


@dataclass(slots=True)
class EdgeOriginClient:
    base_url: str | None
    timeout_seconds: float = DEFAULT_EDGE_ORIGIN_TIMEOUT_SECONDS
    shared_secret: str | None = None
    session: requests.Session | None = None

    def __post_init__(self) -> None:
        normalized = self.base_url.strip().rstrip("/") if self.base_url else ""
        self.base_url = normalized or None
        if self.timeout_seconds <= 0:
            raise ValueError("Edge origin timeout must be positive")
        if self.shared_secret is not None:
            self.shared_secret = self.shared_secret.strip() or None
        if self.session is None:
            self.session = requests.Session()

    @classmethod
    def from_environment(cls) -> "EdgeOriginClient":
        return cls(
            base_url=os.getenv("EDGE_ORIGIN_BASE_URL"),
            timeout_seconds=float(
                os.getenv(
                    "EDGE_ORIGIN_TIMEOUT_SECONDS",
                    str(DEFAULT_EDGE_ORIGIN_TIMEOUT_SECONDS),
                )
            ),
            shared_secret=os.getenv("EDGE_ORIGIN_SHARED_SECRET"),
        )

    @property
    def configured(self) -> bool:
        return self.base_url is not None

    def forward(
        self,
        *,
        path: str,
        body: bytes,
        incoming_headers: Mapping[str, str],
        request_id: str,
    ) -> Response:
        if self.base_url is None:
            raise EdgeOriginUnavailable("The GPU compression origin is not configured.")

        headers = {
            name: value
            for name, value in incoming_headers.items()
            if name.casefold() in _FORWARDED_REQUEST_HEADERS
        }
        headers["Content-Type"] = "application/json"
        headers["X-Request-ID"] = request_id
        if self.shared_secret is not None:
            headers["X-Origin-Shared-Secret"] = self.shared_secret

        assert self.session is not None
        try:
            origin_response = self.session.post(
                f"{self.base_url}{path}",
                data=body,
                headers=headers,
                timeout=self.timeout_seconds,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise EdgeOriginUnavailable(
                "The GPU compression origin is temporarily unavailable."
            ) from exc

        response_headers = {
            name: value
            for name, value in origin_response.headers.items()
            if name.casefold() in _FORWARDED_RESPONSE_HEADERS
        }
        response_headers["X-Edge-Decision"] = "origin"
        response_headers["X-Origin-Status"] = str(origin_response.status_code)
        response_headers.setdefault("X-Request-ID", request_id)
        return Response(
            content=origin_response.content,
            status_code=origin_response.status_code,
            headers=response_headers,
            media_type=None,
        )
