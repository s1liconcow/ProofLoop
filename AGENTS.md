# ProofLoop Agent Instructions

## Python Environment
When working in this directory, always use the virtual environment for running python commands and scripts. Do **not** use the system python.

```bash
# Example: Running the runner explicitly with the venv python
./venv/bin/python -m auto_optimize_spec.run_job path/to/job.json

# Example: Running tests
source venv/bin/activate && pytest tests/
```

## Running ProofLoop Jobs
The orchestrator supports configurations for iterating over explicit `rounds`. Ensure you use `./venv/bin/python` to invoke the `run_job` runner.

## Using `sprite`
ProofLoop includes a `sprite` runner that handles remote environment provisioning using `sprites.dev`. `sprite exec` and `sprite create` correctly function via the CLI authenticated locally.
