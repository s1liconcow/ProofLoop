from pathlib import Path

from auto_optimize_spec.file_utils import build_problem_base_snapshot
from auto_optimize_spec.models import ProblemSpec


def test_build_problem_base_snapshot_without_artifacts_does_not_copy_cwd(
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir(parents=True)

    verifier = job_dir / "scripts" / "verify.sh"
    verifier.parent.mkdir(parents=True)
    verifier.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")

    dst_root = tmp_path / "snapshot"
    warnings: list[str] = []

    problem = ProblemSpec(
        id="p1",
        title="test",
        goal="test",
        input_contract={"format": "files"},
        output_contract={"format": "patch", "required_paths": ["out/"]},
        verification={"type": "command", "command": "bash scripts/verify.sh"},
    )

    build_problem_base_snapshot(
        job_file_dir=job_dir,
        problem=problem,
        environment=None,
        dst_root=dst_root,
        warnings=warnings,
    )

    # Should not mirror the entire current working directory into attempts.
    assert not (dst_root / "README.md").exists()

    # Verifier assets and declared output paths should still be present.
    assert (dst_root / "scripts" / "verify.sh").exists()
    assert (dst_root / "out").is_dir()

    assert all("No artifacts" not in w for w in warnings)
