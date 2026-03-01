from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from auto_optimize_spec.models import (
    AgentSpec,
    OptimizationJob,
    ProblemSetSpec,
    ProblemSpec,
    ProviderSpec,
    RunnerSpec,
    ScoringSpec,
)
from auto_optimize_spec.providers import AgentDraft, create_provider_client
from auto_optimize_spec.runtime import load_job


@dataclass
class AgentExecutionResult:
    runtime_type: str
    command: str
    summary: str
    input_tokens: int
    output_tokens: int
    exit_code: int
    stdout: str
    stderr: str
    elapsed_seconds: float
    stdout_log_path: Optional[str] = None
    stderr_log_path: Optional[str] = None


@dataclass
class VerificationResult:
    passed: bool
    metrics: Dict[str, Any]
    command_exit_code: int
    stdout: str
    stderr: str
    elapsed_seconds: float


@dataclass
class AttemptResult:
    attempt_index: int
    agent_id: str
    provider_id: Optional[str]
    model: Optional[str]
    draft_summary: str
    agent_runtime: AgentExecutionResult
    verification: VerificationResult
    score: float
    score_breakdown: Dict[str, float]
    agent_cost_usd: float
    workspace: str


def safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", value)


def copy_workspace_snapshot(src_root: Path, dst_root: Path) -> None:
    ignore = shutil.ignore_patterns(
        ".git",
        "venv",
        ".venv",
        "__pycache__",
        "*.pyc",
        ".mypy_cache",
        ".pytest_cache",
        "runs",
    )
    shutil.copytree(src_root, dst_root, dirs_exist_ok=True, ignore=ignore)


def resolve_artifact_path(job_file_dir: Path, artifact_path: str) -> Path:
    candidate_from_job = (job_file_dir / artifact_path).resolve()
    if candidate_from_job.exists():
        return candidate_from_job
    return (Path.cwd() / artifact_path).resolve()


def copy_artifact_into_root(job_file_dir: Path, artifact_path: str, mount_to: str, dst_root: Path) -> bool:
    src = resolve_artifact_path(job_file_dir, artifact_path)
    if not src.exists():
        return False
    mount_rel = mount_to.lstrip("/")
    dst = (dst_root / mount_rel).resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)
    return True


def extract_json_metrics(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def extract_metrics_from_workspace(workspace: Path) -> Dict[str, Any]:
    return extract_json_metrics(workspace / "tmp" / "metrics.json")


def eval_expr(expr: str, symbols: Dict[str, Any]) -> Any:
    compact = " ".join(line.strip() for line in expr.splitlines() if line.strip())
    normalized = compact.replace("&&", " and ").replace("||", " or ")
    normalized = re.sub(r"\btrue\b", "True", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bfalse\b", "False", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bnull\b", "None", normalized, flags=re.IGNORECASE)

    def norm(x: float) -> float:
        return float(x)

    class EvalGlobals(dict):
        def __missing__(self, key: str) -> float:
            return 0.0

    globals_map = EvalGlobals({"__builtins__": {}, "norm": norm, "min": min, "max": max, "abs": abs})
    globals_map.update(symbols)
    return eval(normalized, globals_map, {})


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


def compute_agent_cost_usd(provider: ProviderSpec | None, input_tokens: int, output_tokens: int) -> float:
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
) -> tuple[float, Dict[str, float]]:
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


def docker_image_for_runner(runner: RunnerSpec) -> str:
    return runner.image or os.environ.get("AUTO_OPTIMIZE_DOCKER_IMAGE", "auto-optimize/devperf:latest")


