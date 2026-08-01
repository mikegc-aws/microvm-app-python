# microvm-app

A zero-dependency Python framework for **AWS Lambda MicroVMs**.

Lambda drives a MicroVM's lifecycle by calling HTTP hook endpoints your
application must expose — specific POST paths, specific ports, specific
status-code semantics. Getting that plumbing right means reading the hook
spec and standing up a web server before you write a line of business logic.

This library removes that work. Decorate a function with `@app.run` and it
becomes your VM's startup hook; add `@app.entrypoint` for your application
traffic; call `app.serve()`. The library serves all the lifecycle hook
endpoints and your app on a lightweight stdlib web server — correct paths,
ports, and status codes included, with sensible defaults for every hook you
don't implement.

Looking for deployment tooling? The companion CLI lives at
[mikegc-aws/mvm-cli](https://github.com/mikegc-aws/mvm-cli) — it zips your
app and builds the MicroVM image server-side (no docker required).

**Other languages** (previews; this repo is the reference implementation):
[TypeScript/Node](https://github.com/mikegc-aws/microvm-app-js) ·
[Go](https://github.com/mikegc-aws/microvm-app-go)

## The 30-second version

```python
# app.py
from microvm_app import MicroVMApp

app = MicroVMApp()

@app.run
def on_run(ctx):
    print(f"MicroVM {ctx.microvm_id} started, payload: {ctx.payload}")

@app.entrypoint
def handler(request):
    return {"hello": "world"}

if __name__ == "__main__":
    app.serve()
```

## How Lambda MicroVMs work (what this library maps onto)

- Lambda calls **lifecycle hooks** as `POST` requests on
  `/aws/lambda-microvms/runtime/v1/<hook>` at a port you configure
  (default **9000**).
- External traffic arrives on the VM's dedicated HTTPS endpoint and is routed
  to port **8080** by default (`X-aws-proxy-port` header overrides).
- Traffic is only forwarded **after your `/run` hook returns 200**.
- Images are snapshots: a VM starts from pre-initialized memory+disk state, so
  anything unique (IDs, seeds, credentials) must be generated in `@app.run`.

| Decorator | Hook | When |
|---|---|---|
| `@app.ready` | `/ready` | During image build — return `False` for 503 ("not ready, retry"), truthy/`None` when snapshot-ready |
| `@app.validate` | `/validate` | After build, on a fresh VM. Exercise real code paths here — Lambda prefetches the snapshot pages you touch |
| `@app.run` | `/run` | VM started. Receives `RunContext` with `microvm_id` and the `--run-hook-payload` string (`ctx.payload_json()` parses it) |
| `@app.resume` | `/resume` | VM resumed from suspend — refresh credentials, reconnect |
| `@app.suspend` | `/suspend` | Before suspend — flush writes, close connections |
| `@app.terminate` | `/terminate` | Before terminate — final cleanup |

All hooks are optional; unregistered hooks return 200 immediately. Handlers
can take zero arguments or one; sync or `async`. Exceptions return 500 (which
correctly blocks traffic/build for run/ready/validate).

## Traffic handling

```python
@app.entrypoint          # catch-all handler
def handler(request):    # request.method/.path/.headers/.query/.json()/.text
    return {"any": "json"}          # dict/list -> JSON
    # or "text", 204, (201, {...}), b"bytes", Response(418, "teapot")

@app.get("/health")      # explicit routes win over the entrypoint
def health(request):
    return {"status": "healthy"}
```

Module-style also works for tiny scripts:

```python
from microvm_app import run, entrypoint, serve

@run
def on_run(ctx): ...

@entrypoint
def handler(request): ...

serve()
```

## Ports

`MicroVMApp(hook_port=9000, app_port=8080)` — both listeners run from one
`serve()` call. Set them equal to serve everything on a single port.
Env overrides: `MICROVM_HOOK_PORT`, `MICROVM_APP_PORT`.

## Install

```bash
pip install microvm-app          # once published; until then:
pip install git+https://github.com/mikegc-aws/microvm-app-python.git
```

The library is pure stdlib — it adds zero dependencies to your MicroVM image.

## Local development

Run your app locally and poke the hooks with curl — no AWS needed:

```bash
python app.py &
curl -X POST localhost:9000/aws/lambda-microvms/runtime/v1/run \
     -d '{"microvmId":"local","runHookPayload":"{\"tenant\":\"dev\"}"}'
curl localhost:8080/
```

## Tests

```bash
pip install -e ".[dev]" && pytest
```

## Examples

- [`examples/hello`](examples/hello/app.py) — full lifecycle app (all six
  hooks, per-VM unique state, health route). Deployed end-to-end it answers
  with its per-instance ID, tenant from the run payload, and resume count.
- [`examples/agent`](examples/agent/app.py) — an agent-shaped app: the run
  hook receives the agent's task context (cheap, within the 60s hook
  timeout), and `@app.post("/invocations")` runs the agent loop as ordinary
  app traffic with no hook timeout. Conversation history lives in process
  memory and survives suspend/resume.
- [`examples/strands-agent`](examples/strands-agent/) — the same pattern
  with a real [Strands Agents](https://strandsagents.com) agent calling
  Claude on Amazon Bedrock, including a demo-only custom tool the agent
  uses to report on its own MicroVM (illustrative — see the example's
  README before copying it). Deployed and verified end-to-end: model
  calls, tool use, and conversation memory across suspend/resume.
