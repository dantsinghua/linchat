---
description: 启动一个 batch 的 Phase 2a (initializer)。用法：/phase2-start <batch-id>，例如 /phase2-start batch-01
---

执行 Phase 2a，为指定 batch 生成详细执行计划。**只生成 plan，不改代码**。

## 前置参数

由用户指定 `<batch-id>`（如 `batch-01`、`batch-08`）。
如果用户没指定，问她要：

```
请指定要启动的 batch ID。
建议从 04-refactor-plan.json 的 phased_rollout 顺序选取，例如：
- /phase2-start batch-01  (P0 Day-1 fix - 声纹 bug)
- /phase2-start batch-02  (P0 Day-1 fix - 失败测试 1)
```

## 前置检查

### 1. 环境检查

```bash
pwd  # 应在 linchat 项目根目录或 worktree 内
which python | grep -q linchat || echo "⚠️ 虚拟环境未激活，建议先 source .../linchat/bin/activate"
test -f refactor/04-refactor-plan.json || (echo "❌ Phase 1 产物缺失"; exit 1)
test -f docs/legacy-and-debts.md || (echo "❌ legacy-and-debts.md 缺失"; exit 1)
```

### 2. Batch 存在性检查

```bash
EXISTS=$(jq ".batches[] | select(.id == \"<batch-id>\") | .id" refactor/04-refactor-plan.json)
[ -z "$EXISTS" ] && (echo "❌ batch-id 不存在于 04-refactor-plan.json"; exit 1)
```

### 3. Batch 状态检查

```bash
# 如果该 batch 已经 COMPLETED，提示用户
if [ -f refactor/batches/<batch-id>-progress.txt ]; then
  STATUS=$(grep "^STATUS:" refactor/batches/<batch-id>-progress.txt | tail -1)
  case "$STATUS" in
    *COMPLETED*) echo "⚠️ batch <id> 已经 COMPLETED。是否要重做？(yes/no)"; ;;
    *EXECUTED*) echo "⚠️ batch <id> 已 EXECUTED 待 validate。建议运行 /phase2-validate <id>"; ;;
    *FAILED*) echo "⚠️ batch <id> 之前 FAILED。重新启动会覆盖之前的记录"; ;;
    *PLAN_READY*) echo "⚠️ batch <id> 已有 plan，是否重新生成？"; ;;
  esac
fi
```

### 4. 依赖检查

```bash
DEPS=$(jq -r ".batches[] | select(.id == \"<batch-id>\") | .depends_on[]" refactor/04-refactor-plan.json)
for dep in $DEPS; do
  STATUS_FILE="refactor/batches/${dep}-progress.txt"
  if [ ! -f "$STATUS_FILE" ] || ! grep -q "STATUS: COMPLETED" "$STATUS_FILE"; then
    echo "❌ 依赖 $dep 未完成。先做依赖。"
    exit 1
  fi
done
```

### 5. Worktree 检查（推荐但非强制）

```bash
CURRENT_BRANCH=$(git branch --show-current)
if [ "$CURRENT_BRANCH" = "main" ] || [ "$CURRENT_BRANCH" = "master" ]; then
  echo "⚠️ 当前在 $CURRENT_BRANCH 分支。"
  echo "强烈建议先创建 worktree："
  echo "  git worktree add ../linchat-<batch-id> -b refactor/<batch-id>"
  echo "  cd ../linchat-<batch-id>"
  echo ""
  echo "继续在 main 上执行？(不推荐) 输入 yes 继续，其他取消。"
fi
```

## 执行

调用 `batch-initializer` 子代理：

> 为 batch `<batch-id>` 生成详细执行计划。
> 严格按 `.claude/agents/batch-initializer.md` 中定义的 7 步执行。
> 产出：refactor/batches/<batch-id>-plan.md + progress.txt
> 完成后返回 ≤ 300 字摘要。

## 完成后

1. 检查 plan.md 和 progress.txt 都已生成
2. 检查 progress.txt 状态为 `PLAN_READY`
3. 在主对话给安琳：

```
✅ Batch <id> 计划已生成

文件：refactor/batches/<id>-plan.md
预估：N 个文件 / M 行变更 / K 个 session
风险：<risk>
SLO 影响：<blocks_slo or "无">

📋 关键发现：
<initializer 摘要的核心 1-2 条>

⚠️ 需要安琳确认的事项：
<plan.md 第 7 节内容；如果是"无阻塞"也明确说出>

下一步：
1. 打开 refactor/batches/<id>-plan.md 仔细 review
2. 确认无误后运行 /phase2-execute <id>
3. 如需修改 plan，直接编辑 plan.md，然后再运行 /phase2-execute <id>
```

## 禁止

- 禁止在生成 plan 后立即调用 batch-executor（必须等安琳 review）
- 禁止跳过依赖检查
- 禁止在 main 分支上推进未明确同意的执行
