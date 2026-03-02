# proofloop

Schema and typed Python models for defining verifiable optimization jobs executed by multi-agent LLM systems.

## What it covers

- Single problem or problem set definitions
- Verifier configuration (command/test/script/simulator/api)
- Optional scoring formulas with built-ins (`runtime`, `compute`, `agent_cost`, etc.)
- Provider-agnostic model registry
- Multi-agent persona configuration
- Ephemeral runner configuration (Docker/K8s/local/remote)
- Artifacts and Dockerfile-driven environment setup

## Files

- `schema/optimization-job.schema.json`: canonical JSON Schema
- `auto_optimize_spec/models.py`: Pydantic models
- `auto_optimize_spec/validate.py`: validation CLI
- `auto_optimize_spec/run_job.py`: end-to-end skeleton runner CLI
- `examples/single-problem.yaml`: single problem example
- `examples/problem-set.yaml`: problem set example
- `examples/legacy-port-tinyxml2.yaml`: legacy C++ to Rust port example (tinyxml2)
- `examples/legacy-port-tinyxml2-opencode-fireworks.yaml`: external runtime example (OpenCode + FireworksAI)
- `examples/osagefs-cargo-test-bench-codex.yaml`: filesystem optimization example using `cargo test` + `cargo bench`
- `docs/schema.md`: short schema guide
- `docs/e2e-skeleton.md`: runner behavior and usage

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
proofloop-validate examples/single-problem.yaml
proofloop-run examples/legacy-port-tinyxml2.yaml
proofloop-run examples/osagefs-cargo-test-bench-codex.yaml
```
