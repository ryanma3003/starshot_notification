#!/usr/bin/env bash
# Runs every suite, reports pass/fail per file, exits non-zero if any failed.
set -uo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python}"
failed=0
for t in tests/test_*.py; do
    printf '  %-34s ' "$(basename "$t")"
    if out=$("$PY" "$t" 2>&1); then
        echo "PASS  $(echo "$out" | tail -1)"
    else
        echo "FAIL"
        echo "$out" | sed 's/^/      /'
        failed=1
    fi
done
exit $failed
