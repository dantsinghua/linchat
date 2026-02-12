# context 模块指南

> Prompt 与上下文管理模块，负责 System Prompt 组装、对话历史裁剪、Token 计数和上下文监控。

---

## 模块职责

动态组装 LLM 上下文（System Prompt + 记忆 + 历史消息 + 工具定义），管理 Token 预算并在超限时裁剪。

---

## 目录结构

```
apps/context/
├── __init__.py     # 公共 API 重新导出
├── types.py        # 数据结构（MessageRole, PromptMessage, PromptConfig, TokenBreakdown 等）
├── builder.py      # PromptBuilder 组装引擎（动态 Prompt 组装 + 模块注册）
├── trimmer.py      # 消息裁剪器（按优先级丢弃内容以满足 Token 预算）
├── tokenizer.py    # Token 计数工具（基于 tiktoken）
├── loader.py       # Jinja2 模板渲染（render 函数）
├── monitoring.py   # 上下文监控服务（告警评估、MonitorData 组装）
├── apps.py         # Django App 配置
└── templates/      # Jinja2 Prompt 模板（详见 templates/CLAUDE.md）
```

---

## 核心数据结构 (types.py)

| 类 | 说明 |
|----|------|
| `MessageRole` | 枚举：`system` / `user` / `assistant` |
| `PromptMessage` | Prompt 消息单元（role + content） |
| `PromptConfig` | 构建配置（模型名、上下文窗口、保留轮数、记忆预算等） |
| `RetrievedMemory` | 召回的记忆条目（content + type + score） |
| `ToolDefinition` | 工具定义（name + description + parameters） |
| `TokenBreakdown` | Token 分部计数（system_prompt / history / memories / tools / user_input 等） |
| `PromptModule` | 功能模块枚举（base / reasoning / tool_usage / code_assist 等） |

**`TokenBreakdown`** 核心方法：
- `total` → 所有字段之和
- `usage_ratio(max_tokens)` → 上下文使用率
- `to_dict()` → 序列化为扁平字典

---

## PromptBuilder (builder.py)

动态 Prompt 组装引擎，按优先级组装：

| 优先级 | 内容 | 裁剪策略 |
|--------|------|---------|
| P0（不可丢弃） | 基础 System Prompt + 当前用户输入 | 永远保留 |
| P1（最后丢弃） | 最近 N 轮对话历史 | 压缩为摘要 |
| P2 | 记忆上下文 | 按相关度裁剪 |
| P3 | 工具定义 | 移除非必要工具 |

关键 API：
```python
from apps.context import PromptBuilder, PromptConfig

builder = PromptBuilder(config=PromptConfig(...))
messages, breakdown = builder.build(user_input="...", history=[...], memories=[...])
```

---

## 监控服务 (monitoring.py)

| 类 | 方法 | 说明 |
|----|------|------|
| `ContextMonitor` | `evaluate(breakdown, max_tokens)` | 评估告警级别和使用百分比 |
| `AlertLevel` | 枚举 | `normal` / `warning`(>70%) / `critical`(>90%) |

---

## 关键导入路径

```python
from apps.context import PromptBuilder, PromptConfig, TokenBreakdown
from apps.context import count_tokens, trim_messages_to_budget
from apps.context import render_template
from apps.context.monitoring import ContextMonitor, AlertLevel
```
