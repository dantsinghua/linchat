---
description: Phase 2 辅助命令集合：rollback / skip / resume。用法：/phase2-rollback <id>、/phase2-skip <id>、/phase2-resume <id>
---

根据用户调用的子命令分别处理。

## /phase2-rollback <batch-id>

撤销已 EXECUTED 但 VALIDATION_FAILED 的 batch。

```bash
# 1. 检查状态
STATUS=$(grep "^STATUS:" refactor/batches/<id>-progress.txt | tail -1)
case "$STATUS" in
  *VALIDATION_FAILED*|*EXECUTED*) echo "✅ 可回滚";;
  *COMPLETED*) echo "❌ 已 COMPLETED 不应回滚（如确需回滚请手动 git revert）"; exit 1;;
  *) echo "❌ 状态异常: $STATUS"; exit 1;;
esac

# 2. 找到 commit hash
COMMIT=$(grep "Commit:" refactor/batches/<id>-progress.txt | awk '{print $2}' | head -1)

# 3. 二次确认
echo "⚠️ 即将 git revert $COMMIT"
echo "影响：本 batch 的所有改动会被反向 commit。"
echo "确认回滚？(yes/no)"
# 等待 yes

# 4. 执行 revert
git revert --no-edit $COMMIT

# 5. push（同样 push 到 batch 分支，让分支历史也反映回滚）
git push origin refactor/<id>

# 6. 更新 progress
cat >> refactor/batches/<id>-progress.txt <<EOF

## Rollback (<时间>)
- Reverted commit: $COMMIT
- Revert commit: $(git rev-parse HEAD)

STATUS: ROLLED_BACK
EOF

# 7. 更新 04-refactor-plan.json
jq "(.batches[] | select(.id == \"<id>\")) += {\"actual_status\": \"rolled_back\"}" \
  refactor/04-refactor-plan.json > /tmp/p.json && mv /tmp/p.json refactor/04-refactor-plan.json

echo "✅ Batch <id> 已回滚。可重新 /phase2-start <id> 修改 plan 后再执行。"
```

---

## /phase2-skip <batch-id>

接受 batch 失败，标记为 SKIPPED 并继续推进。**不撤销已 push 的代码**（如果有）。

```bash
# 1. 检查状态
STATUS=$(grep "^STATUS:" refactor/batches/<id>-progress.txt | tail -1)
case "$STATUS" in
  *FAILED*|*VALIDATION_FAILED*) echo "✅ 可跳过";;
  *COMPLETED*) echo "❌ 已 COMPLETED 不应跳过"; exit 1;;
  *) echo "⚠️ 当前状态 $STATUS，跳过会标记为放弃。继续？(yes/no)";;
esac

# 2. 二次确认
echo "⚠️ 跳过 batch <id> 意味着本轮重构不再做这个 batch。"
echo "下游依赖此 batch 的其他 batch 可能也需要重新评估。"
echo ""

# 3. 列出受影响的下游 batch
DOWNSTREAM=$(jq -r ".batches[] | select(.depends_on | index(\"<id>\")) | .id" refactor/04-refactor-plan.json)
if [ -n "$DOWNSTREAM" ]; then
  echo "受影响的下游 batch："
  echo "$DOWNSTREAM"
  echo ""
fi

echo "确认跳过？(yes/no)"
# 等待 yes

# 4. 标记
cat >> refactor/batches/<id>-progress.txt <<EOF

## Skipped (<时间>)
- Reason: 用户确认跳过
- Downstream affected: $DOWNSTREAM

STATUS: SKIPPED
EOF

jq "(.batches[] | select(.id == \"<id>\")) += {\"actual_status\": \"skipped\", \"skipped_at\": \"$(date -Iseconds)\"}" \
  refactor/04-refactor-plan.json > /tmp/p.json && mv /tmp/p.json refactor/04-refactor-plan.json

# 5. 推荐下一步
NEXT=$(jq -r ".batches[] | select(.actual_status == null) | .id" refactor/04-refactor-plan.json | head -1)
echo "✅ Batch <id> 标记为 SKIPPED。"
echo "下一步：/phase2-start $NEXT"
```

---

## /phase2-resume <batch-id>

从中断的 batch 续跑。读 progress.txt 判断当前在哪个阶段，调对应的 agent。

```bash
# 1. 读状态
STATUS=$(grep "^STATUS:" refactor/batches/<id>-progress.txt | tail -1)

# 2. 根据状态调对应阶段
case "$STATUS" in
  *PLAN_READY*)
    echo "状态 PLAN_READY → 续跑 executor"
    echo "运行 /phase2-execute <id>"
    ;;
  *EXECUTING*)
    echo "⚠️ 状态 EXECUTING（可能上次中断在执行中）"
    echo "建议先检查 git status，确认未提交改动"
    echo "如果想从头重做：git reset --hard before-<id> && /phase2-execute <id>"
    echo "如果想接着改：手动审查 progress.txt 中已完成的 step，然后 /phase2-execute <id>"
    ;;
  *EXECUTED*)
    echo "状态 EXECUTED → 续跑 validator"
    echo "运行 /phase2-validate <id>"
    ;;
  *VALIDATION_FAILED*|*FAILED*)
    echo "状态 $STATUS → 选择处理方式"
    echo "  /phase2-rollback <id>  — 回滚重做"
    echo "  /phase2-skip <id>      — 接受失败继续下一批"
    echo "  /phase2-start <id>     — 重新生成 plan 后重试"
    ;;
  *COMPLETED*|*SKIPPED*|*ROLLED_BACK*)
    echo "状态 $STATUS（终态）→ 无需 resume"
    NEXT=$(jq -r ".batches[] | select(.actual_status == null) | .id" refactor/04-refactor-plan.json | head -1)
    echo "下一个待执行 batch：$NEXT"
    ;;
esac
```

## 禁止

- 禁止在 phase2-rollback 中跳过二次确认
- 禁止在 phase2-skip 中不列出下游 batch 影响
- 禁止 force push 或修改主干分支
