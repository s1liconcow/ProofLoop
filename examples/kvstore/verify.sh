#!/usr/bin/env bash
set -euo pipefail

crate_dir="${PROOFLOOP_KVSTORE_DIR:-kvstore}"
test_cmd="${PROOFLOOP_TEST_CMD:-cargo test}"
bench_cmd="${PROOFLOOP_BENCH_CMD:-cargo bench --benches -- --noplot}"

mkdir -p tmp

if [[ ! -f "$crate_dir/Cargo.toml" ]]; then
  cat > tmp/metrics.json <<JSON
{"tests_passed": false, "bench_passed": false, "test_runtime_seconds": 0, "bench_runtime_seconds": 0, "total_runtime_seconds": 0}
JSON
  echo "Missing Cargo.toml at $crate_dir/Cargo.toml" >&2
  exit 1
fi

run_step() {
  local cmd="$1"
  local start end
  start=$(date +%s)
  set +e
  (cd "$crate_dir"; bash -lc "$cmd") >&2
  local status=$?
  set -e
  end=$(date +%s)
  printf '%s %s\n' "$status" "$((end - start))"
}

read -r test_status  test_elapsed  < <(run_step "$test_cmd")
read -r bench_status bench_elapsed < <(run_step "$bench_cmd")

tests_passed=false; bench_passed=false
[[ "$test_status"  -eq 0 ]] && tests_passed=true
[[ "$bench_status" -eq 0 ]] && bench_passed=true

total_elapsed=$((test_elapsed + bench_elapsed))

cat > tmp/metrics.json <<JSON
{"tests_passed": $tests_passed, "bench_passed": $bench_passed, "test_runtime_seconds": $test_elapsed, "bench_runtime_seconds": $bench_elapsed, "total_runtime_seconds": $total_elapsed}
JSON

if [[ "$tests_passed" == true && "$bench_passed" == true ]]; then exit 0; fi
echo "Verification failed: tests_passed=$tests_passed bench_passed=$bench_passed" >&2
exit 1
