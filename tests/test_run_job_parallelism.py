from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict
from pathlib import Path

from auto_optimize_spec.models import (
    AgentSpec,
    InputContract,
    JobSpec,
    OptimizationJob,
    OrchestratorSpec,
    OutputContract,
    ProblemSpec,
    RoundSpec,
    RunnerSpec,
    VerificationSpec,
)
from auto_optimize_spec.results import (
    AgentExecutionResult,
    AttemptResult,
    VerificationResult,
)
from auto_optimize_spec.run_job import run_job


def _build_job(parallelism: int) -> OptimizationJob:
    problem = ProblemSpec(
        id="p1",
        title="Problem 1",
        goal="Do the thing",
        input_contract=InputContract(format="stdin"),
        output_contract=OutputContract(format="patch"),
        verification=VerificationSpec(type="command", command="true"),
        max_attempts_per_agent=1,
    )
    agents = [
        AgentSpec(id=f"a{i}", persona=f"agent {i}", provider_id="prov", model="m1")
        for i in range(1, 4)
    ]
    return OptimizationJob(
        job=JobSpec(id="job1", name="Job 1", target=problem),
        agents=agents,
        providers=[],
        runner=RunnerSpec(type="docker", parallelism=parallelism),
        orchestrator=OrchestratorSpec(
            improvement_iterations=1,
            rounds=[
                RoundSpec(
                    agents=[agent.id for agent in agents],
                    execution="concurrent",
                )
            ],
        ),
    )


def _build_collaborative_job() -> OptimizationJob:
    problem = ProblemSpec(
        id="p1",
        title="Problem 1",
        goal="Do the thing",
        input_contract=InputContract(format="stdin"),
        output_contract=OutputContract(format="patch"),
        verification=VerificationSpec(type="command", command="true"),
        max_attempts_per_agent=1,
    )
    agents = [
        AgentSpec(id="a1", persona="agent 1", provider_id="prov", model="m1"),
        AgentSpec(id="a2", persona="agent 2", provider_id="prov", model="m1"),
    ]
    return OptimizationJob(
        job=JobSpec(id="job1", name="Job 1", target=problem),
        agents=agents,
        providers=[],
        runner=RunnerSpec(type="docker", parallelism=2),
        orchestrator=OrchestratorSpec(
            improvement_iterations=1,
            rounds=[
                RoundSpec(
                    agents=[agent.id for agent in agents],
                    mode="collaborative",
                    execution="concurrent",
                )
            ],
        ),
    )


def test_run_job_limits_concurrent_rounds_to_runner_parallelism(
    monkeypatch, tmp_path: Path
) -> None:
    peak_active = 0
    active = 0
    lock = threading.Lock()

    def fake_build_problem_base_snapshot(
        job_file_dir, problem, environment, dst_root, warnings
    ) -> None:
        del job_file_dir, problem, environment, warnings
        dst_root.mkdir(parents=True, exist_ok=True)

    def fake_evaluate_agent_attempt(
        agent,
        problem,
        base_snapshot,
        temp_root,
        job_env,
        output_dir,
        live_agent_output,
        job,
        providers,
        run_summary,
        problem_attempts,
        iteration_idx,
        round_idx,
    ):
        del (
            problem,
            base_snapshot,
            job_env,
            output_dir,
            live_agent_output,
            job,
            providers,
            run_summary,
            iteration_idx,
            round_idx,
        )
        nonlocal active, peak_active
        with lock:
            active += 1
            peak_active = max(peak_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1

        workspace = temp_root / f"{agent.id}-workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        attempt = AttemptResult(
            attempt_index=1,
            agent_id=agent.id,
            provider_id=agent.provider_id,
            model=agent.model,
            draft_summary=f"{agent.id} summary",
            agent_runtime=AgentExecutionResult(
                runtime_type="internal_mock",
                command="",
                summary="ok",
                input_tokens=0,
                output_tokens=0,
                exit_code=0,
                stdout="",
                stderr="",
                elapsed_seconds=0.01,
            ),
            verification=VerificationResult(
                passed=True,
                metrics={},
                command_exit_code=0,
                stdout="",
                stderr="",
                elapsed_seconds=0.01,
            ),
            score=1.0,
            score_breakdown={},
            agent_cost_usd=0.0,
            workspace=str(workspace),
        )
        problem_attempts.append(asdict(attempt))
        return attempt

    monkeypatch.setattr(
        "auto_optimize_spec.run_job.build_problem_base_snapshot",
        fake_build_problem_base_snapshot,
    )
    monkeypatch.setattr(
        "auto_optimize_spec.run_job._evaluate_agent_attempt",
        fake_evaluate_agent_attempt,
    )

    output_dir = tmp_path / "out"
    output_dir.mkdir()

    run_job(
        job=_build_job(parallelism=2),
        job_path=tmp_path / "job.yaml",
        output_dir=output_dir,
        live_agent_output=False,
    )

    assert peak_active == 2
    report = json.loads((output_dir / "run-report.json").read_text(encoding="utf-8"))
    assert report["status"] == "completed"
    assert len(report["problems"]) == 1
    assert len(report["problems"][0]["rounds"]) == 1
    round_summary = report["problems"][0]["rounds"][0]
    assert round_summary["execution"] == "concurrent"
    assert round_summary["effective_execution"] == "concurrent"
    assert round_summary["attempts_completed"] == 3
    assert len(round_summary["scoreboard"]) == 3


