from __future__ import annotations

import argparse
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from auto_optimize_spec.models import OptimizationJob
from auto_optimize_spec.runtime import load_data


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate optimization job config against schema and Pydantic model"
    )
    parser.add_argument("config", type=Path, help="Path to YAML/JSON job config")
    parser.add_argument(
        "--schema",
        type=Path,
        default=Path("schema/optimization-job.schema.json"),
        help="Path to JSON Schema file",
    )
    args = parser.parse_args()

    config_data = load_data(args.config)
    schema_data = json.loads(args.schema.read_text(encoding="utf-8"))

    validator = Draft202012Validator(schema_data)
    errors = sorted(validator.iter_errors(config_data), key=lambda e: e.path)
    if errors:
        for err in errors:
            path = ".".join(str(p) for p in err.path)
            print(f"[schema] {path or '<root>'}: {err.message}")
        return 1

    OptimizationJob.model_validate(config_data)
    print("Validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
