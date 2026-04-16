---
description: 自动扫描 LinChat，生成 docs/legacy-and-debts.md 的数据驱动 draft。安琳 review 后再进入 Phase 1 完整分析。
---

你现在执行 Phase 1a —— Legacy 自动扫描。

## 前置检查

1. `pwd` 确认在 linchat 项目根目录
2. 确认当前 git 状态 clean（或至少无未提交的 backend/frontend 改动）：`git status`
3. 读 `CLAUDE.md` 第 13-15 行，确认当前阶段 ≤ Phase 1（如果用户尚未声明，主动询问是否进入 Phase 1 只读模式）

## 调度子代理

调用 `legacy-scanner` 子代理完成扫描。不要自己做扫描工作，交给子代理，保持主对话上下文干净。

传给子代理的任务描述：

> 扫描 LinChat 仓库，按 `.claude/agents/legacy-scanner.md` 中定义的 8 个步骤执行，
> 最终产出写入 `docs/legacy-and-debts.md`。
> 严格只读，禁止修改业务代码。
> 完成后返回 200 字以内的摘要。

## 子代理完成后

在主对话里给安琳：

1. **一句话总结**：扫描结论（例如："识别出 3 个重点重构候选模块，6 个需要你确认的关键问题"）
2. **Top 3 发现**：从子代理产出中摘出最重要的 3 条
3. **Top 3 必答问题**：子代理第六节列出的、最影响下一步决策的 3 个问题
4. **下一步建议**：
   - 请安琳打开 `docs/legacy-and-debts.md` review
   - 补充第八节列出的主观部分
   - 回答第六节的问题清单
   - 完成后运行 `/phase1-init` 进入完整重构分析

## 不要做的事

- 不要自己读代码做分析，全部委派给 legacy-scanner 子代理
- 不要把子代理产出的全文复制到主对话（它已经在 `docs/legacy-and-debts.md` 里）
- 不要在扫描完后立刻启动 `/phase1-init` —— 必须等安琳 review
- 不要修改任何业务代码
