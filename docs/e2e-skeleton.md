# E2E Skeleton Runner

`proofloop-run` executes a full skeleton flow:

- load + validate config using Pydantic models
- select single problem or problem set
- create ephemeral per-attempt workspaces
- run provider adapter (`mock` currently)
  - or invoke external coding agents (`claude`, `codex`, `opencode`)
- execute verification command/script
- evaluate pass condition
- compute score (formula or builtin fallback)
- persist each attempt workspace under `runs/.../attempts/...`
  - persistence is delta-based (only files changed vs base snapshot + manifest)
- persist each external-agent stdout/stderr log under `runs/.../agent-logs/...`
- emit run report JSON

## Command

```bash
proofloop-run examples/legacy-port-tinyxml2.yaml
```

Optional output path:

```bash
proofloop-run examples/legacy-port-tinyxml2.yaml --output-dir runs/demo
```

## Current limitations (intentional skeleton)

- internal provider clients are mock adapters (no live API calls yet)
- no code-edit application from agent output yet
- external agent runtimes require local CLI binaries to be installed and authenticated
- runner executes locally in ephemeral directories (Docker/K8s orchestration adapter pending)

Despite this, the orchestration loop, verification, scoring, retries, reporting, and workspace retention are end-to-end functional.

When `runner.type: docker`, both agent runtime commands and verifier commands execute inside the configured Docker image against the mounted attempt workspace.

The provided `docker/devperf/Dockerfile` installs `opencode`, `claude`, and `codex` so external agent runtimes can execute in-container.
