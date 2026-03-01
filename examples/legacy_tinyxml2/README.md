# Legacy Port Example: tinyxml2 -> Rust

## Why tinyxml2

- Well-known C++ OSS XML parser with broad adoption.
- Small enough to be practical for iterative agent runs.
- Rich edge cases in XML entity handling make verification meaningful.

## Scope

Port a narrow but high-value slice first:

- Entity decoding and text normalization path from `tinyxml2.cpp`.
- Verify exact output equivalence on a fixed corpus.
- Optimize runtime after correctness is stable.

## Suggested repository layout

- `third_party/tinyxml2/` (pinned checkout, e.g. `10.0.0`)
- `src/` Rust port
- `examples/legacy_tinyxml2/data/` XML compatibility corpus
- `examples/legacy_tinyxml2/verify.sh` verifier entrypoint

## Verifier contract

`verify.sh` should emit machine-readable metrics consumed by the framework:

- `tests_passed` (bool)
- `behavioral_match_rate` (0..1)
- `runtime_ms_median` (number)
- `baseline_runtime_ms_median` (number)

The example job file is `examples/legacy-port-tinyxml2.yaml`.

## External agent runtime variant

Use `examples/legacy-port-tinyxml2-opencode-fireworks.yaml` to drive attempts through external CLIs:

- OpenCode (`opencode`)
- Codex (`codex`)
- Claude Code (`claude`)

For FireworksAI with OpenCode, export `FIREWORKS_API_KEY` before running.
