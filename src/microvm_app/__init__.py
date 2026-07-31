"""microvm-app: developer-friendly SDK for AWS Lambda MicroVMs.

Two styles are supported.

App style:

    from microvm_app import MicroVMApp

    app = MicroVMApp()

    @app.run
    def on_run(ctx): ...

    @app.entrypoint
    def handle(request): ...

    app.serve()

Module style (a process-wide default app, for the smallest scripts):

    from microvm_app import run, entrypoint, serve

    @run
    def on_run(ctx): ...

    @entrypoint
    def handle(request): ...

    serve()
"""

from .app import HOOK_BASE_PATH, LIFECYCLE_HOOKS, MicroVMApp
from .context import Request, Response, RunContext

__version__ = "0.1.0"

# Process-wide default app so users can `from microvm_app import run`.
default_app = MicroVMApp()

run = default_app.run
resume = default_app.resume
suspend = default_app.suspend
terminate = default_app.terminate
ready = default_app.ready
validate = default_app.validate
entrypoint = default_app.entrypoint
route = default_app.route
get = default_app.get
post = default_app.post
serve = default_app.serve

__all__ = [
    "MicroVMApp",
    "Request",
    "Response",
    "RunContext",
    "HOOK_BASE_PATH",
    "LIFECYCLE_HOOKS",
    "default_app",
    "run",
    "resume",
    "suspend",
    "terminate",
    "ready",
    "validate",
    "entrypoint",
    "route",
    "get",
    "post",
    "serve",
]
