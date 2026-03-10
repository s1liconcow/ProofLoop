#!/usr/bin/env bash
set -euo pipefail

repo_dir="${PROOFLOOP_OSAGEFS_DIR:-osagefs}"
test_cmd="${PROOFLOOP_TEST_CMD:-cargo test --workspace --all-targets}"
bench_cmd="${PROOFLOOP_BENCH_CMD:-cargo bench --workspace --benches -- --noplot}"

mkdir -p tmp

if [[ ! -f "$repo_dir/Cargo.toml" ]]; then
  cat > tmp/metrics.json <<JSON
{"tests_passed": false, "bench_passed": false, "test_runtime_seconds": 0, "bench_runtime_seconds": 0, "total_runtime_seconds": 0}
JSON
  echo "Missing Cargo.toml at $repo_dir/Cargo.toml" >&2
  exit 1
fi

run_step() {
  local cmd="$1"
  local start
  local end
  start=$(date +%s)
  set +e
  (
    cd "$repo_dir"
    # Keep command output out of stdout so status parsing remains stable.
    bash -lc "$cmd"
  ) >&2
  local status=$?
  set -e
  end=$(date +%s)
  local elapsed=$((end - start))
  printf '%s %s\n' "$status" "$elapsed"
}

read -r test_status test_elapsed < <(run_step "$test_cmd")
read -r bench_status bench_elapsed < <(run_step "$bench_cmd")

tests_passed=false
bench_passed=false
[[ "$test_status" -eq 0 ]] && tests_passed=true
[[ "$bench_status" -eq 0 ]] && bench_passed=true

total_elapsed=$((test_elapsed + bench_elapsed))

cat > tmp/metrics.json <<JSON
{"tests_passed": $tests_passed, "bench_passed": $bench_passed, "test_runtime_seconds": $test_elapsed, "bench_runtime_seconds": $bench_elapsed, "total_runtime_seconds": $total_elapsed}
JSON

if [[ "$tests_passed" == true && "$bench_passed" == true ]]; then
  exit 0
fi

echo "Verification failed: tests_passed=$tests_passed bench_passed=$bench_passed" >&2
exit 1
