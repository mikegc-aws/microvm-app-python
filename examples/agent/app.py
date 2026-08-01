"""An agent-shaped MicroVM app.

The split that matters:

- @app.startup (port 9000, 1-60s timeout) is the handshake, not the work.
  It receives the agent's identity/task context and returns immediately.
  (startup is an alias of @app.run — "run" is Lambda's name for the hook.)
- @app.post("/invocations") (port 8080, no hook timeout) is where the
  agent actually thinks. Each invocation is ordinary app traffic.

This mirrors the shape of Bedrock AgentCore Runtime's /invocations
endpoint, one layer down. The "agent loop" here is a stub — swap in your
model calls (e.g. boto3 bedrock-runtime) where marked.
"""

import time
import uuid

from microvm_app import MicroVMApp

app = MicroVMApp()

SESSION = {
    "agent_id": None,      # per-VM unique — generated at startup, never at build time
    "task": None,          # delivered via the run-hook payload
    "history": [],         # process memory IS the agent's memory; survives suspend/resume
    "resumes": 0,
}


@app.startup
def on_startup(ctx):
    # Cheap identity work only — this hook has a 1-60 second timeout and
    # traffic doesn't flow until it returns 200. No model calls here.
    SESSION["agent_id"] = str(uuid.uuid4())
    payload = ctx.payload_json() if ctx.payload else {}
    SESSION["task"] = (payload or {}).get("task", "general assistant")
    print(f"run: agent {SESSION['agent_id']} on task {SESSION['task']!r}")


@app.resume
def on_resume():
    # Conversation history is still in memory — nothing to rehydrate.
    # Refresh anything that expires: credentials, connections.
    SESSION["resumes"] += 1
    print(f"resume: #{SESSION['resumes']}, {len(SESSION['history'])} turns intact")


@app.suspend
def on_suspend():
    print(f"suspend: freezing with {len(SESSION['history'])} turns in memory")


def agent_loop(prompt: str) -> str:
    """The actual agent turn. No hook timeout applies here — this can run
    as long as an invocation needs. Replace the body with your model call:

        bedrock = boto3.client("bedrock-runtime")
        response = bedrock.converse(modelId=..., messages=[...])
    """
    time.sleep(0.1)  # stand-in for model latency
    return f"[{SESSION['task']}] considered {prompt!r} " \
           f"with {len(SESSION['history'])} prior turns of context"


@app.post("/invocations")
def invoke(request):
    body = request.json() or {}
    prompt = body.get("prompt", "")
    answer = agent_loop(prompt)
    SESSION["history"].append({"prompt": prompt, "answer": answer})
    return {
        "agent_id": SESSION["agent_id"],
        "answer": answer,
        "turns": len(SESSION["history"]),
        "resumes": SESSION["resumes"],
    }


@app.get("/health")
def health(request):
    return {"status": "healthy", "agent_id": SESSION["agent_id"]}


if __name__ == "__main__":
    app.serve()
