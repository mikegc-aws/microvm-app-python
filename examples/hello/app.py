"""Example MicroVM app: full lifecycle hooks + a tiny JSON API."""

import os
import time
import uuid

from microvm_app import MicroVMApp

app = MicroVMApp()

STATE = {
    "instance_id": None,   # regenerated per-VM in @app.run (snapshot uniqueness!)
    "tenant": None,
    "started_at": None,
    "resumes": 0,
    "requests": 0,
}


@app.ready
def on_ready():
    # Called during image build, before Lambda snapshots the VM.
    print("ready hook: warm caches / preload models here")
    return True


@app.validate
def on_validate():
    # Called on a fresh VM from the new image. Exercise real code paths so
    # Lambda can prefetch the snapshot pages your app actually touches.
    print("validate hook: running smoke checks")
    _ = handler.__name__
    return True


@app.run
def on_run(ctx):
    # Values baked into the snapshot are shared by every VM from this image,
    # so anything unique is generated here.
    STATE["instance_id"] = str(uuid.uuid4())
    STATE["started_at"] = time.time()
    payload = ctx.payload_json() if ctx.payload else None
    STATE["tenant"] = (payload or {}).get("tenant", "anonymous")
    print(f"run hook: microvm={ctx.microvm_id} tenant={STATE['tenant']}")


@app.resume
def on_resume():
    STATE["resumes"] += 1
    print(f"resume hook: resume #{STATE['resumes']} — refresh creds/connections here")


@app.suspend
def on_suspend():
    print("suspend hook: flushing state before checkpoint")


@app.terminate
def on_terminate():
    print("terminate hook: goodbye")


@app.get("/health")
def health(request):
    return {"status": "healthy"}


@app.entrypoint
def handler(request):
    STATE["requests"] += 1
    return {
        "message": "Hello from a Lambda MicroVM! (v2)",
        "instance_id": STATE["instance_id"],
        "tenant": STATE["tenant"],
        "uptime_seconds": round(time.time() - STATE["started_at"], 1) if STATE["started_at"] else None,
        "resumes": STATE["resumes"],
        "requests_served": STATE["requests"],
        "path": request.path,
        "python": os.sys.version.split()[0],
    }


if __name__ == "__main__":
    app.serve()
