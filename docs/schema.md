# Optimization Job Schema (v1)

This schema defines how a user describes optimization tasks for a multi-agent LLM framework with verifiable outcomes.

## Design goals

- Support one problem or a problem set.
- Separate problem definition from verification and scoring.
- Support optional Docker/artifacts/setup instructions.
- Support any LLM provider through provider adapters.
- Support external coding agents (`Claude Code`, `Codex`, `OpenCode`) that already manage tool use and filesystem edits.
- Enable multi-agent orchestration with retries and feedback loops.

## Top-level keys

- `schema_version`: version string (e.g., `v1`).
- `job`: the task target and environment.
- `providers`: provider/model registry used by agents.
  - Optional when agents use external runtimes (e.g. OpenCode with FireworksAI configured in env vars).
- `agents`: N agent personas and model assignments.
- `runner`: execution backend details for ephemeral environments.
- `orchestrator`: selection/retry/feedback policy.
- `reporting`: output format and artifact options.

## Problem vs ProblemSet

`job.target` can be either:

- `ProblemSpec` for a single optimization challenge.
- `ProblemSetSpec` for a batch of related challenges.

Each problem includes:

- `goal`: optimization objective.
- `input_contract` and `output_contract`: how data enters/leaves.
- `verification`: machine-checkable pass/fail + metrics.
- `scoring` (optional): score function for ranking valid solutions.
- `default_prompt_appendix` (optional): extra prompt lines appended to the runner's default per-problem instructions.

## Verification model

`verification.type` supports command/test/script/simulator/api/custom.

The verifier can collect metrics (`collect_metrics`) used by:

- `pass_condition` (hard gate)
- `scoring.formula` (ranking)

## Built-in scoring components

Supported built-ins:

- `runtime`
- `compute`
- `agent_cost`
- `pass_rate`
- `memory`
- `energy`

You can combine built-ins with custom verifier metrics in `formula`.

## Multi-provider support

`providers` is an adapter registry. Agents reference providers by `provider_id` and pick a model by name. This allows mixing providers in one run (e.g., OpenAI + Anthropic + local Ollama).

## Agent runtime backends

Each agent can choose one of these runtime modes:

- `internal_mock` (default): current built-in adapter path (`provider_id` + `model` required).
- `claude_code`: invoke local Claude Code CLI in workspace.
- `codex`: invoke local Codex CLI in workspace.
- `opencode`: invoke local OpenCode CLI in workspace.

For external runtimes, set per-agent runtime config:

- `executable`, `args`, `prompt_mode` (`stdin` or `arg`)
- `env` (for provider selection/API config, e.g. FireworksAI)
- `timeout_seconds`

## Runtime model

`runner` is where agent attempts execute (Docker/Kubernetes/local/remote/custom). `ephemeral=true` is the default for isolation. Resource limits and network policy are first-class.

For `runner.type: docker`, set `runner.image` to the verification image tag (for example, `proofloop/devperf:latest`). Verification commands run inside that container via the Python Docker SDK.

## Files

- Canonical JSON Schema: `schema/optimization-job.schema.json`
- Python models: `auto_optimize_spec/models.py`
- Example single problem: `examples/single-problem.yaml`
- Example problem set: `examples/problem-set.yaml`
