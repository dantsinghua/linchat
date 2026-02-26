# context 模块指南

> Prompt 与上下文管理模块，负责 System Prompt 组装、对话历史裁剪、Token 计数和上下文监控。

---

## 模块职责

动态组装 LLM 上下文（System Prompt + 记忆 + 历史消息 + 工具定义），管理 Token 预算并在超限时裁剪。

---

## 目录结构

```
apps/context/
├── __init__.py     # 公共 API 重新导出（所有外部导入应从此处引用）
├── types.py        # 数据结构（MessageRole, PromptMessage, PromptConfig, TokenBreakdown 等）
├── builder.py      # PromptBuilder 组装引擎（动态 Prompt 组装 + 模块注册 + 兼容常量）
├── trimmer.py      # 消息裁剪器（按优先级丢弃内容以满足 Token 预算）
├── tokenizer.py    # Token 计数工具（兼容层，实际实现位于 apps.common.tokenizer）
├── loader.py       # Jinja2 模板加载器（render 函数，模板目录指向 templates/）
├── monitoring.py   # 上下文监控服务（告警评估、MonitorData 组装、结构化日志）
├── apps.py         # Django App 配置（ContextConfig）
└── templates/      # Jinja2 Prompt 模板（详见 templates/CLAUDE.md）
```

---

## 核心数据结构 (types.py)

| 类 | 说明 |
|----|------|
| `MessageRole` | 枚举：`system` / `user` / `assistant` |
| `PromptMessage` | Prompt 消息单元（role + content + name），支持 `to_dict()` 序列化 |
| `PromptConfig` | 构建配置（model_name / max_context_window / effective_window_ratio=0.9 / keep_recent_rounds=10 / max_memory_items=5 / memory_token_budget=2000 / user_id / user_display_name / user_timezone） |
| `RetrievedMemory` | 召回的记忆条目（content + memory_type + relevance_score + created_at） |
| `ToolDefinition` | 工具定义（name + description + parameters + enabled） |
| `TokenBreakdown` | Token 分部计数（静态 6 字段 + 动态 3 字段，详见下方） |
| `PromptModule` | 功能模块枚举（BASE / REASONING / TOOL_USAGE / CODE_ASSIST / CREATIVE_WRITING / DATA_ANALYSIS / CUSTOM） |

**`TokenBreakdown`** 字段详情：

| 字段 | 类型 | 说明 |
|------|------|------|
| `system_prompt` | int | 系统 Prompt token 数（静态） |
| `history_messages` | int | 历史消息 token 数（静态） |
| `retrieved_memories` | int | 召回记忆 token 数（静态） |
| `compaction_summary` | int | 压缩摘要 token 数（静态） |
| `tool_definitions` | int | 工具定义 token 数（静态） |
| `user_input` | int | 用户输入 token 数（静态） |
| `tool_calls` | int | 工具调用 token 数（Agent 执行中动态累加） |
| `tool_results` | int | 工具结果 token 数（Agent 执行中动态累加） |
| `tool_call_count` | int | 工具调用次数（Agent 执行中动态累加） |

核心方法：
- `total` -> 所有字段之和（不含 tool_call_count）
- `usage_ratio(max_tokens)` -> 上下文使用率
- `to_dict()` -> 序列化为扁平字典（键名使用简短别名）

---

## PromptBuilder (builder.py)

动态 Prompt 组装引擎，按优先级组装：

| 优先级 | 内容 | 裁剪策略 |
|--------|------|---------|
| P0（不可丢弃） | 基础 System Prompt + 当前用户输入 | 永远保留 |
| P1（最后丢弃） | 最近 N 轮对话历史 | 超限时裁剪 |
| P2（优先丢弃） | 召回记忆 + 压缩摘要 | 按相关度裁剪 |
| P3（可选） | 工具定义 + 功能模块 prompt | 移除非必要工具 |

### 主要 API

