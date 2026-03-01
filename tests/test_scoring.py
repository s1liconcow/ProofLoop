from auto_optimize_spec.models import ScoringSpec
from auto_optimize_spec.scoring import score_attempt


def test_score_attempt_default_prefers_pass() -> None:
    score_pass, _ = score_attempt(None, {}, True, 0.1, 0.0)
    score_fail, _ = score_attempt(None, {}, False, 0.1, 0.0)
    assert score_pass > score_fail


def test_score_attempt_formula_minimize_semantics() -> None:
    scoring = ScoringSpec(
        mode="composite", formula="runtime_ms + cpu_seconds + agent_cost_usd"
    )
    score, breakdown = score_attempt(
        scoring=scoring,
        metrics={"runtime_ms": 10, "cpu_seconds": 2},
        passed=True,
        elapsed_seconds=1.0,
        agent_cost_usd=0.5,
    )
    assert score == -(10 + 2 + 0.5)
    assert breakdown["runtime"] == 10
