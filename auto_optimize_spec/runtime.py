from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from auto_optimize_spec.models import OptimizationJob


def load_data(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        return yaml.safe_load(text)
    return json.loads(text)


def load_job(path: Path) -> OptimizationJob:
    return OptimizationJob.model_validate(load_data(path))
