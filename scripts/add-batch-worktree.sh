#!/usr/bin/env bash
# add-batch-worktree.sh — 为下一个 batch 创建 worktree
#
# 做的事：
#   1. 前置校验（仓库内、base-ref 存在、目标路径/分支没冲突）
#   2. cd 到主 worktree（git worktree add 从主 worktree 最稳）
#   3. git fetch origin
#   4. git worktree add /home/dantsinghua/work/linchat-<batch-id> \
#                       -b refactor/<batch-id> <base-ref>
#   5. 进新 worktree 做 sanity check（branch / log / status）
#   6. 打印后续手工操作提示（首次启动 services、/phase2-start 等）
#
# 不做的事：
#   - 不自动启动服务（heavy，留给你手动决定）
#   - 不预生成 plan（交给 /phase2-start <id>）
#   - 不触碰业务代码
#
# 用法：
#   bash scripts/add-batch-worktree.sh <batch-id> [base-ref]
#
# 参数：
#   <batch-id>   新 batch 的 id（如 batch-06），决定目标路径和分支名
#   [base-ref]   基点 git ref（commit sha / 分支名 / tag），默认 "refactor/batch-05"
#                也可以是 HEAD (当前 worktree 的 HEAD)
#
# 示例：
#   bash scripts/add-batch-worktree.sh batch-06                      # 默认基点 refactor/batch-05
#   bash scripts/add-batch-worktree.sh batch-06 refactor/batch-05    # 明示当前 batch-05 tip
#   bash scripts/add-batch-worktree.sh batch-06 ee840bf              # 从 batch-04 COMPLETED 起

set -euo pipefail

BATCH_ID="${1:-}"
BASE_REF="${2:-refactor/batch-05}"

if [[ -z "$BATCH_ID" ]]; then
    echo "❌ 用法：$0 <batch-id> [base-ref]"
    echo "   例：$0 batch-06"
    echo "   例：$0 batch-06 refactor/batch-05   # 显式指定基点"
    exit 1
fi

MAIN_DIR="/home/dantsinghua/work/linchat"
NEW_DIR="/home/dantsinghua/work/linchat-${BATCH_ID}"
NEW_BRANCH="refactor/${BATCH_ID}"

note() { echo "[$(date '+%H:%M:%S')] $*"; }
die()  { echo "[$(date '+%H:%M:%S')] ❌ $*" >&2; exit 1; }

# ============ [1/6] 前置校验 ============
note "==> [1/6] 前置校验"

[[ -d "$MAIN_DIR/.git" || -f "$MAIN_DIR/.git" ]] || die "$MAIN_DIR 不是 git 仓库/worktree"

# 目标路径不能已存在
[[ ! -e "$NEW_DIR" ]] || die "目标路径已存在：$NEW_DIR"

# 分支不能已存在（本地或远程）
if git -C "$MAIN_DIR" show-ref --verify --quiet "refs/heads/$NEW_BRANCH"; then
    die "本地分支已存在：$NEW_BRANCH"
fi
note "    目标路径可用：$NEW_DIR"
note "    目标分支可用：$NEW_BRANCH"

# ============ [2/6] 切到主 worktree ============
note ""
note "==> [2/6] 切到主 worktree：$MAIN_DIR"
cd "$MAIN_DIR"
note "    pwd=$(pwd)"

# ============ [3/6] fetch origin ============
note ""
note "==> [3/6] git fetch origin"
git fetch origin 2>&1 | sed 's/^/    /'

# 校验 base-ref 可解析
if ! git rev-parse --verify "$BASE_REF" >/dev/null 2>&1; then
    # 可能是远程分支
    if ! git rev-parse --verify "origin/$BASE_REF" >/dev/null 2>&1; then
        die "base-ref 无法解析：$BASE_REF（不是本地 ref，也不是 origin/$BASE_REF）"
    fi
    BASE_REF="origin/$BASE_REF"
    note "    base-ref 解析为 $BASE_REF"
fi
BASE_SHA=$(git rev-parse --short "$BASE_REF")
BASE_MSG=$(git log -1 --format='%s' "$BASE_REF")
note "    基点 = $BASE_SHA  $BASE_MSG"

# 远程也检查是否有同名分支（避免覆盖）
if git show-ref --verify --quiet "refs/remotes/origin/$NEW_BRANCH"; then
    die "origin 已存在 $NEW_BRANCH；若要重做，先删远程：git push origin --delete $NEW_BRANCH"
fi

# ============ [4/6] git worktree add ============
note ""
note "==> [4/6] 创建 worktree + 新分支"
note "    git worktree add $NEW_DIR -b $NEW_BRANCH $BASE_REF"
git worktree add "$NEW_DIR" -b "$NEW_BRANCH" "$BASE_REF" 2>&1 | sed 's/^/    /'

# ============ [5/6] sanity check ============
note ""
note "==> [5/6] 新 worktree sanity check"
cd "$NEW_DIR"

ACTUAL_BRANCH=$(git branch --show-current)
ACTUAL_HEAD=$(git rev-parse --short HEAD)
DIRTY_COUNT=$(git status --porcelain | wc -l)

[[ "$ACTUAL_BRANCH" == "$NEW_BRANCH" ]] || die "branch 不对：$ACTUAL_BRANCH"
[[ "$ACTUAL_HEAD" == "$BASE_SHA" ]]     || die "HEAD 不对：$ACTUAL_HEAD ≠ $BASE_SHA"
note "    branch = $ACTUAL_BRANCH ✅"
note "    HEAD   = $ACTUAL_HEAD ✅"
note "    dirty  = $DIRTY_COUNT ✅"

note ""
note "    最近 5 条 commit（应与基点完全一致）："
git log --oneline -5 | sed 's/^/      /'

# ============ [6/6] 后续提示 ============
note ""
note "==> [6/6] 完成 — 后续手工操作"
cat <<EOF

📁 新 worktree：$NEW_DIR
🌿 新分支：$NEW_BRANCH  (= $BASE_SHA)

下一步（按需执行）：

  # A. 进入新 worktree
  cd $NEW_DIR

  # B. 启动服务（需要跑 E2E 时才做；首次会 rebuild 前端 ~1-2 分钟）
  bash scripts/start-worktree.sh restart

  # C. 生成 batch plan（在 Claude Code 里）
  /phase2-start $BATCH_ID

  # D. plan 通过 review 后
  /phase2-execute $BATCH_ID

  # E. executed 后验证
  /phase2-validate $BATCH_ID

💡 提示：
  - batch-05 的 cleanup-pre-execute.sh / finalize-batch-05.sh 已被 $NEW_BRANCH 继承；
    新 batch 如需专属 validate 脚本，参考 scripts/validate-batch-05.sh 改写。
  - /phase2-start 前记得 source /home/dantsinghua/work/linchat/linchat/bin/activate
    （虚拟环境只存在于主 worktree）。
EOF
