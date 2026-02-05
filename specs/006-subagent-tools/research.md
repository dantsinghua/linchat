# Research: 主对话流程 SubAgent 化重构

**Feature**: 006-subagent-tools
**Date**: 2026-02-05

---

## R-001: SubAgent 作为 LangChain Tool 的实现模式

**Decision**: 使用 `@tool` 装饰的异步函数，内部创建 `create_react_agent` + `ainvoke` 执行。

**Rationale**:
- 与现有工具模式（`@tool` 装饰器）完全一致，无需引入新抽象
- 利用 LangChain function calling 自动路由，主 agent LLM 根据工具描述决定委派
- SubAgent 内部工具调用对主 agent 完全透明
- `ainvoke` 而非 `astream`，SubAgent 结果需要作为整体 tool result 返回

**Alternatives considered**:
- LangGraph `Command` 模式 — 过于复杂，需要自定义 StateGraph，不适合"工具替换"场景
- 嵌套 StateGraph — 调试困难，架构复杂度过高
- 简单函数包装（不用 agent） — 失去多步推理和自纠错能力

---

## R-002: user_id 传递机制

**Decision**: 通过 `RunnableConfig.configurable.user_id` 传递，与现有工具完全一致。

**Rationale**: 无需引入新机制。SubAgent tool 函数从 config 提取 user_id，传递给内部 agent 的 config，内部工具（search/memory/repl）自动从 config 获取 user_id。

**Alternatives considered**: 无 — 现有机制已是最佳方案。

---

## R-003: 流式输出兼容性

**Decision**: SubAgent 使用 `ainvoke` 非流式执行。主 agent 的 `astream_events` 会捕获嵌套事件，需在 `agent_service.py` 中过滤 SubAgent 的 `on_chat_model_stream` 事件。

**Rationale**:
- 用户只应看到主 agent 整合后的流式回复
- SubAgent 内部 LLM 的 stream 输出属于中间过程，不应暴露给用户
- 过滤方式：检查 `on_chat_model_stream` 事件的 `tags` 或 `parent_ids`，仅处理主 agent 级别的事件

**Alternatives considered**:
- SubAgent 也流式输出 — 违反规范（用户不应看到 SubAgent 中间过程）
- 不过滤事件 — SubAgent 的 LLM 中间输出会被误当作最终回复

---

## R-004: 超时控制

**Decision**: SubAgent tool 函数内部使用 `asyncio.timeout(60)` 包裹 `ainvoke`。超时后返回友好文本给主 agent。

**Rationale**: 简单可控，与 Python 原生异步超时机制一致。超时不会导致异常冒泡中断主 agent，而是作为 tool result 返回错误信息。

**Alternatives considered**:
- LangGraph 内置超时 — 需要额外配置，且超时行为不够可控
- 不设超时 — 依赖外层 `AGENT_TOTAL_TIMEOUT`，但 SubAgent 可能长时间阻塞

---

## R-005: 监控面板兼容性

**Decision**: 不过滤 `on_tool_end` 和 `on_chat_model_end` 事件，全部累加 token 统计。SubAgent 级和内部工具级的 `on_tool_end` 都记录到 `tool_processes`。

**Rationale**:
- `total_prompt_tokens` / `total_completion_tokens` 应反映真实总消耗（包括 SubAgent 内部 LLM 调用）
- `tool_processes` 展示两层调用链路（SubAgent + 内部工具），提供更细粒度的可观测性
- `breakdown.tool_calls` / `tool_results` 累加所有层级的工具 token

**Alternatives considered**:
- 只记录 SubAgent 级 — 丢失内部工具调用细节
- 过滤 SubAgent 内部事件 — token 统计不准确

---

## R-006: prompt 模板精简策略

**Decision**: `tool_usage.j2` 从 ~85 行精简到 ~15 行（仅通用原则），具体工具使用指南移入各 SubAgent 内部 prompt。

**Token 减少估算**:
- 当前 `tool_usage.j2`: ~85 行，~1200 tokens
- 精简后: ~15 行，~200 tokens
- 减少: ~83%，满足 SC-005 (>50%)

**Rationale**: 主 agent 只需理解 SubAgent 的能力边界（通过 tool description），不需要知道具体工具操作细节。详细指南嵌入 SubAgent 内部 prompt，不占用主 agent 的 context window。

**Alternatives considered**:
- 完全移除 `tool_usage.j2` — 通用工具使用原则仍有价值（并行调用、错误处理等）
- 保留部分工具描述 — 违反 SubAgent 封装原则，主 agent 不应感知具体工具
