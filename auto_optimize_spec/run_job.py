from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from auto_optimize_spec.agent_runtime import run_external_agent, run_internal_agent
from auto_optimize_spec.evaluation import evaluate_pass_condition
from auto_optimize_spec.file_utils import (
    build_problem_base_snapshot,
    copy_workspace_snapshot,
    ensure_reporting_dir,
    persist_workspace,
)
from auto_optimize_spec.models import (
    AgentSpec,
    OptimizationJob,
    ProblemSetSpec,
    ProblemSpec,
    ProviderSpec,
    RoundSpec,
)
from auto_optimize_spec.results import AttemptResult, VerificationResult
from auto_optimize_spec.runtime import load_job
from auto_optimize_spec.scoring import compute_agent_cost_usd, score_attempt
from auto_optimize_spec.verification import run_command


def select_problems(target: ProblemSpec | ProblemSetSpec) -> List[ProblemSpec]:
    if isinstance(target, ProblemSpec):
        return [target]

    problems = list(target.problems)
    if target.selection == "all":
        return problems
    if target.selection == "sample":
        size = target.sample_size or len(problems)
        return problems[:size]
    if target.selection == "top_k":
        size = target.top_k or len(problems)
        return problems[:size]
    return problems


def _evaluate_agent_attempt(
    agent: AgentSpec,
    problem: ProblemSpec,
    base_snapshot: Path,
    temp_root: Path,
    job_env: Dict[str, str],
    output_dir: Path,
    live_agent_output: bool,
    job: OptimizationJob,
    providers: Dict[str, ProviderSpec],
    run_summary: Dict[str, Any],
    problem_attempts: List[Dict[str, Any]],
    iteration_idx: int,
    round_idx: int,
) -> AttemptResult | None:
    feedback: str | None = None
    max_attempts = min(agent.max_iterations, problem.max_attempts_per_agent)
    agent_best: AttemptResult | None = None

    for attempt_idx in range(1, max_attempts + 1):
        attempt_id = (
            f"iter{iteration_idx}-round{round_idx}-agent{agent.id}-attempt{attempt_idx}"
        )
        workspace = temp_root / attempt_id
        copy_workspace_snapshot(base_snapshot, workspace)
        (workspace / "tmp").mkdir(parents=True, exist_ok=True)

        provider: ProviderSpec | None = None
        if agent.runtime and agent.runtime.type in {
            "claude_code",
            "codex",
            "opencode",
        }:
            agent_execution = run_external_agent(
                agent=agent,
                problem=problem,
                attempt_idx=attempt_idx,
                feedback=feedback,
                workspace=workspace,
                base_env=job_env,
                output_dir=output_dir,
                live_output=live_agent_output,
                runner=job.runner,
            )
            if agent_execution.exit_code != 0:
                run_summary["warnings"].append(
                    f"Agent runtime failed: agent={agent.id} runtime={agent_execution.runtime_type} "
                    f"exit={agent_execution.exit_code}"
                )
        else:
            if not agent.provider_id or not agent.model:
                run_summary["warnings"].append(
                    f"Agent {agent.id} missing provider_id/model and no external runtime configured."
                )
                continue
            provider = providers.get(agent.provider_id)
            if not provider:
                run_summary["warnings"].append(
                    f"Agent {agent.id} references unknown provider_id={agent.provider_id}."
                )
                continue
            agent_execution = run_internal_agent(
                agent=agent,
                problem=problem,
                attempt_idx=attempt_idx,
                feedback=feedback,
                provider=provider,
            )

        verify_command = problem.verification.command
        if not verify_command and problem.verification.script:
            verify_command = f"bash {problem.verification.script.path}"
        if not verify_command:
            verify_command = "true"

        verification = run_command(
            command=verify_command,
            cwd=workspace,
            timeout_seconds=problem.verification.timeout_seconds,
            env=job_env,
            runner=job.runner,
        )

        passed = evaluate_pass_condition(problem, verification)
        persisted_workspace = persist_workspace(
            output_dir=output_dir,
            problem_id=problem.id,
            agent_id=f"{agent.id}-iter{iteration_idx}-round{round_idx}",
            attempt_idx=attempt_idx,
            workspace=workspace,
            base_snapshot=base_snapshot,
        )
        cost = compute_agent_cost_usd(
            provider,
            agent_execution.input_tokens,
            agent_execution.output_tokens,
        )
        if "agent_cost_usd" in verification.metrics:
            cost = float(verification.metrics["agent_cost_usd"])
        score, breakdown = score_attempt(
            scoring=problem.scoring,
            metrics=verification.metrics,
            passed=passed,
            elapsed_seconds=verification.elapsed_seconds,
            agent_cost_usd=cost,
        )

        attempt = AttemptResult(
            attempt_index=attempt_idx,
            agent_id=agent.id,
            provider_id=provider.id if provider else agent.provider_id,
            model=agent.model,
            draft_summary=agent_execution.summary,
            agent_runtime=agent_execution,
            verification=VerificationResult(
                passed=passed,
                metrics=verification.metrics,
                command_exit_code=verification.command_exit_code,
                stdout=verification.stdout,
                stderr=verification.stderr,
                elapsed_seconds=verification.elapsed_seconds,
            ),
            score=score,
            score_breakdown=breakdown,
            agent_cost_usd=cost,
            workspace=str(persisted_workspace),
        )

        # We append to the shared problem_attempts list safely (we'll process this later or pass a thread-safe structure,
        # but since list append is thread-safe in CPython, this is generally okay. Better to return it).
        problem_attempts.append(asdict(attempt))

        if agent_best is None or attempt.score > agent_best.score:
            agent_best = attempt

        if passed:
            break

        feedback = (
            f"Runtime exit={agent_execution.exit_code}; verifier exit={verification.command_exit_code}. "
            f"Metrics={verification.metrics}. Runtime stderr={agent_execution.stderr[:220]}. "
            f"Verifier stderr={verification.stderr[:220]}"
        )
    return agent_best


