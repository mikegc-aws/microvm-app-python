"""A Strands Agents agent running inside a Lambda MicroVM.

Pattern:

- @app.startup (port 9000, 1-60s hook timeout) is the handshake: it
  receives per-VM identity and the agent's system prompt / task via the
  run-hook payload, and constructs the Agent object. Cheap — no model
  calls here. (startup is an alias of @app.run, Lambda's name for the hook.)
- @app.post("/invocations") (port 8080, no hook timeout) runs agent turns
  as ordinary app traffic. The Strands Agent object holds the conversation
  history in process memory, so it survives suspend/resume — the MicroVM
  snapshot IS the session store.

Run payload example:
    mvm run --internet --payload '{"system_prompt": "You are a pirate."}'

(--internet gives the VM egress to reach Amazon Bedrock, and the MicroVM
needs an execution role with bedrock:InvokeModel* permissions.)
"""

import time
import uuid

from strands import Agent, tool
from strands.models import BedrockModel

from microvm_app import MicroVMApp

app = MicroVMApp()

STATE = {
    "agent": None,        # built per-VM in @app.startup — never at build time
    "agent_id": None,
    "started_at": None,
    "resumes": 0,
}

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant running inside an AWS Lambda MicroVM. "
    "Answer concisely. Use your tools when they apply."
)


@tool
def microvm_status() -> dict:
    """Report the status of the MicroVM this agent is running in:
    its unique agent id, uptime in seconds, and how many times it has
    been suspended and resumed.

    DEMO ONLY — do not ship a tool like this in production. It hands
    anyone who can reach the endpoint a way to enumerate infrastructure
    internals (instance identity, uptime, lifecycle history) through the
    model. Real agent tools should expose task capabilities, not
    introspection of the runtime they happen to be running on.
    """
    return {
        "agent_id": STATE["agent_id"],
        "uptime_seconds": round(time.time() - STATE["started_at"], 1),
        "resumes": STATE["resumes"],
        "turns_in_memory": len(STATE["agent"].messages) if STATE["agent"] else 0,
    }


def build_agent(system_prompt: str) -> Agent:
    model = BedrockModel(model_id="us.anthropic.claude-opus-5")
    return Agent(
        model=model,
        system_prompt=system_prompt,
        tools=[microvm_status],
        callback_handler=None,  # no console streaming; we return the result
    )


@app.startup
def on_startup(ctx):
    # Identity and configuration only — the run hook has a 1-60s timeout
    # and traffic doesn't flow until it returns. Constructing the Agent
    # makes no network calls; the first model call happens per-invocation.
    STATE["agent_id"] = str(uuid.uuid4())
    STATE["started_at"] = time.time()
    payload = (ctx.payload_json() if ctx.payload else None) or {}
    STATE["agent"] = build_agent(payload.get("system_prompt", DEFAULT_SYSTEM_PROMPT))
    print(f"run: strands agent {STATE['agent_id']} ready (microvm={ctx.microvm_id})")


@app.resume
def on_resume():
    # The Agent object — including its full conversation history — was
    # frozen in the snapshot and is intact. boto3 re-establishes its HTTPS
    # connections automatically on the next call, so nothing to rebuild.
    STATE["resumes"] += 1
    turns = len(STATE["agent"].messages) if STATE["agent"] else 0
    print(f"resume: #{STATE['resumes']}, {turns} messages intact")


@app.suspend
def on_suspend():
    print("suspend: freezing agent mid-conversation")


@app.post("/invocations")
def invoke(request):
    # No hook timeout applies here — a turn can run as long as it needs.
    if STATE["agent"] is None:
        # Local dev convenience: /run hook hasn't fired yet.
        STATE["agent_id"] = STATE["agent_id"] or str(uuid.uuid4())
        STATE["started_at"] = STATE["started_at"] or time.time()
        STATE["agent"] = build_agent(DEFAULT_SYSTEM_PROMPT)
    prompt = (request.json() or {}).get("prompt", "")
    if not prompt:
        return 400, {"error": "POST a JSON body like {\"prompt\": \"...\"}"}
    result = STATE["agent"](prompt)
    return {
        "agent_id": STATE["agent_id"],
        "answer": str(result),
        "turns": len(STATE["agent"].messages),
        "resumes": STATE["resumes"],
    }


@app.get("/health")
def health(request):
    return {"status": "healthy", "agent_id": STATE["agent_id"]}


if __name__ == "__main__":
    app.serve()
