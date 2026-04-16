---
description: Phase 1 完整重构分析。调度 4 个子代理，产出架构图、运行问题、性能瓶颈、分批次重构方案。只读，不修改业务代码。
---

你现在执行 Phase 1 完整重构分析。**严格按以下步骤顺序执行，不要跳步，不要自己做分析**。

## 前置检查

1. `pwd` 确认在 linchat 项目根目录
2. `git status` 确认 backend/frontend 无未提交改动
3. 确认 `docs/legacy-and-debts.md` 已被安琳 review（检查文件末尾是否还有"Draft"标记；如果有，提醒安琳先 review，不要继续）
4. 创建或恢复进度文件：

```bash
mkdir -p refactor
if [ ! -f refactor/claude-progress.txt ]; then
  cat > refactor/claude-progress.txt <<'EOF'
# LinChat Phase 1 Progress Log

## Session Start: <当前时间>
- [ ] Step 1: architecture-analyzer  → refactor/01-architecture-map.md
- [ ] Step 2: log-diagnostician      → refactor/02-issue-diagnosis.md
- [ ] Step 3: call-chain-profiler    → refactor/03-call-chain-analysis.md
- [ ] Step 4: refactor-planner       → refactor/04-refactor-plan.json + 05-executive-summary.md

## Pending Decisions (由各子代理汇总)

## Notes
EOF
fi
```

## 核心约束（整个流程不变）

- **所有分析委派给子代理**，不要在主对话中自己读 backend/ 的代码
- **主对话只读**：CLAUDE.md、docs/legacy-and-debts.md、refactor/*.md、refactor/*.json
- 每个子代理完成后，在主对话中**仅输出 ≤ 200 字的摘要**，不要复制产出全文
- 每个子代理完成后，更新 `refactor/claude-progress.txt` 勾选对应项，追加发现的 Open Questions

## Step 1: 架构分析

调用 `architecture-analyzer` 子代理。

任务描述：

> 按 `.claude/agents/architecture-analyzer.md` 中的 8 个步骤执行架构分析。
> 先读 `docs/legacy-and-debts.md` 作为先验，再扫描 `backend/`。
> 产出写入 `refactor/01-architecture-map.md`，篇幅不超过 500 行。
> 完成后返回 ≤ 200 字摘要。

完成后：
- 验证 `refactor/01-architecture-map.md` 存在且 > 100 行
- 更新进度文件
- 把子代理摘要贴在主对话

**如果子代理产出异常（< 50 行或格式错误），重新调用一次，并告知"第一次产出不完整"**。

## Step 2: 日志诊断

调用 `log-diagnostician` 子代理。

任务描述：

> 按 `.claude/agents/log-diagnostician.md` 的 8 个步骤执行。
> 重点定位 `logs/` 下的日志文件；如果日志缺失，诚实说明数据限制。
> 产出写入 `refactor/02-issue-diagnosis.md`。
> 完成后返回 ≤ 200 字摘要。

完成后同 Step 1。

**如果日志完全缺失或不可读，子代理应产出一份"数据限制说明"文档，标注需要安琳手动提供日志导出。不要伪造数据。**

## Step 3: 调用链性能分析

调用 `call-chain-profiler` 子代理。

任务描述：

> 按 `.claude/agents/call-chain-profiler.md` 的 8 个步骤执行。
> 先读 `docs/legacy-and-debts.md` 和 `refactor/02-issue-diagnosis.md` 作为先验。
> 产出写入 `refactor/03-call-chain-analysis.md`。
> 只做静态分析，禁止跑压测或真实 LLM 调用。
> 完成后返回 ≤ 200 字摘要。

完成后同 Step 1。

## Step 4: 综合重构方案

调用 `refactor-planner` 子代理。

任务描述：

> 按 `.claude/agents/refactor-planner.md` 执行。
> 读取 `CLAUDE.md`、`docs/legacy-and-debts.md`、以及 `refactor/01-03` 三份产出。
> 产出 `refactor/04-refactor-plan.json` 和 `refactor/05-executive-summary.md`。
> 严格遵守每个 batch ≤ 15 文件、≤ 500 行、单 commit 可回滚的约束。
> 尊重安琳在 legacy 第七节的"重构禁区"。
> 完成后返回 ≤ 200 字摘要。

完成后：
- 验证两份产出都存在
- 验证 04-refactor-plan.json 是合法 JSON：`python -c "import json; json.load(open('refactor/04-refactor-plan.json'))"`
- 如果 JSON 格式错误，让 refactor-planner 修复

## Step 5: 汇总交付

所有子代理完成后，在主对话中给安琳：

1. **一段总体评估**（≤ 200 字）
   - 项目健康度打分
   - 关键发现
   - 推荐的起步 batch

2. **明确指向两份关键文件**
   - `refactor/05-executive-summary.md`（必读）
   - `refactor/04-refactor-plan.json`（供 Phase 2 消费）

3. **Pending Decisions 清单**（从 claude-progress.txt 汇总）
   - 列出所有需要安琳回答的问题
   - 标明每个问题阻塞哪个 batch

4. **明确的下一步**
   - 请安琳 review 05-executive-summary.md
   - 回答 Pending Decisions
   - 确认无误后，运行 `/phase2-start batch-01` 进入执行阶段

## 错误处理

- 如果任何子代理崩溃或产出异常，**停止**，在主对话报告问题，让安琳决定是重跑、跳过还是中止
- 不要为了"完成流程"而伪造产出
- 如果上下文接近上限（>150k tokens），主动提示："主对话上下文接近上限，建议结束本 session，下次从 `/phase1-resume` 继续"

## 完成标志

主对话最终一条消息包含以下要素即算完成：

- [x] 4 份产出文件都存在且格式正确
- [x] refactor/claude-progress.txt 4 项全部勾选
- [x] Pending Decisions 清晰列出
- [x] 明确的下一步行动给到安琳

## 禁止

- 禁止自己读 backend/frontend 代码做分析（全部委派）
- 禁止修改业务代码
- 禁止跳过任何子代理
- 禁止产出 04-refactor-plan.json 时违反 CLAUDE.md 红线
- 禁止在未完成 Step 1-3 时先跑 Step 4
