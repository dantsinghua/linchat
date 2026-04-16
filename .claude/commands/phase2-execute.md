---
description: 执行已 review 通过的 batch plan，调用 batch-executor 改代码 + commit + push。用法：/phase2-execute <batch-id>
---

执行 Phase 2b，按 plan 改代码。**这一步会真正修改业务代码**。

## 前置检查

### 1. Plan 必须已 review

```bash
test -f refactor/batches/<batch-id>-plan.md || (echo "❌ plan 不存在，先跑 /phase2-start <id>"; exit 1)
test -f refactor/batches/<batch-id>-progress.txt || (echo "❌ progress 不存在"; exit 1)

STATUS=$(grep "^STATUS:" refactor/batches/<batch-id>-progress.txt | tail -1)
case "$STATUS" in
  *PLAN_READY*) echo "✅ 状态正确，可执行";;
  *EXECUTED*) echo "⚠️ 已执行过，是否重做？(yes/no)"; ;;
  *COMPLETED*) echo "❌ 已完成，无需重新执行"; exit 1;;
  *) echo "❌ 状态异常: $STATUS"; exit 1;;
esac
```

### 2. 强制确认安琳已 review plan

在主对话明确询问：

```
🚨 即将执行 batch <id> 的代码改动。

确认事项：
1. 你是否已 review refactor/batches/<id>-plan.md？
2. plan 中第 7 节"需要安琳确认的事项"是否已全部解决？
3. 如果是 P1 voice batch，是否已确认涉及的 voice 模块改动方向？

请明确回复 "execute confirmed" 才会继续。
其他任何回复都会取消执行。
```

**严格等待安琳明确回复 "execute confirmed"**。

### 3. Git 状态检查

```bash
# 必须 clean
DIRTY=$(git status --porcelain | wc -l)
if [ "$DIRTY" != "0" ]; then
  echo "❌ Git 有未提交改动，无法执行 batch。"
  echo "请先 commit 或 stash。"
  exit 1
fi

# 记录起始点
git tag "before-<batch-id>" 2>/dev/null || git tag -d "before-<batch-id>" && git tag "before-<batch-id>"
echo "✅ 已打标签 before-<batch-id>，失败时可 git reset --hard before-<batch-id>"
```

### 4. 服务运行状态确认

```bash
# 仅警告，不强制
./scripts/services.sh status 2>/dev/null && echo "ℹ️ 应用服务运行中" || echo "ℹ️ 应用服务未运行（不影响代码改动，但影响手动验证）"
```

## 执行

调用 `batch-executor` 子代理：

> 执行 batch `<batch-id>`。
> 严格按 `refactor/batches/<batch-id>-plan.md` 第 3 节"详细改动计划"执行。
> 严格按 `.claude/agents/batch-executor.md` 中定义的 9 步流程。
> 失败重试上限 3 次，超过则标记 FAILED 并停止。
> 完成后自动 commit + push 到 refactor/<batch-id> 分支。
> 完成后返回 ≤ 300 字摘要。

## 完成后

### 成功路径
```
✅ Batch <id> 执行完成

改动统计：
- 文件：N 个
- 新增行：+M
- 删除行：-K
- Commit: <hash>
- 已 push 到 origin/refactor/<id>

测试：
- Lint: ✅
- 涉及 app 测试: ✅ N/N 通过

(P1 batch 才有) 性能：
- before P50: X.Xs
- after P50: Y.Ys
- 削减: Z.Zs

下一步：
1. 运行 /phase2-validate <id> 进行最终验证（包括手动验证清单）
2. 验证通过后即可推进下一个 batch
```

### 失败路径
```
❌ Batch <id> 执行失败（已自动回退）

失败原因：<具体>
失败位置：<文件 / 步骤>

按你之前的策略（自动跳过失败 batch）：
- progress.txt 已标记 STATUS: FAILED
- 04-refactor-plan.json 中 actual_status 也已更新

下一步选项：
1. /phase2-start <next-batch-id>  — 跳过本批继续
2. /phase2-start <id>             — 重新生成 plan 并重试
3. 手动调试：
   git tag before-<id>            ← 回退点已存
   cat refactor/batches/<id>-progress.txt
```

## 禁止

- 禁止在用户未明确回复 "execute confirmed" 时启动
- 禁止在 git 不 clean 时启动
- 禁止跳过状态检查（PLAN_READY 是唯一允许进入的状态）
- 禁止在 main / master 分支上直接 push（executor 应 push 到 refactor/<id> 分支）
