from auto_optimize_spec.evaluation import eval_expr


def test_eval_expr_handles_json_booleans_and_missing_symbols() -> None:
    symbols = {"tests_passed": True, "runtime_ms": 12}
    expr = "tests_passed == true && behavioral_match_rate >= 0.0 && runtime_ms < 20"
    assert eval_expr(expr, symbols) is True


def test_eval_expr_multiline_formula() -> None:
    symbols = {"a": 2, "b": 4}
    expr = """
    0.5*norm(a)
    + 0.5*norm(b)
    """
    assert eval_expr(expr, symbols) == 3.0