| 方法 | 说明 |
|------|------|
| `build_system_prompt()` | 组装完整 System Prompt（基础模板 + 已启用模块 + 自定义模块 + 附加指令） |
| `build_memory_block(memories)` | 将召回记忆格式化为 Jinja2 渲染文本（按相关度排序，限制 max_memory_items 条） |
| `build_compaction_block(summary)` | 格式化压缩摘要为上下文块 |
| `build_tool_context(tools)` | 格式化工具定义列表（含参数说明） |
| `build_conversation_history(history)` | 将对话历史转为 PromptMessage 列表（截取最近 N 轮） |
| `build_conversation_history_block(history)` | 将对话历史格式化为文本块（配对 user+assistant 轮次），嵌入 SystemMessage |
| `build_messages(user_input, ...)` | 组装完整消息列表（dict 格式，用于非 LangGraph 场景） |
| `build_preamble(...)` | 组装 LangGraph 前导消息（SystemMessage 列表，用于 Agent preamble） |
| `build_preamble_with_breakdown(user_input, ...)` | 组装 preamble 并返回 `(preamble_list, TokenBreakdown)` 元组 |

### 模块管理方法

| 方法 | 说明 |
|------|------|
| `enable_module(module)` | 启用 PromptModule |
| `disable_module(module)` | 禁用 PromptModule |
| `enable_custom_module(name)` | 启用已注册的自定义模块 |
| `add_system_instruction(text)` | 追加附加系统指令 |

默认启用模块：`BASE` + `REASONING` + `TOOL_USAGE`

### 模块级函数

| 函数 | 说明 |
|------|------|
| `register_custom_module(name, prompt_text)` | 注册自定义 prompt 模块到全局注册表 |
| `get_module_prompt(module)` | 获取模块 prompt 文本（通过 Jinja2 渲染） |
| `get_custom_module_prompt(name)` | 获取自定义模块 prompt 文本 |

### 兼容性常量

builder.py 底部导出以下兼容常量，供旧代码（如 test_prompts.py）直接导入：

| 常量 | 对应模板 | 说明 |
|------|---------|------|
| `COMPACTION_PROMPT_TEMPLATE` | compaction_task.j2 | 上下文压缩任务 Prompt（含 `{conversation_text}` 占位符） |
| `DAILY_SUMMARY_PROMPT_TEMPLATE` | daily_summary.j2 | 每日总结 Prompt（含 `{conversation_text}`, `{date}` 占位符） |
| `MONTHLY_SUMMARY_PROMPT_TEMPLATE` | monthly_summary.j2 | 月度总结 Prompt（含 `{daily_summaries}`, `{year_month}` 占位符） |
| `CRONMEM_PROMPT_TEMPLATE` | cronmem_extract.j2 | 定时记忆抽取 Prompt（含 `{existing_memories}`, `{conversation_text}` 占位符） |
| `BASE_SYSTEM_PROMPT` | system_base.j2 | 基础系统 Prompt（无参数渲染） |
| `BEHAVIOR_GUIDELINES` | behavior.j2 | 行为准则 |
| `REASONING_GUIDELINES` | reasoning.j2 | 推理指南 |
| `TOOL_USAGE_GUIDELINES` | tool_usage.j2 | 工具使用指南 |
| `MEMORY_CONTEXT_HEADER` | memory_context.j2 | 记忆上下文头部 |
| `MEMORY_CONTEXT_EMPTY` | memory_empty.j2 | 无记忆占位 |
| `TOOL_CONTEXT_HEADER` | tool_context.j2 | 工具上下文头部 |
| `COMPACTION_CONTEXT_HEADER` | compaction_context.j2 | 压缩上下文头部 |

---

## 消息裁剪器 (trimmer.py)

按优先级裁剪消息列表，使总 token 数不超过预算。

### 裁剪优先级（TrimLevel）

| 级别 | 值 | 内容 | 说明 |
|------|---|------|------|
| `PROTECTED` | 0 | system prompt + 最后一条 user 消息 | 不可丢弃 |
| `FIRST` | 1 | 对话历史（user/assistant 消息） | 最先被裁剪 |
| `SECOND` | 2 | 工具内容（name="tools"） | 其次裁剪 |
| `LAST` | 3 | 记忆内容（name="memory"/"compaction"） | 最后裁剪 |

裁剪顺序：L1 -> L2 -> L3，PROTECTED 永不丢弃。

### 主函数

```python
from apps.context.trimmer import trim_messages_to_budget

trimmed = trim_messages_to_budget(messages, token_budget=4096)
```

---

## 监控服务 (monitoring.py)

### AlertLevel 枚举

| 级别 | 使用率阈值 | 日志级别 |
|------|-----------|---------|
| `NORMAL` | < 70% | DEBUG |
| `WARNING` | >= 70% | WARNING |
| `CRITICAL` | >= 90% | ERROR |

