#!/bin/bash
# Full backend test suite for the refactor loop. All-green gate: exit 0 iff 0 failed/error.
# Writes refactor/loop/last-validation.json. Re-execs into its own systemd scope when the
# calling session's cgroup has the 512-pid cap (claude session scope) — see qtrade-host-ops §5.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CG=$(cut -d: -f3 /proc/self/cgroup)
if [ -z "${LOOP_SCOPED:-}" ] && [ "$(cat "/sys/fs/cgroup${CG}/pids.max" 2>/dev/null)" = "512" ]; then
  exec systemd-run --user --collect --pipe --quiet -E LOOP_SCOPED=1 "$0" "$@"
fi

cd "$ROOT/backend"
START=$(date +%s)
OUT=$("$ROOT/linchat/bin/python" -m pytest -q --tb=short -p no:cacheprovider 2>&1 | tail -40)
RC=$?
DUR=$(( $(date +%s) - START ))
SUMMARY=$(echo "$OUT" | grep -E '^[0-9]+ (passed|failed)|passed|failed|error' | tail -1)
PASSED=$(echo "$SUMMARY" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' || echo 0)
FAILED=$(echo "$SUMMARY" | grep -oE '[0-9]+ (failed|error)' | grep -oE '[0-9]+' | paste -sd+ | bc 2>/dev/null || echo 0)
[ -z "$FAILED" ] && FAILED=0

cat > "$ROOT/refactor/loop/last-validation.json" <<EOF
{"ts": "$(date -Is)", "passed": ${PASSED:-0}, "failed": ${FAILED}, "duration_s": $DUR,
 "green": $([ "$RC" = 0 ] && [ "$FAILED" = 0 ] && echo true || echo false),
 "summary": "$(echo "$SUMMARY" | tr '"' "'" )"}
EOF
echo "$SUMMARY (rc=$RC, ${DUR}s)"
[ "$RC" = 0 ] && [ "$FAILED" = 0 ]
