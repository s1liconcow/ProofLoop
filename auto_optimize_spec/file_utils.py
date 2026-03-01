from __future__ import annotations

import filecmp
import json
import os
import re
import shlex
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from auto_optimize_spec.models import EnvironmentInput, OptimizationJob, ProblemSpec

PERSIST_SKIP_DIR_PREFIXES = (
    "runs",
    "venv",
    ".venv",
    "tmp",
    "port_rust/target",
    "examples/legacy_tinyxml2/tmp",
)


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


def copy_artifact_into_root(
    job_file_dir: Path, artifact_path: str, mount_to: str, dst_root: Path
) -> bool:
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


def build_problem_base_snapshot(
    *,
    job_file_dir: Path,
    problem: ProblemSpec,
    environment: EnvironmentInput | None,
    dst_root: Path,
    warnings: List[str],
) -> None:
    dst_root.mkdir(parents=True, exist_ok=True)

    copied_anything = False
    if environment and environment.artifacts:
        for artifact in environment.artifacts:
            copied = copy_artifact_into_root(
                job_file_dir, artifact.path, artifact.mount_to, dst_root
            )
            if copied:
                copied_anything = True
            else:
                warnings.append(
                    f"Artifact not found: path={artifact.path} mount_to={artifact.mount_to}"
                )
    else:
        # Backward-compatible fallback when no artifacts are declared.
        copy_workspace_snapshot(Path.cwd(), dst_root)
        copied_anything = True

    # Ensure output contract paths exist in the workspace even if not mounted as artifacts.
    for required in problem.output_contract.required_paths:
        required_rel = required.lstrip("/").lstrip("./")
        if not required_rel:
            continue
        required_path = dst_root / required_rel
        if required.endswith("/") or not required_path.suffix:
            required_path.mkdir(parents=True, exist_ok=True)
        else:
            required_path.parent.mkdir(parents=True, exist_ok=True)

    # Ensure verifier-referenced files are available in minimal snapshots.
    referenced_paths = _collect_verification_paths(problem)
    for rel_path in referenced_paths:
        copied = _copy_reference_path_into_root(
            job_file_dir=job_file_dir,
            rel_path=rel_path,
            dst_root=dst_root,
        )
        if not copied:
            warnings.append(f"Verifier path not found: {rel_path}")

    if not copied_anything and not referenced_paths:
        warnings.append(
            f"No artifacts or verifier assets were mounted for problem={problem.id}; "
            "agent workspace may be empty."
        )


def _collect_verification_paths(problem: ProblemSpec) -> List[str]:
    out: List[str] = []
    if problem.verification.script and problem.verification.script.path:
        out.append(problem.verification.script.path)
    if problem.verification.command:
        out.extend(_extract_paths_from_command(problem.verification.command))
    # Preserve order while deduplicating.
    seen = set()
    deduped: List[str] = []
    for item in out:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def _extract_paths_from_command(command: str) -> List[str]:
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()

    candidates: List[str] = []
    for idx, token in enumerate(tokens):
        if token in {"bash", "sh", "python", "python3"} and idx + 1 < len(tokens):
            maybe_path = tokens[idx + 1]
            if not maybe_path.startswith("-"):
                candidates.append(maybe_path)
            continue

        if token.startswith("./") or token.startswith("../"):
            candidates.append(token)
            continue

        if "/" in token and not token.startswith("-"):
            candidates.append(token)

    return candidates


def _copy_reference_path_into_root(
    *, job_file_dir: Path, rel_path: str, dst_root: Path
) -> bool:
    rel_clean = rel_path.lstrip("./")
    if not rel_clean or rel_path.startswith("/"):
        return False

    src = resolve_artifact_path(job_file_dir, rel_clean)
    if not src.exists():
        return False

    dst = dst_root / rel_clean
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


def ensure_reporting_dir(
    job: OptimizationJob, explicit_output_dir: Path | None
) -> Path:
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
    base_snapshot: Path | None = None,
) -> Path:
    target = (
        output_dir
        / "attempts"
        / safe_name(problem_id)
        / safe_name(agent_id)
        / f"attempt-{attempt_idx}"
    )
    if target.exists():
        shutil.rmtree(target, onerror=_handle_remove_readonly)
    target.parent.mkdir(parents=True, exist_ok=True)
    if base_snapshot is None:
        shutil.copytree(workspace, target)
        return target

    target.mkdir(parents=True, exist_ok=True)

    # Persist only files that changed vs the base snapshot to avoid duplicating the full repo tree.
    copied_files: list[str] = []
    deleted_files: list[str] = []

    for ws_file in workspace.rglob("*"):
        if ws_file.is_dir():
            continue
        rel = ws_file.relative_to(workspace)
        rel_str = str(rel)
        if rel_str.startswith(PERSIST_SKIP_DIR_PREFIXES):
            continue
        base_file = base_snapshot / rel
        try:
            should_copy = (not base_file.exists()) or (
                not filecmp.cmp(ws_file, base_file, shallow=False)
            )
        except (OSError, PermissionError):
            continue
        if should_copy:
            dst = target / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(ws_file, dst)
            except (OSError, PermissionError):
                continue
            copied_files.append(str(rel))

    for base_file in base_snapshot.rglob("*"):
        if base_file.is_dir():
            continue
        rel = base_file.relative_to(base_snapshot)
        rel_str = str(rel)
        if rel_str.startswith(PERSIST_SKIP_DIR_PREFIXES):
            continue
        ws_file = workspace / rel
        if not ws_file.exists():
            deleted_files.append(str(rel))

    manifest = {
        "mode": "delta",
        "copied_files_count": len(copied_files),
        "deleted_files_count": len(deleted_files),
        "copied_files": copied_files,
        "deleted_files": deleted_files,
    }
    (target / "proofloop-manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return target


def _handle_remove_readonly(func: Any, path: str, exc_info: Any) -> None:
    try:
        os.chmod(path, 0o700)
        func(path)
    except Exception:
        pass