def _max_round_workers(job: OptimizationJob, round_agents: List[AgentSpec]) -> int:
    if not round_agents:
        return 1
    return max(1, min(job.runner.parallelism, len(round_agents)))


def _effective_round_execution(round_spec: RoundSpec) -> str:
    if round_spec.mode == "collaborative":
        return "sequential"
    return round_spec.execution


def _write_run_report(output_dir: Path, run_summary: Dict[str, Any]) -> None:
    run_summary["updated_at"] = datetime.now(tz=timezone.utc).isoformat()
    tmp_path = output_dir / "run-report.json.tmp"
    report_path = output_dir / "run-report.json"
    tmp_path.write_text(json.dumps(run_summary, indent=2), encoding="utf-8")
    tmp_path.replace(report_path)


def _round_summary(
    *,
    iteration_idx: int,
    round_idx: int,
    round_spec: RoundSpec,
    round_results: List[AttemptResult],
    best_overall: AttemptResult | None,
) -> Dict[str, Any]:
    best_in_round = max(round_results, key=lambda r: r.score) if round_results else None
    return {
        "iteration_index": iteration_idx,
        "round_index": round_idx,
        "mode": round_spec.mode,
        "execution": round_spec.execution,
        "effective_execution": _effective_round_execution(round_spec),
        "agent_ids": list(round_spec.agents),
        "attempts_completed": len(round_results),
        "best_attempt_in_round": asdict(best_in_round) if best_in_round else None,
        "best_attempt_overall": asdict(best_overall) if best_overall else None,
        "scoreboard": [
            {
                "agent_id": result.agent_id,
                "attempt_index": result.attempt_index,
                "score": result.score,
                "passed": result.verification.passed,
                "agent_cost_usd": result.agent_cost_usd,
                "verification_elapsed_seconds": result.verification.elapsed_seconds,
                "metrics": result.verification.metrics,
                "workspace": result.workspace,
            }
            for result in sorted(round_results, key=lambda r: r.score, reverse=True)
        ],
    }


