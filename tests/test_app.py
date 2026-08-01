"""Unit tests for MicroVMApp — dispatch logic and live HTTP behavior."""

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from microvm_app import HOOK_BASE_PATH, MicroVMApp, Request, Response


def make_request(method="POST", path="/", body=b"", headers=None, query=None):
    return Request(method=method, path=path, query=query or {},
                   headers=headers or {}, body=body)


def hook_path(name):
    return f"{HOOK_BASE_PATH}/{name}"


# ---------------------------------------------------------------------------
# Hook dispatch (no sockets)
# ---------------------------------------------------------------------------

class TestHookDispatch:
    def test_run_hook_receives_context(self):
        app = MicroVMApp()
        seen = {}

        @app.run
        def on_run(ctx):
            seen["id"] = ctx.microvm_id
            seen["payload"] = ctx.payload

        body = json.dumps({"microvmId": "mvm-123", "runHookPayload": "tenant-42"})
        response = app.handle(make_request(path=hook_path("run"), body=body.encode()))
        assert response.status == 200
        assert seen == {"id": "mvm-123", "payload": "tenant-42"}
        assert app.context.microvm_id == "mvm-123"

    def test_run_context_payload_json(self):
        app = MicroVMApp()

        @app.run
        def on_run(ctx):
            assert ctx.payload_json() == {"tenant": "acme"}

        body = json.dumps({"microvmId": "m", "runHookPayload": '{"tenant": "acme"}'})
        assert app.handle(make_request(path=hook_path("run"), body=body.encode())).status == 200

    def test_zero_arg_handlers_work(self):
        app = MicroVMApp()
        called = []

        @app.suspend
        def on_suspend():
            called.append(True)

        assert app.handle(make_request(path=hook_path("suspend"))).status == 200
        assert called == [True]

    def test_unregistered_hook_returns_200(self):
        # Lambda always calls enabled hooks; a missing handler must not fail the VM.
        app = MicroVMApp()
        for name in ("run", "resume", "suspend", "terminate", "ready", "validate"):
            assert app.handle(make_request(path=hook_path(name))).status == 200

    def test_ready_false_returns_503(self):
        app = MicroVMApp()
        state = {"ready": False}

        @app.ready
        def on_ready():
            return state["ready"]

        assert app.handle(make_request(path=hook_path("ready"))).status == 503
        state["ready"] = True
        assert app.handle(make_request(path=hook_path("ready"))).status == 200

    def test_handler_exception_returns_500(self):
        app = MicroVMApp()

        @app.run
        def on_run(ctx):
            raise RuntimeError("boom")

        assert app.handle(make_request(path=hook_path("run"), body=b"{}")).status == 500

    def test_hooks_reject_get(self):
        app = MicroVMApp()
        assert app.handle(make_request(method="GET", path=hook_path("run"))).status == 405

    def test_unknown_hook_404(self):
        assert MicroVMApp().handle(make_request(path=hook_path("nonsense"))).status == 404

    def test_decorator_with_parens(self):
        app = MicroVMApp()

        @app.run()
        def on_run(ctx):
            return None

        assert app._hooks["run"] is on_run

    def test_startup_is_alias_of_run(self):
        # @app.startup registers the same hook Lambda calls "run".
        app = MicroVMApp()
        seen = {}

        @app.startup
        def on_startup(ctx):
            seen["id"] = ctx.microvm_id

        assert app._hooks["run"] is on_startup
        body = json.dumps({"microvmId": "mvm-alias"})
        response = app.handle(make_request(path=hook_path("run"), body=body.encode()))
        assert response.status == 200
        assert seen == {"id": "mvm-alias"}

    def test_startup_and_run_share_registration(self):
        # Last registration wins — they are one slot, not two hooks.
        app = MicroVMApp()

        @app.startup
        def first(ctx):
            return None

        @app.run
        def second(ctx):
            return None

        assert app._hooks["run"] is second

    def test_async_handler(self):
        app = MicroVMApp()

        @app.terminate
        async def on_terminate():
            return {"flushed": True}

        response = app.handle(make_request(path=hook_path("terminate")))
        assert response.status == 200


# ---------------------------------------------------------------------------
# App traffic dispatch
# ---------------------------------------------------------------------------

