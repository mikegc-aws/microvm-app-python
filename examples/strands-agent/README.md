# Strands Agents on a Lambda MicroVM

A [Strands Agents](https://strandsagents.com) agent served from a MicroVM,
with the conversation living in process memory across suspend/resume.

The split:

- `@app.run` (hooks port, 1–60s timeout) builds the `Agent` object from the
  run-hook payload — system prompt in, no model calls.
- `@app.post("/invocations")` (app port, no hook timeout) runs agent turns.
  The `Agent` object holds the conversation history, so the MicroVM snapshot
  *is* the session store: suspend, resume, and the agent still remembers
  every turn. No session database, no rehydration.

The agent has one custom tool, `microvm_status`, so you can ask it about its
own VM (uptime, resume count, turns held in memory).

## Run locally

Uses your local AWS credentials for Amazon Bedrock:

```bash
pip install microvm-app strands-agents
python app.py &
curl -X POST localhost:9000/aws/lambda-microvms/runtime/v1/run \
     -d '{"microvmId":"local","runHookPayload":"{\"system_prompt\":\"You are terse.\"}"}'
curl -X POST localhost:8080/invocations -d '{"prompt":"What is Firecracker?"}'
```

## Deploy

The MicroVM needs internet egress (to reach Bedrock) and an execution role
with `bedrock:InvokeModel*`:

```bash
mvm deploy --name mvm-strands-agent
mvm run --internet --execution-role <your-bedrock-role> \
        --payload '{"system_prompt":"You are a terse assistant."}'
mvm invoke <microvm-id> /invocations -d '{"prompt":"Hello!"}'

# The part worth seeing: suspend costs $0, resume keeps the conversation
mvm suspend <microvm-id>
mvm resume <microvm-id>
mvm invoke <microvm-id> /invocations -d '{"prompt":"What did I ask you first?"}'
```