def run_command_in_docker(
    command: str,
    cwd: Path,
    timeout_seconds: int,
    env: Dict[str, str],
    runner: RunnerSpec,
) -> tuple[int, str, str]:
    try:
        import docker  # type: ignore
    except ImportError as exc:
        return 127, "", f"docker python library is not installed: {exc}"

    image = docker_image_for_runner(runner)
    try:
        client = docker.from_env()
    except Exception as exc:  # pragma: no cover - environment specific
        return 127, "", f"failed to initialize docker client: {exc}"

    mem_limit = None
    nano_cpus = None
    if runner.resource_limits:
        mem_limit = runner.resource_limits.memory
        if runner.resource_limits.cpu:
            try:
                nano_cpus = int(float(runner.resource_limits.cpu) * 1_000_000_000)
            except ValueError:
                nano_cpus = None

    container = None
    try:
        container = client.containers.run(
            image=image,
            command=["bash", "-lc", command],
            working_dir="/workspace",
            volumes={str(cwd.resolve()): {"bind": "/workspace", "mode": "rw"}},
            environment=env,
            network_disabled=(runner.network_policy == "disabled"),
            mem_limit=mem_limit,
            nano_cpus=nano_cpus,
            detach=True,
            stdout=True,
            stderr=True,
        )
        result = container.wait(timeout=timeout_seconds)
        exit_code = int(result.get("StatusCode", 1))
        stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
        stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")
        return exit_code, stdout, stderr
    except Exception as exc:  # pragma: no cover - environment specific
        msg = str(exc)
        if "Read timed out" in msg or "timed out" in msg.lower():
            if container is not None:
                try:
                    container.kill()
                except Exception:
                    pass
            return 124, "", f"verification timed out in docker image {image}"
        return 1, "", f"docker verification failed: {exc}"
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:
                pass
        try:
            client.close()
        except Exception:
            pass


