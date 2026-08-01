"""MicroVMApp — a tiny, zero-dependency web framework for AWS Lambda MicroVMs.

Usage:

    from microvm_app import MicroVMApp

    app = MicroVMApp()

    @app.startup          # alias of @app.run — Lambda's name for this hook
    def on_startup(ctx):
        print("MicroVM", ctx.microvm_id, "started with payload", ctx.payload)

    @app.entrypoint
    def handle(request):
        return {"hello": "world"}

    if __name__ == "__main__":
        app.serve()

Lambda invokes lifecycle hooks as POST requests on
``/aws/lambda-microvms/runtime/v1/<hook>`` (default port 9000) and routes
external traffic to your app port (default 8080). ``serve()`` runs both
listeners; if the ports are equal a single listener handles everything.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from .context import Request, Response, RunContext

logger = logging.getLogger("microvm_app")

HOOK_BASE_PATH = "/aws/lambda-microvms/runtime/v1"
LIFECYCLE_HOOKS = ("run", "resume", "suspend", "terminate", "ready", "validate")

DEFAULT_HOOK_PORT = int(os.environ.get("MICROVM_HOOK_PORT", "9000"))
DEFAULT_APP_PORT = int(os.environ.get("MICROVM_APP_PORT", "8080"))


def _call_handler(func: Callable, *args: Any) -> Any:
    """Call a handler with as many of ``args`` as its signature accepts.

    Lets users write ``def on_run():`` or ``def on_run(ctx):`` — both work.
    Async handlers are supported and run to completion on a private loop.
    """
    try:
        sig = inspect.signature(func)
        params = [
            p for p in sig.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        has_var_positional = any(
            p.kind == p.VAR_POSITIONAL for p in sig.parameters.values()
        )
        n = len(args) if has_var_positional else min(len(params), len(args))
    except (TypeError, ValueError):
        n = len(args)
    result = func(*args[:n])
    if inspect.iscoroutine(result):
        result = asyncio.run(result)
    return result


def _normalize_response(result: Any, *, hook: bool = False) -> Response:
    """Convert whatever a handler returned into a Response."""
    if isinstance(result, Response):
        return result
    if result is None or result is True:
        return Response(200, b"", content_type="application/json")
    if result is False:
        # Ready/validate semantics: "not ready yet" -> 503, Lambda retries.
        # For other handlers False also maps to 503 (service unavailable).
        return Response(503 if hook else 500, b"")
    if isinstance(result, int):
        return Response(result, b"")
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], int):
        inner = _normalize_response(result[1], hook=hook)
        inner.status = result[0]
        return inner
    if isinstance(result, bytes):
        return Response(200, result, content_type="application/octet-stream")
    if isinstance(result, str):
        return Response(200, result.encode(), content_type="text/plain; charset=utf-8")
    # dict / list / dataclass-ish -> JSON
    return Response(
        200,
        json.dumps(result, default=str).encode(),
        content_type="application/json",
    )


class MicroVMApp:
    """Application object holding lifecycle hooks and HTTP routes."""

    def __init__(
        self,
        hook_port: int = DEFAULT_HOOK_PORT,
        app_port: int = DEFAULT_APP_PORT,
        debug: bool = False,
    ):
        self.hook_port = hook_port
        self.app_port = app_port
        self.debug = debug
        self._hooks: Dict[str, Callable] = {}
        # routes: list of (method, path, handler); path "*" is a catch-all
        self._routes: List[Tuple[str, str, Callable]] = []
        self._entrypoint: Optional[Callable] = None
        self.context: Optional[RunContext] = None
        self._run_completed = threading.Event()
        self._servers: List[ThreadingHTTPServer] = []
        self._configure_logging()

    def _configure_logging(self) -> None:
        if not logging.getLogger().handlers:
            logging.basicConfig(
                level=logging.DEBUG if self.debug else logging.INFO,
                format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            )

    # ------------------------------------------------------------------
    # Lifecycle hook decorators
    # ------------------------------------------------------------------
    def _hook_decorator(self, name: str, func: Optional[Callable] = None):
        """Support both ``@app.run`` and ``@app.run()`` forms."""
        def register(f: Callable) -> Callable:
            self._hooks[name] = f
            return f

        if func is not None:
            if not callable(func):
                raise TypeError(
                    f"@app.{name} decorates a function; got {type(func).__name__}. "
                    f"(To start the server, call app.serve().)"
                )
            return register(func)
        return register

    def startup(self, func: Optional[Callable] = None):
        """Hook invoked when a MicroVM starts from the image snapshot.

        This is the recommended decorator for per-instance initialization:
        generate unique values (IDs, seeds, secrets), record the identity
        and task context Lambda delivers, and return quickly — the hook
        has a 1-60 second timeout and external traffic is only forwarded
        after it returns 200. Defer expensive work (DB connections, model
        calls) to your request handlers.

        The handler receives a :class:`RunContext` (microvm_id, payload,
        payload_json()).

        ``@app.startup`` and ``@app.run`` are the same hook. Lambda calls
        it ``run`` (the endpoint is ``/aws/lambda-microvms/runtime/v1/run``
        and that's the name you'll see in AWS docs, CloudWatch logs, and
        hook configuration); ``startup`` is the alias that says what the
        handler is for. Use whichever reads better — registering both is
        an error only in the sense that the second registration wins.
        """
        return self._hook_decorator("run", func)

    # Alias: same hook under the platform's name. See startup() docstring.
    run = startup

    def resume(self, func: Optional[Callable] = None):
        """Hook invoked when a MicroVM resumes from the suspended state."""
        return self._hook_decorator("resume", func)

    def suspend(self, func: Optional[Callable] = None):
        """Hook invoked just before a MicroVM is suspended."""
        return self._hook_decorator("suspend", func)

    def terminate(self, func: Optional[Callable] = None):
        """Hook invoked just before a MicroVM is terminated."""
        return self._hook_decorator("terminate", func)

    def ready(self, func: Optional[Callable] = None):
        """Image-build hook: return truthy/None when snapshot-ready, False for 503."""
        return self._hook_decorator("ready", func)

    def validate(self, func: Optional[Callable] = None):
        """Image-build hook: exercise the app after build; False -> 503 (retry)."""
        return self._hook_decorator("validate", func)

    # ------------------------------------------------------------------
    # Traffic routing
    # ------------------------------------------------------------------
    def entrypoint(self, func: Callable) -> Callable:
        """Catch-all handler for inbound traffic.

        Receives a :class:`Request`. Whatever it returns is serialized
        (dict/list -> JSON, str -> text, Response -> as-is).
        """
        self._entrypoint = func
        return func

    def route(self, path: str, methods: Optional[List[str]] = None):
        """Register a handler for a specific path, e.g. ``@app.route("/chat")``."""
        methods = [m.upper() for m in (methods or ["GET", "POST"])]

        def register(f: Callable) -> Callable:
            for m in methods:
                self._routes.append((m, path, f))
            return f

        return register

    def get(self, path: str):
        return self.route(path, ["GET"])

    def post(self, path: str):
        return self.route(path, ["POST"])

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------
    def _dispatch_hook(self, name: str, request: Request) -> Response:
        handler = self._hooks.get(name)
        logger.info("lifecycle hook: %s (handler=%s)", name,
                    getattr(handler, "__name__", None) if handler else "default")
        if name == "run":
            body = {}
            try:
                body = request.json() or {}
            except (ValueError, TypeError):
                logger.warning("run hook body was not valid JSON")
            self.context = RunContext(
                microvm_id=body.get("microvmId"),
                payload=body.get("runHookPayload"),
                raw=body,
            )
        try:
            if handler is None:
                result = None  # no handler registered -> 200 immediately
            elif name == "run":
                result = _call_handler(handler, self.context)
            else:
                result = _call_handler(handler, request)
            response = _normalize_response(result, hook=True)
        except Exception:
            logger.exception("error in %s hook handler", name)
            # Fail loudly: for run/ready/validate a non-2xx blocks
            # traffic / the build, which is what you want on a broken init.
            response = Response(500, b"hook handler raised an exception")
        if name == "run" and 200 <= response.status < 300:
            self._run_completed.set()
        return response

    def _dispatch_app(self, request: Request) -> Response:
        for method, path, handler in self._routes:
            if request.method == method and request.path == path:
                return self._safe_call(handler, request)
        if self._entrypoint is not None:
            return self._safe_call(self._entrypoint, request)
        return Response(404, b"not found")

    def _safe_call(self, handler: Callable, request: Request) -> Response:
        try:
            return _normalize_response(_call_handler(handler, request))
        except Exception:
            logger.exception("error in request handler %s",
                             getattr(handler, "__name__", handler))
            return Response(500, b"internal error")

    def handle(self, request: Request) -> Response:
        """Route a request to a hook or app handler (also used by tests)."""
        if request.path.startswith(HOOK_BASE_PATH + "/"):
            name = request.path[len(HOOK_BASE_PATH) + 1:].strip("/")
            if name in LIFECYCLE_HOOKS:
                if request.method != "POST":
                    return Response(405, b"hooks accept POST only")
                return self._dispatch_hook(name, request)
            return Response(404, b"unknown hook")
        return self._dispatch_app(request)

    # ------------------------------------------------------------------
    # Serving
    # ------------------------------------------------------------------
    def _make_server(self, port: int, label: str) -> ThreadingHTTPServer:
        app = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _handle(self) -> None:
                parsed = urlparse(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                request = Request(
                    method=self.command,
                    path=parsed.path,
                    query={k: v[0] for k, v in parse_qs(parsed.query).items()},
                    headers={k.lower(): v for k, v in self.headers.items()},
                    body=body,
                )
                response = app.handle(request)
                payload = response.body
                if isinstance(payload, str):
                    payload = payload.encode()
                elif not isinstance(payload, (bytes, bytearray)):
                    payload = json.dumps(payload, default=str).encode()
                self.send_response(response.status)
                ctype = response.content_type or "application/json"
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(payload)))
                for k, v in response.headers.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = _handle

            def log_message(self, fmt: str, *args: Any) -> None:
                logger.debug("%s %s", label, fmt % args)

        server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        server.daemon_threads = True
        return server

    def serve(self, block: bool = True) -> None:
        """Start the hook listener and the app listener.

        With ``block=True`` (the default, for production) this never
        returns. ``block=False`` starts background threads — handy for
        tests and local dev.
        """
        ports = {self.hook_port, self.app_port}  # collapses if equal
        for port in sorted(ports):
            server = self._make_server(port, f"port {port}")
            self._servers.append(server)
            thread = threading.Thread(
                target=server.serve_forever, daemon=True,
                name=f"microvm-app-{port}",
            )
            thread.start()
            logger.info("listening on 0.0.0.0:%s%s", port,
                        " (hooks + app)" if len(ports) == 1
                        else (" (hooks)" if port == self.hook_port else " (app)"))
        if block:
            try:
                threading.Event().wait()
            except KeyboardInterrupt:
                self.shutdown()

    # `run` is taken by the lifecycle decorator, so `start` and `serve`
    # are the aliases for launching the server.
    start = serve

    def shutdown(self) -> None:
        for server in self._servers:
            server.shutdown()
            server.server_close()
        self._servers.clear()
