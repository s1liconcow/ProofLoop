from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Dict, Tuple

from auto_optimize_spec.file_utils import extract_metrics_from_workspace
from auto_optimize_spec.models import RunnerSpec
from auto_optimize_spec.results import VerificationResult

DEFAULT_DOCKER_IMAGE = "proofloop/devperf:latest"


def docker_image_for_runner(runner: RunnerSpec) -> str:
    return (
        runner.image
        or os.environ.get("PROOFLOOP_DOCKER_IMAGE")
        or os.environ.get("AUTO_OPTIMIZE_DOCKER_IMAGE")
        or DEFAULT_DOCKER_IMAGE
    )


def normalize_docker_mem_limit(value: str | None) -> str | int | None:
    if not value:
        return None
    raw = value.strip()
    m = re.fullmatch(r"(?i)(\d+(?:\.\d+)?)\s*([kmgt]i?|b)?", raw)
    if not m:
        return raw
    num = float(m.group(1))
    unit = (m.group(2) or "b").lower()
    factors = {
        "b": 1,
        "k": 1000,
        "m": 1000**2,
        "g": 1000**3,
        "t": 1000**4,
        "ki": 1024,
        "mi": 1024**2,
        "gi": 1024**3,
        "ti": 1024**4,
    }
    return int(num * factors[unit])


def run_command_in_docker(
    command: str,
    cwd: Path,
    timeout_seconds: int,
    env: Dict[str, str],
    runner: RunnerSpec,
) -> Tuple[int, str, str]:
    try:
        import docker  # type: ignore
    except ImportError as exc:
        return 127, "", f"docker python library is not installed: {exc}"

    image = docker_image_for_runner(runner)
    try:
        client = docker.from_env()
    except Exception as exc:  # pragma: no cover
        return 127, "", f"failed to initialize docker client: {exc}"

    mem_limit = None
    nano_cpus = None
    if runner.resource_limits:
        mem_limit = normalize_docker_mem_limit(runner.resource_limits.memory)
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
            user=f"{os.getuid()}:{os.getgid()}",
            network_disabled=(runner.network_policy == "disabled"),
            mem_limit=mem_limit,
            nano_cpus=nano_cpus,
            detach=True,
            stdout=True,
            stderr=True,
        )
        result = container.wait(timeout=timeout_seconds)
        exit_code = int(result.get("StatusCode", 1))
        stdout = container.logs(stdout=True, stderr=False).decode(
            "utf-8", errors="replace"
        )
        stderr = container.logs(stdout=False, stderr=True).decode(
            "utf-8", errors="replace"
        )
        return exit_code, stdout, stderr
    except Exception as exc:  # pragma: no cover
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
