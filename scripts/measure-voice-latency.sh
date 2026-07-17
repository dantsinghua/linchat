#!/bin/bash
# =============================================================================
# LinChat 端到端语音延迟测量脚本
#
# 原理：从后端日志中提取时间戳事件对，计算各阶段延迟
#
# 日志事件链（ambient 模式）：
#   Pipeline launch → TTS WS connected → TTS audio.done → HA 音箱 TTS 播报成功
#
# 延迟分解：
#   Pipeline→TTS connected = LLM 推理（含 Agent 路由 + 流式生成）
#   TTS connected→audio.done = TTS 合成
#   audio.done→HA 播报成功 = HA 下发
#   Pipeline→HA 播报成功 = 端到端 Pipeline 延迟
#
# 用法：./scripts/measure-voice-latency.sh [N=20]
# 输出：JSON 到 stdout
# =============================================================================

set -e

N="${1:-20}"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
LOG_FILE="/tmp/linchat-backend.log"
TMP_DIR=$(mktemp -d)
trap "rm -rf $TMP_DIR" EXIT

# -----------------------------------------------------------------------------
# 检查依赖
# -----------------------------------------------------------------------------

if ! command -v python3 &> /dev/null; then
  echo '{"error": "python3 未安装"}' >&2
  exit 1
fi

if [ ! -f "$LOG_FILE" ]; then
  cat <<EOF
{
  "error": "无法定位 LinChat 日志",
  "checked_path": "$LOG_FILE",
  "hint": "确认后端已通过 services.sh 启动",
  "timestamp": "$TIMESTAMP"
}
EOF
  exit 1
fi

# -----------------------------------------------------------------------------
# 用 Python 解析日志事件，配对计算延迟
# -----------------------------------------------------------------------------

python3 - "$LOG_FILE" "$N" "$TIMESTAMP" <<'PYEOF'
import sys
import re
import json
from datetime import datetime

log_file = sys.argv[1]
n_requested = int(sys.argv[2])
timestamp = sys.argv[3]

# 日志时间格式: "INFO 2026-04-16 15:47:21,315 Pipeline launch: ..."
TS_RE = re.compile(r'^(?:INFO|WARNING|DEBUG|ERROR)\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2},\d{3})\s+(.*)')

def parse_ts(ts_str):
    return datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S,%f")

def delta_ms(t1, t2):
    return (t2 - t1).total_seconds() * 1000

# 读取日志
events = []
summary_pipelines = []  # batch-07+ 新格式：latency.summary 单行 JSON 汇总

def try_parse_summary(line):
    """解析 batch-07 引入的 latency.summary JSON 汇总行（JSONFormatter 整行 JSON）。"""
    if 'latency.summary' not in line:
        return None
    start = line.find('{')
    if start < 0:
        return None
    try:
        obj = json.loads(line[start:])
    except (ValueError, TypeError):
        return None
    if obj.get('stage') != 'latency.summary':
        return None
    total = obj.get('total_from_pipeline_ms')
    if total is None:
        return None
    hops = obj.get('hops') or {}
    ts_raw = obj.get('ts') or obj.get('timestamp') or obj.get('asctime') or ''
    launch = str(ts_raw)[11:19] if len(str(ts_raw)) >= 19 else str(ts_raw)
    return {
        'seg': obj.get('seg', 'unknown'),
        'total_ms': float(total),
        'llm_ms': hops.get('llm_total'),
        'tts_ms': (hops.get('tts_connect') or 0) + (hops.get('tts_synth') or 0)
                  if ('tts_synth' in hops or 'tts_connect' in hops) else None,
        'ha_ms': hops.get('ha'),
        'launch': launch,
    }

with open(log_file, 'r', errors='replace') as f:
    for line in f:
        line = line.strip()
        s = try_parse_summary(line)
        if s is not None:
            summary_pipelines.append(s)
            continue
        m = TS_RE.match(line)
        if not m:
            continue
        ts_str, body = m.group(1), m.group(2)

        if 'Pipeline launch:' in body and 'mode=ambient' in body:
            seg_m = re.search(r'seg=(\w+)', body)
            seg = seg_m.group(1) if seg_m else 'unknown'
            events.append(('pipeline_launch', parse_ts(ts_str), seg))
        elif 'TTS WS connected:' in body:
            events.append(('tts_connected', parse_ts(ts_str), None))
        elif 'TTS audio.done received' in body:
            events.append(('tts_done', parse_ts(ts_str), None))
        elif 'HA 音箱 TTS 播报成功' in body:
            events.append(('ha_done', parse_ts(ts_str), None))