def test_collaborative_round_chains_workspaces_between_agents(
    monkeypatch, tmp_path: Path
) -> None:
    base_snapshots_seen: list[str] = []

    def fake_build_problem_base_snapshot(
        job_file_dir, problem, environment, dst_root, warnings
    ) -> None:
        del job_file_dir, problem, environment, warnings
        dst_root.mkdir(parents=True, exist_ok=True)

    def fake_evaluate_agent_attempt(
        agent,
        problem,
        base_snapshot,
        temp_root,
        job_env,
        output_dir,
        live_agent_output,
        job,
        providers,
        run_summary,
        problem_attempts,
        iteration_idx,
        round_idx,
    ):
        del (
            problem,
            job_env,
            output_dir,
            live_agent_output,
            job,
            providers,
            run_summary,
            iteration_idx,
            round_idx,
        )
        base_snapshots_seen.append(Path(base_snapshot).name)
        workspace = temp_root / f"{agent.id}-workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        attempt = AttemptResult(
            attempt_index=1,
            agent_id=agent.id,
            provider_id=agent.provider_id,
            model=agent.model,
            draft_summary=f"{agent.id} summary",
            agent_runtime=AgentExecutionResult(
                runtime_type="internal_mock",
                command="",
                summary="ok",
                input_tokens=0,
                output_tokens=0,
                exit_code=0,
                stdout="",
                stderr="",
                elapsed_seconds=0.01,
            ),
            verification=VerificationResult(
                passed=True,
                metrics={},
                command_exit_code=0,
                stdout="",
                stderr="",
                elapsed_seconds=0.01,
            ),
            score=1.0,
            score_breakdown={},
            agent_cost_usd=0.0,
            workspace=str(workspace),
        )
        problem_attempts.append(asdict(attempt))
        return attempt

    monkeypatch.setattr(
        "auto_optimize_spec.run_job.build_problem_base_snapshot",
        fake_build_problem_base_snapshot,
    )
    monkeypatch.setattr(
        "auto_optimize_spec.run_job._evaluate_agent_attempt",
        fake_evaluate_agent_attempt,
    )

    output_dir = tmp_path / "out-collab"
    output_dir.mkdir()

    run_job(
        job=_build_collaborative_job(),
        job_path=tmp_path / "job.yaml",
        output_dir=output_dir,
        live_agent_output=False,
    )

    assert base_snapshots_seen == ["base", "a1-workspace"]
    report = json.loads((output_dir / "run-report.json").read_text(encoding="utf-8"))
    round_summary = report["problems"][0]["rounds"][0]
    assert round_summary["mode"] == "collaborative"
    assert round_summary["execution"] == "concurrent"
    assert round_summary["effective_execution"] == "sequential"
    assert any(
        "Collaborative rounds execute sequentially" in w for w in report["warnings"]
    )
