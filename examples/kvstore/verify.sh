#!/usr/bin/env bash
set -euo pipefail

crate_dir="${PROOFLOOP_KVSTORE_DIR:-kvstore}"
test_cmd="${PROOFLOOP_TEST_CMD:-cargo test}"
bench_cmd="${PROOFLOOP_BENCH_CMD:-cargo bench --bench bench -- --noplot}"
verify_lock_dir="${PROOFLOOP_VERIFY_LOCK_DIR:-}"

mkdir -p tmp
bench_log="tmp/criterion-bench-output.log"
test_log="tmp/criterion-test-output.log"

if [[ ! -f "$crate_dir/Cargo.toml" ]]; then
  cat > tmp/metrics.json <<JSON
{"tests_passed": false, "bench_passed": false, "bench_metrics_captured": false, "test_runtime_seconds": 0, "bench_runtime_seconds": 0, "total_runtime_seconds": 0}
JSON
  echo "Missing Cargo.toml at $crate_dir/Cargo.toml" >&2
  exit 1
fi

run_step() {
  local cmd="$1"
  local log_path="$2"
  local start end
  start=$(python3 - <<'PY'
import time
print(time.time())
PY
)
  set +e
  (cd "$crate_dir"; bash -lc "$cmd") >"$log_path" 2>&1
  local status=$?
  set -e
  cat "$log_path" >&2
  end=$(python3 - <<'PY'
import time
print(time.time())
PY
)
  python3 - "$status" "$start" "$end" <<'PY'
import sys

status = int(sys.argv[1])
start = float(sys.argv[2])
end = float(sys.argv[3])
print(f"{status} {end - start:.6f}")
PY
}

run_step_with_lock() {
  local cmd="$1"
  local log_path="$2"
  if [[ -n "$verify_lock_dir" ]]; then
    mkdir -p "$verify_lock_dir"
    flock "$verify_lock_dir/kvstore-bench.lock" bash -lc "
      set -euo pipefail
      $(printf 'crate_dir=%q\n' "$crate_dir")
      $(printf 'cmd=%q\n' "$cmd")
      $(printf 'log_path=%q\n' "$log_path")
      $(declare -f run_step)
      run_step \"\$cmd\" \"\$log_path\"
    "
  else
    run_step "$cmd" "$log_path"
  fi
}

read -r test_status  test_elapsed  < <(run_step "$test_cmd" "$test_log")
read -r bench_status bench_elapsed < <(run_step_with_lock "$bench_cmd" "$bench_log")

tests_passed=false; bench_passed=false
[[ "$test_status"  -eq 0 ]] && tests_passed=true
[[ "$bench_status" -eq 0 ]] && bench_passed=true

python3 - "$bench_log" "$test_status" "$bench_status" "$test_elapsed" "$bench_elapsed" > tmp/metrics.json <<'PY'
import json
import re
import sys
from pathlib import Path

bench_log = Path(sys.argv[1])
test_status = int(sys.argv[2])
bench_status = int(sys.argv[3])
test_elapsed = float(sys.argv[4])
bench_elapsed = float(sys.argv[5])

unit_scale = {
    "ns": 1.0,
    "us": 1_000.0,
    "µs": 1_000.0,
    "μs": 1_000.0,
    "ms": 1_000_000.0,
    "s": 1_000_000_000.0,
}

pattern = re.compile(
    r"^(?P<name>[A-Za-z0-9_./-]+)\s+time:\s+\[(?P<low>[0-9.]+)\s+(?P<low_unit>ns|us|µs|μs|ms|s)\s+"
    r"(?P<mid>[0-9.]+)\s+(?P<mid_unit>ns|us|µs|μs|ms|s)\s+(?P<high>[0-9.]+)\s+(?P<high_unit>ns|us|µs|μs|ms|s)\]$"
)

def to_ns(value: str, unit: str) -> float:
    return float(value) * unit_scale[unit]

metrics = {
    "tests_passed": test_status == 0,
    "bench_passed": bench_status == 0,
    "bench_metrics_captured": False,
    "test_runtime_seconds": round(test_elapsed, 6),
    "bench_runtime_seconds": round(bench_elapsed, 6),
    "total_runtime_seconds": round(test_elapsed + bench_elapsed, 6),
}

bench_names = []
if bench_log.exists():
    for raw_line in bench_log.read_text(encoding="utf-8", errors="replace").splitlines():
        match = pattern.match(raw_line.strip())
        if not match:
            continue
        name = re.sub(r"[^A-Za-z0-9_]+", "_", match.group("name")).strip("_").lower()
        bench_names.append(name)
        low_ns = to_ns(match.group("low"), match.group("low_unit"))
        mid_ns = to_ns(match.group("mid"), match.group("mid_unit"))
        high_ns = to_ns(match.group("high"), match.group("high_unit"))
        metrics[f"bench_{name}_low_ns"] = round(low_ns, 3)
        metrics[f"bench_{name}_median_ns"] = round(mid_ns, 3)
        metrics[f"bench_{name}_high_ns"] = round(high_ns, 3)

if bench_names:
    metrics["bench_metrics_captured"] = True
    metrics["bench_benchmark_count"] = len(bench_names)
    metrics["bench_total_median_ns"] = round(
        sum(metrics[f"bench_{name}_median_ns"] for name in bench_names), 3
    )

print(json.dumps(metrics))
PY

if [[ "$tests_passed" == true && "$bench_passed" == true ]]; then
  exit 0
fi

echo "Verification failed: tests_passed=$tests_passed bench_passed=$bench_passed" >&2
exit 1
