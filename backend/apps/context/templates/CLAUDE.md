# context/templates 指南

> Jinja2 Prompt 模板目录，由 `apps.context.loader.render()` 渲染。

---

## 模板清单

### 系统 Prompt 模板（PromptBuilder 组装时使用）

| 模板 | 用途 | Jinja2 变量 |
|------|------|-------------|
| `system_base.j2` | 基础 System Prompt（角色定义 + 日期 + 时区） | `today_date`, `user_timezone` |
| `behavior.j2` | 回复规范模块（语言风格、准确性、安全隐私） | 无 |
| `reasoning.j2` | 思维与推理模块（结构化思考、任务分解、上下文感知） | 无 |
| `tool_usage.j2` | 工具使用指南模块（调用原则、并行调用、失败处理） | 无 |
| `code_assist.j2` | 代码辅助专项模块（编码规范、问题诊断、安全意识） | 无 |
| `creative_writing.j2` | 创意写作专项模块（写作原则、格式结构） | 无 |
| `data_analysis.j2` | 数据分析专项模块（分析方法、数据质量） | 无 |

### 上下文注入模板

| 模板 | 用途 | Jinja2 变量 |
|------|------|-------------|
| `memory_context.j2` | 记忆上下文注入（含引导语 + 记忆条目列表） | `memory_entries` |
| `memory_empty.j2` | 无记忆时的占位文本 | 无 |
| `tool_context.j2` | 工具上下文注入（可用工具列表） | `tool_definitions` |
| `compaction_context.j2` | 压缩摘要上下文（之前对话的压缩摘要） | `compaction_summary` |
| `conversation_history.j2` | 对话历史格式化（配对的 user/assistant 轮次） | `turns`（列表，每项含 `user` 和 `assistant` 键） |

### 任务 Prompt 模板（Celery 定时任务 / Agent 调用时使用）

| 模板 | 用途 | Jinja2 变量 |
|------|------|-------------|
| `compaction_task.j2` | 上下文压缩任务 Prompt（将对话压缩为摘要） | `conversation_text` |
| `daily_summary.j2` | 每日对话总结 Prompt（结构化日摘要） | `conversation_text`, `date` |
| `monthly_summary.j2` | 月度综合摘要 Prompt（基于每日摘要归纳） | `daily_summaries`, `year_month` |
| `cronmem_extract.j2` | 定时记忆事实抽取 Prompt（从对话中提取用户事实信息） | `existing_memories`, `conversation_text` |

---

## 模块与模板映射关系

`PromptModule` 枚举通过 `builder.py` 中的 `_MODULE_TEMPLATES` 字典映射到模板文件：

| PromptModule | 模板文件 |
|-------------|---------|
| `BASE` | `behavior.j2` |
| `REASONING` | `reasoning.j2` |
| `TOOL_USAGE` | `tool_usage.j2` |
| `CODE_ASSIST` | `code_assist.j2` |
| `CREATIVE_WRITING` | `creative_writing.j2` |
| `DATA_ANALYSIS` | `data_analysis.j2` |
| `CUSTOM` | 无映射（通过自定义注册表获取） |

注意：`system_base.j2` 不通过模块映射，而是由 `build_system_prompt()` 直接渲染。

---

## 使用方式

```python
from apps.context.loader import render

# 无参数渲染
text = render("behavior.j2")

# 带参数渲染
text = render("system_base.j2", today_date="2026-02-14", user_timezone="Asia/Shanghai")
text = render("memory_context.j2", memory_entries="1. [记忆] 用户喜欢Python")
text = render("conversation_history.j2", turns=[{"user": "你好", "assistant": "你好！"}])
```

---

## 注意事项

1. 模板文件使用 UTF-8 编码，支持中文内容
2. `loader.py` 的 Jinja2 Environment 配置了 `keep_trailing_newline=True`，保留模板末尾换行
3. 任务 Prompt 模板（compaction_task / daily_summary 等）在 builder.py 中通过 `render()` 预渲染为兼容常量，使用 `str.format()` 占位符（如 `{conversation_text}`）而非 Jinja2 变量
4. 修改模板内容会直接影响 LLM 生成行为，需谨慎测试
