# context 模块指南

> **单用户单会话原则**: 一个用户永远对应一个会话，Message 中没有 conversation_id，只有 user_id。不存在多会话、不考虑并发会话。所有隔离按 user_id 粒度。

---

## 模块职责

动态组装 LLM 上下文（System Prompt + 记忆 + 历史消息 + 工具定义），管理 Token 预算并在超限时裁剪。无数据模型。

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `__init__.py` | 公共 API 统一导出（所有外部导入应从此处引用） |
| `types.py` | 数据结构：MessageRole, PromptMessage, PromptConfig, RetrievedMemory, ToolDefinition, TokenBreakdown, PromptModule |
| `builder.py` | PromptBuilder 组装引擎 + 模块注册 + 兼容常量（COMPACTION_PROMPT_TEMPLATE 等） |
| `builder_helpers.py` | **新增** — 从 builder.py 提取的辅助函数：`format_memory_block()`、`format_tool_context()`、`pair_conversation_turns()` |
| `trimmer.py` | 消息裁剪器：按 TrimLevel 优先级丢弃内容以满足 Token 预算 |
| `tokenizer.py` | 兼容层 — 实际实现在 `apps.common.tokenizer` |
| `loader.py` | Jinja2 模板加载器（`render()` 函数，模板目录 `templates/`） |
| `monitoring.py` | 上下文监控：AlertLevel 枚举 + ContextMonitor（告警评估、MonitorData 组装） |
| `templates/` | 16 个 Jinja2 Prompt 模板（详见下方） |

---

## PromptBuilder (builder.py)

核心方法: `build_system_prompt()`, `build_memory_block()`, `build_compaction_block()`, `build_tool_context()`, `build_conversation_history()`, `build_conversation_history_block()`, `build_messages()`, `build_preamble()`, `build_preamble_with_breakdown()`。

`builder_helpers.py`（**新增**）提取了辅助函数: `format_memory_block()`, `format_tool_context()`, `pair_conversation_turns()`, `_MEMORY_TYPE_LABELS`。

---

## 裁剪优先级 (trimmer.py)

| TrimLevel | 内容 | 策略 |
|-----------|------|------|
| PROTECTED (0) | system prompt + 最后一条 user 消息 | 不可丢弃 |
| FIRST (1) | 对话历史 | 最先裁剪 |
| SECOND (2) | 工具内容 | 其次裁剪 |
| LAST (3) | 记忆/压缩摘要 | 最后裁剪 |

---

## 监控 (monitoring.py)

| AlertLevel | 使用率 | 日志级别 |
|------------|--------|---------|
| NORMAL | < 70% | DEBUG |
| WARNING | >= 70% | WARNING |
| CRITICAL | >= 90% | ERROR |

`ContextMonitor.build_monitor_data()` 组装完整监控 payload，用于前端 SSE 推送。

---

## 模板文件 (templates/) — 16 个 Jinja2 模板

核心: `system_base.j2`, `behavior.j2`, `reasoning.j2`, `tool_usage.j2`
可选模块: `code_assist.j2`, `creative_writing.j2`, `data_analysis.j2`
上下文块: `memory_context.j2`, `memory_empty.j2`, `compaction_context.j2`, `compaction_task.j2`, `tool_context.j2`, `conversation_history.j2`
记忆任务: `daily_summary.j2`, `monthly_summary.j2`, `cronmem_extract.j2`

---

## 被依赖

- `apps.graph.services.context_service`: 调用 PromptBuilder 构建上下文
- `apps.graph.agent`: 调用 build_preamble 构建 Agent 前导消息
- `apps.memory.tasks`: 使用兼容常量（CRONMEM_PROMPT_TEMPLATE 等）
- `apps.graph.services.inference_service`: 使用 ContextMonitor 构建监控数据

---

## 注意事项

1. 模板修改直接影响 LLM 行为，修改后需验证生成质量
2. 所有 token 计数必须使用 `apps.common.tokenizer.count_tokens()`
3. 兼容常量（`COMPACTION_PROMPT_TEMPLATE` 等）使用 `str.format()` 占位符，不是 Jinja2 变量
4. PromptBuilder 实例不应跨请求共享，每次请求创建新实例