# 配对：找 pipeline_launch → tts_connected → tts_done → ha_done 序列
pipelines = []
i = 0
while i < len(events):
    if events[i][0] != 'pipeline_launch':
        i += 1
        continue

    launch_ts = events[i][1]
    seg = events[i][2]
    tts_conn_ts = tts_done_ts = ha_done_ts = None

    # 向后搜索后续事件（30s 窗口内）
    j = i + 1
    while j < len(events):
        evt_type, evt_ts, _ = events[j]
        elapsed = (evt_ts - launch_ts).total_seconds()
        if elapsed > 60:  # 超过 60s 认为不属于同一次 pipeline
            break
        if evt_type == 'pipeline_launch':
            break  # 遇到下一个 pipeline，当前配对结束
        if evt_type == 'tts_connected' and tts_conn_ts is None:
            tts_conn_ts = evt_ts
        elif evt_type == 'tts_done' and tts_done_ts is None:
            tts_done_ts = evt_ts
        elif evt_type == 'ha_done' and ha_done_ts is None:
            ha_done_ts = evt_ts
            break  # 完整配对
        j += 1

    if ha_done_ts:
        total_ms = delta_ms(launch_ts, ha_done_ts)
        llm_ms = delta_ms(launch_ts, tts_conn_ts) if tts_conn_ts else None
        tts_ms = delta_ms(tts_conn_ts, tts_done_ts) if (tts_conn_ts and tts_done_ts) else None
        ha_ms = delta_ms(tts_done_ts, ha_done_ts) if (tts_done_ts and ha_done_ts) else None

        pipelines.append({
            'seg': seg,
            'total_ms': total_ms,
            'llm_ms': llm_ms,
            'tts_ms': tts_ms,
            'ha_ms': ha_ms,
            'launch': launch_ts.strftime('%H:%M:%S'),
        })

    i = j if j > i else i + 1

# 合并新格式汇总行（batch-07+）与旧事件链配对结果；日志本身按时间有序，
# 两个列表各自有序，按出现顺序简单拼接（同一次 pipeline 不会同时产生两种记录）
pipelines = pipelines + summary_pipelines

# 取最近 N 个
pipelines = pipelines[-n_requested:]

if not pipelines:
    print(json.dumps({
        "error": "日志中未找到完整的 ambient Pipeline 事件链",
        "hint": "需要 Pipeline launch → TTS WS connected → TTS audio.done → HA 播报成功 完整序列",
        "events_found": len(events),
        "timestamp": timestamp,
    }, ensure_ascii=False, indent=2))
    sys.exit(1)

# 计算分位数
def percentiles(values):
    if not values:
        return {"p25": None, "p50": None, "p75": None, "max": None, "min": None}
    s = sorted(values)
    n = len(s)
    def pct(p):
        idx = max(0, min(n - 1, int(n * p)))
        return round(s[idx])
    return {"p25": pct(0.25), "p50": pct(0.50), "p75": pct(0.75), "max": round(s[-1]), "min": round(s[0])}

totals = [p['total_ms'] for p in pipelines]
llms = [p['llm_ms'] for p in pipelines if p['llm_ms'] is not None]
ttss = [p['tts_ms'] for p in pipelines if p['tts_ms'] is not None]
has = [p['ha_ms'] for p in pipelines if p['ha_ms'] is not None]

total_pct = percentiles(totals)
llm_pct = percentiles(llms)
tts_pct = percentiles(ttss)
ha_pct = percentiles(has)

# SLO 判定（Pipeline P50 < 5000ms）
slo_met = total_pct['p50'] is not None and total_pct['p50'] < 5000

result = {
    "timestamp": timestamp,
    "samples": len(pipelines),
    "n_requested": n_requested,
    "log_file": log_file,
    "pipeline_ms": total_pct,
    "pipeline_s": {
        k: round(v / 1000, 1) if v is not None else None
        for k, v in total_pct.items()
    },
    "breakdown_ms": {
        "llm_inference": llm_pct,
        "tts_synthesis": tts_pct,
        "ha_dispatch": ha_pct,
    },
    "breakdown_s": {
        "llm_inference": {k: round(v / 1000, 1) if v is not None else None for k, v in llm_pct.items()},
        "tts_synthesis": {k: round(v / 1000, 1) if v is not None else None for k, v in tts_pct.items()},
        "ha_dispatch": {k: round(v / 1000, 1) if v is not None else None for k, v in ha_pct.items()},
    },
    "slo_target_s": 5.0,
    "slo_met": slo_met,
    "recent_samples": [
        {
            "time": p['launch'],
            "total_s": round(p['total_ms'] / 1000, 1),
            "llm_s": round(p['llm_ms'] / 1000, 1) if p['llm_ms'] else None,
            "tts_s": round(p['tts_ms'] / 1000, 1) if p['tts_ms'] else None,
            "ha_s": round(p['ha_ms'] / 1000, 1) if p['ha_ms'] else None,
        }
        for p in pipelines[-5:]  # 最近 5 个样本明细
    ],
    "notes": "延迟不含聚合等待(~3s)和 ASR 时间(~1s)，实际端到端再加 ~4s。"
}

print(json.dumps(result, ensure_ascii=False, indent=2))
PYEOF