def run_command(
    command: str,
    cwd: Path,
    timeout_seconds: int,
    env: Dict[str, str],
    runner: RunnerSpec,
) -> VerificationResult:
    start = time.perf_counter()
    if runner.type == "docker":
        exit_code, stdout, stderr = run_command_in_docker(
            command=command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            env=env,
            runner=runner,
        )
        elapsed = time.perf_counter() - start
        metrics = extract_metrics_from_workspace(cwd)
        return VerificationResult(
            passed=exit_code == 0,
            metrics=metrics,
            command_exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            elapsed_seconds=elapsed,
        )
    else:
        try:
            proc = subprocess.run(
                ["bash", "-lc", command],
                cwd=str(cwd),
                env={**os.environ, **env},
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            elapsed = time.perf_counter() - start
            return VerificationResult(
                passed=False,
                metrics={},
                command_exit_code=124,
                stdout="",
                stderr="verification timed out in local runner",
                elapsed_seconds=elapsed,
            )
    elapsed = time.perf_counter() - start
    metrics = extract_metrics_from_workspace(cwd)
    return VerificationResult(
        passed=proc.returncode == 0,
        metrics=metrics,
        command_exit_code=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        elapsed_seconds=elapsed,
    )


def evaluate_pass_condition(problem: ProblemSpec, verification: VerificationResult) -> bool:
    base = verification.passed
    if not problem.verification.pass_condition:
        return base

    symbols: Dict[str, Any] = {**verification.metrics}
    symbols.update({"tests_passed": bool(verification.metrics.get("tests_passed", base))})
    try:
        return bool(eval_expr(problem.verification.pass_condition, symbols))
    except Exception:
        return False


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


def ensure_reporting_dir(job: OptimizationJob, explicit_output_dir: Path | None) -> Path:
    if explicit_output_dir:
        out = explicit_output_dir
    elif job.reporting and job.reporting.output_dir:
        out = Path(job.reporting.output_dir)
    else:
        stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
        out = Path("runs") / f"{job.job.id}-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    return out


def persist_workspace(
    output_dir: Path,
    problem_id: str,
    agent_id: str,
    attempt_idx: int,
    workspace: Path,
) -> Path:
    target = (
        output_dir
        / "attempts"
        / safe_name(problem_id)
        / safe_name(agent_id)
        / f"attempt-{attempt_idx}"
    )
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(workspace, target)
    return target


def default_external_executable(runtime_type: str) -> str:
    if runtime_type == "claude_code":
        return "claude"
    if runtime_type == "codex":
        return "codex"
    if runtime_type == "opencode":
        return "opencode"
    return ""


def build_agent_prompt(agent: AgentSpec, problem: ProblemSpec, attempt_idx: int, feedback: str | None) -> str:
    parts = [
        f"You are agent '{agent.id}' with persona: {agent.persona}",
        f"Problem id: {problem.id}",
        f"Title: {problem.title}",
        f"Goal: {problem.goal}",
        "Workspace scope and permissions:",
        "- You have full access to the CURRENT WORKING DIRECTORY (the provided workspace snapshot).",
        "- Do NOT request or access absolute paths like /, /workspace, /tmp, or parent dirs via ..",
        "- Use only repo-relative paths (e.g., third_party/tinyxml2, examples/legacy_tinyxml2, src).",
        "Constraints:",
    ]
    parts.extend(f"- {c}" for c in problem.constraints)
    parts.append("Likely relevant files/paths:")
    parts.append("- third_party/tinyxml2/tinyxml2.cpp (legacy C++ behavior reference)")
    parts.append("- examples/legacy_tinyxml2/data/ (verification corpus)")
    parts.append("- examples/legacy_tinyxml2/verify.sh (verifier entrypoint)")
    parts.append("- port_rust/src/lib.rs (Rust port implementation)")
    parts.append("- port_rust/Cargo.toml (Rust crate manifest)")
    if problem.output_contract.required_paths:
        parts.append("Required output paths:")
        parts.extend(f"- {p}" for p in problem.output_contract.required_paths)
    if problem.verification.command:
        parts.append(f"Verifier command: {problem.verification.command}")
    parts.append(f"Attempt: {attempt_idx}")
    if feedback:
        parts.append("Feedback from previous attempt:")
        parts.append(feedback)
    parts.append("Apply changes directly in the current workspace.")

    if agent.runtime and agent.runtime.prompt_template:
        parts.append("Additional instructions:")
        parts.append(agent.runtime.prompt_template)

    return "\n".join(parts)


def run_external_agent(
    agent: AgentSpec,
    problem: ProblemSpec,
    attempt_idx: int,
    feedback: str | None,
    workspace: Path,
    base_env: Dict[str, str],
    output_dir: Path,
    live_output: bool,
) -> AgentExecutionResult:
    runtime = agent.runtime
    assert runtime is not None

    prompt = build_agent_prompt(agent, problem, attempt_idx, feedback)
    executable = runtime.executable or default_external_executable(runtime.type)
    args = list(runtime.args)

    if not executable:
        return AgentExecutionResult(
            runtime_type=runtime.type,
            command="",
            summary="No executable configured.",
            input_tokens=0,
            output_tokens=0,
            exit_code=127,
            stdout="",
            stderr="No executable configured for agent runtime.",
            elapsed_seconds=0.0,
        )

    cmd_parts = [executable, *args]
    stdin_payload = None
    if runtime.prompt_mode == "arg":
        cmd_parts.append(prompt)
    else:
        stdin_payload = prompt

    log_dir = output_dir / "agent-logs" / safe_name(problem.id) / safe_name(agent.id)
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_log = log_dir / f"attempt-{attempt_idx}.stdout.log"
    stderr_log = log_dir / f"attempt-{attempt_idx}.stderr.log"

    start = time.perf_counter()
    try:
        proc = subprocess.Popen(
            cmd_parts,
            cwd=str(workspace),
            env={**os.environ, **base_env, **runtime.env},
            stdin=subprocess.PIPE if stdin_payload is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError:
        return AgentExecutionResult(
            runtime_type=runtime.type,
            command=" ".join(shlex.quote(x) for x in cmd_parts),
            summary=f"{runtime.type} executable not found: {executable}",
            input_tokens=0,
            output_tokens=0,
            exit_code=127,
            stdout="",
            stderr=f"Executable not found: {executable}",
            elapsed_seconds=time.perf_counter() - start,
            stdout_log_path=str(stdout_log),
            stderr_log_path=str(stderr_log),
        )
    stdout_chunks: List[str] = []
    stderr_chunks: List[str] = []

    def stream_pipe(pipe: Any, chunks: List[str], log_path: Path, stream_name: str) -> None:
        with log_path.open("w", encoding="utf-8") as fh:
            for line in iter(pipe.readline, ""):
                chunks.append(line)
                fh.write(line)
                fh.flush()
                if live_output:
                    print(f"[agent:{agent.id}][{stream_name}] {line}", end="")
        pipe.close()

    stdout_thread = threading.Thread(
        target=stream_pipe, args=(proc.stdout, stdout_chunks, stdout_log, "stdout"), daemon=True
    )
    stderr_thread = threading.Thread(
        target=stream_pipe, args=(proc.stderr, stderr_chunks, stderr_log, "stderr"), daemon=True
    )
    stdout_thread.start()
    stderr_thread.start()

    if stdin_payload is not None and proc.stdin is not None:
        proc.stdin.write(stdin_payload)
        proc.stdin.close()

    timed_out = False
    try:
        proc.wait(timeout=runtime.timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        proc.wait()

    stdout_thread.join()
    stderr_thread.join()
    elapsed = time.perf_counter() - start

    stdout = "".join(stdout_chunks)
    stderr = "".join(stderr_chunks)
    if timed_out:
        stderr = (stderr + "\nAgent runtime timed out.").strip()
        exit_code = 124
    else:
        exit_code = proc.returncode

    summary = stdout[:400] if stdout else f"{runtime.type} exited {exit_code}: {stderr[:220]}"

    agent_metrics = extract_json_metrics(workspace / "tmp" / "agent_metrics.json")
    input_tokens = int(agent_metrics.get("input_tokens", 0))
    output_tokens = int(agent_metrics.get("output_tokens", 0))

    return AgentExecutionResult(
        runtime_type=runtime.type,
        command=" ".join(shlex.quote(x) for x in cmd_parts),
        summary=summary,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        elapsed_seconds=elapsed,
        stdout_log_path=str(stdout_log),
        stderr_log_path=str(stderr_log),
    )


def run_internal_agent(
    agent: AgentSpec,
    problem: ProblemSpec,
    attempt_idx: int,
    feedback: str | None,
    provider: ProviderSpec,
) -> AgentExecutionResult:
    client = create_provider_client(provider)
    draft: AgentDraft = client.propose(agent=agent, problem=problem, attempt_index=attempt_idx, feedback=feedback)
    return AgentExecutionResult(
        runtime_type="internal_mock",
        command="internal_provider_adapter",
        summary=draft.summary,
        input_tokens=draft.input_tokens,
        output_tokens=draft.output_tokens,
        exit_code=0,
        stdout=draft.summary,
        stderr="",
        elapsed_seconds=0.0,
        stdout_log_path=None,
        stderr_log_path=None,
    )


def run_job(job: OptimizationJob, job_path: Path, output_dir: Path, live_agent_output: bool = True) -> Dict[str, Any]:
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
        with tempfile.TemporaryDirectory(prefix=f"auto-opt-{problem.id}-") as temp_dir:
            temp_root = Path(temp_dir)
            base_snapshot = temp_root / "base"
            copy_workspace_snapshot(Path.cwd(), base_snapshot)

            if job.job.environment:
                for artifact in job.job.environment.artifacts:
                    copied = copy_artifact_into_root(job_path.parent, artifact.path, artifact.mount_to, base_snapshot)
                    if not copied:
                        run_summary["warnings"].append(
                            f"Artifact not found: path={artifact.path} mount_to={artifact.mount_to}"
                        )

                for setup_cmd in job.job.environment.setup_commands:
                    subprocess.run(["bash", "-lc", setup_cmd], cwd=str(base_snapshot), check=False, capture_output=True)

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
                    if agent.runtime and agent.runtime.type in {"claude_code", "codex", "opencode"}:
                        agent_execution = run_external_agent(
                            agent=agent,
                            problem=problem,
                            attempt_idx=attempt_idx,
                            feedback=feedback,
                            workspace=workspace,
                            base_env=job_env,
                            output_dir=output_dir,
                            live_output=live_agent_output,
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
                    )
                    cost = compute_agent_cost_usd(provider, agent_execution.input_tokens, agent_execution.output_tokens)
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
    (output_dir / "run-report.json").write_text(json.dumps(run_summary, indent=2), encoding="utf-8")
    return run_summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute an end-to-end skeleton run for an optimization job")
    parser.add_argument("config", type=Path, help="Path to YAML/JSON job config")
    parser.add_argument("--output-dir", type=Path, default=None, help="Override report output directory")
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
