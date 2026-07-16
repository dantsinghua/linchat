#!/bin/bash
# Perf benchmark for the refactor loop. Metrics:
#   voice_e2e_p50_ms  — 会话响应总时长（primary; from measure-voice-latency.sh backend-log pairs）
#   api_p95_ms        — captcha API via nginx, 40 samples (exercises nginx+uvicorn+redis stack)
# Usage: perf_bench.sh --baseline   (save refactor/loop/baseline-metrics.json)
#        perf_bench.sh              (measure + print improvement % vs baseline)
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BASE="$ROOT/refactor/loop/baseline-metrics.json"

api_p95() {
  for i in $(seq 1 40); do
    curl -s -o /dev/null -w '%{time_total}\n' "http://127.0.0.1:8080/linchat/api/v1/auth/captcha/" 2>/dev/null
  done | sort -n | awk '{a[NR]=$1} END{printf "%.0f", a[int(NR*0.95)]*1000}'
}

VOICE=$( { "$ROOT/scripts/measure-voice-latency.sh" 20 2>/dev/null || true; } | /usr/bin/python3 -c '
import json,sys
try: print(int(json.load(sys.stdin)["pipeline_ms"]["p50"]))
except Exception: print(0)' | tail -1)
API=$(api_p95)
NOW=$(cat <<EOF
{"ts": "$(date -Is)", "voice_e2e_p50_ms": ${VOICE:-0}, "api_p95_ms": ${API:-0}}
EOF
)
if [ "${1:-}" = "--baseline" ]; then
  echo "$NOW" > "$BASE" && echo "baseline saved: $NOW"
  exit 0
fi
echo "current:  $NOW"
if [ -f "$BASE" ]; then
  echo "baseline: $(cat "$BASE")"
  /usr/bin/python3 - "$BASE" <<EOF
import json, sys
b = json.load(open(sys.argv[1])); c = json.loads('''$NOW''')
ok = True
for k in ("voice_e2e_p50_ms", "api_p95_ms"):
    if b.get(k) and c.get(k):
        imp = (b[k] - c[k]) / b[k] * 100
        print(f"{k}: {b[k]} -> {c[k]}  improvement {imp:+.1f}%")
        if k == "voice_e2e_p50_ms": ok = imp >= 20
    else:
        print(f"{k}: insufficient data (baseline={b.get(k)}, current={c.get(k)})")
        if k == "voice_e2e_p50_ms": ok = False  # primary metric must have data
print("PERF_TARGET:", "MET" if ok else "NOT_MET")
EOF
else
  echo "no baseline yet — run with --baseline first"
fi
