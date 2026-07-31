"""Request and lifecycle context objects for microvm-app."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class RunContext:
    """Delivered to the @app.run hook when Lambda starts a MicroVM.

    Lambda POSTs ``{"microvmId": ..., "runHookPayload": ...}`` to the run
    hook. ``payload`` is the raw string passed to ``run-microvm
    --run-hook-payload`` (max 16 KB); use :meth:`payload_json` if you put
    JSON in it.
    """

    microvm_id: Optional[str] = None
    payload: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def payload_json(self) -> Any:
        """Parse the run-hook payload as JSON (None if empty)."""
        if not self.payload:
            return None
        return json.loads(self.payload)


@dataclass
class Request:
    """An inbound HTTP request delivered to @app.route / @app.entrypoint handlers."""

    method: str
    path: str
    query: Dict[str, str]
    headers: Dict[str, str]
    body: bytes

    def json(self) -> Any:
        """Parse the request body as JSON (None if empty)."""
        if not self.body:
            return None
        return json.loads(self.body)

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


@dataclass
class Response:
    """Explicit response object handlers may return.

    Handlers may also return: None (200), a dict/list (JSON), a str (text),
    an int (status code), bytes (octet-stream), or a (status, body) tuple.
    Ready/validate handlers may return False to signal 503 (not ready yet).
    """

    status: int = 200
    body: Any = b""
    headers: Dict[str, str] = field(default_factory=dict)
    content_type: Optional[str] = None
