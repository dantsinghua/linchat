---
description: 验证已 EXECUTED 的 batch。用法：/phase2-validate <batch-id>
---

执行 Phase 2c 验证。**这一步包含手动验证清单，需要安琳配合**。

## 前置检查

```bash
test -f refactor/batches/<batch-id>-progress.txt || (echo "❌ progress 不存在"; exit 1)

STATUS=$(grep "^STATUS:" refactor/batches/<batch-id>-progress.txt | tail -1)
case "$STATUS" in
  *EXECUTED*) echo "✅ 状态正确，可验证";;
  *COMPLETED*) echo "ℹ️ 已 COMPLETED，无需重新验证"; exit 0;;
  *PLAN_READY*) echo "❌ 还未执行，请先 /phase2-execute <id>"; exit 1;;
  *FAILED*) echo "❌ 执行失败，无法验证。考虑 /phase2-start <id> 重新规划"; exit 1;;
  *) echo "❌ 状态异常: $STATUS"; exit 1;;
esac
```

## 执行

调用 `batch-validator` 子代理：

> 验证 batch `<batch-id>`。
> 严格按 `.claude/agents/batch-validator.md` 中定义的 8 步流程。
> 自动化验证你独立完成；手动验证打印清单等安琳回复。
> 完成后写入 refactor/batches/<id>-validation.md。

## 验证流程中

`batch-validator` 在 Step 5 会打印手动验证清单，主对话停在那里等待。

安琳回复格式：
- `validation pass` — 全部手动验证通过
- `validation fail: <原因>` — 手动验证发现问题
- `validation skip` — 跳过手动验证（不推荐，但允许）

## 完成后

### 通过情况
```
✅ Batch <id> 验证完成（COMPLETED）

汇总：
- 自动化：N/N ✅
- 回归：M 个 app 全绿
- (P1) SLO 削减：X.Xs（预期 -A~Bs ✅）
- 手动：K/K ✅

📊 累计进度：
- 已完成 N / 27 个 batch
- 当前阶段：<P0 Day-1 / P0 观测性 / P1 语音 / ...>
- 距下一里程碑：<还需 X 个 batch>

下一步建议：
- /phase2-start <next-batch-id>  — 推进下一个 batch
- /phase2-status                 — 查看完整进度看板
```

### 失败情况
```
❌ Batch <id> 验证失败（VALIDATION_FAILED）

失败项：
- <具体失败>

⚠️ 此 batch 已 push 到 refactor/<id> 但未合并主干。

操作选项：
1. /phase2-rollback <id>  — 自动 git revert + 删除 batch 分支
2. 手动调试：
   git checkout refactor/<id>
   cat refactor/batches/<id>-validation.md
3. /phase2-skip <id>      — 接受失败标记，继续下一批（但保留分支供后续修补）
```

## 禁止

- 禁止跳过手动验证清单（即使你觉得"应该没问题"）
- 禁止在没有 before/after 数据时声称 SLO 达成
- 禁止自动 rollback（必须等安琳明确指令）