class TestAppDispatch:
    def test_entrypoint_catch_all(self):
        app = MicroVMApp()

        @app.entrypoint
        def handler(request):
            return {"path": request.path}

        response = app.handle(make_request(method="GET", path="/anything"))
        assert response.status == 200
        assert json.loads(response.body) == {"path": "/anything"}

    def test_route_beats_entrypoint(self):
        app = MicroVMApp()

        @app.entrypoint
        def fallback(request):
            return "fallback"

        @app.get("/health")
        def health(request):
            return {"ok": True}

        assert json.loads(app.handle(make_request(method="GET", path="/health")).body) == {"ok": True}
        assert app.handle(make_request(method="GET", path="/other")).body == b"fallback"

    def test_no_handler_404(self):
        assert MicroVMApp().handle(make_request(method="GET", path="/")).status == 404

    def test_return_types(self):
        app = MicroVMApp()

        @app.get("/str")
        def a(request):
            return "hello"

        @app.get("/tuple")
        def b(request):
            return 201, {"made": True}

        @app.get("/int")
        def c(request):
            return 204

        @app.get("/resp")
        def d(request):
            return Response(418, "teapot", content_type="text/plain")

        assert app.handle(make_request(method="GET", path="/str")).body == b"hello"
        r = app.handle(make_request(method="GET", path="/tuple"))
        assert (r.status, json.loads(r.body)) == (201, {"made": True})
        assert app.handle(make_request(method="GET", path="/int")).status == 204
        assert app.handle(make_request(method="GET", path="/resp")).status == 418

    def test_request_json_helper(self):
        app = MicroVMApp()

        @app.post("/echo")
        def echo(request):
            return request.json()

        response = app.handle(make_request(method="POST", path="/echo", body=b'{"a": 1}'))
        assert json.loads(response.body) == {"a": 1}

    def test_handler_exception_500(self):
        app = MicroVMApp()

        @app.entrypoint
        def handler(request):
            raise ValueError("nope")

        assert app.handle(make_request(method="GET", path="/")).status == 500


# ---------------------------------------------------------------------------
# Live HTTP servers (real sockets, both ports)
# ---------------------------------------------------------------------------

def http(method, port, path, body=None):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=body, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


@pytest.fixture
def live_app():
    app = MicroVMApp(hook_port=19000, app_port=18080)

    @app.run
    def on_run(ctx):
        app.tenant = (ctx.payload_json() or {}).get("tenant")

    @app.entrypoint
    def handler(request):
        return {"tenant": getattr(app, "tenant", None), "path": request.path}

    app.serve(block=False)
    time.sleep(0.2)
    yield app
    app.shutdown()


class TestLiveServer:
    def test_full_lifecycle_over_http(self, live_app):
        body = json.dumps({
            "microvmId": "mvm-live",
            "runHookPayload": json.dumps({"tenant": "acme"}),
        }).encode()
        status, _ = http("POST", 19000, hook_path("run"), body)
        assert status == 200

        status, data = http("GET", 18080, "/whoami")
        assert status == 200
        assert json.loads(data) == {"tenant": "acme", "path": "/whoami"}

    def test_hooks_not_exposed_only_on_app_port_path_still_routed(self, live_app):
        # Hook paths are recognized on either listener (Lambda only calls the
        # configured hook port; serving them on both is harmless and makes
        # single-port setups work).
        status, _ = http("POST", 19000, hook_path("suspend"))
        assert status == 200

    def test_404_on_unknown_hook(self, live_app):
        status, _ = http("POST", 19000, hook_path("bogus"))
        assert status == 404


def test_single_port_mode():
    app = MicroVMApp(hook_port=17070, app_port=17070)

    @app.entrypoint
    def handler(request):
        return "app"

    app.serve(block=False)
    time.sleep(0.2)
    try:
        assert http("POST", 17070, hook_path("run"), b"{}")[0] == 200
        assert http("GET", 17070, "/")[1] == b"app"
        assert len(app._servers) == 1
    finally:
        app.shutdown()


def test_module_level_api():
    import microvm_app

    assert callable(microvm_app.run)
    assert callable(microvm_app.startup)
    assert microvm_app.startup is microvm_app.run
    assert callable(microvm_app.entrypoint)
    assert microvm_app.default_app is not None
