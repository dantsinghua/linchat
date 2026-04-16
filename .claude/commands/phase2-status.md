---
description: 查看 Phase 2 整体进度看板。无参数。
---

显示所有 27 个 batch 的当前状态，给安琳一个全局视图。

## 执行

```bash
# 1. 总览
TOTAL=$(jq '.batches | length' refactor/04-refactor-plan.json)
COMPLETED=$(jq '[.batches[] | select(.actual_status == "completed")] | length' refactor/04-refactor-plan.json)
EXECUTED=$(jq '[.batches[] | select(.actual_status == "executed")] | length' refactor/04-refactor-plan.json)
FAILED=$(jq '[.batches[] | select(.actual_status == "failed")] | length' refactor/04-refactor-plan.json)
SKIPPED=$(jq '[.batches[] | select(.actual_status == "skipped")] | length' refactor/04-refactor-plan.json)
PENDING=$((TOTAL - COMPLETED - EXECUTED - FAILED - SKIPPED))

echo "═══════════════════════════════════════════════════════════"
echo "  LinChat Phase 2 进度看板"
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "总进度: $COMPLETED / $TOTAL ($(echo "scale=1; $COMPLETED * 100 / $TOTAL" | bc)%)"
echo ""
echo "✅ 已完成:    $COMPLETED"
echo "🔄 已执行待验证: $EXECUTED"
echo "❌ 失败:      $FAILED"
echo "⏭️ 跳过:      $SKIPPED"
echo "⏳ 待开始:    $PENDING"
echo ""

# 2. 按阶段分组进度
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  按阶段分组"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

for phase in phase_p0_day1_fix phase_p0_observability phase_p1_voice_slo \
              phase_p1_other_perf phase_p2_deadcode phase_p2_shim_cleanup \
              phase_p2_settings_split phase_p2_voice_mixin phase_p2_except_reduce \
              phase_p2_doc_cleanup phase_p3_test_coverage; do
  
  BATCHES=$(jq -r ".phased_rollout.${phase}[]?" refactor/04-refactor-plan.json)
  [ -z "$BATCHES" ] && continue
  
  echo ""
  echo "📂 $phase:"
  for bid in $BATCHES; do
    STATUS=$(jq -r ".batches[] | select(.id == \"$bid\") | .actual_status // \"pending\"" refactor/04-refactor-plan.json)
    TITLE=$(jq -r ".batches[] | select(.id == \"$bid\") | .title" refactor/04-refactor-plan.json)
    
    case "$STATUS" in
      completed)  ICON="✅";;
      executed)   ICON="🔄";;
      failed)     ICON="❌";;
      skipped)    ICON="⏭️";;
      *)          ICON="⏳";;
    esac
    echo "   $ICON $bid: $TITLE"
  done
done

# 3. SLO 进度（如有）
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  端到端语音延迟进度（5s SLO）"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if ls refactor/baselines/*-after.json 2>/dev/null > /dev/null; then
  echo ""
  echo "Baseline (Phase 1): P50 = 10.4s"
  echo ""
  for f in $(ls -t refactor/baselines/*-after.json 2>/dev/null); do
    BID=$(basename $f -after.json)
    P50=$(jq -r '.pipeline.p50' $f 2>/dev/null)
    [ -n "$P50" ] && echo "  $BID 后: P50 = ${P50}s"
  done
  
  LATEST=$(ls -t refactor/baselines/*-after.json 2>/dev/null | head -1)
  if [ -n "$LATEST" ]; then
    LATEST_P50=$(jq -r '.pipeline.p50' $LATEST)
    if (( $(echo "$LATEST_P50 < 5" | bc -l) )); then
      echo ""
      echo "  🎯 已达 5s SLO ✅"
    else
      echo ""
      echo "  ⏳ 距 5s SLO 还差 $(echo "$LATEST_P50 - 5" | bc -l)s"
    fi
  fi
else
  echo ""
  echo "  尚未采集 SLO 数据"
fi

# 4. 失败 batch 详情（如有）
if [ "$FAILED" -gt 0 ]; then
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  ❌ 失败 batch 详情"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  jq -r '.batches[] | select(.actual_status == "failed") | "  \(.id): \(.title)"' refactor/04-refactor-plan.json
  echo ""
  echo "  详情见 refactor/batches/<id>-progress.txt"
fi

# 5. 推荐下一步
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  推荐下一步"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# 找第一个 pending 且依赖已完成的 batch
NEXT=$(jq -r '
  .batches[] |
  select(.actual_status == null or .actual_status == "pending") |
  .id
' refactor/04-refactor-plan.json | head -1)

if [ -n "$NEXT" ]; then
  echo ""
  echo "  /phase2-start $NEXT"
else
  echo ""
  echo "  🎉 所有 batch 都已处理完毕！"
fi
```

## 完成后

仅在主对话打印上述输出，不修改任何文件。