def run_job(
    job: OptimizationJob,
    job_path: Path,
    output_dir: Path,
    live_agent_output: bool = True,
) -> Dict[str, Any]:
    providers = {p.id: p for p in job.providers}
    selected_problems = select_problems(job.job.target)
    job_env = job.job.environment.env if job.job.environment else {}

    run_summary: Dict[str, Any] = {
        "job_id": job.job.id,
        "job_name": job.job.name,
        "schema_version": job.schema_version,
        "status": "running",
        "started_at": datetime.now(tz=timezone.utc).isoformat(),
        "warnings": [],
        "problems": [],
    }
    _write_run_report(output_dir, run_summary)

    iterations = job.orchestrator.improvement_iterations if job.orchestrator else 1
    rounds = (
        job.orchestrator.rounds if job.orchestrator and job.orchestrator.rounds else []
    )

    if not rounds and job.agents:
        rounds = [
            RoundSpec(
                agents=[a.id for a in job.agents],
                mode="competitive",
                execution="sequential",
            )
        ]

    agents_by_id = {a.id: a for a in job.agents}

    for problem in selected_problems:
        with tempfile.TemporaryDirectory(prefix=f"proofloop-{problem.id}-") as temp_dir:
            temp_root = Path(temp_dir)
            initial_base_snapshot = temp_root / "base"
            build_problem_base_snapshot(
                job_file_dir=job_path.parent,
                problem=problem,
                environment=job.job.environment,
                dst_root=initial_base_snapshot,
                warnings=run_summary["warnings"],
            )

            if job.job.environment:
                for setup_cmd in job.job.environment.setup_commands:
                    setup_proc = subprocess.run(
                        ["bash", "-lc", setup_cmd],
                        cwd=str(initial_base_snapshot),
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    if setup_proc.returncode != 0:
                        run_summary["warnings"].append(
                            "Setup command failed "
                            f"(problem={problem.id}, exit={setup_proc.returncode}): {setup_cmd}\n"
                            f"stderr: {setup_proc.stderr[:400]}"
                        )

            problem_attempts: List[Dict[str, Any]] = []
            problem_rounds: List[Dict[str, Any]] = []
            best_overall: AttemptResult | None = None
            current_base_snapshot = initial_base_snapshot
            problem_summary: Dict[str, Any] = {
                "problem_id": problem.id,
                "title": problem.title,
                "attempts": problem_attempts,
                "rounds": problem_rounds,
                "best_attempt": None,
            }
            run_summary["problems"].append(problem_summary)
            _write_run_report(output_dir, run_summary)

            for iter_idx in range(1, iterations + 1):
                for round_idx, round_spec in enumerate(rounds, start=1):
                    round_agents = [
                        agents_by_id[aid]
                        for aid in round_spec.agents
                        if aid in agents_by_id
                    ]
                    if not round_agents:
                        continue

                    round_results: List[AttemptResult] = []
                    effective_execution = _effective_round_execution(round_spec)
                    round_base_snapshot = current_base_snapshot

                    if (
                        round_spec.mode == "collaborative"
                        and round_spec.execution == "concurrent"
                    ):
                        run_summary["warnings"].append(
                            "Collaborative rounds execute sequentially to allow workspace handoff "
                            f"(problem={problem.id}, iteration={iter_idx}, round={round_idx})."
                        )

                    if effective_execution == "concurrent":
                        with concurrent.futures.ThreadPoolExecutor(
                            max_workers=_max_round_workers(job, round_agents)
                        ) as executor:
                            future_to_agent = {
                                executor.submit(
                                    _evaluate_agent_attempt,
                                    agent,
                                    problem,
                                    round_base_snapshot,
                                    temp_root,
                                    job_env,
                                    output_dir,
                                    live_agent_output,
                                    job,
                                    providers,
                                    run_summary,
                                    problem_attempts,
                                    iter_idx,
                                    round_idx,
                                ): agent
                                for agent in round_agents
                            }
                            for future in concurrent.futures.as_completed(
                                future_to_agent
                            ):
                                res = future.result()
                                if res:
                                    round_results.append(res)
                    else:
                        for agent in round_agents:
                            res = _evaluate_agent_attempt(
                                agent,
                                problem,
                                round_base_snapshot,
                                temp_root,
                                job_env,
                                output_dir,
                                live_agent_output,
                                job,
                                providers,
                                run_summary,
                                problem_attempts,
                                iter_idx,
                                round_idx,
                            )
                            if res:
                                round_results.append(res)
                                if (
                                    round_spec.mode == "collaborative"
                                    and res.workspace
                                    and Path(res.workspace).exists()
                                ):
                                    round_base_snapshot = Path(res.workspace)

                    if round_results:
                        best_in_round = max(round_results, key=lambda r: r.score)

                        # Only update the base snapshot if we actually produced a valid output
                        if (
                            best_in_round.workspace
                            and Path(best_in_round.workspace).exists()
                        ):
                            current_base_snapshot = Path(best_in_round.workspace)

                        if (
                            best_overall is None
                            or best_in_round.score > best_overall.score
                        ):
                            best_overall = best_in_round
                    problem_rounds.append(
                        _round_summary(
                            iteration_idx=iter_idx,
                            round_idx=round_idx,
                            round_spec=round_spec,
                            round_results=round_results,
                            best_overall=best_overall,
                        )
                    )
                    problem_summary["best_attempt"] = (
                        asdict(best_overall) if best_overall else None
                    )
                    _write_run_report(output_dir, run_summary)
            problem_summary["best_attempt"] = (
                asdict(best_overall) if best_overall else None
            )
            _write_run_report(output_dir, run_summary)

    run_summary["status"] = "completed"
    run_summary["finished_at"] = datetime.now(tz=timezone.utc).isoformat()
    _write_run_report(output_dir, run_summary)
    return run_summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Execute an end-to-end skeleton run for an optimization job"
    )
    parser.add_argument("config", type=Path, help="Path to YAML/JSON job config")
    parser.add_argument(
        "--output-dir", type=Path, default=None, help="Override report output directory"
    )
    parser.add_argument(
        "--no-stream-agent-output",
        action="store_true",
        help="Disable live agent stdout/stderr streaming to terminal (logs are still saved).",
    )
    args = parser.parse_args()

    job = load_job(args.config)
    out_dir = ensure_reporting_dir(job, args.output_dir)
    result = run_job(
        job=job,
        job_path=args.config.resolve(),
        output_dir=out_dir,
        live_agent_output=not args.no_stream_agent_output,
    )

    print(f"Run complete: {out_dir / 'run-report.json'}")
    print(f"Problems executed: {len(result['problems'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
