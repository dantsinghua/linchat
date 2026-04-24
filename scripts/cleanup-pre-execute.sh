#!/usr/bin/env bash
# cleanup-pre-execute.sh — 清理 worktree 准备进入 /phase2-execute <batch-id>
#
# 职责：
#   1. 还原 claude-mem 自动改动的 CLAUDE.md（modified）
#   2. 删除 claude-mem 新建的空 CLAUDE.md 壳（untracked）
#   3. 把 refactor/batches/<batch-id>-{plan.md,progress.txt} 提交成 prep commit
#   4. 输出最终 git status，供安琳核对
#
# 前提：claude-mem 是本工作流中 CLAUDE.md 的唯一写入者；若你手改过某个
# CLAUDE.md 又没提交，先自行 commit/stash，再跑本脚本。
#
# 不触碰业务代码。只动：CLAUDE.md、refactor/batches/<batch-id>-*。
#
# 使用：bash scripts/cleanup-pre-execute.sh batch-05
# 退出：0 = 干净可 execute；1 = 还有人工待处理项

set -euo pipefail

BATCH_ID="${1:-}"
if [[ -z "$BATCH_ID" ]]; then
  echo "❌ 用法：$0 <batch-id>   例如：$0 batch-05"
  exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

PLAN="refactor/batches/${BATCH_ID}-plan.md"
PROG="refactor/batches/${BATCH_ID}-progress.txt"

# 从 plan 第一行标题抽取 batch 描述作为 commit message 用（失败则 fallback）
plan_title=""
if [[ -f "$PLAN" ]]; then
  # plan 第 2 行通常是 "# Batch xxx Plan — <title>"；退而求其次取第一个 "#"
  plan_title=$(grep -m1 -E '^#\s' "$PLAN" | sed -E 's/^#+\s*//')
fi
commit_msg="plan(${BATCH_ID}): ${plan_title:-$BATCH_ID plan + 决策}"

echo "===== [1/4] 还原 claude-mem 改过的 CLAUDE.md ====="
mapfile -t MODIFIED_CLAUDE_MD < <(git diff --name-only -- '*CLAUDE.md')
if [[ ${#MODIFIED_CLAUDE_MD[@]} -gt 0 ]]; then
  for f in "${MODIFIED_CLAUDE_MD[@]}"; do
    echo "  ↺ restore $f"
    git checkout -- "$f"
  done
else
  echo "  （无 modified CLAUDE.md）"
fi

echo
echo "===== [2/4] 删除 claude-mem 新建的空 CLAUDE.md 壳 ====="
mapfile -t UNTRACKED_CLAUDE_MD < <(git ls-files --others --exclude-standard -- '*CLAUDE.md')
if [[ ${#UNTRACKED_CLAUDE_MD[@]} -gt 0 ]]; then
  for f in "${UNTRACKED_CLAUDE_MD[@]}"; do
    # 壳特征：去掉空白后仅剩 <claude-mem-context></claude-mem-context>
    normalized=$(tr -d '[:space:]' < "$f")
    if [[ "$normalized" == "<claude-mem-context></claude-mem-context>" ]]; then
      echo "  🗑  rm $f"
      rm -- "$f"
    else
      echo "  ⚠️  $f 非空壳（含真内容），跳过"
    fi
  done
else
  echo "  （无 untracked CLAUDE.md）"
fi

echo
echo "===== [3/4] 提交 batch prep 产物 ====="
if [[ ! -f "$PLAN" || ! -f "$PROG" ]]; then
  echo "  ⚠️  未找到 $PLAN 或 $PROG，跳过 commit"
else
  TO_ADD=()
  # untracked
  for f in "$PLAN" "$PROG"; do
    if ! git ls-files --error-unmatch "$f" >/dev/null 2>&1; then
      TO_ADD+=("$f")
    fi
  done
  # modified（已 tracked 但有改动）
  mapfile -t MODIFIED_PREP < <(git diff --name-only -- "$PLAN" "$PROG")
  for f in "${MODIFIED_PREP[@]}"; do
    [[ -n "$f" ]] && TO_ADD+=("$f")
  done

  if [[ ${#TO_ADD[@]} -eq 0 ]]; then
    echo "  （plan/progress 已在最新 commit 中，跳过）"
  else
    printf '  + %s\n' "${TO_ADD[@]}"
    git add "${TO_ADD[@]}"
    git commit -m "$commit_msg"
    echo "  ✅ prep commit 已创建：$commit_msg"
  fi
fi

echo
echo "===== [4/4] 最终 git status ====="
git status --short
echo
REMAINING=$(git status --porcelain | wc -l)
if [[ "$REMAINING" == "0" ]]; then
  echo "✅ worktree 干净，可进入 /phase2-execute ${BATCH_ID}"
  exit 0
else
  echo "⚠️  还剩 $REMAINING 个脏文件，需人工处理后再 execute"
  exit 1
fi
