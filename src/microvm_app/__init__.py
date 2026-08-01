"""microvm-app: developer-friendly SDK for AWS Lambda MicroVMs.

Two styles are supported.

App style:

    from microvm_app import MicroVMApp

    app = MicroVMApp()

    @app.startup            # alias of @app.run — Lambda's name for the hook
    def on_startup(ctx): ...

    @app.entrypoint
    def handle(request): ...

    app.serve()

Module style (a process-wide default app, for the smallest scripts):

    from microvm_app import startup, entrypoint, serve

    @startup
    def on_startup(ctx): ...

    @entrypoint
    def handle(request): ...

    serve()

Naming note: Lambda calls the instance-start hook ``run`` — the endpoint
is ``/aws/lambda-microvms/runtime/v1/run``, and "run" is the name in AWS
docs, hook configuration, and CloudWatch logs. ``startup`` is this
library's alias for the same hook, named for what the handler does:
per-instance initialization. Both are exported and interchangeable.
"""

from .app import HOOK_BASE_PATH, LIFECYCLE_HOOKS, MicroVMApp
from .context import Request, Response, RunContext

__version__ = "0.1.0"

# Process-wide default app so users can `from microvm_app import startup`.
default_app = MicroVMApp()

startup = default_app.startup
run = startup                  # alias of startup — the platform's name
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
    "startup",
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
