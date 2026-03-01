from __future__ import annotations

import re
from typing import Any, Dict

from auto_optimize_spec.models import ProblemSpec
from auto_optimize_spec.results import VerificationResult


def eval_expr(expr: str, symbols: Dict[str, Any]) -> Any:
    compact = " ".join(line.strip() for line in expr.splitlines() if line.strip())
    normalized = compact.replace("&&", " and ").replace("||", " or ")
    normalized = re.sub(r"\btrue\b", "True", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bfalse\b", "False", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bnull\b", "None", normalized, flags=re.IGNORECASE)

    def norm(x: float) -> float:
        return float(x)

    globals_map = {"__builtins__": {}, "norm": norm, "min": min, "max": max, "abs": abs}
    locals_map: Dict[str, Any] = dict(symbols)

    for _ in range(16):
        try:
            return eval(normalized, globals_map, locals_map)
        except NameError as exc:
            m = re.search(r"name '([^']+)' is not defined", str(exc))
            if not m:
                raise
            missing = m.group(1)
            if missing in {"norm", "min", "max", "abs"}:
                raise
            locals_map[missing] = 0.0
    return eval(normalized, globals_map, locals_map)


def evaluate_pass_condition(
    problem: ProblemSpec, verification: VerificationResult
) -> bool:
    base = verification.passed
    if not problem.verification.pass_condition:
        return base

    symbols: Dict[str, Any] = {**verification.metrics}
    symbols.update(
        {"tests_passed": bool(verification.metrics.get("tests_passed", base))}
    )
    try:
        return bool(eval_expr(problem.verification.pass_condition, symbols))
    except Exception:
        return False
