from __future__ import annotations

import os
import shlex
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

from auto_optimize_spec.file_utils import extract_json_metrics, safe_name
from auto_optimize_spec.models import AgentSpec, ProblemSpec, ProviderSpec, RunnerSpec
from auto_optimize_spec.providers import AgentDraft, create_provider_client
from auto_optimize_spec.results import AgentExecutionResult
from auto_optimize_spec.verification import (
    docker_image_for_runner,
    normalize_docker_mem_limit,
)


def default_external_executable(runtime_type: str) -> str:
    if runtime_type == "claude_code":
        return "claude"
    if runtime_type == "codex":
        return "codex"
    if runtime_type == "opencode":
        return "opencode"
    return ""


def build_agent_prompt(
    agent: AgentSpec, problem: ProblemSpec, attempt_idx: int, feedback: str | None
) -> str:
    parts = [
        f"You are agent '{agent.id}' with persona: {agent.persona}",
        f"Problem id: {problem.id}",
        f"Title: {problem.title}",
        f"Goal: {problem.goal}",
        "Workspace scope and permissions:",
        "- You have full access to the CURRENT WORKING DIRECTORY (the provided workspace snapshot).",
        "- Do NOT request or access absolute paths like /, /workspace, /tmp, or parent dirs via ..",
        "- Use only repo-relative paths inside this workspace.",
        "Constraints:",
    ]
    parts.extend(f"- {c}" for c in problem.constraints)
    if problem.default_prompt_appendix:
        parts.append("Default prompt appendix:")
        parts.extend(f"- {line}" for line in problem.default_prompt_appendix)
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
    runner: RunnerSpec,
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

    if runner.type == "docker":
        return run_external_agent_in_docker(
            agent=agent,
            runtime_type=runtime.type,
            cmd_parts=cmd_parts,
            stdin_payload=stdin_payload,
            workspace=workspace,
            base_env=base_env,
            runtime_env=runtime.env,
            runner=runner,
            stdout_log=stdout_log,
            stderr_log=stderr_log,
            live_output=live_output,
            start_time=start,
        )
    elif runner.type == "sprite":
        return run_external_agent_in_sprite(
            agent=agent,
            runtime_type=runtime.type,
            cmd_parts=cmd_parts,
            stdin_payload=stdin_payload,
            workspace=workspace,
            base_env=base_env,
            runtime_env=runtime.env,
            runner=runner,
            stdout_log=stdout_log,
            stderr_log=stderr_log,
            live_output=live_output,
            start_time=start,
        )

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

    def stream_pipe(
        pipe: Any, chunks: List[str], log_path: Path, stream_name: str
    ) -> None:
        with log_path.open("w", encoding="utf-8") as fh:
            for line in iter(pipe.readline, ""):
                chunks.append(line)
                fh.write(line)
                fh.flush()
                if live_output:
                    print(f"[agent:{agent.id}][{stream_name}] {line}", end="")
        pipe.close()

    stdout_thread = threading.Thread(
        target=stream_pipe,
        args=(proc.stdout, stdout_chunks, stdout_log, "stdout"),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=stream_pipe,
        args=(proc.stderr, stderr_chunks, stderr_log, "stderr"),
        daemon=True,
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

    summary = (
        stdout[:400] if stdout else f"{runtime.type} exited {exit_code}: {stderr[:220]}"
    )

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


def run_external_agent_in_docker(
    agent: AgentSpec,
    runtime_type: str,
    cmd_parts: List[str],
    stdin_payload: str | None,
    workspace: Path,
    base_env: Dict[str, str],
    runtime_env: Dict[str, str],
    runner: RunnerSpec,
    stdout_log: Path,
    stderr_log: Path,
    live_output: bool,
    start_time: float,
) -> AgentExecutionResult:
    try:
        import docker  # type: ignore
    except ImportError as exc:
        return AgentExecutionResult(
            runtime_type=runtime_type,
            command=" ".join(shlex.quote(x) for x in cmd_parts),
            summary=f"{runtime_type} docker mode unavailable: {exc}",
            input_tokens=0,
            output_tokens=0,
            exit_code=127,
            stdout="",
            stderr=f"docker python library not installed: {exc}",
            elapsed_seconds=time.perf_counter() - start_time,
            stdout_log_path=str(stdout_log),
            stderr_log_path=str(stderr_log),
        )

    image = docker_image_for_runner(runner)
    cmd_str = " ".join(shlex.quote(x) for x in cmd_parts)
    if stdin_payload is not None:
        cmd_str = (
            f"cat >/tmp/proofloop_prompt.txt && {cmd_str} < /tmp/proofloop_prompt.txt"
        )

    env = {
        k: v
        for k, v in os.environ.items()
        if k in {"FIREWORKS_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"}
    }
    env.update(base_env)
    env.update(runtime_env)

    mem_limit = None
    nano_cpus = None
    if runner.resource_limits:
        mem_limit = normalize_docker_mem_limit(runner.resource_limits.memory)
        if runner.resource_limits.cpu:
            try:
                nano_cpus = int(float(runner.resource_limits.cpu) * 1_000_000_000)
            except ValueError:
                nano_cpus = None

    try:
        client = docker.from_env()
    except Exception as exc:  # pragma: no cover
        return AgentExecutionResult(
            runtime_type=runtime_type,
            command=cmd_str,
            summary=f"{runtime_type} docker client init failed",
            input_tokens=0,
            output_tokens=0,
            exit_code=127,
            stdout="",
            stderr=f"failed to initialize docker client: {exc}",
            elapsed_seconds=time.perf_counter() - start_time,
            stdout_log_path=str(stdout_log),
            stderr_log_path=str(stderr_log),
        )

    container = None
    stdout_lines: List[str] = []
    stderr_lines: List[str] = []
    exit_code = 1
    timed_out = False

    try:
        container = client.containers.run(
            image=image,
            command=["bash", "-lc", cmd_str],
            working_dir="/workspace",
            volumes={str(workspace.resolve()): {"bind": "/workspace", "mode": "rw"}},
            environment=env,
            user=f"{os.getuid()}:{os.getgid()}",
            network_disabled=(runner.network_policy == "disabled"),
            mem_limit=mem_limit,
            nano_cpus=nano_cpus,
            stdin_open=stdin_payload is not None,
            detach=True,
            stdout=True,
            stderr=True,
        )

        if stdin_payload is not None:
            sock = container.attach_socket(params={"stdin": 1, "stream": 1})
            try:
                sock._sock.sendall(stdin_payload.encode("utf-8"))
            finally:
                sock.close()

        with (
            stdout_log.open("w", encoding="utf-8") as out_fh,
            stderr_log.open("w", encoding="utf-8") as err_fh,
        ):
            for chunk in container.logs(
                stream=True, stdout=True, stderr=True, follow=True
            ):
                text = chunk.decode("utf-8", errors="replace")
                stdout_lines.append(text)
                out_fh.write(text)
                out_fh.flush()
                if live_output:
                    print(f"[agent:{agent.id}][stdout] {text}", end="")

            try:
                result = container.wait(
                    timeout=agent.runtime.timeout_seconds if agent.runtime else 900
                )
                exit_code = int(result.get("StatusCode", 1))
            except Exception:
                timed_out = True
                try:
                    container.kill()
                except Exception:
                    pass
                exit_code = 124
                err_fh.write("Agent runtime timed out.\n")
                err_fh.flush()
                stderr_lines.append("Agent runtime timed out.\n")
                if live_output:
                    print(f"[agent:{agent.id}][stderr] Agent runtime timed out.")
    except Exception as exc:  # pragma: no cover
        stderr_lines.append(f"Docker agent execution failed: {exc}\n")
        with stderr_log.open("a", encoding="utf-8") as err_fh:
            err_fh.write(f"Docker agent execution failed: {exc}\n")
        exit_code = 1
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

    elapsed = time.perf_counter() - start_time
    stdout = "".join(stdout_lines)
    stderr = "".join(stderr_lines).strip()
    summary = (
        stdout[:400] if stdout else f"{runtime_type} exited {exit_code}: {stderr[:220]}"
    )
    if timed_out and not stderr:
        stderr = "Agent runtime timed out."

    agent_metrics = extract_json_metrics(workspace / "tmp" / "agent_metrics.json")
    input_tokens = int(agent_metrics.get("input_tokens", 0))
    output_tokens = int(agent_metrics.get("output_tokens", 0))

    return AgentExecutionResult(
        runtime_type=runtime_type,
        command=cmd_str,
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
    draft: AgentDraft = client.propose(
        agent=agent, problem=problem, attempt_index=attempt_idx, feedback=feedback
    )
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


def run_external_agent_in_sprite(
    agent: AgentSpec,
    runtime_type: str,
    cmd_parts: List[str],
    stdin_payload: str | None,
    workspace: Path,
    base_env: Dict[str, str],
    runtime_env: Dict[str, str],
    runner: RunnerSpec,
    stdout_log: Path,
    stderr_log: Path,
    live_output: bool,
    start_time: float,
) -> AgentExecutionResult:
    sprite_name = f"proofloop-{agent.id}-{int(time.time())}"
    tar_path = workspace / ".." / f"{sprite_name}_in.tar.gz"
    import tarfile

    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(workspace, arcname=".")

    cmd_str = " ".join(shlex.quote(x) for x in cmd_parts)
    if stdin_payload is not None:
        cmd_str = (
            f"cat >/tmp/proofloop_prompt.txt && {cmd_str} < /tmp/proofloop_prompt.txt"
        )

    env_vars = []
    for k, v in {**base_env, **runtime_env}.items():
        env_vars.append("-env")
        env_vars.append(f"{k}={v}")

    create_proc = subprocess.run(
        ["sprite", "create", "-skip-console", sprite_name],
        capture_output=True,
        text=True,
    )
    if create_proc.returncode != 0:
        return AgentExecutionResult(
            runtime_type=runtime_type,
            command=cmd_str,
            summary=f"Failed to create sprite: {create_proc.stderr}",
            input_tokens=0,
            output_tokens=0,
            exit_code=1,
            stdout="",
            stderr=create_proc.stderr,
            elapsed_seconds=time.perf_counter() - start_time,
            stdout_log_path=str(stdout_log),
            stderr_log_path=str(stderr_log),
        )

    upload_proc = subprocess.run(
        [
            "sprite",
            "exec",
            "-s",
            sprite_name,
            "-file",
            f"{tar_path}:/tmp/workspace.tar.gz",
            "sh",
            "-c",
            "mkdir -p /workspace && tar -xzf /tmp/workspace.tar.gz -C /workspace",
        ],
        capture_output=True,
        text=True,
    )
    if upload_proc.returncode != 0:
        subprocess.run(
            ["sprite", "destroy", sprite_name],
            input="y\n",
            text=True,
            capture_output=True,
        )
        return AgentExecutionResult(
            runtime_type=runtime_type,
            command=cmd_str,
            summary=f"Failed to upload to sprite: {upload_proc.stderr}",
            input_tokens=0,
            output_tokens=0,
            exit_code=1,
            stdout="",
            stderr=upload_proc.stderr,
            elapsed_seconds=time.perf_counter() - start_time,
            stdout_log_path=str(stdout_log),
            stderr_log_path=str(stderr_log),
        )

    stdout_lines: List[str] = []
    stderr_lines: List[str] = []
    exit_code = 1
    timed_out = False

    exec_cmd = (
        ["sprite", "exec", "-s", sprite_name, "-dir", "/workspace"]
        + env_vars
        + ["bash", "-lc", cmd_str]
    )

    try:
        proc = subprocess.Popen(
            exec_cmd,
            stdin=subprocess.PIPE if stdin_payload is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        def stream_pipe(pipe, chunks, log_path, stream_name):
            with log_path.open("w", encoding="utf-8") as fh:
                for line in iter(pipe.readline, ""):
                    chunks.append(line)
                    fh.write(line)
                    fh.flush()
                    if live_output:
                        print(f"[agent:{agent.id}][{stream_name}] {line}", end="")
            pipe.close()

        stdout_thread = threading.Thread(
            target=stream_pipe,
            args=(proc.stdout, stdout_lines, stdout_log, "stdout"),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=stream_pipe,
            args=(proc.stderr, stderr_lines, stderr_log, "stderr"),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        if stdin_payload is not None and proc.stdin is not None:
            proc.stdin.write(stdin_payload)
            proc.stdin.close()

        try:
            proc.wait(timeout=agent.runtime.timeout_seconds if agent.runtime else 900)
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            proc.wait()
            exit_code = 124
            stderr_lines.append("Agent runtime timed out.\n")
            if live_output:
                print(f"[agent:{agent.id}][stderr] Agent runtime timed out.")

        stdout_thread.join()
        stderr_thread.join()
    except Exception as exc:
        stderr_lines.append(f"Sprite agent execution failed: {exc}\n")
        exit_code = 1

    dl_proc = subprocess.run(
        [
            "sprite",
            "exec",
            "-s",
            sprite_name,
            "-dir",
            "/workspace",
            "sh",
            "-c",
            "tar -czf - . | base64",
        ],
        capture_output=True,
        text=True,
    )
    if dl_proc.returncode == 0 and dl_proc.stdout:
        import base64
        import io

        try:
            decoded = base64.b64decode(dl_proc.stdout)
            with tarfile.open(fileobj=io.BytesIO(decoded), mode="r:gz") as tar:
                tar.extractall(path=workspace)
        except Exception as e:
            stderr_lines.append(f"\nFailed to extract results from sprite: {e}")
            exit_code = 1 if exit_code == 0 else exit_code

    subprocess.run(
        ["sprite", "destroy", sprite_name], input="y\n", text=True, capture_output=True
    )

    elapsed = time.perf_counter() - start_time
    stdout = "".join(stdout_lines)
    stderr = "".join(stderr_lines).strip()
    summary = (
        stdout[:400] if stdout else f"{runtime_type} exited {exit_code}: {stderr[:220]}"
    )

    agent_metrics = extract_json_metrics(workspace / "tmp" / "agent_metrics.json")
    input_tokens = int(agent_metrics.get("input_tokens", 0))
    output_tokens = int(agent_metrics.get("output_tokens", 0))

    return AgentExecutionResult(
        runtime_type=runtime_type,
        command=cmd_str,
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