### ContextMonitor 类

| 方法 | 说明 |
|------|------|
| `evaluate(breakdown, max_tokens)` | 评估告警级别和使用百分比，返回 `(AlertLevel, float)` |
| `build_monitor_data(breakdown, max_tokens, model_name, ...)` | 组装完整 MonitorData payload（含 breakdown / memory_types / memory_records / tool_processes），并输出结构化日志 |

`build_monitor_data` 额外参数：`input_tokens` / `output_tokens` / `memory_results` / `tool_processes`

MonitorData payload 用于前端上下文监控面板的 SSE 推送。

---

## Token 计数 (tokenizer.py)

兼容层，实际实现位于 `apps.common.tokenizer`。

重新导出以下函数：
- `count_tokens(text)` -> int
- `count_messages_tokens(messages)` -> int
- `_get_encoder()` -> tiktoken Encoder

---

## 模板加载器 (loader.py)

基于 Jinja2 的模板渲染。模板目录为 `apps/context/templates/`。

```python
from apps.context.loader import render

text = render("system_base.j2", today_date="2026-02-14", user_timezone="Asia/Shanghai")
```

---

## 关键导入路径

```python
# 推荐：从 __init__.py 统一导入
from apps.context import PromptBuilder, PromptConfig, TokenBreakdown
from apps.context import count_tokens, count_messages_tokens
from apps.context import trim_messages_to_budget, TrimLevel, TaggedMessage
from apps.context import render_template
from apps.context import (
    COMPACTION_PROMPT_TEMPLATE, DAILY_SUMMARY_PROMPT_TEMPLATE,
    MONTHLY_SUMMARY_PROMPT_TEMPLATE, CRONMEM_PROMPT_TEMPLATE,
)

# 直接导入
from apps.context.monitoring import ContextMonitor, AlertLevel
from apps.context.builder import PromptBuilder
from apps.context.loader import render
```

---

## 数据流

```
chat 视图 / graph Agent
    |
    v
PromptBuilder.build_preamble_with_breakdown(user_input, history, memories, ...)
    |
    ├── build_system_prompt()  -> system_base.j2 + behavior.j2 + reasoning.j2 + tool_usage.j2
    ├── build_compaction_block() -> compaction_context.j2
    ├── build_memory_block()   -> memory_context.j2
    ├── build_tool_context()   -> tool_context.j2
    └── build_conversation_history_block() -> conversation_history.j2
    |
    v
(preamble: list[SystemMessage], breakdown: TokenBreakdown)
    |
    v
ContextMonitor.build_monitor_data(breakdown, ...)  ->  SSE 推送前端监控面板
```

---

## 依赖关系

### 外部依赖
- `jinja2`: 模板渲染
- `tiktoken` (通过 `apps.common.tokenizer`): Token 计数
- `langchain_core.messages.SystemMessage`: LangGraph preamble 构建

### 被依赖
- `apps.chat.services.context_service`: 调用 PromptBuilder 构建上下文
- `apps.graph.agent`: 调用 build_preamble 构建 Agent 前导消息
- `apps.memory.tasks`: 使用兼容常量（CRONMEM_PROMPT_TEMPLATE 等）
- `apps.chat.services.inference_service`: 使用 ContextMonitor 构建监控数据

---

## 测试方法

```bash
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend

# 运行 context 相关测试
pytest tests/chat/test_prompts.py -v

# 带覆盖率
pytest tests/chat/test_prompts.py --cov=apps.context --cov-report=term-missing
```

---

## 注意事项

1. **模板修改需谨慎**: 模板文本直接影响 LLM 行为，修改后需验证生成质量
2. **Token 计数一致性**: 所有 token 计数必须使用 `apps.common.tokenizer.count_tokens()`，禁止自行计算
3. **兼容常量使用 str.format()**: `COMPACTION_PROMPT_TEMPLATE` 等常量中的 `{xxx}` 是 Python str.format() 占位符，不是 Jinja2 变量
4. **PromptBuilder 线程安全**: PromptBuilder 实例不应跨请求共享，每次请求应创建新实例
5. **记忆类型标签映射**: builder.py 中的 `_MEMORY_TYPE_LABELS` 字典控制记忆条目的中文标签显示
