#!/usr/bin/env bash
# finalize-batch-05.sh — batch-05 收尾：提交验证产出 + push
#
# 做两个独立 commit（遵循 CLAUDE.md 的 "<type>(<scope>): <desc>" 规范）：
#   1. validate(batch-05) — validation.md / runtime-e2e.md / progress.txt / 04-refactor-plan.json
#   2. chore(scripts)     — validate-batch-05.sh
#
# 然后 push 到 origin/refactor/batch-05。
#
# 明确不触碰：frontend/node_modules（symlink，.gitignore 里虽有 node_modules/ 但
# 不匹配 symlink；使用显式文件路径避免误加）
#
# 前置：batch-05-progress.txt 底部 STATUS: COMPLETED；当前分支 refactor/batch-05
#
# 用法：bash scripts/finalize-batch-05.sh
# 退出：0 成功；非零 = 中途失败

set -euo pipefail

WORKTREE="/home/dantsinghua/work/linchat-batch-05"
cd "$WORKTREE"

note() { echo "[$(date '+%H:%M:%S')] $*"; }
die()  { echo "[$(date '+%H:%M:%S')] ❌ $*" >&2; exit 1; }

# ============ 前置检查 ============
note "==> [1/5] 前置检查"

# 分支
BRANCH=$(git branch --show-current)
[[ "$BRANCH" == "refactor/batch-05" ]] || die "当前分支是 $BRANCH，应是 refactor/batch-05"
note "    branch=$BRANCH ✅"

# STATUS 已 COMPLETED
STATUS=$(grep "^STATUS:" refactor/batches/batch-05-progress.txt | tail -1)
[[ "$STATUS" == "STATUS: COMPLETED" ]] || die "progress 不是 COMPLETED，是 '$STATUS'"
note "    $STATUS ✅"

# 四个预期文件都在
for f in \
    refactor/batches/batch-05-validation.md \
    refactor/batches/batch-05-runtime-e2e.md \
    refactor/batches/batch-05-progress.txt \
    refactor/04-refactor-plan.json \
    scripts/validate-batch-05.sh; do
    [[ -f "$f" ]] || die "缺文件：$f"
done
note "    5 个目标文件齐全 ✅"

# 04-refactor-plan.json 确实标了 completed
ACTUAL=$(jq -r '.batches[] | select(.id == "batch-05") | .actual_status // "null"' refactor/04-refactor-plan.json)
[[ "$ACTUAL" == "completed" ]] || die "batch-05.actual_status=$ACTUAL，不是 completed"
note "    batch-05.actual_status=$ACTUAL ✅"

# ============ Commit 1：validation 产出 ============
note ""
note "==> [2/5] Commit 1 — validate(batch-05) Phase 2c 产出"

git add \
    refactor/batches/batch-05-validation.md \
    refactor/batches/batch-05-runtime-e2e.md \
    refactor/batches/batch-05-progress.txt \
    refactor/04-refactor-plan.json

# 校验只有这 4 个文件在暂存区
STAGED=$(git diff --cached --name-only)
EXPECTED=$'refactor/04-refactor-plan.json\nrefactor/batches/batch-05-progress.txt\nrefactor/batches/batch-05-runtime-e2e.md\nrefactor/batches/batch-05-validation.md'
ACTUAL_STAGED=$(echo "$STAGED" | sort | tr '\n' '|')
EXPECTED_SORTED=$(echo "$EXPECTED" | sort | tr '\n' '|')
if [[ "$ACTUAL_STAGED" != "$EXPECTED_SORTED" ]]; then
    die "暂存区不符合预期\n  expected:\n$EXPECTED\n  actual:\n$STAGED"
fi
note "    暂存区文件符合预期"

git commit -m "validate(batch-05): Phase 2c COMPLETED — 8/8 E2E PASS + 标记 actual_status=completed

- Gate 1: 478 target tests passed
- Gate 2: 1606 full suite / 0 failures (≥ baseline 1603)
- Voice regression: 688 passed (contextvar 幂等 set 无回归)
- Ruff: 12 E701/E702 全 batch-03 pre-existing 残留，净减 1 零新增
- 手动 E2E（scripts/validate-batch-05.sh，8/8 PASS）:
  * JSON 日志合法、TID 23 条、5 类 logger 覆盖
  * 响应头 X-Request-ID 回写、DB.request_id==TID
  * Langfuse trace 包含 TID、并发 0 crosstalk
  * Gateway span metadata.trace_id: soft pass（plain chat 不触发 record_gateway_span，留多模态/文档/语音场景补证）"

note "    commit $(git log -1 --format='%h') 已创建"

# ============ Commit 2：validate 脚本 ============
note ""
note "==> [3/5] Commit 2 — chore(scripts) validate-batch-05.sh"

# 可能还会有 finalize 脚本自己（本脚本执行前已存在于 fs 但 untracked）
git add scripts/validate-batch-05.sh

STAGED2=$(git diff --cached --name-only)
if [[ "$STAGED2" != "scripts/validate-batch-05.sh" ]]; then
    die "暂存区不符合预期，应只有 scripts/validate-batch-05.sh，实际：\n$STAGED2"
fi

git commit -m "chore(scripts): 新增 validate-batch-05.sh — batch-05 运行时 E2E 校验脚本

8 项校验覆盖：
  1. 后端日志合法 JSON
  2. 单请求 X-Request-ID 贯穿（logger 覆盖 + 日志条数 ≥ 10）
  3. 响应头 X-Request-ID 回写 + DB Message.request_id 匹配
  4. Langfuse trace 包含 trace_id（API 查询）
  5. Gateway span metadata.trace_id 继承（soft pass 模式，容忍 plain text chat 不触发）
  6. 并发 2 条 TID 无 contextvar 串扰

铸造 admin token 走 Django shell 直写 Redis（绕过 captcha），不依赖 Playwright 登录流。"

note "    commit $(git log -1 --format='%h') 已创建"

# ============ Push ============
note ""
note "==> [4/5] push 到 origin/refactor/batch-05"
git push origin refactor/batch-05
note "    push 完成"

# ============ 总结 ============
note ""
note "==> [5/5] 完成"
note "最近 4 条 commit："
git log --oneline -4
note ""
note "剩余 worktree 状态："
git status --short | grep -v "^?? frontend/node_modules" || true
note "（frontend/node_modules 是 start-worktree.sh 建的 symlink，.gitignore 对 symlink 不敏感，保留）"
note ""
note "✅ batch-05 收尾完成，可进 batch-06"
