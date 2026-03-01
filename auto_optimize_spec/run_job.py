from __future__ import annotations

import argparse
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
    copy_artifact_into_root,
    copy_workspace_snapshot,
    ensure_reporting_dir,
    persist_workspace,
)
from auto_optimize_spec.models import (
    OptimizationJob,
    ProblemSetSpec,
    ProblemSpec,
    ProviderSpec,
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
        "started_at": datetime.now(tz=timezone.utc).isoformat(),
        "warnings": [],
        "problems": [],
    }

    for problem in selected_problems:
        with tempfile.TemporaryDirectory(prefix=f"proofloop-{problem.id}-") as temp_dir:
            temp_root = Path(temp_dir)
            base_snapshot = temp_root / "base"
            copy_workspace_snapshot(Path.cwd(), base_snapshot)

            if job.job.environment:
                for artifact in job.job.environment.artifacts:
                    copied = copy_artifact_into_root(
                        job_path.parent, artifact.path, artifact.mount_to, base_snapshot
                    )
                    if not copied:
                        run_summary["warnings"].append(
                            f"Artifact not found: path={artifact.path} mount_to={artifact.mount_to}"
                        )

                for setup_cmd in job.job.environment.setup_commands:
                    subprocess.run(
                        ["bash", "-lc", setup_cmd],
                        cwd=str(base_snapshot),
                        check=False,
                        capture_output=True,
                    )

            problem_attempts: List[Dict[str, Any]] = []
            best: AttemptResult | None = None

            for agent in job.agents:
                feedback: str | None = None
                max_attempts = min(agent.max_iterations, problem.max_attempts_per_agent)

                for attempt_idx in range(1, max_attempts + 1):
                    workspace = temp_root / f"attempt-{agent.id}-{attempt_idx}"
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
                        agent_id=agent.id,
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

                    attempt_dict = asdict(attempt)
                    problem_attempts.append(attempt_dict)

                    if best is None or attempt.score > best.score:
                        best = attempt

                    if passed:
                        break

                    feedback = (
                        f"Runtime exit={agent_execution.exit_code}; verifier exit={verification.command_exit_code}. "
                        f"Metrics={verification.metrics}. Runtime stderr={agent_execution.stderr[:220]}. "
                        f"Verifier stderr={verification.stderr[:220]}"
                    )

            run_summary["problems"].append(
                {
                    "problem_id": problem.id,
                    "title": problem.title,
                    "attempts": problem_attempts,
                    "best_attempt": asdict(best) if best else None,
                }
            )

    run_summary["finished_at"] = datetime.now(tz=timezone.utc).isoformat()
    (output_dir / "run-report.json").write_text(
        json.dumps(run_summary, indent=2), encoding="utf-8"
    )
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
