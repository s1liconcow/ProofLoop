from auto_optimize_spec.models import RunnerResourceLimits, RunnerSpec
from auto_optimize_spec.verification import (
    docker_image_for_runner,
    normalize_docker_mem_limit,
)


def test_normalize_docker_mem_limit_binary_units() -> None:
    assert normalize_docker_mem_limit("12Gi") == 12 * 1024**3
    assert normalize_docker_mem_limit("512Mi") == 512 * 1024**2


def test_docker_image_resolution_prefers_runner_image() -> None:
    runner = RunnerSpec(
        type="docker",
        image="custom/image:1",
        resource_limits=RunnerResourceLimits(cpu="2", memory="4Gi"),
    )
    assert docker_image_for_runner(runner) == "custom/image:1"
