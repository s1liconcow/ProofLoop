from __future__ import annotations

from typing import Any, Dict, Tuple

from auto_optimize_spec.evaluation import eval_expr
from auto_optimize_spec.models import ProviderSpec, ScoringSpec


def infer_runtime_metric(metrics: Dict[str, Any], elapsed_seconds: float) -> float:
    for key in ("runtime_ms_median", "runtime_ms_p95", "runtime_ms", "latency_ms"):
        if key in metrics:
            return float(metrics[key])
    return elapsed_seconds * 1000.0


def infer_compute_metric(metrics: Dict[str, Any], elapsed_seconds: float) -> float:
    if "cpu_seconds" in metrics:
        return float(metrics["cpu_seconds"])
    return elapsed_seconds


def infer_pass_rate(metrics: Dict[str, Any], passed: bool) -> float:
    if "pass_rate" in metrics:
        return float(metrics["pass_rate"])
    if "behavioral_match_rate" in metrics:
        return float(metrics["behavioral_match_rate"])
    if "tests_passed" in metrics:
        return 1.0 if bool(metrics["tests_passed"]) else 0.0
    return 1.0 if passed else 0.0


def compute_agent_cost_usd(
    provider: ProviderSpec | None, input_tokens: int, output_tokens: int
) -> float:
    if not provider or not provider.pricing:
        return 0.0
    in_rate = provider.pricing.input_token_usd_per_million or 0.0
    out_rate = provider.pricing.output_token_usd_per_million or 0.0
    return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000.0


def score_attempt(
    scoring: ScoringSpec | None,
    metrics: Dict[str, Any],
    passed: bool,
    elapsed_seconds: float,
    agent_cost_usd: float,
) -> Tuple[float, Dict[str, float]]:
    runtime = infer_runtime_metric(metrics, elapsed_seconds)
    compute = infer_compute_metric(metrics, elapsed_seconds)
    pass_rate = infer_pass_rate(metrics, passed)
    memory = float(metrics.get("memory_mb", 0.0))
    energy = float(metrics.get("energy_kwh", 0.0))

    symbols: Dict[str, Any] = {**metrics}
    symbols.update(
        {
            "runtime_ms": runtime,
            "runtime_ms_median": metrics.get("runtime_ms_median", runtime),
            "cpu_seconds": compute,
            "agent_cost_usd": agent_cost_usd,
            "pass_rate": pass_rate,
            "memory_mb": memory,
            "energy_kwh": energy,
            "tests_passed": bool(metrics.get("tests_passed", passed)),
        }
    )

    breakdown = {
        "runtime": runtime,
        "compute": compute,
        "agent_cost": agent_cost_usd,
        "pass_rate": pass_rate,
        "memory": memory,
        "energy": energy,
    }

    if not scoring:
        base = 1000.0 if passed else 0.0
        return base - runtime - (10.0 * agent_cost_usd), breakdown

    if scoring.formula:
        value = float(eval_expr(scoring.formula, symbols))
        if scoring.mode == "maximize":
            return value, breakdown
        return -value, breakdown

    builtins = scoring.builtins or ["runtime", "compute", "agent_cost"]
    weights = scoring.weights or {}
    weight_sum = 0.0
    total = 0.0
    for name in builtins:
        w = float(weights.get(name, 1.0))
        weight_sum += w
        total += w * float(breakdown.get(name, 0.0))
    if weight_sum == 0:
        return 0.0, breakdown

    value = total / weight_sum
    if scoring.mode == "maximize":
        return value, breakdown
    return -value, breakdown
